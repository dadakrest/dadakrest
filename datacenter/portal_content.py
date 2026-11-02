"""The portal's reference pages: data documentation, updates and connection status.

Every route here is MEMBER: a full session of an approved account, or the
owner's. The guard in `portal_web.authorize` runs first, so a signed-out
visitor is sent to sign in and a pending one to their status page.

Data documentation (/data-docs)
    Generated on every visit from the live databases: each of the seven,
    its tables and their columns as SQLite reports them, the datasets with
    their record counts, the fee tables, the costed plans, and the PDFs in
    docs/pdf, served from /data-docs/pdf/<name> to signed-in members only.
    The accounts store is mentioned and never described: nothing about
    sign-in records belongs on a page every member can open.

Updates (/updates)
    Coding and data updates from `updates.json`, a maintained file written
    from this branch's git history, and portal updates, which are the
    announcements the owner posts in the owner center.

Connection status (/connection)
    What the server can reach, from `connectivity.ConnectionChecker`: cached
    for a minute, every check bounded by a deadline. The page says plainly
    that the portal does not provide internet access or a VPN.
"""

from __future__ import annotations

import json
import re
from datetime import date
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import DATABASES
from .connectivity import OFF, OK, PROBLEM
from .core import DataCenter
from .plans import PlanError, cost_plan, plan_names
from .portal_web import (
    MEMBER,
    Context,
    NotFound,
    Response,
    Route,
    error_box,
    esc,
    markup,
    money,
    table,
)

UPDATES_FILE = Path(__file__).resolve().parent / "updates.json"
#: Where the business PDFs live in a checkout. An installed package has no
#: docs/ folder, and the page says so instead of listing nothing.
PDF_DIR = Path(__file__).resolve().parent.parent / "docs" / "pdf"

UPDATE_KINDS = (("coding", "Coding updates"), ("data", "Data updates"))
FEE_DATASETS = ("government_fees", "service_fees", "employment_fees")

PDF_TITLES = {
    "ai-data-center.pdf": "AI Data Center overview",
    "ai-tools-and-databases.pdf": "AI Tools and Databases reference",
    "employment-application.pdf": "Employment application (fillable form)",
    "fee-schedule.pdf": "Fee Schedule",
    "license-agreement.pdf": "Software and Services License Agreement (template)",
    "llc-operating-agreement.pdf": "LLC operating agreement (template)",
}

