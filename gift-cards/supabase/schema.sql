-- Family Gift Cards: sync between phones.
--
-- Each family is an append-only log of change events. Phones push the changes
-- they make and pull everyone else's, then rebuild the same state from the log.
-- A family is identified only by its secret family code (stored hashed); the
-- tables are closed to the public API roles and reachable only through the
-- gc_* functions below.

create table if not exists public.gc_families (
  id          uuid primary key default gen_random_uuid(),
  secret_hash bytea not null unique,
  last_seq    bigint not null default 0,
  bytes       bigint not null default 0,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create table if not exists public.gc_events (
  family_id  uuid not null references public.gc_families (id) on delete cascade,
  seq        bigint not null,
  event_id   uuid not null,
  device_id  text not null,
  body       jsonb not null,
  created_at timestamptz not null default now(),
  primary key (family_id, seq),
  unique (family_id, event_id)
);

alter table public.gc_families enable row level security;
alter table public.gc_events enable row level security;
revoke all on table public.gc_families, public.gc_events from anon, authenticated;

-- Register a new family code. Idempotent, so a retried request is harmless.
create or replace function public.gc_create_family(p_secret text)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_hash bytea;
begin
  if p_secret is null or p_secret !~ '^[0-9A-Z]{20,64}$' then
    raise exception 'bad_code';
  end if;
  v_hash := sha256(convert_to(p_secret, 'UTF8'));

  perform pg_advisory_xact_lock(hashtext('gc_create_family'));
  if exists (select 1 from public.gc_families where secret_hash = v_hash) then
    return jsonb_build_object('ok', true);
  end if;
  -- The app serves one household; the cap stops strangers filling the database.
  if (select count(*) from public.gc_families) >= 20 then
    raise exception 'server_full';
  end if;

  insert into public.gc_families (secret_hash) values (v_hash);
  return jsonb_build_object('ok', true);
end;
$$;

-- Append events to a family's log. Events already stored (same id) are skipped,
-- so resending after a dropped connection never duplicates a change.
create or replace function public.gc_push(p_secret text, p_device text, p_events jsonb)
returns jsonb
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_fam   record;
  v_ev    jsonb;
  v_id    uuid;
  v_seq   bigint;
  v_bytes bigint;
  v_size  integer;
  v_added integer := 0;
begin
  if p_secret is null or p_secret !~ '^[0-9A-Z]{20,64}$' then
    raise exception 'bad_code';
  end if;
  if p_device is null or p_device !~ '^[0-9A-Za-z-]{1,64}$' then
    raise exception 'bad_device';
  end if;
  if p_events is null or jsonb_typeof(p_events) <> 'array'
     or jsonb_array_length(p_events) not between 1 and 200 then
    raise exception 'bad_events';
  end if;
  if octet_length(p_events::text) > 2000000 then
    raise exception 'too_big';
  end if;

  -- The row lock serializes writers per family, so seq numbers are handed out
  -- (and committed) strictly in order and a reader's cursor never skips one.
  select id, last_seq, bytes into v_fam
    from public.gc_families
   where secret_hash = sha256(convert_to(p_secret, 'UTF8'))
     for update;
  if not found then
    raise exception 'family_not_found';
  end if;

  v_seq := v_fam.last_seq;
  v_bytes := v_fam.bytes;
  for v_ev in select value from jsonb_array_elements(p_events) loop
    if jsonb_typeof(v_ev) is distinct from 'object'
       or coalesce(v_ev ->> 'id', '') !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
       or jsonb_typeof(v_ev -> 'type') is distinct from 'string' then
      raise exception 'bad_event';
    end if;
    v_size := octet_length(v_ev::text);
    if v_size > 1000000 then
      raise exception 'too_big';
    end if;
    v_id := (v_ev ->> 'id')::uuid;
    continue when exists (
      select 1 from public.gc_events where family_id = v_fam.id and event_id = v_id
    );

    v_seq := v_seq + 1;
    v_bytes := v_bytes + v_size;
    insert into public.gc_events (family_id, seq, event_id, device_id, body)
    values (v_fam.id, v_seq, v_id, p_device, v_ev);
    v_added := v_added + 1;
  end loop;

  if v_bytes > 20000000 then
    raise exception 'family_full';
  end if;

  update public.gc_families
     set last_seq = v_seq, bytes = v_bytes, updated_at = now()
   where id = v_fam.id;

  return jsonb_build_object('last_seq', v_seq, 'added', v_added);
end;
$$;

-- Read a family's events after a cursor, in order. Pages stop at about 2 MB.
create or replace function public.gc_pull(p_secret text, p_after bigint default 0, p_limit integer default 500)
returns jsonb
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_id     uuid;
  v_last   bigint;
  v_events jsonb;
begin
  if p_secret is null or p_secret !~ '^[0-9A-Z]{20,64}$' then
    raise exception 'bad_code';
  end if;

  select id, last_seq into v_id, v_last
    from public.gc_families
   where secret_hash = sha256(convert_to(p_secret, 'UTF8'));
  if not found then
    raise exception 'family_not_found';
  end if;

  select coalesce(jsonb_agg(jsonb_build_object('seq', x.seq, 'body', x.body) order by x.seq), '[]'::jsonb)
    into v_events
    from (
      select e.seq, e.body,
             sum(octet_length(e.body::text)) over (order by e.seq) - octet_length(e.body::text) as bytes_before
        from public.gc_events e
       where e.family_id = v_id
         and e.seq > greatest(coalesce(p_after, 0), 0)
         and e.seq <= v_last
       order by e.seq
       limit greatest(1, least(coalesce(p_limit, 500), 1000))
    ) x
   where x.bytes_before < 2000000;

  return jsonb_build_object('last_seq', v_last, 'events', v_events);
end;
$$;

revoke all on function public.gc_create_family(text) from public;
revoke all on function public.gc_push(text, text, jsonb) from public;
revoke all on function public.gc_pull(text, bigint, integer) from public;
grant execute on function public.gc_create_family(text) to anon, authenticated;
grant execute on function public.gc_push(text, text, jsonb) to anon, authenticated;
grant execute on function public.gc_pull(text, bigint, integer) to anon, authenticated;
