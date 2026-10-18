"""AI Tools and Databases: a reference for what the data center runs and holds.

    python3 tools/pdf/build.py ai_tools_and_databases
    python3 tools/pdf/ai_tools_and_databases.py

Covers the AI model registry, every table of the seven databases as the live
schema describes it, the datasets and their fields, the command line and the
portal routes, and how search works. Model rows, columns, row counts, command
help, routes and the search example are read from the code and the bundled
data when the document is built; only the prose around them is written by hand.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import REPO, document, esc, records, render, seeded_center, table  # noqa: E402

from datacenter import cli, portal, vectors  # noqa: E402
from datacenter.config import BUCKET_SPACE, DATABASES, DEFAULT_ROOT  # noqa: E402
from datacenter.core import DataCenter  # noqa: E402
from datacenter.plans import plan_names  # noqa: E402

NAME = "ai-tools-and-databases"
TITLE = "AI Tools and Databases"

#: The order the registry is presented in. A task the data adds later is listed
#: after these, so a new kind of model is shown rather than dropped.
TASK_ORDER = ("embedding", "chat", "rerank", "speech-to-text", "text-to-speech",
              "image-generation", "vision", "code", "moderation")

TASK_LABELS = {
    "embedding": "Embedding",
    "chat": "Chat",
    "rerank": "Rerank",
    "speech-to-text": "Speech to text",
    "text-to-speech": "Text to speech",
    "image-generation": "Image generation",
    "vision": "Vision",
    "code": "Code",
    "moderation": "Moderation",
}

TASK_NOTES = {
    "embedding": "Turn text into vectors, for semantic search, clustering and retrieval.",
    "chat": "General-purpose language models that answer, summarise and reason over text.",
    "rerank": "Score a short list of candidate passages against a query, to reorder search "
              "results.",
    "speech-to-text": "Transcribe recorded or streamed audio into text.",
    "text-to-speech": "Read text aloud as synthesised speech.",
    "image-generation": "Create images from a text prompt.",
    "vision": "Take images alongside text: describe, caption, answer questions and read text "
              "in them.",
    "code": "Language models tuned to write, complete and explain source code.",
    "moderation": "Classify text or images against a content policy before they reach a "
                  "person or another model.",
}

#: What each portal page shows. Keyed by the route as `datacenter.portal`
#: declares it; the build fails if a route is added or removed without this
#: table following, so the document cannot list a page that is not there.
ROUTE_NOTES = {
    "/": "One card per database with its total rows, then each database's state, size "
         "and rows per table.",
    "/search": "A search box. With <code>?q=</code> it runs semantic search and lists the "
               "hits with score, title and source.",
    "/documents": "Every stored document with its external id, source and tags.",
    "/contacts": "Every contact with role, organization, email and phone.",
    "/datasets": "Every dataset with its description and record count.",
    "/fees": "The government, service and employment fee tables.",
    "/plans": "Each business plan with its months, total, monthly average and subtotal per "
              "fee table.",
    "/events": "The most recent audit events, newest first.",
    "/documents/": "One document: its tags, then its body with a small set of Markdown "
                   "rendered after escaping.",
    "/datasets/": "Every record of one dataset, one column per schema field.",
    "/plans/": "One plan costed line by line, with subtotals per fee table, the total and the "
               "monthly average.",
}

#: The part of the path a prefix route takes, as shown in the route table.
PREFIX_PLACEHOLDERS = {"/documents/": "id", "/datasets/": "name", "/plans/": "name"}

#: The worked search example. The long query is the one docs/DATA_CENTER.md
#: uses to explain IDF; its first word on its own is the comparison.
EXAMPLE_QUERY = "network segmentation for clusters"
EXAMPLE_LIMIT = 3

#: A table with at most this many rows is never split across pages.
KEEP_ROWS = 12

NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven",
                "eight", "nine", "ten", "eleven", "twelve")

EXTRA_CSS = """
/* A totals row belongs after a table's last row only; Chromium would otherwise
   repeat a tfoot on every page the table spans. The page wrapper keeps its own. */
table:not(.page) > tfoot { display: table-row-group; }
.cols { display: flex; gap: 16pt; align-items: flex-start; }
.cols > div { flex: 1 1 0; min-width: 0; }
.keep { break-inside: avoid; }
.glance { flex-wrap: nowrap; gap: 6pt; }
.glance .stat { flex: 1 1 0; min-width: 0; padding: 6pt 7pt; }
.glance .stat span { display: block; line-height: 1.3; }
p code, li code { white-space: nowrap; }
/* Reference tables are scanned, not read: tighter rows than the shared style. */
table:not(.page) td { padding: 2.4pt 6pt; line-height: 1.32; }
table:not(.page) th { padding: 3pt 6pt; line-height: 1.25; white-space: nowrap; }
td code { font-size: 8.5pt; }
.models td code { font-size: 8pt; }
td .sub { display: block; color: var(--muted); font-size: 8.5pt; }
h3 .count, .tname .count { font-weight: 400; color: var(--muted); font-size: 9pt; }
.lede { margin: 0 0 4pt; break-after: avoid; }
.tname { font-weight: 700; font-size: 9.5pt; margin: 10pt 0 0; break-after: avoid; }
.tname code { font-size: 9.5pt; }
.after { font-size: 8.5pt; color: var(--muted); margin: -6pt 0 10pt; }
.after code { font-size: 8pt; }
.dbhead { margin: 18pt 0 2pt; }
.dbhead + .lede { color: var(--muted); font-size: 9.5pt; }
ul.fields { columns: 3; column-gap: 14pt; list-style: none; padding: 0; margin: 2pt 0 12pt;
            font-size: 9pt; }
