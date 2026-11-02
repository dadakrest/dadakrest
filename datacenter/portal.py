"""The web portal over the data center, with real sign-in.

`python -m datacenter portal` serves the seven databases as pages: search,
documents, contacts, datasets, the fee tables, the costed business plans and
the audit trail. The sign-in, request and account pages are in
`portal_auth`, the owner center in `portal_owner`, and what they share (the
guard, forms, cookies, page layout) in `portal_web`.

The data stays read-only.
    No page writes to the seven databases. Everything that changes the data
    center goes through the CLI or the Python API, where it is logged to the
    audit trail by a named caller. The POST handlers change accounts and
    portal updates only, in the separate accounts store.

Nobody sees data without both sign-in steps.
    A password or Google, then a code emailed to the account's address (or
    one backup code), on an account the owner approved. /healthz is the one
    page open to everyone, and it says only "ok".

Local by default.
    The server binds 127.0.0.1 unless `allow_remote=True` (CLI:
    --allow-remote), and `allow_network` limits who is served at all to the
    networks listed, such as a VPN's.

Everything a page displays comes from the databases or from people, so every
value is escaped on the way out; see `portal_web.markup`, which escapes
before it adds any tag of its own.
"""

from __future__ import annotations

import os
import re
import socket
import sys
import threading
import time
from collections.abc import Iterable
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, quote, urlsplit

