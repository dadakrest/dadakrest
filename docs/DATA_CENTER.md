# Software da Database User 13

A small AI data center written in Python. It provisions and operates **seven
SQLite databases**, indexes documents for semantic search, and keeps an audit
trail of everything that happens.

No third-party packages. No network access required. Python 3.10 or newer.

## The seven databases

Each database is its own file under the storage root, so one domain can be
backed up, inspected or replaced without touching the others.

| # | Database     | What it holds                                                   |
|---|--------------|-----------------------------------------------------------------|
| 1 | `contacts`   | People and organizations, with their channels                    |
| 2 | `documents`  | Documents, their text, metadata and tags                         |
| 3 | `datasets`   | Structured records grouped into named datasets                   |
| 4 | `models`     | Registry of AI models and their versions                         |
| 5 | `embeddings` | Vector index backing semantic search                             |
| 6 | `jobs`       | Queue of processing work, with a run history                     |
| 7 | `audit`      | Append-only log of every write and operation                     |

## Quick start

```bash
python -m datacenter init                  # create the seven databases
python -m datacenter seed                  # load demo contacts, documents, records
python -m datacenter status                # health, size and row counts
python -m datacenter search "who do I page during a security incident"
```

```
0.4907  #4    Incident response contacts
```

Databases land in `./storage` by default. Override with `--root somewhere/else`
or the `SDDU13_ROOT` environment variable.

Installing the package (`pip install -e .`) also gives you an `sddu13` command
that behaves identically to `python -m datacenter`.

## Commands

| Command | Does |
|---|---|
| `init` | Create all seven databases and apply their schemas. Idempotent. |
| `seed [--data DIR]` | Validate the JSON data files and load them. A refill: safe to repeat. |
| `validate-data [--data DIR]` | Check the data files without writing anything. |
| `status [--json]` | Per-database state, size and row counts. |
| `search QUERY [--limit N]` | Semantic search over indexed documents (`N` is at least 1). |
| `add-document TITLE [--body ... \| --file ...] [--tag t]` | Store and index a document. |
| `add-contact NAME [--email ... --organization ...]` | Store a contact. |
| `documents` / `contacts` / `datasets` | List what is stored. |
| `fees [--table government\|service\|employment]` | Show the fee tables. |
| `plans` | List the business plans with their totals. |
| `plan NAME [--json]` | Cost one plan, line by line. |
| `portal [--host H] [--port P]` | Serve the read-only web portal. |
| `jobs [--run]` | Show the queue, or run the next pending job. |
| `verify` | Check every database is present, complete and readable. Exits non-zero on failure. |
| `backup [DEST]` | Copy every provisioned database into a timestamped folder with a manifest. |
| `events [--limit N]` | Read the audit trail. |

A database counts as provisioned when it holds the tables its spec calls for,
not merely when a file of that name exists, so an empty or damaged file reads
as unprovisioned and unhealthy rather than as a working database with nothing
in it. `verify` applies the same test and creates nothing: running it against a
mistyped `--root` reports seven failures instead of leaving seven empty files
behind. Reading from a root that was never initialised raises a
`FileNotFoundError` naming the database and telling you to run `init`, and a
backup leaves out — rather than invents — a database whose file is missing.

## Data files

`seed` pours `datacenter/data/*.json` into the databases; `validate-data`
checks those files without writing anything. `datacenter/loader.py` documents
the format of each file at the top.

Validation is strict, and refusing a file is the point: a value of the wrong
type is reported rather than coerced, so a document whose `external_id` is the
number `1` is rejected instead of silently overwriting the document whose id is
the string `"1"` (SQLite stores both in a `TEXT` column as `'1'`). The same
applies to `is_primary` and `is_active`, which must be real JSON booleans —
`"false"` is a non-empty string and used to set the flag — and to values that
are only whitespace, which count as empty. Contact addresses must sit under a
reserved domain (`.example`, `.test`, `.invalid`, `.localhost`,
`example.com/net/org`) so the data stays fictional. Each problem names the file
it came from and the entry's index inside that file
(`documents/10-ai-fundamentals.json[3]`), not a running count across the parts.
A `--data` path that does not exist, or that is a regular file, is reported as
a problem and exits non-zero instead of validating clean and loading nothing.