ul.fields li { break-inside: avoid; margin: 0 0 1.5pt; }
ul.fields code { font-size: 8.5pt; }
ul.fields .type { color: var(--muted); }
.dataset p { font-size: 9.5pt; margin-bottom: 4pt; }
.arg { margin: 0 0 2pt; }
.arg:last-child { margin-bottom: 0; }
.arg code { white-space: nowrap; }
.note { font-size: 8.5pt; color: var(--muted); margin: -4pt 0 10pt; }
ul.toc { columns: 3; column-gap: 18pt; margin: 0 0 4pt; padding: 0; list-style: none;
         font-size: 9.5pt; }
ul.toc li { margin: 0 0 1pt; }
.grid.glance { margin-bottom: 8pt; }
h3.sub { font-size: 10pt; margin: 8pt 0 2pt; }
"""


# -- small helpers ----------------------------------------------------------------

def words(n: int) -> str:
    """Small counts as words, as prose writes them; larger ones as digits."""
    return NUMBER_WORDS[n] if 0 <= n < len(NUMBER_WORDS) else f"{n:,}"


ORDINALS = ("first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
            "ninth", "tenth")


def ordinal(n: int) -> str:
    return ORDINALS[n - 1] if 1 <= n <= len(ORDINALS) else f"number {n:,}"


def plural(n: int, one: str, many: str | None = None) -> str:
    """A count with its noun, in digits: for headings, captions and tables."""
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def counted(n: int, one: str, many: str | None = None) -> str:
    """A count with its noun as prose writes it: small numbers as words."""
    return f"{words(n)} {one if n == 1 else (many or one + 's')}"


def join_and(items: list[str], word: str = "and") -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {word} " + items[-1]


def cap(text: str) -> str:
    """Upper-case the first letter only, leaving names inside untouched."""
    return text[:1].upper() + text[1:]


def code(value: Any) -> str:
    return f"<code>{esc(value)}</code>"


def stat(value: str, label: str) -> str:
    return f'<div class="stat"><b>{value}</b><span>{label}</span></div>'


def sentence(text: str) -> str:
    """Help text as a sentence: capital first letter, full stop at the end."""
    text = text.strip()
    if not text:
        return ""
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."


def inline_code(text: str) -> str:
    """Escape a description, then set its `backticked` names in code."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(text))


def portable(text: Any) -> str:
    """Strip the build machine's paths out of help text and defaults.

    The parser's defaults are absolute paths resolved when it is built; the
    document states them the way a reader would type them instead.
    """
    value = str(text)
    value = value.replace(str(DEFAULT_ROOT), "./storage")
    return value.replace(str(REPO) + "/", "")


def quoted(name: str) -> str:
    """An SQL identifier, quoted."""
    return '"' + name.replace('"', '""') + '"'


def task_label(task: str) -> str:
    return TASK_LABELS.get(task, task[:1].upper() + task[1:])


def task_order(tasks: list[str]) -> list[str]:
    known = [t for t in TASK_ORDER if t in tasks]
    return known + sorted(t for t in set(tasks) if t not in TASK_ORDER)


def is_active(model: dict[str, Any]) -> bool:
    value = model.get("is_active")
    if value is None:
        raise ValueError(f"{model.get('name')}@{model.get('version')}: is_active is missing")
    return int(value) == 1


def count(center: DataCenter, key: str, table_name: str) -> int:
    row = center.db(key).query_one(f"SELECT COUNT(*) AS n FROM {quoted(table_name)}")
    return int(row["n"])


# -- reading the live schema ---------------------------------------------------------

def read_table(center: DataCenter, key: str, table_name: str) -> dict[str, Any]:
    """Columns, keys, references, indexes and row count of one live table."""
    db = center.db(key)
    columns = [dict(row) for row in db.query(f"PRAGMA table_info({quoted(table_name)})")]
    if not columns:
        raise ValueError(f"{key}.db has no table {table_name}")
    references = {}
    for row in db.query(f"PRAGMA foreign_key_list({quoted(table_name)})"):
        references[row["from"]] = dict(row)
    constraints = []
    for row in db.query(f"PRAGMA index_list({quoted(table_name)})"):
        if row["origin"] == "pk":
            continue  # already shown in the Key column
        cols = [r["name"] for r in db.query(f"PRAGMA index_info({quoted(row['name'])})")]
        constraints.append({
            "name": row["name"],
            "unique": int(row["unique"]) == 1,
            "declared": row["origin"] == "u",  # a UNIQUE clause rather than CREATE INDEX
            "columns": cols,
        })
    return {"name": table_name, "columns": columns, "references": references,
            "constraints": constraints, "rows": count(center, key, table_name)}


def read_databases(center: DataCenter) -> list[dict[str, Any]]:
    out = []
    for spec in DATABASES:
        live = center.db(spec.key).tables()
        missing = [t for t in spec.tables if t not in live]
        if missing:
            raise ValueError(f"{spec.key}.db is missing {', '.join(missing)}")
        extra = [t for t in live if t not in spec.tables]
        tables = [read_table(center, spec.key, t) for t in list(spec.tables) + extra]
        out.append({"spec": spec, "tables": tables, "extra": extra})
    return out


