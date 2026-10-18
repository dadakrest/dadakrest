"""A read-only web portal over the data center.

`python -m datacenter portal` serves the seven databases as pages: search,
documents, contacts, datasets, the fee tables and the costed business plans.

Two deliberate limits:

Read-only.
    There are no POST handlers and no write paths. Everything that changes the
    data center goes through the CLI or the Python API, where it is logged to
    the audit trail by a named caller. A browser tab cannot delete a document.

Local by default.
    The server binds 127.0.0.1 unless told otherwise. There is no
    authentication of any kind, so binding it to a reachable address publishes
    every contact, document and fee to whoever can route to it — `serve`
    refuses to do that without `allow_remote=True`, and the CLI makes you pass
    `--host` explicitly and prints a warning.

Everything a page displays comes from the databases, so every value is escaped
on the way out; see `_markup`, which escapes before it adds any tag of its own.
"""

from __future__ import annotations

import html
import json
import re
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlparse

from .config import DATA_CENTER_NAME, VERSION
from .core import DataCenter
from .plans import PlanError, cost_plan, plan_names

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8013

FEE_VIEWS: dict[str, tuple[str, tuple[str, ...]]] = {
    "government": ("government_fees",
                   ("fee_code", "jurisdiction", "name", "category", "period", "amount_usd", "authority")),
    "service": ("service_fees",
                ("fee_code", "service", "category", "unit", "unit_price_usd", "minimum_units")),
    "employment": ("employment_fees",
                   ("fee_code", "role", "employment_type", "base_salary_usd",
                    "employer_tax_pct", "benefits_usd_month", "recruitment_fee_usd")),
}

STYLE = """
:root{--bg:#f7f7f6;--panel:#fff;--ink:#17201f;--muted:#5d6a68;--rule:#dfe3e2;
--accent:#0f5f68;--accent-bg:#e8f1f1;--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media(prefers-color-scheme:dark){:root{--bg:#0f1514;--panel:#171f1e;--ink:#e6ecea;
--muted:#94a3a0;--rule:#27322f;--accent:#57c2c9;--accent-bg:#14282a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);line-height:1.55;
font:16px/1.55 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
header{border-bottom:1px solid var(--rule);background:var(--panel)}
.bar{max-width:1100px;margin:0 auto;padding:14px 20px;display:flex;flex-wrap:wrap;
gap:10px 18px;align-items:baseline}
.brand{font-weight:700;text-decoration:none;color:var(--ink);margin-right:6px}
nav{display:flex;flex-wrap:wrap;gap:6px 16px}
nav a{color:var(--muted);text-decoration:none;font-size:14px}
nav a:hover,nav a:focus{color:var(--accent)}
main{max-width:1100px;margin:0 auto;padding:26px 20px 64px}
h1{font-size:25px;margin:0 0 6px;line-height:1.2}
h2{font-size:14px;font-family:var(--mono);font-weight:500;letter-spacing:.08em;
text-transform:uppercase;color:var(--muted);margin:34px 0 12px;
padding-bottom:8px;border-bottom:1px solid var(--rule)}
p.sub{color:var(--muted);margin:0 0 18px}
a{color:var(--accent)}
form{display:flex;gap:8px;margin:0 0 20px;flex-wrap:wrap}
input[type=search]{flex:1;min-width:220px;padding:9px 12px;font-size:15px;
border:1px solid var(--rule);border-radius:6px;background:var(--panel);color:var(--ink)}
button{padding:9px 16px;font-size:15px;font-weight:600;border:1px solid var(--accent);
border-radius:6px;background:var(--accent);color:var(--panel);cursor:pointer}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--rule);vertical-align:top}
th{font-weight:600;color:var(--muted);white-space:nowrap}
td.num{text-align:right;font-variant-numeric:tabular-nums;font-family:var(--mono);white-space:nowrap}
tr:last-child td{border-bottom:none}
tfoot td{font-weight:700;border-top:2px solid var(--rule)}
code,.mono{font-family:var(--mono);font-size:13px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.card{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:14px 16px}
.card .n{font-size:24px;font-weight:700;font-variant-numeric:tabular-nums}
.card .l{color:var(--muted);font-size:13px}
.doc{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:20px 22px}
.doc p{margin:0 0 12px}.doc ul,.doc ol{margin:0 0 12px;padding-left:22px}
.tag{display:inline-block;font-family:var(--mono);font-size:12px;background:var(--accent-bg);
color:var(--accent);padding:2px 8px;border-radius:10px;margin-right:6px}
.score{font-family:var(--mono);color:var(--accent);font-size:13px}
.empty{color:var(--muted);padding:16px 0}
.warn{background:var(--accent-bg);border:1px solid var(--rule);border-left:3px solid var(--accent);
border-radius:6px;padding:12px 16px;margin:0 0 20px;font-size:14px;color:var(--muted)}
footer{max-width:1100px;margin:0 auto;padding:20px;color:var(--muted);
font-family:var(--mono);font-size:12px;border-top:1px solid var(--rule)}
@media(max-width:560px){th,td{padding:7px 8px}}
"""