Loading is all-or-nothing: the files are validated first, and the writes run in
a transaction per database that is rolled back if anything fails part-way
through, so a rejected or failing refill leaves the previous contents intact.
(Seven separate SQLite files cannot be committed as a single unit, so the
guarantee is "nothing is written unless the whole load succeeds", not a
cross-file two-phase commit.)

A load is a refill rather than an append. Every entry carries a natural key —
an organization's name, a contact's email, a document's `external_id`, a
model's name and version, a dataset's name plus the record's payload — so
running it again updates what changed and leaves the rest alone. A dataset's
records are replaced wholesale: editing a record's value replaces that record
instead of storing the new version beside the old one, and a record removed
from the file is removed from the database. Records added through
`center.add_record()` to a dataset that also appears in `datasets.json` are
therefore dropped by the next seed.

## Using it from Python

```python
from datacenter import DataCenter

with DataCenter("storage") as center:
    center.provision()

    center.add_contact(
        "Ada Okoro",
        email="ada@northgate.example",
        role="Operations lead",
        organization="Northgate Logistics",
    )

    center.add_document(
        "Network hardening checklist",
        "Segment the network, rotate router credentials, review firewall rules.",
        tags=["security"],
    )

    for hit in center.search("firewall rules"):
        print(hit["score"], hit["title"])
```

Writing a document indexes it automatically. Pass `embed=False` to store it
without indexing, then catch up later with `center.reindex()` or by queueing a
`reindex` job.

## How search works

`datacenter/vectors.py` contains a deterministic hashing embedder: text is
NFKC-normalized and lowercased, split into Unicode word tokens, stripped of
stopwords, and each remaining token is hashed into a bucket with a log-damped
count. An embedding is a sparse map from bucket to weight, L2-normalized, so a
document only costs as many numbers as it has distinct words.

The bucket space is deliberately enormous (`config.BUCKET_SPACE`, just under
2**63). Nothing is allocated for it — only the buckets a text actually uses are
stored — and it makes two different words landing in the same bucket a ~2**-63
event. Search embeds the query the same way and ranks by cosine similarity,
which for unit vectors is the dot product over the buckets they share.

There is no score floor by default, and the reason is worth stating because an
earlier version got it wrong. A one-word query scores exactly 1/sqrt(N) against
a document of N distinct words, so any absolute floor high enough to reject a
collision in a short document also rejects a real match in a long one: at a
floor of 0.15, no document over about 44 distinct words could be found by a
single word, while a collision in a 32-word document scored 0.177 and passed.
Collisions are made negligible in the embedder rather than filtered by score.
A document sharing no bucket with the query scores exactly zero and is never
returned; `min_score` remains available to ask for a stronger overlap.

Query terms are weighted by **IDF** before scoring, using BM25's form,
`log(1 + (N - df + 0.5) / (df + 0.5))`, over the `N` indexed documents. A word
few documents use counts for more than one most of them use, so in the bundled
corpus "network segmentation for clusters" now ranks the segmentation runbook
first instead of the general hardening checklist that won on the common word
"network". Only the query is reweighted: weighting stored vectors too would tie
each one to the corpus it was indexed in, so adding a document would mean
re-embedding every other. A one-word query is unaffected, since one reweighted
term normalizes back to weight 1.

What IDF does not do is connect different words for the same idea. There is no
stemming, so "paid" does not match "pay" — and a stemmer would not rescue that
case anyway, being an irregular form. Asking about "on-call rotation pay" ranks
the sickness policy, which uses all three words, above the hours policy that
actually explains on-call pay but says "paid". A real embedding model is the
fix for that.

Scores are comparable within one result list, not across queries: a long
document matching one term will always score below a short one matching the
same term.

This is a stand-in for a real embedding model, chosen so the data center runs
anywhere with nothing installed. To upgrade retrieval, replace `embed`
with a call to a real embedding model, keep the return type (a sparse map of
index to weight, L2-normalized), register the model with
`center.register_model(...)`, and
re-embed with `center.reindex(model="your-model")`. The `embeddings` table keys
on `(document_id, model)`, so several models can coexist, and each row records
the bucket space its vector was built in, so search only compares vectors that
mean the same thing: a row whose stored blob is unreadable is skipped instead
of aborting the query.

Changing the scheme — a new model, or a different `BUCKET_SPACE` — leaves every
stored vector outside that filter until `center.reindex()` rebuilds them.
Rather than answer "no matches" for every query, search raises and says to
reindex, and `status --json` reports the count as `stale_embeddings`.

## Fees and business plans