# -- sections ---------------------------------------------------------------------

def at_a_glance(dbs, models, datasets, commands, routes) -> str:
    tables = sum(len(d["tables"]) for d in dbs)
    rows = sum(t["rows"] for d in dbs for t in d["tables"])
    names = {m["name"] for m in models}
    providers = {m["provider"] for m in models}
    record_total = sum(int(d["records"]) for d in datasets)
    return (
        '<div class="grid glance">'
        + stat(esc(len(DATABASES)), f"databases, {esc(tables)} tables, {esc(f'{rows:,}')} rows")
        + stat(esc(len(names)), f"AI models, {esc(len(models))} versions")
        + stat(esc(len(providers)), f"model providers, {esc(len({m['task'] for m in models}))} tasks")
        + stat(esc(len(datasets)), f"datasets, {esc(f'{record_total:,}')} records")
        + stat(esc(len(commands)), "command-line commands")
        + stat(esc(routes), "portal routes")
        + "</div>"
    )


def contents(titles: list[str]) -> str:
    """The section list. The titles carry their own numbers, so no list markers."""
    items = "".join(f"<li>{esc(t)}</li>" for t in titles)
    return f'<ul class="toc">{items}</ul>'


def section_models(models: list[dict[str, Any]], model_rows: int, version_rows: int,
                   embeddings: dict[str, list[int]], title: str) -> str:
    names = sorted({m["name"] for m in models})
    if len(names) != model_rows or len(models) != version_rows:
        raise ValueError(f"registry mismatch: list_models() gives {len(names)} models and "
                         f"{len(models)} versions, the tables hold {model_rows} and {version_rows}")
    active = [m for m in models if is_active(m)]
    inactive = [m for m in models if not is_active(m)]
    active_names = {m["name"] for m in active}
    no_active = sorted(n for n in names if n not in active_names)
    beside = sum(1 for m in inactive if m["name"] in active_names)
    tasks = task_order([m["task"] for m in models])
    with_dims = task_order([m["task"] for m in models if m.get("dimensions") is not None])

    # Summary by task, then by provider.
    def summarise(key: str, value: str, label: str) -> list[str]:
        group = [m for m in models if m[key] == value]
        return [esc(label), esc(len({m["name"] for m in group})), esc(len(group)),
                esc(sum(1 for m in group if is_active(m)))]

    by_provider = Counter(m["provider"] for m in models)
    provider_order = sorted(by_provider, key=lambda p: (-by_provider[p], p.lower()))
    task_rows = [summarise("task", t, task_label(t)) for t in tasks]
    totals = ["Total", esc(len(names)), esc(len(models)), esc(len(active))]
    prov_rows = [summarise("provider", p, p) for p in provider_order]
    half = (len(prov_rows) + 1) // 2
    summary_columns = ["Models", "Versions", "Active"]
    summary_widths = ["46%", "18%", "18%", "18%"]

    status_note = f"{esc(len(active))} of the {esc(len(models))} versions are active."
    if inactive:
        if beside == len(inactive):
            status_note += (f" The other {esc(words(len(inactive)))} "
                            f"{'is' if len(inactive) == 1 else 'are'} inactive, each kept beside "
                            f"an active version of the same model.")
        else:
            status_note += (f" {esc(cap(counted(len(inactive), 'version')))} "
                            f"{'is' if len(inactive) == 1 else 'are'} inactive.")
    if no_active:
        status_note += (f" {esc(cap(counted(len(no_active), 'model')))} "
                        f"{'has' if len(no_active) == 1 else 'have'} no active version: "
                        f"{join_and([code(n) for n in no_active])}.")

    parts = [f"""
<h2>{esc(title)}</h2>
<p>The <code>models</code> database registers the AI models the data center can call or host.
Its <code>models</code> table has one row per model name, with its provider and task;
<code>model_versions</code> has one row per version and marks which are active. The registry
holds {esc(plural(len(names), "model"))} from {esc(plural(len(provider_order), "provider"))}, in
{esc(plural(len(models), "version"))} across {esc(counted(len(tasks), "task"))}.
{status_note}</p>
<div class="keep">
<h3 class="sub">By task</h3>
{table(["Task"] + summary_columns, task_rows, numeric={1, 2, 3}, widths=summary_widths,
       footer=totals)}
</div>
"""]
    # Counted per provider after the per-task lists, which name the provider of
    # every model; the totals are the same as the task table's.
    by_provider_html = f"""
<div class="keep">
<h3>By provider <span class="count">&middot; most versions first; totals as by task</span></h3>
<div class="cols">
  <div>{table(["Provider"] + summary_columns, prov_rows[:half], numeric={1, 2, 3},
              widths=summary_widths)}</div>
  <div>{table(["Provider"] + summary_columns, prov_rows[half:], numeric={1, 2, 3},
              widths=summary_widths)}</div>
</div>
</div>
"""

    local = vectors.LOCAL_MODEL_NAME
    for task in tasks:
        group = [m for m in models if m["task"] == task]
        show_dims = any(m.get("dimensions") is not None for m in group)
        rows = []
        flagged = False
        for m in group:
            row = [code(m["name"]), esc(m["provider"]), esc(m["version"])]
            if show_dims:
                dims = m.get("dimensions")
                cell = "&mdash;" if dims is None else esc(f"{int(dims):,}")
                stored = embeddings.get(m["name"], [])
                if m["name"] == local and dims is not None and stored and stored != [int(dims)]:
                    cell += "*"
                    flagged = True
                row.append(cell)
            row.append("yes" if is_active(m) else "no")
            rows.append(row)
        columns = ["Model", "Provider", "Version"] + (["Dimensions"] if show_dims else []) + ["Active"]
        numeric = {3} if show_dims else set()
        widths = (["44%", "22%", "13%", "13%", "8%"] if show_dims else ["47%", "22%", "23%", "8%"])
        group_names = {m["name"] for m in group}
        group_active = sum(1 for m in group if is_active(m))
        counts = (f"{plural(len(group_names), 'model')}, {plural(len(group), 'version')}, "
                  f"{group_active:,} active")
        note = ""
        if flagged:
            local_dims = next(int(m["dimensions"]) for m in group if m["name"] == local)
            spaces = embeddings[local]
            space_text = join_and([f"{s:,}" + (" (2<sup>63</sup> &minus; 1)" if s == 2**63 - 1 else "")
                                   for s in spaces])
            note = (f'<p class="note">* <code>{esc(local)}</code> is the built-in embedder that '
                    f"indexes the stored documents (section 5). Its registry entry lists "
                    f"{esc(f'{local_dims:,}')} dimensions, but its vectors are sparse: the "
                    f"<code>embeddings</code> table records the bucket space they were hashed "
                    f"into, {space_text}, and each vector stores only the buckets its text "
                    f"uses.</p>")
        dims_text = ""
        if show_dims:
            others = [task_label(t).lower() for t in with_dims if t != task]
            dims_text = (" Dimensions is the length of the vector a model returns; the registry "
                         "records it for " + (f"{esc(join_and([task_label(task).lower()] + others))} "
                                              "models" if others else "these models")
                         + " only.")
        # A short list is kept whole; a long one may run on, with its header
        # repeated, rather than leave most of a page empty.
        keep = " keep" if len(rows) <= KEEP_ROWS else ""
        parts.append(f"""
<div class="models{keep}">
<h3>{esc(task_label(task))} <span class="count">&middot; {esc(counts)}</span></h3>
<p class="lede">{esc(TASK_NOTES.get(task, ""))}{dims_text}</p>
{table(columns, rows, numeric=numeric, widths=widths)}
{note}
</div>
""")
    parts.append(by_provider_html)
    return "".join(parts)


