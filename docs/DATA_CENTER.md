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
| `seed` | Load demo data so there is something to query. |
| `status [--json]` | Per-database state, size and row counts. |
| `search QUERY [--limit N]` | Semantic search over indexed documents (`N` is at least 1). |
| `add-document TITLE [--body ... \| --file ...] [--tag t]` | Store and index a document. |
| `add-contact NAME [--email ... --organization ...]` | Store a contact. |
| `documents` / `contacts` | List what is stored. |
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
lowercased, tokenized, stripped of stopwords, and each remaining token is
hashed into one of 4096 buckets with a log-damped count. The resulting vector
is L2-normalized and stored sparsely — only the non-zero buckets are written,
so a short document costs a few hundred bytes rather than tens of kilobytes.

Search embeds the query the same way and ranks documents by cosine similarity,
summing over the buckets the query itself uses rather than walking thousands of
zeros per document. Scores below `DataCenter.min_score` (0.15) are dropped,
because distinct words can hash into the same bucket and a collision otherwise
looks like a weak match. A document that shares no bucket with the query scores
exactly zero and is never returned, whatever floor the caller passes.

This is a stand-in for a real embedding model, chosen so the data center runs
anywhere with nothing installed. To upgrade retrieval, replace `embed_text`
with a call to a real embedding model, keep the return type (a list of floats,
L2-normalized), register the model with `center.register_model(...)`, and
re-embed with `center.reindex(model="your-model")`. The `embeddings` table keys
on `(document_id, model)`, so several models can coexist, and each row records
the width the embedder actually returned, so search only compares vectors of
matching width: a row that disagrees with the vector stored beside it is
skipped instead of aborting the query.

Changing the width — a new model, or a different `EMBEDDING_DIM` — leaves every
stored vector outside that filter until `center.reindex()` rebuilds them.
Rather than answer "no matches" for every query, search raises and says to
reindex, and `status --json` reports the count as `stale_embeddings`.

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
  vectors.py   hashing embedder and cosine similarity
  seed.py      demo data
  cli.py       command line interface
docs/          this file
tests/         unittest suite
```