from . import portal_auth, portal_owner
from .accounts import PASSWORD_ITERATIONS, AccountError, AccountStore
from .config import DATA_CENTER_NAME, VERSION
from .core import DataCenter
from .google_signin import GoogleConfigError, HttpPost, check_public_url
from .google_signin import config_from_env as google_config_from_env
from .mailer import Mailer, MailNotConfigured, mailer_from_env
from .plans import PlanError, cost_plan, plan_names
from .portal_web import (
    MAX_BODY,
    MEMBER,
    NAV,
    PUBLIC,
    STYLE,
    STYLESHEET,
    Context,
    Form,
    HttpError,
    NotFound,
    PortalSettings,
    Request,
    Response,
    Route,
    authorize,
    client_ip,
    error_box,
    esc,
    markup,
    money,
    network_allowed,
    page,
    parse_cookies,
    parse_networks,
    same_origin,
    security_headers,
    table,
    throttle_probe,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8013
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")

#: How often expired sessions, codes and flows are swept out of the store.
PURGE_EVERY = 600.0

# Names earlier callers import from here; they now live in portal_web.
_markup = markup
__all__ = ["NAV", "build_server", "esc", "money", "page", "render", "resolve", "serve", "table"]

FEE_VIEWS: dict[str, tuple[str, tuple[str, ...]]] = {
    "government": ("government_fees",
                   ("fee_code", "jurisdiction", "name", "category", "period", "amount_usd", "authority")),
    "service": ("service_fees",
                ("fee_code", "service", "category", "unit", "unit_price_usd", "minimum_units")),
    "employment": ("employment_fees",
                   ("fee_code", "role", "employment_type", "base_salary_usd",
                    "employer_tax_pct", "benefits_usd_month", "recruitment_fee_usd")),
}


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
    body = f'<div class="doc">{markup(document["body"])}</div>'
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


def _normalise(path: str) -> str:
    """"/documents/" is "/documents"."""
    return path.rstrip("/") or "/"


def resolve(center: DataCenter, path: str, query: dict[str, list[str]]) -> tuple[str, str, str]:
    """The data view for one path, as (title, subtitle, body). Raises NotFound."""
    path = _normalise(path)
    if path in ROUTES:
        return ROUTES[path](center, query)
    for prefix, view in PREFIXES.items():
        if path.startswith(prefix):
            remainder = path[len(prefix):]
            if remainder and "/" not in remainder:
                return view(center, remainder)
    raise NotFound(f"no page at {path}")


def render(center: DataCenter, path: str, query: dict[str, list[str]]) -> tuple[int, bytes]:
    """Resolve one path to a status code and a rendered page, with no sign-in:
    for callers that already decided who may see the data."""
    try:
        title, subtitle, body = resolve(center, path, query)
        return HTTPStatus.OK, page(title, body, subtitle=subtitle)
    except NotFound as error:
        return HTTPStatus.NOT_FOUND, page(
            "Not found", f'<p class="empty">{esc(error)}</p><p><a href="/">Back to the overview</a></p>')


# -- the web routes -----------------------------------------------------------

def _data_page(ctx: Context, _match: re.Match[str]) -> Response:
    title, subtitle, body = resolve(ctx.center(), ctx.request.path, ctx.request.query)
    return ctx.page(title, body, subtitle=subtitle)


def _health(_ctx: Context, _match: re.Match[str]) -> Response:
    # Says it is up and nothing else: no version, no counts, no paths.
    return Response(HTTPStatus.OK, b'{"status":"ok"}', "application/json")


def _stylesheet(_ctx: Context, _match: re.Match[str]) -> Response:
    return Response(HTTPStatus.OK, STYLE.encode("utf-8"), "text/css; charset=utf-8", cacheable=True)


DATA_ROUTES: tuple[Route, ...] = (
    tuple(Route("GET", re.escape(path), MEMBER, _data_page) for path in ROUTES)
    + tuple(Route("GET", re.escape(prefix) + "[^/]+", MEMBER, _data_page) for prefix in PREFIXES)
)

WEB_ROUTES: tuple[Route, ...] = (
    Route("GET", "/healthz", PUBLIC, _health),
    Route("GET", re.escape(STYLESHEET), PUBLIC, _stylesheet),
    *DATA_ROUTES,
    *portal_auth.ROUTES,
    *portal_owner.ROUTES,
)


class PortalApp:
    """Takes a parsed request through the guard to its handler."""

    def __init__(self, settings: PortalSettings, routes: Iterable[Route] = WEB_ROUTES) -> None:
        self.settings = settings
        self.routes = tuple(routes)
        self._lock = threading.Lock()
        self._provisioned = False
        self._purged_at = float("-inf")

    def open_store(self) -> AccountStore:
        """A store for one request. The first creates the store's file, and
        every PURGE_EVERY seconds one sweeps out what has expired."""
        settings = self.settings
        store = AccountStore(settings.root, mailer=settings.mailer, environ=settings.environ,
                             clock=settings.clock, password_iterations=settings.password_iterations)
        try:
            with self._lock:
                if not self._provisioned:
                    store.provision()
                    self._provisioned = True
                if time.monotonic() - self._purged_at >= PURGE_EVERY:
                    store.purge_expired()
                    self._purged_at = time.monotonic()
        except BaseException:
            store.close()
            raise
        return store

    def find(self, method: str, path: str) -> tuple[Route | None, re.Match[str] | None, list[str]]:
        """The route for this method and path, and the methods the path takes."""
        methods: list[str] = []
        for route in self.routes:
            match = route.regex.fullmatch(path)
            if match is None:
                continue
            if route.method == method:
                return route, match, methods
            methods.append(route.method)
        return None, None, methods

    def handle(self, request: Request) -> Response:
        if not self.settings.host_allowed(request.header("Host")):
            return Response(HTTPStatus.BAD_REQUEST, page(
                "Unknown address", f'<p class="empty">This portal answers at '
                f'{esc(self.settings.public_url)}.</p>', nav=()))
        request.path = _normalise(request.path)
        route, match, methods = self.find(request.method, request.path)
        if route is not None and route.access == PUBLIC:
            assert match is not None
            return route.handler(Context(self.settings, request, None), match)

        store = self.open_store()
        ctx = Context(self.settings, request, store)
        try:
            response = self._dispatch(ctx, route, match, methods)
        finally:
            ctx.close()
            store.close()
        response.headers.extend(("Set-Cookie", cookie) for cookie in ctx.set_cookies)
        return response

    def _dispatch(self, ctx: Context, route: Route | None, match: re.Match[str] | None,
                  methods: list[str]) -> Response:
        request = ctx.request
        try:
            ctx.load_session()
            if request.method == "POST":
                refusal = self._check_form(ctx)
                if refusal is not None:
                    return refusal
            if route is None or match is None:
                if ctx.session is None:
                    throttle_probe(ctx)
                if methods:
                    error = HttpError(HTTPStatus.METHOD_NOT_ALLOWED, "This page does not take that method.")
                    response = self._error(ctx, error)
                    response.headers.append(("Allow", ", ".join(sorted(set(methods)))))
                    return response
                raise NotFound(f"no page at {request.path}")
            refusal = authorize(ctx, route, match)
            if refusal is not None:
                return refusal
            return route.handler(ctx, match)
        except NotFound as error:
            body = f'<p class="empty">{esc(error)}</p><p><a href="/">Back to the overview</a></p>'
            return ctx.page("Not found", body, status=HTTPStatus.NOT_FOUND)
        except HttpError as error:
            return self._error(ctx, error)
        except FileNotFoundError:
            body = ('<p class="error">The data center is not set up on this server yet. '
                    "The owner needs to run: python -m datacenter init</p>")
            return ctx.page("Not set up", body, status=HTTPStatus.SERVICE_UNAVAILABLE)

    @staticmethod
    def _error(ctx: Context, error: HttpError) -> Response:
        response = ctx.page(error.title, error_box(error.message), status=error.status, bare=not ctx.full)
        if error.retry_after:
            response.headers.append(("Retry-After", str(error.retry_after)))
        return response

    def _check_form(self, ctx: Context) -> Response | None:
        """Every POST: from this portal's own pages, with this caller's token.
        A refusal here never counts as a strike: a request that fails it may
        have been forged by another site, and its victim did nothing."""
        request = ctx.request
        if not same_origin(request, self.settings):
            reason = "This form did not come from this portal."
        elif not ctx.csrf_ok():
            reason = "This form has expired. Go back, reload the page and try again."
        else:
            return None
        if ctx.full and ctx.account is not None:
            ctx.store.log_security_event("form_refused", account_id=ctx.account.id, ip=request.ip,
                                         detail=f"POST {request.path}: {reason}")
        return self._error(ctx, HttpError(HTTPStatus.FORBIDDEN, reason))


# -- HTTP -----------------------------------------------------------------------

_QUERY = re.compile(r"\?[^\s'\"]*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

#: A form body larger than MAX_BODY is still read off the socket, up to this
#: much, so the client sees the 413 rather than a reset connection.
_DRAIN_LIMIT = 1024 * 1024


class PortalHandler(BaseHTTPRequestHandler):
    """Reads one request, hands it to the app, writes the answer with the
    security headers every response carries."""

    server_version = f"SDDU13/{VERSION}"
    root: Path = Path("storage")
    quiet: bool = False
    app: PortalApp | None = None
    #: Seconds a client may take over its request before it is dropped.
    timeout = 30

    def do_GET(self) -> None:  # noqa: N802 - the name BaseHTTPRequestHandler dispatches to
        self._serve("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._serve("POST")

    def _serve(self, method: str) -> None:
        app = self.app
        assert app is not None, "build the handler with build_server"
        settings = app.settings
        forwarded = ",".join(self.headers.get_all("X-Forwarded-For") or [])
        ip = client_ip(self.client_address[0], forwarded, trust_proxy=settings.trust_proxy)
        if not network_allowed(ip, settings.allowed_networks):
            self.close_connection = True
            self._send(Response(HTTPStatus.FORBIDDEN, page(
                "Not available", '<p class="empty">This portal is not open to your network.</p>', nav=())))
            return
        try:
            response = app.handle(self._read(method, ip))
        except HttpError as error:
            response = Response(error.status, page(error.title, error_box(error.message), nav=()))
        except Exception as error:  # a bad page must not take the server down
            self.log_error("%s %s failed: %s: %s", method, urlsplit(self.path).path,
                           type(error).__name__, error)
            response = Response(HTTPStatus.INTERNAL_SERVER_ERROR, page(
                "Something went wrong", '<p class="empty">The page could not be shown. Try again, and '
                "tell the owner if it keeps happening.</p>", nav=()))
        self._send(response)

    def _read(self, method: str, ip: str) -> Request:
        if len(self.path) > 4096:
            raise HttpError(HTTPStatus.REQUEST_URI_TOO_LONG, "That address is too long.")
        parts = urlsplit(self.path)
        try:
            query = parse_qs(parts.query, max_num_fields=50)
        except ValueError:
            raise HttpError(HTTPStatus.BAD_REQUEST, "The address has too many parameters.") from None
        form = Form()
        if method == "POST":
            form = self._read_form()
        return Request(method=method, path=parts.path or "/", query=query, headers=self.headers, ip=ip,
                       cookies=parse_cookies(self.headers.get_all("Cookie") or []), form=form)

    def _read_form(self) -> Form:
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise HttpError(HTTPStatus.LENGTH_REQUIRED, "Send the form with a Content-Length.")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.close_connection = True
            raise HttpError(HTTPStatus.LENGTH_REQUIRED, "Send the form with a Content-Length.") from None
        if length < 0:
            self.close_connection = True
            raise HttpError(HTTPStatus.BAD_REQUEST, "The Content-Length is not valid.")
        if length > MAX_BODY:
            self.close_connection = True
            if length <= _DRAIN_LIMIT:
                self.rfile.read(length)
            raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "That form is too large.")
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        body = self.rfile.read(length)
        if kind != "application/x-www-form-urlencoded":
            raise HttpError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                            "Send forms as application/x-www-form-urlencoded.")
        return Form.parse(body)

    def _send(self, response: Response) -> None:
        settings = self.app.settings if self.app is not None else PortalSettings(root=self.root)
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        for name, value in security_headers(settings, cacheable=response.cacheable):
            self.send_header(name, value)
        for name, value in response.headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(response.body)

    def log_request(self, code: Any = "-", size: Any = "-") -> None:
        # The path only: a Google callback's query string carries a one-time code.
        status = code.value if isinstance(code, HTTPStatus) else code
        path = urlsplit(getattr(self, "path", "") or "").path[:200]
        self.log_message('"%s %s" %s %s', getattr(self, "command", None) or "-", path, status, size)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class's name
        if self.quiet:
            return
        message = _QUERY.sub("?...", format % args)
        message = _CONTROL.sub(lambda found: f"\\x{ord(found.group()):02x}", message)
        sys.stderr.write(f"{self.address_string()} - - [{self.log_date_time_string()}] {message}\n")


def portal_settings(
    root: Path | str,
    *,
    host: str = DEFAULT_HOST,
    mail_outbox: Path | str | None = None,
    allow_network: Iterable[str] = (),
    trust_proxy: bool = False,
    https_cookies: bool = False,
    public_url: str | None = None,
    environ: Mapping[str, str] | None = None,
    mailer: Mailer | None = None,
    google_http_post: HttpPost | None = None,
    clock: Callable[[], datetime] | None = None,
    password_iterations: int = PASSWORD_ITERATIONS,
) -> PortalSettings:
    """Read the configuration once, at start-up. Raises ValueError for a
    setting that cannot work; mail or Google that is not set up is recorded
    and shown, not raised, so the owner can still reach the server."""
    env = dict(os.environ if environ is None else environ)
    if public_url:
        env["SDDU13_PUBLIC_URL"] = public_url
    url = env.get("SDDU13_PUBLIC_URL", "").strip()
    if url:
        try:
            url = check_public_url(url)
        except GoogleConfigError as error:
            raise ValueError(str(error)) from None

    mail_problem = ""
    if mailer is None:
        try:
            mailer = mailer_from_env(env, outbox=mail_outbox)
        except MailNotConfigured as error:
            mail_problem = str(error)

    google, google_problem = None, ""
    try:
        google = google_config_from_env(env)
    except GoogleConfigError as error:
        google_problem = str(error)

    return PortalSettings(
        root=Path(root), environ=env, mailer=mailer, mail_problem=mail_problem, google=google,
        google_problem=google_problem, google_http_post=google_http_post, public_url=url,
        https_cookies=https_cookies, allowed_networks=parse_networks(allow_network),
        trust_proxy=trust_proxy, host=host, clock=clock, password_iterations=password_iterations)


def build_server(root: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
                 *, allow_remote: bool = False, quiet: bool = False, **options: Any) -> ThreadingHTTPServer:
    """Build the server without starting it. `options` are `portal_settings`'s.

    Refuses a reachable address without `allow_remote`: anyone who can reach
    it gets the sign-in page, so that should be a decision, not a default.
    """
    if host not in LOCAL_HOSTS and not allow_remote:
        raise ValueError(
            f"refusing to bind {host}: that serves the portal to every machine that can reach it. "
            "Pass allow_remote=True (CLI: --allow-remote) if that is what you want, ideally with "
            "--allow-network for your VPN's range."
        )

    settings = portal_settings(root, host=host, **options)
    app = PortalApp(settings)
    handler = type("BoundPortalHandler", (PortalHandler,),
                   {"root": Path(root), "quiet": quiet, "app": app})
    ThreadingHTTPServer.allow_reuse_address = True
    ThreadingHTTPServer.address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
    server = ThreadingHTTPServer((host, port), handler)
    server.portal = app  # type: ignore[attr-defined] - for serve() and tests
    return server


def startup_report(app: PortalApp, bound_host: str, bound_port: int) -> list[str]:
    """What `serve` prints: where it is, and anything that stops sign-in working."""
    settings = app.settings
    lines = [f"{DATA_CENTER_NAME} portal on http://{bound_host}:{bound_port}  (root: {settings.root})"]
    if settings.public_url:
        lines.append(f"public URL: {settings.public_url}")
    if settings.mailer is not None:
        lines.append(f"email: {settings.mailer.describe()}")
    else:
        lines.append(f"SIGN-IN IS OFF. {settings.mail_problem}")
    if settings.google is not None:
        lines.append("Google sign-in: on")
    else:
        lines.append(f"Google sign-in: {settings.google_problem or 'not set up'}")
    store = app.open_store()
    try:
        owner = store.owner_email()
    finally:
        store.close()
    lines.append(f"owner: {owner}" if owner else
                 "no owner email yet: set SDDU13_OWNER_EMAIL to the owner's address")
    if settings.allowed_networks:
        lines.append("serving only: " + ", ".join(str(network) for network in settings.allowed_networks))
    if settings.trust_proxy:
        lines.append("trusting X-Forwarded-For from a proxy on this machine")
    if settings.host not in LOCAL_HOSTS:
        if not settings.allowed_networks:
            lines.append("WARNING: bound to a reachable address with no --allow-network: "
                         "anyone who can reach it gets the sign-in page.")
        if not settings.secure_cookies:
            lines.append("WARNING: cookies can travel over plain HTTP; put HTTPS in front and use --https-cookies.")
    return lines


def serve(root: Path | str, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          *, allow_remote: bool = False, **options: Any) -> None:  # pragma: no cover - blocks
    """Serve until interrupted."""
    server = build_server(root, host, port, allow_remote=allow_remote, **options)
    app: PortalApp = server.portal  # type: ignore[attr-defined]
    try:
        for line in startup_report(app, *server.server_address[:2]):
            print(line)
    except AccountError as error:
        server.server_close()
        raise ValueError(str(error)) from None
    print("Ctrl-C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