def column_rows(info: dict[str, Any]) -> list[list[str]]:
    pk_total = sum(1 for c in info["columns"] if int(c["pk"]) > 0)
    rows = []
    for c in info["columns"]:
        pk = int(c["pk"])
        if pk == 0:
            key = ""
        elif pk_total == 1:
            key = "primary"
        else:
            key = f"primary, {pk} of {pk_total}"
        ref = info["references"].get(c["name"])
        ref_text = ""
        if ref is not None:
            ref_text = code(f"{ref['table']}.{ref['to']}")
            action = str(ref.get("on_delete") or "").upper()
            if action and action != "NO ACTION":
                ref_text += f'<span class="sub">on delete {esc(action.lower())}</span>'
        rows.append([
            code(c["name"]),
            esc(c["type"]) if c["type"] else "&mdash;",
            "yes" if int(c["notnull"]) == 1 else "no",
            "&mdash;" if c["dflt_value"] is None else code(c["dflt_value"]),
            esc(key),
            ref_text,
        ])
    return rows


def constraint_line(info: dict[str, Any]) -> str:
    unique, indexes = [], []
    for item in info["constraints"]:
        cols = "(" + ", ".join(item["columns"]) + ")"
        if item["unique"] and item["declared"]:
            unique.append(code(cols))
        elif item["unique"]:
            unique.append(f"{code(cols)} by index {code(item['name'])}")
        else:
            indexes.append(f"{code(item['name'])} {code(cols)}")
    parts = []
    if unique:
        parts.append("Unique: " + "; ".join(unique) + ".")
    if indexes:
        parts.append(("Index: " if len(indexes) == 1 else "Indexes: ") + "; ".join(indexes) + ".")
    return f'<p class="after">{" ".join(parts)}</p>' if parts else ""