Three datasets hold the money side, and each row carries a `fee_code`:

| Dataset | What it holds |
|---|---|
| `government_fees` | Registration, permits, inspections, environmental, utility and tax administration charges the business **pays**. |
| `service_fees` | The rate card the business **charges** — compute, inference, storage, egress, colocation, support, professional work. |
| `employment_fees` | Per role: base salary, the employer tax percentage on top of it, monthly benefits, and any recruitment fee. |

> The figures and the jurisdictions they name are invented for this sample data
> set. They are not legal, tax or payroll advice.

A **business plan** is a set of lines in the `business_plans` dataset, each
naming a `fee_code`, a quantity and a number of months. `datacenter/plans.py`
is what connects a line to the fee it refers to and costs it:

```bash
python -m datacenter plans
python -m datacenter plan year-one-operating
```

```
code      line                         basis                            amount
------------------------------------------------------------------------------
EMP-001   Operations lead              1 person x 12 months        $131,888.00
...
GOV-021   Sales tax filings            quarterly x4                     $180.00
------------------------------------------------------------------------------
          TOTAL                                                    $463,043.00
```

Each table prices things differently, so each has its own rule:

- **government** — the amount, once per time it falls due inside the plan's
  months. A quarterly fee over twelve months is charged four times.
- **service** — `unit_price_usd` per unit, honouring `minimum_units`. A
  per-month unit (`rack-month`, `gpu-month`, `month`) bills every month of the
  line; a `year` unit bills every started year; anything else is the quantity
  as the plan states it.
- **employment** — a twelfth of base salary grossed up by `employer_tax_pct`,
  plus monthly benefits, times people times months, plus the recruitment fee
  once per person.

A line naming a `fee_code` that no table defines is **refused**, not costed as
zero. A plan that silently leaves a cost out is worse than one that will not
add up.

## The portal

```bash
python -m datacenter portal          # http://127.0.0.1:8013
```

Serves the data center as pages — overview, search, documents, contacts,
datasets, fees, plans and the audit trail — using nothing but `http.server`.

Two limits are enforced rather than merely intended:

**It is read-only.** There is no POST handler and no write path; a POST gets
`501`. Everything that changes the data center goes through the CLI or the
Python API, where the audit trail records a named caller.

**It binds `127.0.0.1` unless told otherwise.** There is no authentication of
any kind, so a reachable bind publishes every contact, document and fee to
whatever can route to it. The portal refuses a non-local `--host` unless you
also pass `--allow-remote`, and warns when you do. Put it behind something
that authenticates before exposing it.

Everything a page displays comes from a database, so everything is escaped on
the way out. Document bodies get a very small Markdown renderer that escapes
first and only then adds its own tags, so a document containing a script tag is
shown as those characters rather than run.

## Jobs

Work that should not block a write goes on the queue:

```python
center.submit_job("reindex", {"reason": "new embedding model"}, priority=10)
center.run_next_job()
```

`run_next_job` claims the highest-priority pending job, runs it, and records
the outcome in `job_runs`. A job that raises is marked `failed` and the queue
keeps moving — one bad job never stalls the rest. Supported kinds are
`reindex` and `noop`; add your own in `DataCenter.run_next_job`.

## Audit trail

Every write goes through `DataCenter`, and every write logs an event:

```bash
python -m datacenter events --limit 5
```

```
2026-09-19T14:44:00Z  jobs        run_job            reindex #1: done
2026-09-19T14:44:00Z  embeddings  index_document     document 4
```

## Backups

```bash
python -m datacenter backup backups/
```

Uses SQLite's online backup API, so it is safe on a live database. The output
is a timestamped folder holding all seven files plus a `manifest.json`. A
backup folder is itself a valid storage root:

```bash
python -m datacenter --root backups/20260919T144400Z status
```

## Tests

```bash
python -m unittest discover -s tests
```

## Layout

```
datacenter/
  config.py    the seven database specs, name, paths, constants
  schema.py    SQL schema per database
  engine.py    SQLite wrapper: connect, query, upsert, backup, integrity
  core.py      DataCenter: provisioning, writes, search, jobs, audit, backups
  vectors.py   hashing embedder and sparse cosine similarity
  plans.py     connects business plan lines to the fee tables and costs them
  portal.py    read-only web portal (http.server, local by default)
  seed.py      demo data
  cli.py       command line interface
docs/          this file
tests/         unittest suite
```