_PDF_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,80}\.pdf")
_COMMIT = re.compile(r"[0-9a-f]{7,40}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_ENTRY_KEYS = {"date", "title", "summary", "commits"}

STATE_CLASS = {OK: "ok", PROBLEM: "bad", OFF: "hint"}


# -- updates.json -------------------------------------------------------------------

class UpdatesError(ValueError):
    """updates.json is missing or not in the expected shape."""


def _entry(kind: str, index: int, raw: Any) -> dict[str, Any]:
    where = f"updates.json {kind}[{index}]"
    if not isinstance(raw, dict):
        raise UpdatesError(f"{where} is not an object")
    unknown = set(raw) - _ENTRY_KEYS
    if unknown:
        raise UpdatesError(f"{where} has unknown keys: {', '.join(sorted(unknown))}")
    for key, limit in (("date", 10), ("title", 120), ("summary", 1000)):
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise UpdatesError(f"{where}: {key} must be text of 1 to {limit} characters")
    try:
        if not _DATE.fullmatch(raw["date"]):
            raise ValueError(raw["date"])
        day = date.fromisoformat(raw["date"])
    except ValueError:
        raise UpdatesError(f"{where}: date must be a real date written YYYY-MM-DD") from None
    commits = raw.get("commits", [])
    if not isinstance(commits, list) or not all(isinstance(c, str) and _COMMIT.fullmatch(c) for c in commits):
        raise UpdatesError(f"{where}: commits must be a list of git commit hashes")
    return {"date": day, "title": raw["title"].strip(), "summary": raw["summary"].strip(),
            "commits": list(commits)}


def load_updates(path: Path | str | None = None) -> dict[str, list[dict[str, Any]]]:
    """The coding and data updates, each list newest first, read from `path`
    (default UPDATES_FILE) on every call. Raises UpdatesError, naming the
    entry, for anything not in the expected shape."""
    path = UPDATES_FILE if path is None else path
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise UpdatesError(f"cannot read {Path(path).name}: {error.strerror or error}") from None
    except (ValueError, UnicodeDecodeError):
        raise UpdatesError(f"{Path(path).name} is not valid JSON") from None
    if not isinstance(raw, dict):
        raise UpdatesError("updates.json must be an object")
    updates: dict[str, list[dict[str, Any]]] = {}
    for kind, _label in UPDATE_KINDS:
        entries = raw.get(kind)
        if not isinstance(entries, list):
            raise UpdatesError(f"updates.json needs a {kind!r} list")
        parsed = [_entry(kind, index, entry) for index, entry in enumerate(entries)]
        # Stable, so entries on the same day keep the order the file gives them.
        updates[kind] = sorted(parsed, key=lambda entry: entry["date"], reverse=True)
    return updates


# -- PDFs -----------------------------------------------------------------------------

def available_pdfs(directory: Path | None = None) -> list[Path]:
    """The PDFs that may be served: plain files with plain names, no links."""
    directory = PDF_DIR if directory is None else directory
    if not directory.is_dir():
        return []
    return [path for path in sorted(directory.glob("*.pdf"))
            if _PDF_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink()]


def pdf_title(name: str) -> str:
    return PDF_TITLES.get(name) or name.removesuffix(".pdf").replace("-", " ").capitalize()


def pdf_file(ctx: Context, match: re.Match[str]) -> Response:
    # Only a name found in the folder listing is opened, so no path from the
    # request ever reaches the filesystem.
    found = {path.name: path for path in available_pdfs()}.get(match["name"])
    if found is None:
        raise NotFound(f"no document {match['name']}")
    return Response(HTTPStatus.OK, found.read_bytes(), "application/pdf",
                    headers=[("Content-Disposition", f'inline; filename="{found.name}"')])


# -- data documentation -----------------------------------------------------------------

def _schema_section(center: DataCenter) -> tuple[str, int, int]:
    """Each database's tables and columns, read from the files themselves.
    Returns the HTML and the number of tables and columns described."""
    parts: list[str] = []
    tables_seen = columns_seen = 0
    for spec in DATABASES:
        database = center.db(spec.key)
        parts.append(f'<h2 id="db-{esc(spec.key)}">{esc(spec.key)} database</h2>'
                     f'<p class="sub">{esc(spec.title)}: {esc(spec.purpose)} '
                     f"File: <code>{esc(spec.filename)}</code></p>")
        if not database.path.exists():
            parts.append('<p class="empty">Not provisioned on this server yet.</p>')
            continue
        counts = database.row_counts()
        for name in database.tables():
            columns = database.columns(name)
            tables_seen += 1
            columns_seen += len(columns)
            rows = [[
                f"<code>{esc(column['name'])}</code>",
                esc(column["type"] or "-"),
                "primary key" if column["pk"] else ("required" if column["notnull"] else ""),
                f"<code>{esc(column['default'])}</code>" if column["default"] is not None else "",
            ] for column in columns]
            parts.append(f'<h3>{esc(name)} <span class="hint">{counts.get(name, 0):,} rows</span></h3>'
                         + table(["column", "type", "", "default"], rows))
    return "".join(parts), tables_seen, columns_seen


def _datasets_section(datasets: list[dict[str, Any]]) -> str:
    rows = [[
        f'<a href="/datasets/{esc(quote(dataset["name"]))}">{esc(dataset["name"])}</a>',
        esc(dataset["description"] or "-"),
        f'{dataset["records"]:,}',
        esc(", ".join(f"{field} ({kind})" for field, kind in dataset["schema"].items()) or "-"),
    ] for dataset in datasets]
    return ("<h2 id=\"datasets\">Datasets</h2>"
            '<p class="sub">Named groups of records in the datasets database.</p>'
            + table(["dataset", "description", "records", "fields"], rows, numeric={2}))


def _fees_section(datasets: list[dict[str, Any]]) -> str:
    known = {dataset["name"]: dataset for dataset in datasets}
    rows = []
    for name in FEE_DATASETS:
        dataset = known.get(name)
        if dataset is None:
            rows.append([f"<code>{esc(name)}</code>", "Not loaded on this server.", "0", "-"])
            continue
        rows.append([f'<a href="/datasets/{esc(quote(name))}"><code>{esc(name)}</code></a>',
                     esc(dataset["description"] or "-"), f'{dataset["records"]:,}',
                     esc(", ".join(dataset["schema"]) or "-")])
    return ('<h2 id="fees">Fee tables</h2>'
            '<p class="sub">Every row has a <code>fee_code</code>, which is how a business plan line '
            'finds its price. All three side by side: <a href="/fees">Fees</a>.</p>'
            + table(["table", "what it holds", "rows", "columns"], rows, numeric={2}))


def _plans_section(center: DataCenter) -> str:
    rows = []
    for name in plan_names(center):
        try:
            plan = cost_plan(center, name)
        except PlanError as error:
            rows.append([esc(name), "-", "-", "-", esc(f"cannot be costed: {error}")])
            continue
        rows.append([f'<a href="/plans/{esc(quote(name))}">{esc(name)}</a>', str(plan["months"]),
                     str(len(plan["lines"])), money(plan["total_usd"]),
                     esc(", ".join(sorted(plan["subtotals"])))])
    return ('<h2 id="plans">Business plans</h2>'
            '<p class="sub">Lines in the <code>business_plans</code> dataset, costed against the fee '
            "tables each time.</p>"
            + table(["plan", "months", "lines", "total", "draws on"], rows, numeric={1, 2, 3}))


def _pdfs_section() -> str:
    pdfs = available_pdfs()
    if not pdfs:
        return ('<h2 id="pdfs">PDF documents</h2>'
                '<p class="empty">The PDFs are not on this server (they live in docs/pdf in a '
                "checkout of the source).</p>")
    rows = [[f'<a href="/data-docs/pdf/{esc(path.name)}">{esc(pdf_title(path.name))}</a>',
             f"<code>{esc(path.name)}</code>", f"{path.stat().st_size / 1024:,.0f} KB"] for path in pdfs]
    return ('<h2 id="pdfs">PDF documents</h2>'
            '<p class="sub">The business documents built from this data. They open in the browser.</p>'
            + table(["document", "file", "size"], rows, numeric={2}))


def data_docs_page(ctx: Context, _match: re.Match[str]) -> Response:
    center = ctx.center()
    datasets = center.list_datasets()
    schema, tables, columns = _schema_section(center)
    pdfs = len(available_pdfs())
    plans = len(plan_names(center))
    cards = "".join(
        f'<a class="card" href="#{anchor}"><div class="n">{count:,}</div><div class="l">{esc(label)}</div></a>'
        for count, label, anchor in (
            (len(DATABASES), "databases", f"db-{DATABASES[0].key}"),
            (tables, "tables", f"db-{DATABASES[0].key}"),
            (columns, "columns", f"db-{DATABASES[0].key}"),
            (len(datasets), "datasets", "datasets"),
            (sum(dataset["records"] for dataset in datasets), "records", "datasets"),
            (plans, "business plans", "plans"),
            (pdfs, "PDF documents", "pdfs"),
        ))
    contents = " · ".join(f'<a href="#db-{esc(spec.key)}">{esc(spec.key)}</a>' for spec in DATABASES)
    body = (f'<div class="cards">{cards}</div>'
            f'<p class="tabs">{contents} · <a href="#datasets">datasets</a> · <a href="#fees">fees</a> · '
            '<a href="#plans">plans</a> · <a href="#pdfs">PDFs</a></p>'
            + schema + _datasets_section(datasets) + _fees_section(datasets) + _plans_section(center)
            + _pdfs_section()
            + '<p class="sub">Sign-in records are kept in a separate accounts store, not in these seven '
              "databases, and are not described here.</p>")
    return ctx.page("Data documentation", body,
                    subtitle="Generated from the live databases each time this page opens.")


# -- updates ----------------------------------------------------------------------------

def _update_rows(entries: list[dict[str, Any]]) -> list[list[str]]:
    return [[
        esc(entry["date"].isoformat()),
        f'<strong>{esc(entry["title"])}</strong><br>{esc(entry["summary"])}',
        " ".join(f"<code>{esc(commit)}</code>" for commit in entry["commits"]) or "-",
    ] for entry in entries]


def updates_page(ctx: Context, _match: re.Match[str]) -> Response:
    announcements = ctx.store.list_announcements(limit=50)
    portal_rows = [[esc(item["created_at"][:10]),
                    f'<strong>{esc(item["title"])}</strong>{markup(item["body"])}']
                   for item in announcements]
    body = ('<p class="tabs"><a href="#portal">Portal updates</a> · <a href="#coding">Coding updates</a> · '
            '<a href="#data">Data updates</a></p>'
            '<h2 id="portal">Portal updates</h2>'
            '<p class="sub">Notes from the owner.</p>'
            + table(["posted", "update"], portal_rows))
    try:
        updates = load_updates()
    except UpdatesError as error:
        body += error_box(f"The coding and data updates could not be read: {error}.")
    else:
        for kind, label in UPDATE_KINDS:
            body += (f'<h2 id="{kind}">{esc(label)}</h2>'
                     + table(["date", "what changed", "commits"], _update_rows(updates[kind])))
    return ctx.page("Updates", body, subtitle="What changed in the portal, the code and the data.")


# -- connection status --------------------------------------------------------------------

def connection_page(ctx: Context, _match: re.Match[str]) -> Response:
    settings = ctx.settings
    results = settings.connectivity.report(settings.root, mailer=settings.mailer,
                                           google_configured=settings.google is not None)
    rows = [[f'<span class="{STATE_CLASS[result.state]}">{esc(result.label)}</span>',
             esc(result.name), esc(result.detail), esc(f"{result.checked_at:%Y-%m-%d %H:%M:%S} UTC")]
            for result in results]
    problems = sum(result.state == PROBLEM for result in results)
    summary = ("Everything checked is working." if not problems
               else f"{problems} problem{'s' if problems != 1 else ''} found.")
    body = ('<p class="notice">This page checks what the portal\'s server can reach. The portal does not '
            "provide internet access and is not a VPN: people reach it over their own internet "
            "connection, and a VPN such as Tailscale or WireGuard, set up separately, is what puts them "
            "on the same private network as the server.</p>"
            f"<p>{esc(summary)}</p>"
            + table(["", "check", "detail", "checked"], rows)
            + f'<p class="sub">Checks run on the server with short timeouts and are reused for '
              f"{settings.connectivity.ttl:g} seconds.</p>")
    return ctx.page("Connection status", body, subtitle="The portal's own health and its connections.")


ROUTES: tuple[Route, ...] = (
    Route("GET", "/data-docs", MEMBER, data_docs_page),
    Route("GET", r"/data-docs/pdf/(?P<name>[a-z0-9][a-z0-9-]*\.pdf)", MEMBER, pdf_file),
    Route("GET", "/updates", MEMBER, updates_page),
    Route("GET", "/connection", MEMBER, connection_page),
)