def cross_file_links(dbs: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """Columns that point into another database file: `x_id` with no foreign key,
    where a table named `xs` lives in a different database. SQLite cannot
    enforce those, so the document says who does."""
    home = {t["name"]: d["spec"].key for d in dbs for t in d["tables"]}
    links = []
    for d in dbs:
        for t in d["tables"]:
            for c in t["columns"]:
                name = c["name"]
                if not name.endswith("_id") or name in t["references"]:
                    continue
                target = name[:-3] + "s"
                if target in home and home[target] != d["spec"].key:
                    links.append((t["name"], name, target))
    return links


def section_databases(dbs: list[dict[str, Any]], title: str) -> str:
    table_total = sum(len(d["tables"]) for d in dbs)
    column_total = sum(len(t["columns"]) for d in dbs for t in d["tables"])
    row_total = sum(t["rows"] for d in dbs for t in d["tables"])
    links = cross_file_links(dbs)
    links_text = ""
    if links:
        named = join_and([f"{code(f'{t}.{c}')} to {code(target)}" for t, c, target in links])
        links_text = (f"A foreign key cannot reach into another file, so the link from {named} "
                      f"is kept by the <code>DataCenter</code> class rather than by SQLite.")
    parts = [f"""
<h2>{esc(title)}</h2>
<p>Each database is a separate SQLite file under the storage root. The columns below are read
from the live schema with <code>PRAGMA table_info</code>, so they show what SQLite holds rather
than what the source code intends: {esc(plural(table_total, "table"))} with
{esc(plural(column_total, "column"))} in all. Row counts are those after
<code>python -m datacenter seed</code> fills an empty root, which loads the bundled data files and
runs the jobs that loading queues; together the tables hold {esc(f"{row_total:,}")} rows. The
audit count grows with every later command.</p>
<p>"Not null" is SQLite's own flag. An <code>INTEGER PRIMARY KEY</code> column is SQLite's row id:
it reports as nullable, but SQLite assigns the next id when none is given. "References" names
the column a foreign key points at, with what happens to this row when that one is deleted.
{links_text}</p>
"""]
    for index, d in enumerate(dbs, start=1):
        spec = d["spec"]
        rows_here = sum(t["rows"] for t in d["tables"])
        head = f"""
<h3 class="dbhead">{esc(index)}. {esc(spec.title)} <span class="count">&middot;
<code>{esc(spec.filename)}</code> &middot; {esc(plural(len(d['tables']), 'table'))},
{esc(plural(rows_here, 'row'))}</span></h3>
<p class="lede">{esc(spec.purpose)}</p>"""
        for position, info in enumerate(d["tables"]):
            extra = " (not in the database spec)" if info["name"] in d["extra"] else ""
            block = f"""
<p class="tname">{code(info['name'])} <span class="count">&middot; {esc(plural(info['rows'], 'row'))}{esc(extra)}</span></p>
{table(["Column", "Type", "Not null", "Default", "Key", "References"], column_rows(info),
       widths=["22%", "11%", "10%", "19%", "14%", "24%"])}
{constraint_line(info)}"""
            # The database heading travels with its first table.
            parts.append(f'<div class="keep">{head if position == 0 else ""}{block}</div>')
    return "".join(parts)


def section_datasets(center: DataCenter, datasets: list[dict[str, Any]], record_rows: int,
                     title: str) -> str:
    total = sum(int(d["records"]) for d in datasets)
    if total != record_rows:
        raise ValueError(f"datasets list {total} records, the records table holds {record_rows}")
    field_total = sum(len(d["schema"]) for d in datasets)
    types = Counter(t for d in datasets for t in d["schema"].values())
    type_text = join_and([f"{n:,} {t}" for t, n in sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))])

    summary = [[code(d["name"]), esc(f"{int(d['records']):,}"), esc(len(d["schema"]))] for d in datasets]
    half = (len(summary) + 1) // 2
    summary_widths = ["60%", "20%", "20%"]

    blocks = []
    complete = True
    blank_any = False
    for d in datasets:
        payloads = records(center, d["name"])
        if len(payloads) != int(d["records"]):
            raise ValueError(f"{d['name']}: list_datasets() and list_records() disagree")
        extra = sorted(set().union(*(p.keys() for p in payloads)) - set(d["schema"])) if payloads else []
        items = []
        for field, kind in d["schema"].items():
            have = sum(1 for p in payloads if p.get(field) is not None)
            blank = sum(1 for p in payloads if p.get(field) == "")
            detail = []
            if have != len(payloads):
                complete = False
                detail.append(f"in {have:,} of {len(payloads):,}")
            if blank:
                blank_any = True
                detail.append(f"blank in {blank:,}")
            tail = f' <span class="type">({esc(", ".join(detail))})</span>' if detail else ""
            items.append(f'<li>{code(field)} <span class="type">{esc(kind)}</span>{tail}</li>')
        extra_note = ""
        if extra:
            complete = False
            extra_note = ("<p>Records also carry fields the schema does not list: "
                          + join_and([code(f) for f in extra]) + ".</p>")
        blocks.append(f"""
<div class="dataset keep">
<h3>{code(d['name'])} <span class="count">&middot; {esc(plural(int(d['records']), 'record'))},
{esc(plural(len(d['schema']), 'field'))}</span></h3>
<p>{inline_code(d['description'] or 'No description recorded.')}</p>
<ul class="fields">{''.join(items) or '<li>No schema recorded.</li>'}</ul>
{extra_note}
</div>""")

    coverage = ("Every record carries every field its schema lists"
                + (", though some leave a text field blank, as marked." if blank_any else ".")
                if complete else
                "Where a field is missing from some records, the list says how many have it.")

    return f"""
<h2>{esc(title)}</h2>
<p>The <code>datasets</code> database groups structured records into named datasets. Each
record is stored as one JSON object, and each dataset keeps a schema naming its fields and their
types. There are {esc(counted(len(datasets), "dataset"))} holding
{esc(plural(total, "record"))}, with {esc(plural(field_total, "field"))} between them:
{esc(type_text)}. {coverage}</p>
<div class="cols keep">
  <div>{table(["Dataset", "Records", "Fields"], summary[:half], numeric={1, 2},
              widths=summary_widths)}</div>
  <div>{table(["Dataset", "Records", "Fields"], summary[half:], numeric={1, 2},
              widths=summary_widths)}</div>
</div>
{''.join(blocks)}
"""


# -- the command line -----------------------------------------------------------