NAV = (
    ("/", "Overview"), ("/search", "Search"), ("/documents", "Documents"),
    ("/contacts", "Contacts"), ("/datasets", "Datasets"), ("/fees", "Fees"),
    ("/plans", "Plans"), ("/events", "Audit"),
)


# -- rendering ----------------------------------------------------------------

def esc(value: Any) -> str:
    """Everything shown on a page goes through here first."""
    return html.escape("" if value is None else str(value), quote=True)


def money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return esc(value)


_BOLD = re.compile(r"\*\*([^*]+)\*\*")
_ORDERED = re.compile(r"^\d+[.)]\s*")
_CODE = re.compile(r"`([^`]+)`")


def _markup(text: str) -> str:
    """Render a stored document body as HTML.

    The text is escaped first and only then given tags, so a document
    containing `<script>` is displayed as those characters rather than run.
    Markdown support is deliberately tiny: paragraphs, bullet and numbered
    lists, bold and inline code.
    """
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue

        def inline(value: str) -> str:
            safe = esc(value)
            safe = _BOLD.sub(r"<strong>\1</strong>", safe)
            return _CODE.sub(r"<code>\1</code>", safe)

        if all(line.startswith(("- ", "* ")) for line in lines):
            items = "".join(f"<li>{inline(line[2:])}</li>" for line in lines)
            out.append(f"<ul>{items}</ul>")
        elif all(_ORDERED.match(line) for line in lines):
            items = "".join(f"<li>{inline(_ORDERED.sub('', line))}</li>" for line in lines)
            out.append(f"<ol>{items}</ol>")
        else:
            out.append(f"<p>{inline(' '.join(lines))}</p>")
    return "".join(out)


def page(title: str, body: str, *, subtitle: str = "") -> bytes:
    nav = "".join(f'<a href="{path}">{esc(label)}</a>' for path, label in NAV)
    sub = f'<p class="sub">{esc(subtitle)}</p>' if subtitle else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — {esc(DATA_CENTER_NAME)}</title>