def subcommands(parser: argparse.ArgumentParser) -> list[tuple[str, str, argparse.ArgumentParser]]:
    found = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for choice in action._choices_actions:
                found.append((choice.dest, choice.help or "", action.choices[choice.dest]))
    if not found:
        raise ValueError("the CLI parser has no subcommands")
    return found


def describe_argument(action: argparse.Action) -> tuple[str, str]:
    """An argument as (how it is typed, what the parser says about it)."""
    if action.option_strings:
        flag = max(action.option_strings, key=len)
        token = flag if action.nargs == 0 else f"{flag} {action.metavar or action.dest.upper()}"
    else:
        token = action.metavar or action.dest
    help_text = "" if action.help in (None, argparse.SUPPRESS) else portable(action.help)

    notes = []
    if action.choices:
        choices = join_and([code(c) for c in action.choices], "or")
        notes.append((esc(sentence(help_text))[:-1] + ": " if help_text else "One of ") + choices + ".")
    elif help_text:
        notes.append(esc(sentence(help_text)))
    if action.type is cli._positive_int:
        notes.append("A whole number, at least 1.")
    if not action.option_strings:
        if action.nargs == "?":
            notes.append("Optional.")
        elif action.required:
            notes.append("Required.")
    default = action.default
    has_default = (default is not None and default is not False and default != ""
                   and default != argparse.SUPPRESS)
    if has_default and "default" not in help_text.lower():
        notes.append(f"Default {code(portable(default))}.")
    return code(token), " ".join(notes)


def global_options(parser: argparse.ArgumentParser) -> list[list[str]]:
    rows = []
    for action in parser._actions:
        if isinstance(action, (argparse._SubParsersAction, argparse._HelpAction)):
            continue
        token, notes = describe_argument(action)
        rows.append([token, notes])
    return rows


def command_rows(parser: argparse.ArgumentParser) -> list[list[str]]:
    rows = []
    for name, help_text, sub in subcommands(parser):
        args = []
        for action in sub._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            token, notes = describe_argument(action)
            args.append(f'<div class="arg">{token}{"&ensp;" + notes if notes else ""}</div>')
        rows.append([code(name), esc(sentence(help_text)), "".join(args) or "&mdash;"])
    return rows


def script_name() -> str | None:
    """The console script pyproject.toml installs for the CLI, if any."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    section = re.search(r"^\[project\.scripts\]\s*$(.*?)(?=^\[|\Z)", text, re.M | re.S)
    if not section:
        return None
    match = re.search(r'^\s*([A-Za-z0-9_.-]+)\s*=\s*"datacenter\.cli:main"', section.group(1), re.M)
    return match.group(1) if match else None


def section_tools(center: DataCenter, title: str) -> tuple[str, int, int]:
    parser = cli.build_parser()
    commands = command_rows(parser)
    options = global_options(parser)
    script = script_name()
    script_note = (f" Installing the package with <code>pip install -e .</code> also gives an "
                   f"<code>{esc(script)}</code> command that behaves the same way." if script else "")
    portal_options = {a.dest for name, _help, sub in subcommands(parser) if name == "portal"
                      for a in sub._actions}
    if "allow_remote" not in portal_options:
        raise ValueError("the portal command no longer has --allow-remote; update the text")

    # Portal routes, checked against the code in both directions.
    declared = list(portal.ROUTES) + list(portal.PREFIXES)
    missing = [r for r in declared if r not in ROUTE_NOTES]
    stale = [r for r in ROUTE_NOTES if r not in declared]
    if missing or stale:
        raise ValueError(f"portal routes changed: undocumented {missing}, gone {stale}")
    nav = dict(portal.NAV)
    unknown_nav = [p for p in nav if p not in portal.ROUTES]
    if unknown_nav:
        raise ValueError(f"portal menu links to unrouted paths {unknown_nav}")

    documents = center.list_documents()
    datasets = center.list_datasets()
    plans = plan_names(center)
    examples = {
        "/documents/": str(documents[0]["id"]) if documents else None,
        "/datasets/": datasets[0]["name"] if datasets else None,
        "/plans/": plans[0] if plans else None,
    }
    if set(examples) != set(portal.PREFIXES) or set(PREFIX_PLACEHOLDERS) != set(portal.PREFIXES):
        raise ValueError("portal prefixes changed; update PREFIX_PLACEHOLDERS and the examples")

    route_rows = []
    for path, view in portal.ROUTES.items():
        status, _ = portal.render(center, path, {})
        if status != 200:
            raise ValueError(f"portal route {path} answered {status}")
        route_rows.append([code(path), esc(nav.get(path, "")) or "&mdash;", code(view.__name__),
                           ROUTE_NOTES[path]])
    for prefix, view in portal.PREFIXES.items():
        shows = ROUTE_NOTES[prefix]
        example = examples[prefix]
        if example is not None:
            url = prefix + quote(example)
            status, _ = portal.render(center, url, {})
            if status != 200:
                raise ValueError(f"portal page {url} answered {status}")
            shows += f' For example {code(url)}.'
        route_rows.append([code(f"{prefix}<{PREFIX_PLACEHOLDERS[prefix]}>"), "&mdash;",
                           code(view.__name__), shows])

    portal_url = f"http://{portal.DEFAULT_HOST}:{portal.DEFAULT_PORT}"
    in_menu = ("all of them" if len(nav) == len(portal.ROUTES)
               else f"{words(len(nav))} of them")
    html_text = f"""
<h2>{esc(title)}</h2>
<p>Two tools operate the data center: the command line, which can read and write, and the web
portal, which only reads. Both work on the same storage root.</p>
<h3>The command line</h3>
<p>The command line is one program, run from the repository root as
<code>python -m datacenter &lt;command&gt;</code>.{script_note} It has
{esc(plural(len(commands), "command"))}, listed below with the help text and arguments the parser
itself declares. Every command also takes <code>-h</code> for its own help.</p>
<div class="keep">
<h3>Options before the command</h3>
{table(["Option", "What it does"], options, widths=["24%", "76%"])}
<p class="note">The <code>SDDU13_ROOT</code> environment variable, when set, replaces
<code>./storage</code> as the default root.</p>
</div>
<h3>Commands</h3>
{table(["Command", "What it does", "Arguments and options"], commands,
       widths=["17%", "31%", "52%"])}
<h3>The portal</h3>
<p><code>python -m datacenter portal</code> serves the data center as web pages on
<code>{esc(portal_url)}</code>. It answers GET requests only and has no write path: changes go
through the command line or the Python API, where the audit trail records them. It has no
authentication, so it binds <code>{esc(portal.DEFAULT_HOST)}</code> and refuses a non-local
<code>--host</code> unless <code>--allow-remote</code> is also passed. It serves
{esc(counted(len(portal.ROUTES), "fixed route"))}, {esc(in_menu)} in its menu, and
{esc(words(len(portal.PREFIXES)))} more that take a name or id after the slash:</p>
{table(["Path", "Menu", "Handler", "What it shows"], route_rows,
       widths=["20%", "11%", "19%", "50%"])}
"""
    return html_text, len(commands), len(declared)


# -- search ------------------------------------------------------------------------

def section_search(center: DataCenter, indexed: int, documents: int, title: str) -> str:
    stopwords = len(vectors.STOPWORDS)
    hyphen_example = "on-call"
    hyphen_tokens = vectors.tokenize(hyphen_example)
    hyphen_stop = [p for p in hyphen_example.split("-") if p in vectors.STOPWORDS]
    stop_example = f" such as {code(hyphen_stop[0])}" if hyphen_stop else ""
    floor = DataCenter.min_score
    floor_text = ("There is no minimum score by default" if floor == 0
                  else f"The default minimum score is {floor:g}")
    parser = cli.build_parser()
    limit = next((a.default for name, _help, sub in subcommands(parser) if name == "search"
                  for a in sub._actions if a.dest == "limit"), None)
    limit_text = (f" The <code>search</code> command shows the top {esc(words(int(limit)))} "
                  f"unless given <code>--limit</code>." if limit is not None else "")
    space = f"{BUCKET_SPACE:,}" + (" (2<sup>63</sup> &minus; 1)" if BUCKET_SPACE == 2**63 - 1 else "")
    coverage = (f"one for each of the {esc(f'{documents:,}')} stored documents"
                if indexed == documents else
                f"for {esc(f'{indexed:,}')} of the {esc(f'{documents:,}')} stored documents")

    # Worked example: document frequency and IDF of each query term, read from
    # the stored vectors exactly as search counts them, then the results.
    stored = [vectors.loads(r["vector"]) for r in center.db("embeddings").query(
        "SELECT vector FROM embeddings WHERE model = ? AND dimensions = ?",
        (vectors.LOCAL_MODEL_NAME, BUCKET_SPACE))]
    terms = vectors.tokenize(EXAMPLE_QUERY)
    dropped = [w for w in EXAMPLE_QUERY.split() if w.lower() in vectors.STOPWORDS]
    term_rows = []
    for term in dict.fromkeys(terms):
        bucket = next(iter(vectors.embed(term)))
        df = sum(1 for vector in stored if bucket in vector)
        term_rows.append([code(term), esc(f"{df:,}"), esc(f"{vectors.idf(len(stored), df):.3f}")])
    full = center.search(EXAMPLE_QUERY, limit=EXAMPLE_LIMIT)
    first_word = terms[0]
    single = center.search(first_word, limit=EXAMPLE_LIMIT)
    if not full or not single:
        raise ValueError("the worked search example returned no results")
    result_rows = [[esc(i), esc(hit["title"]), esc(f"{hit['score']:.4f}")]
                   for i, hit in enumerate(full, start=1)]
    single_rank = next((i for i, hit in enumerate(single, start=1)
                        if hit["document_id"] == full[0]["document_id"]), None)
    if single[0]["document_id"] != full[0]["document_id"]:
        shift = (f"On its own, <code>{esc(first_word)}</code> ranks &ldquo;{esc(single[0]['title'])}&rdquo; "
                 f"first" + (f" and &ldquo;{esc(full[0]['title'])}&rdquo; {esc(ordinal(single_rank))}"
                             if single_rank else
                             f", and &ldquo;{esc(full[0]['title'])}&rdquo; is not in its top "
                             f"{words(EXAMPLE_LIMIT)}")
                 + ". The rarer terms move it to the top.")
    else:
        shift = (f"On its own, <code>{esc(first_word)}</code> already ranks this document "
                 "first.")
    dropped_text = (f" (<code>{esc(join_and(dropped))}</code> is a stopword)"
                    if dropped else "")

    return f"""