<style>{STYLE}</style></head>
<body><header><div class="bar">
<a class="brand" href="/">{esc(DATA_CENTER_NAME)}</a><nav>{nav}</nav>
</div></header>
<main><h1>{esc(title)}</h1>{sub}{body}</main>
<footer>{esc(DATA_CENTER_NAME)} v{esc(VERSION)} · read-only portal · stdlib only</footer>
</body></html>""".encode("utf-8")


def table(columns: list[str], rows: list[list[str]], *, numeric: set[int] = frozenset(),
          footer: list[str] | None = None) -> str:
    if not rows:
        return '<p class="empty">Nothing here yet.</p>'
    head = "".join(f"<th>{esc(column)}</th>" for column in columns)
    body = "".join(
        "<tr>" + "".join(
            f'<td class="num">{cell}</td>' if index in numeric else f"<td>{cell}</td>"
            for index, cell in enumerate(row)
        ) + "</tr>"
        for row in rows
    )
    foot = ""
    if footer:
        foot = "<tfoot><tr>" + "".join(
            f'<td class="num">{cell}</td>' if index in numeric else f"<td>{cell}</td>"
            for index, cell in enumerate(footer)
        ) + "</tr></tfoot>"
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody>{foot}</table></div>'


# -- views --------------------------------------------------------------------

def view_overview(center: DataCenter, _query: dict[str, list[str]]) -> tuple[str, str, str]:
    report = center.status()
    cards = "".join(
        f'<div class="card"><div class="n">{sum(db["rows"].values()):,}</div>'
        f'<div class="l">{esc(db["title"])}</div></div>'
        for db in report["databases"]
    )
    rows = [[
        f'<code>{esc(db["key"])}</code>',
        esc(db["title"]),
        "healthy" if db["healthy"] else ("not provisioned" if not db["provisioned"] else "DEGRADED"),
        f'{db["size_bytes"] / 1024:,.0f} KB',
        esc(", ".join(f"{t}={n}" for t, n in db["rows"].items()) or "-"),
    ] for db in report["databases"]]
    body = (f'<div class="cards">{cards}</div>'
            + "<h2>Databases</h2>"
            + table(["key", "holds", "state", "size", "rows"], rows, numeric={3}))
    return "Overview", f"Seven databases at {report['root']}", body


def view_search(center: DataCenter, query: dict[str, list[str]]) -> tuple[str, str, str]:
    term = (query.get("q") or [""])[0].strip()
    form = (f'<form method="get" action="/search">'
            f'<input type="search" name="q" value="{esc(term)}" placeholder="Search the knowledge base" autofocus>'
            f'<button type="submit">Search</button></form>')
    if not term:
        return "Search", "Semantic search across every indexed document.", form

    results = center.search(term, limit=20)
    if not results:
        return "Search", f"No document matches {term!r}.", form + '<p class="empty">No matches.</p>'
    rows = [[
        f'<span class="score">{hit["score"]:.4f}</span>',
        f'<a href="/documents/{hit["document_id"]}">{esc(hit["title"])}</a>',
        esc(hit["source"]),
    ] for hit in results]
    return ("Search", f"{len(results)} documents match {term!r}.",
            form + table(["score", "document", "source"], rows))


def view_documents(center: DataCenter, _query: dict[str, list[str]]) -> tuple[str, str, str]:
    documents = center.list_documents()
    rows = [[
        f'<a href="/documents/{document["id"]}">{esc(document["title"])}</a>',
        f'<code>{esc(document["external_id"] or "-")}</code>',
        esc(document["source"] or "-"),
        "".join(f'<span class="tag">{esc(tag)}</span>' for tag in center.document_tags(document["id"])),
    ] for document in documents]
    return ("Documents", f"{len(documents)} documents in the knowledge base.",
            table(["title", "external id", "source", "tags"], rows))


def view_document(center: DataCenter, document_id: str) -> tuple[str, str, str]:
    try:
        document = center.get_document(int(document_id))
    except ValueError:
        document = None
    if document is None:
        raise NotFound(f"no document {document_id}")
    tags = "".join(f'<span class="tag">{esc(tag)}</span>' for tag in center.document_tags(document["id"]))
    body = f'<div class="doc">{_markup(document["body"])}</div>'
    subtitle = " · ".join(part for part in (
        document["source"], document["external_id"], document["owner_email"]) if part)
    return document["title"], subtitle, (tags + body if tags else body)


def view_contacts(center: DataCenter, _query: dict[str, list[str]]) -> tuple[str, str, str]:
    contacts = center.list_contacts()
    rows = [[esc(c["full_name"]), esc(c["role"] or "-"), esc(c["organization"] or "-"),
             f'<code>{esc(c["email"] or "-")}</code>', esc(c["phone"] or "-")] for c in contacts]
    return ("Contacts", f"{len(contacts)} people on record.",
            table(["name", "role", "organization", "email", "phone"], rows))


def view_datasets(center: DataCenter, _query: dict[str, list[str]]) -> tuple[str, str, str]:
    datasets = center.list_datasets()
    rows = [[
        f'<a href="/datasets/{quote(dataset["name"])}">{esc(dataset["name"])}</a>',
        esc(dataset["description"] or "-"),
        f'{dataset["records"]:,}',
    ] for dataset in datasets]
    return ("Datasets", f"{len(datasets)} datasets.",
            table(["dataset", "description", "records"], rows, numeric={2}))


def view_dataset(center: DataCenter, name: str) -> tuple[str, str, str]:
    known = {dataset["name"]: dataset for dataset in center.list_datasets()}
    if name not in known:
        raise NotFound(f"no dataset {name}")
    records = center.list_records(name)
    columns = list(known[name]["schema"]) or sorted(
        {key for record in records for key in record["payload"]})
    numeric = {index for index, column in enumerate(columns)
               if known[name]["schema"].get(column) in ("number", "integer")}
    rows = [[esc(record["payload"].get(column, "")) for column in columns] for record in records]
    return (name, f'{len(records):,} records. {known[name]["description"] or ""}',
            table(columns, rows, numeric=numeric))


def view_fees(center: DataCenter, _query: dict[str, list[str]]) -> tuple[str, str, str]:
    sections: list[str] = []
    for label, (dataset, columns) in FEE_VIEWS.items():
        records = center.list_records(dataset)
        numeric = {index for index, column in enumerate(columns)
                   if column.endswith(("_usd", "_pct", "_units"))}
        rows = [[
            money(record["payload"].get(column)) if column.endswith("_usd")
            else esc(record["payload"].get(column, ""))
            for column in columns
        ] for record in records]
        sections.append(f"<h2>{esc(label)} — {esc(dataset)} ({len(records)})</h2>"
                        + table(list(columns), rows, numeric=numeric))
    return ("Fees",
            "Statutory fees the business pays, the rate card it charges, and what each role costs to employ. "
            "Figures are illustrative, not legal or tax advice.",
            "".join(sections))


def view_plans(center: DataCenter, _query: dict[str, list[str]]) -> tuple[str, str, str]:
    rows = []
    for name in plan_names(center):
        plan = cost_plan(center, name)
        rows.append([
            f'<a href="/plans/{quote(name)}">{esc(name)}</a>',
            str(plan["months"]),
            money(plan["total_usd"]),
            money(plan["monthly_average_usd"]),
            esc(", ".join(f"{table_.split('_')[0]} {money(amount)}"
                          for table_, amount in sorted(plan["subtotals"].items()))),
        ])
    return ("Business plans",
            "Each plan's lines connect to the fee tables through fee_code.",
            table(["plan", "months", "total", "per month", "made up of"], rows, numeric={1, 2, 3}))


def view_plan(center: DataCenter, name: str) -> tuple[str, str, str]:
    try:
        plan = cost_plan(center, name)
    except PlanError as error:
        raise NotFound(str(error)) from error
    rows = [[
        f'<code>{esc(row["fee_code"])}</code>',
        esc(row["line"]),
        esc(row["description"]),
        esc(row["basis"]),
        money(row["amount_usd"]),
    ] for row in plan["lines"]]
    footer = ["", "TOTAL", "", "", money(plan["total_usd"])]
    subtotals = "".join(
        f'<div class="card"><div class="n">{money(amount)}</div>'
        f'<div class="l">{esc(table_)}</div></div>'
        for table_, amount in sorted(plan["subtotals"].items()))
    subtotals += (f'<div class="card"><div class="n">{money(plan["monthly_average_usd"])}</div>'
                  f'<div class="l">per month</div></div>')
    return (plan["plan"], f'{plan["months"]} months · {money(plan["total_usd"])} total',
            f'<div class="cards">{subtotals}</div><h2>Lines</h2>'
            + table(["code", "line", "fee", "basis", "amount"], rows, numeric={4}, footer=footer))


def view_events(center: DataCenter, _query: dict[str, list[str]]) -> tuple[str, str, str]:
    events = center.recent_events(limit=100)
    rows = [[esc(e["occurred_at"]), f'<code>{esc(e["database"])}</code>',
             esc(e["action"]), esc(e["detail"])] for e in events]
    return ("Audit trail", f"The {len(events)} most recent events.",
            table(["when", "database", "action", "detail"], rows))


class NotFound(Exception):
    """A page that does not correspond to anything stored."""


ROUTES: dict[str, Callable[[DataCenter, dict[str, list[str]]], tuple[str, str, str]]] = {
    "/": view_overview,
    "/search": view_search,
    "/documents": view_documents,
    "/contacts": view_contacts,
    "/datasets": view_datasets,
    "/fees": view_fees,
    "/plans": view_plans,
    "/events": view_events,
}

PREFIXES: dict[str, Callable[[DataCenter, str], tuple[str, str, str]]] = {
    "/documents/": view_document,
    "/datasets/": view_dataset,
    "/plans/": view_plan,
}


def render(center: DataCenter, path: str, query: dict[str, list[str]]) -> tuple[int, bytes]:
    """Resolve one path to a status code and a rendered page."""
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/") or "/"

    try:
        if path in ROUTES:
            title, subtitle, body = ROUTES[path](center, query)
            return HTTPStatus.OK, page(title, body, subtitle=subtitle)

        for prefix, view in PREFIXES.items():
            if path.startswith(prefix):
                remainder = path[len(prefix):]
                if remainder and "/" not in remainder:
                    title, subtitle, body = view(center, remainder)
                    return HTTPStatus.OK, page(title, body, subtitle=subtitle)

        raise NotFound(f"no page at {path}")
    except NotFound as error:
        return HTTPStatus.NOT_FOUND, page(
            "Not found", f'<p class="empty">{esc(error)}</p><p><a href="/">Back to the overview</a></p>')


class PortalHandler(BaseHTTPRequestHandler):
    """Serves pages. GET only — the portal never writes."""

    server_version = f"SDDU13/{VERSION}"
    root: Path = Path("storage")
    quiet: bool = False

    def do_GET(self) -> None:  # noqa: N802 - the name BaseHTTPRequestHandler dispatches to
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send(HTTPStatus.OK, b'{"status":"ok"}', "application/json")
            return

        # A connection per request: SQLite connections belong to the thread
        # that opened them, and this server handles requests on many.
        center = DataCenter(root=self.root)
        try:
            status, body = render(center, parsed.path, parse_qs(parsed.query))
        except Exception as error:  # a bad page must not take the server down
            self.log_error("rendering %s failed: %s", parsed.path, error)
            status, body = HTTPStatus.INTERNAL_SERVER_ERROR, page(
                "Something went wrong", f'<p class="empty">{esc(error)}</p>')
        finally:
            center.close()
        self._send(status, body)

    def _send(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        if not self.quiet:
            super().log_message(format, *args)


def build_server(root: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 *, allow_remote: bool = False, quiet: bool = False) -> ThreadingHTTPServer:
    """Build the server without starting it. Raises if asked to publish
    an unauthenticated portal to a reachable address."""
    if host not in ("127.0.0.1", "localhost", "::1") and not allow_remote:
        raise ValueError(
            f"refusing to bind {host}: the portal has no authentication, so this would publish "
            "every contact, document and fee to anything that can reach it. "
            "Pass allow_remote=True (CLI: --allow-remote) if that is what you want."
        )

    handler = type("BoundPortalHandler", (PortalHandler,),
                   {"root": Path(root), "quiet": quiet})
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
    return ThreadingHTTPServer((host, port), handler)


def serve(root: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          *, allow_remote: bool = False) -> None:  # pragma: no cover - blocks
    """Serve until interrupted."""
    server = build_server(root, host, port, allow_remote=allow_remote)
    bound_host, bound_port = server.server_address[:2]
    print(f"{DATA_CENTER_NAME} portal on http://{bound_host}:{bound_port}  (root: {root})")
    if allow_remote and host not in ("127.0.0.1", "localhost", "::1"):
        print("WARNING: bound to a reachable address with no authentication.")
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