<h2>{esc(title)}</h2>
<p>Search runs on a built-in embedder, <code>{esc(vectors.LOCAL_MODEL_NAME)}</code> in
<code>datacenter/vectors.py</code>, so it needs no model service and nothing installed. When a
document is stored, its title and body are case-folded, NFKC-normalised and split into Unicode
word tokens. Any character that is not a letter or digit separates tokens, and
{esc(f"{stopwords:,}")} stopwords{stop_example} are dropped, so <code>{esc(hyphen_example)}</code>
is indexed as {join_and([code(t) for t in hyphen_tokens]) or "nothing"}. Each remaining token is
hashed with BLAKE2b
into one of {space} buckets, its count is damped to 1 + log(count), and the result is scaled to
unit length. The embedding is a sparse, hashed bag of words: a map from bucket to weight that
holds only the buckets the text uses, so the huge bucket space costs nothing and two different
words share a bucket with a probability near 2<sup>&minus;63</sup>. Word order is not kept. The
<code>embeddings</code> database holds {esc(plural(indexed, "vector"))}, {coverage}.</p>
<p>A query is embedded the same way, and then each of its terms is weighted by inverse document
frequency in BM25's form, log(1 + (N &minus; df + 0.5) / (df + 0.5)), where N is the number of
indexed documents and df the number that contain the term: a word few documents use counts for
more than one most of them use. Only the query is reweighted; stored vectors stay as they were
indexed, so adding a document never means re-embedding the others. The score is the cosine
similarity, which for unit vectors is the sum of products over the buckets the query and the
document share. A document sharing no bucket with the query scores exactly zero and is never
returned. {floor_text}.{limit_text} There is no stemming and no notion of synonyms: only the
exact word form matches, so &ldquo;paid&rdquo; does not find &ldquo;pay&rdquo;. Scores rank the
results of one query; they are not comparable across queries, since a long document matching a
term always scores below a short one matching the same term.</p>
<div class="keep">
<h3>Worked example</h3>
<p>The query <code>{esc(EXAMPLE_QUERY)}</code> keeps {esc(words(len(terms)))}
terms{dropped_text}; their weights over the {esc(f"{len(stored):,}")} indexed documents and the
top results are below. {shift}</p>
<div class="cols">
  <div>{table(["Term", "Documents with it", "IDF weight"], term_rows, numeric={1, 2},
              widths=["40%", "34%", "26%"])}</div>
  <div>{table(["#", "Document", "Score"], result_rows, numeric={0, 2},
              widths=["9%", "70%", "21%"])}</div>
</div>
</div>
"""


# -- build -------------------------------------------------------------------------

def build() -> Path:
    titles = [
        "AI model registry",
        f"The {words(len(DATABASES))} databases in detail",
        "Datasets",
        "Tools",
        "Search",
    ]
    numbered = [f"{i}. {t}" for i, t in enumerate(titles, start=1)]

    with seeded_center() as center:
        # `python -m datacenter seed` runs the jobs that loading queues before
        # it returns. Do the same, so the row counts are the ones a reader gets
        # from `seed` and then `status` on an empty root, and the ones the AI
        # Data Center document prints.
        while center.run_next_job() is not None:
            pass
        pending = center.pending_jobs()
        if pending:
            raise ValueError(f"{len(pending)} jobs are still pending after running the queue")
        # Everything counted is read before anything below writes: a search
        # logs an audit event, which would change the row counts.
        dbs = read_databases(center)
        rows_of = {(d["spec"].key, t["name"]): t["rows"] for d in dbs for t in d["tables"]}
        models = center.list_models()
        datasets = center.list_datasets()
        documents = len(center.list_documents())
        embeddings: dict[str, list[int]] = defaultdict(list)
        for row in center.db("embeddings").query(
                "SELECT DISTINCT model, dimensions FROM embeddings ORDER BY model, dimensions"):
            embeddings[row["model"]].append(int(row["dimensions"]))
        indexed = int(center.db("embeddings").query_one(
            "SELECT COUNT(*) AS n FROM embeddings WHERE model = ? AND dimensions = ?",
            (vectors.LOCAL_MODEL_NAME, BUCKET_SPACE))["n"])

        if not models or not datasets:
            raise ValueError("the bundled data has no models or no datasets")

        models_html = section_models(models, rows_of[("models", "models")],
                                     rows_of[("models", "model_versions")], embeddings, numbered[0])
        databases_html = section_databases(dbs, numbered[1])
        datasets_html = section_datasets(center, datasets, rows_of[("datasets", "records")],
                                         numbered[2])
        tools_html, command_count, route_count = section_tools(center, numbered[3])
        search_html = section_search(center, indexed, documents, numbered[4])

        commands = command_rows(cli.build_parser())
        body = "".join([
            f"<style>{EXTRA_CSS}</style>",
            at_a_glance(dbs, models, datasets, commands, route_count),
            contents(numbered),
            models_html,
            databases_html,
            datasets_html,
            tools_html,
            search_html,
        ])

    html_text = document(
        TITLE,
        body,
        eyebrow="Reference",
        subtitle="The AI models the data center registers, the tables and datasets it holds, "
                 "the tools that operate it, and how its search works.",
        notice="Built from the software and its bundled sample data. Model entries, table "
               "columns, row counts, command help and portal routes are read when the document "
               "is built, not copied by hand.",
    )
    for leak in (str(REPO), str(DEFAULT_ROOT), "/tmp/"):
        if leak in html_text:
            raise ValueError(f"the document would print a build-machine path: {leak}")
    return render(NAME, html_text)


if __name__ == "__main__":
    print(build())
