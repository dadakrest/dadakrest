"""What every portal page shares: HTML, forms, cookies, the request context,
and the one guard every route goes through.

`portal.py` owns the data pages, the dispatcher and the server;
`portal_auth.py` the sign-in and account pages; `portal_owner.py` the owner
center; `portal_content.py` the data documentation, updates and connection
pages. All four build on this module.

The guard
    Every route is declared with an access level (`Route.access`) and the
    dispatcher calls `authorize` before any handler runs, so no page has to
    remember to check who is asking. `Route` refuses the two mistakes that
    would matter most: a path under /owner that is not owner-only, and an
    owner-only route outside /owner. A route whose path names an account
    (`(?P<account>...)`) is that person's own: anyone else asking for it is
    refused, and given a strike when signed in.

Strikes and blocks
    A signed-in person who is not the owner and asks for an owner page, or
    for someone else's account page, gets a strike; three in 24 hours block
    the account. A POST to an owner action, or a form whose `account` field
    was changed to someone else's, blocks at once. A POST only gets that far
    after its CSRF token and origin have been checked, and a GET only counts
    when the browser says no other site started it (Sec-Fetch-Site), so
    nobody can get a member blocked by luring them to a page that links or
    posts here.

Forms
    Every POST carries a CSRF token derived from the caller's session cookie,
    or, before sign-in, from a random pre-auth cookie, and must also come from
    this portal's own origin (Origin, else Referer). Every value a page shows
    goes through `esc`.
"""

from __future__ import annotations

import html
import ipaddress
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .accounts import (
    PASSWORD_ITERATIONS,
    PREAUTH_COOKIE,
    SESSION_COOKIE,
    Account,
    AccountStore,
    NewSession,
    Session,
    clear_cookie,
    cookie_header,
    session_cookie,
)
from .config import DATA_CENTER_NAME, VERSION
from .connectivity import ConnectionChecker
from .core import DataCenter
from .google_signin import GoogleConfig, GoogleSignIn, HttpPost
from .mailer import Mailer

#: The largest form body the portal reads. Every form here is a few fields.
MAX_BODY = 64 * 1024
MAX_FIELDS = 50

CONTENT_SECURITY_POLICY = "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
STYLESHEET = "/portal.css"

#: Anonymous requests for pages that need sign-in, per address. Probing is
#: throttled rather than blocked: there is no account to block.
PROBE_LIMIT = 60
PROBE_WINDOW = timedelta(minutes=15)

STYLE = """
:root{--bg:#f7f7f6;--panel:#fff;--ink:#17201f;--muted:#5d6a68;--rule:#dfe3e2;
--accent:#0f5f68;--accent-bg:#e8f1f1;--bad:#b42318;--bad-bg:#fdeceb;--good:#18794e;--warn:#8a5a00;
--mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
@media(prefers-color-scheme:dark){:root{--bg:#0f1514;--panel:#171f1e;--ink:#e6ecea;
--muted:#94a3a0;--rule:#27322f;--accent:#57c2c9;--accent-bg:#14282a;--bad:#ff8a80;--bad-bg:#2c1716;
--good:#5fd39a;--warn:#f0c060}}
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
.who{margin-left:auto;display:flex;gap:10px;align-items:baseline;font-size:14px;color:var(--muted)}
main{max-width:1100px;margin:0 auto;padding:26px 20px 64px}
h1{font-size:25px;margin:0 0 6px;line-height:1.2}
h2{font-size:14px;font-family:var(--mono);font-weight:500;letter-spacing:.08em;
text-transform:uppercase;color:var(--muted);margin:34px 0 12px;
padding-bottom:8px;border-bottom:1px solid var(--rule)}
h3{font-size:16px;margin:22px 0 8px}
p.sub{color:var(--muted);margin:0 0 18px}
a{color:var(--accent)}
form{display:flex;gap:8px;margin:0 0 20px;flex-wrap:wrap}
form.stack{display:grid;gap:14px;max-width:460px;margin:0 0 24px}
form.stack label{display:grid;gap:5px;font-size:14px;font-weight:600}
form.inline{display:inline;margin:0}
input[type=search]{flex:1;min-width:220px;padding:9px 12px;font-size:15px;
border:1px solid var(--rule);border-radius:6px;background:var(--panel);color:var(--ink)}
input[type=text],input[type=email],input[type=password],textarea{width:100%;padding:9px 12px;
font:inherit;font-size:15px;border:1px solid var(--rule);border-radius:6px;background:var(--panel);color:var(--ink)}
textarea{min-height:96px;resize:vertical}
.hint{font-weight:400;color:var(--muted);font-size:13px}
button,.button{display:inline-block;padding:9px 16px;font-size:15px;font-weight:600;border:1px solid var(--accent);
border-radius:6px;background:var(--accent);color:var(--panel);cursor:pointer;text-decoration:none}
button.secondary,.button.secondary{background:var(--panel);color:var(--accent)}
button.danger{background:var(--bad);border-color:var(--bad);color:var(--panel)}
button.link{background:none;border:none;color:var(--accent);padding:0;font-size:14px;font-weight:500;
text-decoration:underline}
.actions{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.actions form{display:flex;gap:6px;margin:0;flex-wrap:nowrap}
.actions input[type=text]{width:auto;min-width:150px;padding:6px 8px;font-size:13px}
.actions button{padding:6px 10px;font-size:13px}
.tabs{display:flex;flex-wrap:wrap;gap:6px 16px;margin:0 0 18px;font-size:14px}
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
a.card{color:var(--ink);text-decoration:none}a.card:hover{border-color:var(--accent)}
.doc{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:20px 22px}
.doc p{margin:0 0 12px}.doc ul,.doc ol{margin:0 0 12px;padding-left:22px}
.tag{display:inline-block;font-family:var(--mono);font-size:12px;background:var(--accent-bg);
color:var(--accent);padding:2px 8px;border-radius:10px;margin-right:6px}
.score{font-family:var(--mono);color:var(--accent);font-size:13px}
.empty{color:var(--muted);padding:16px 0}
.warn,.notice{background:var(--accent-bg);border:1px solid var(--rule);border-left:3px solid var(--accent);
border-radius:6px;padding:12px 16px;margin:0 0 20px;font-size:14px;color:var(--ink)}
.error{background:var(--bad-bg);border:1px solid var(--rule);border-left:3px solid var(--bad);
border-radius:6px;padding:12px 16px;margin:0 0 20px;font-size:14px;color:var(--ink)}
.ok{color:var(--good);font-weight:600}.bad{color:var(--bad);font-weight:600}.check{color:var(--warn);font-weight:600}
footer{max-width:1100px;margin:0 auto;padding:20px;color:var(--muted);
font-family:var(--mono);font-size:12px;border-top:1px solid var(--rule)}
@media(max-width:560px){th,td{padding:7px 8px}.who{margin-left:0}}
"""

NAV = (
    ("/", "Overview"), ("/search", "Search"), ("/documents", "Documents"),
    ("/contacts", "Contacts"), ("/datasets", "Datasets"), ("/fees", "Fees"),
    ("/plans", "Plans"), ("/events", "Audit"),
)

#: Pages about the portal rather than the data (portal_content), shown to
#: everyone who may use it.
MEMBER_NAV = (("/data-docs", "Data docs"), ("/updates", "Updates"), ("/connection", "Connection"))

#: Messages a redirect can ask the next page to show (`?notice=key`). Only
#: these fixed texts are ever shown, so the query string cannot put words
#: on a page.
NOTICES = {
    "signed-out": "You are signed out.",
    "signed-out-everywhere": "You are signed out on every device.",
    "code-sent": "We emailed you a new code. Codes expire in 10 minutes.",
    "code-not-sent": "We did not send another code: too many were sent in the last hour. "
                     "Use the last one we sent, or a backup code.",
    "backup-sent": "We emailed you a new set of backup codes. Print them and keep them off shared "
                   "devices. Any older set no longer works.",
    "backup-later": "You have no backup codes left. Ask for a new set on your account page.",
    "backup-failed": "We could not email your backup codes. Use “Email me new backup codes” "
                     "on your account page.",
    "name-saved": "Your name is saved.",
    "password-changed": "Your password is changed. Your other sessions are signed out.",
    "request-sent": "Your request is with the owner.",
    "approved": "Approved.",
    "declined": "Declined.",
    "removed": "Access removed, and every session of theirs signed out.",
    "blocked": "Blocked for four months, and every session of theirs signed out.",
    "unblocked": "Unblocked. They can ask for access again; access is not restored.",
    "extended": "Kept blocked for four more months.",
    "reset-sent": "A password reset code was emailed to them.",
    "sessions-ended": "Every session of theirs is signed out.",
    "posted": "Update posted.",
    "deleted": "Update deleted.",
}

OWNER_NEVER_SEES = ("Nobody can see a password, a security code or a backup code here, the owner "
                    "included. The owner can approve, decline, remove, block, reset and sign people "
                    "out, never read their secrets.")


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


def markup(text: str) -> str:
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


def page(title: str, body: str, *, subtitle: str = "", nav: Sequence[tuple[str, str]] = NAV,
         user: str = "", notice: str = "") -> bytes:
    """A whole page. `body` and `user` are HTML the caller already escaped;
    everything else is escaped here. The stylesheet is a separate file
    because the Content-Security-Policy forbids inline styles."""
    links = "".join(f'<a href="{esc(path)}">{esc(label)}</a>' for path, label in nav)
    nav_html = f"<nav>{links}</nav>" if links else ""
    sub = f'<p class="sub">{esc(subtitle)}</p>' if subtitle else ""
    note = f'<p class="notice">{esc(notice)}</p>' if notice else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)} — {esc(DATA_CENTER_NAME)}</title>
<link rel="stylesheet" href="{STYLESHEET}"></head>
<body><header><div class="bar">
<a class="brand" href="/">{esc(DATA_CENTER_NAME)}</a>{nav_html}{user}
</div></header>
<main><h1>{esc(title)}</h1>{sub}{note}{body}</main>
<footer>{esc(DATA_CENTER_NAME)} v{esc(VERSION)} · the data is read-only here · stdlib only</footer>
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


def hidden(name: str, value: Any) -> str:
    return f'<input type="hidden" name="{esc(name)}" value="{esc(value)}">'


def input_field(label: str, name: str, *, kind: str = "text", value: str = "", required: bool = True,
          autocomplete: str = "", hint: str = "", extra: str = "") -> str:
    """One labelled input. Passwords are never given a value back."""
    if kind == "password":
        value = ""
    attributes = [f'name="{esc(name)}"']
    if autocomplete:
        attributes.append(f'autocomplete="{esc(autocomplete)}"')
    if required:
        attributes.append("required")
    if extra:
        attributes.append(extra)
    hint_html = f' <span class="hint">{esc(hint)}</span>' if hint else ""
    if kind == "textarea":
        control = f'<textarea {" ".join(attributes)}>{esc(value)}</textarea>'
    else:
        control = f'<input type="{esc(kind)}" value="{esc(value)}" {" ".join(attributes)}>'
    return f"<label>{esc(label)}{hint_html}{control}</label>"


def error_box(message: str) -> str:
    return f'<p class="error" role="alert">{esc(message)}</p>' if message else ""


# -- requests and responses ---------------------------------------------------

class HttpError(Exception):
    """Stop and answer with this status and a short, safe message."""

    def __init__(self, status: int, message: str, *, title: str = "",
                 retry_after: int | None = None) -> None:
        super().__init__(message)
        self.status = int(status)
        self.message = message
        self.title = title or HTTPStatus(self.status).phrase
        self.retry_after = retry_after


class NotFound(Exception):
    """A page that does not correspond to anything stored."""


class Form:
    """A parsed application/x-www-form-urlencoded body."""

    def __init__(self, fields: Mapping[str, list[str]] | None = None) -> None:
        self._fields = dict(fields or {})

    @classmethod
    def parse(cls, body: bytes) -> "Form":
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            raise HttpError(HTTPStatus.BAD_REQUEST, "The form was not valid UTF-8.") from None
        try:
            fields = parse_qs(text, keep_blank_values=True, max_num_fields=MAX_FIELDS)
        except ValueError:
            raise HttpError(HTTPStatus.BAD_REQUEST, "The form has too many fields.") from None
        return cls(fields)

    def get(self, name: str) -> str:
        """The field's one value, or "". A repeated field is refused rather
        than guessed at: which copy a check reads must never be in doubt."""
        values = self._fields.get(name, [])
        if len(values) > 1:
            raise HttpError(HTTPStatus.BAD_REQUEST, "The form repeats a field.")
        return values[0] if values else ""

    def has(self, name: str) -> bool:
        return name in self._fields


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: Any  # email.message.Message: case-insensitive .get and .get_all
    ip: str
    cookies: dict[str, str] = field(default_factory=dict)
    form: Form = field(default_factory=Form)

    def query_value(self, name: str) -> str:
        values = self.query.get(name) or [""]
        return values[0]

    def header(self, name: str) -> str:
        return (self.headers.get(name) if self.headers is not None else None) or ""


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = "text/html; charset=utf-8"
    headers: list[tuple[str, str]] = field(default_factory=list)
    #: Only the stylesheet may be cached; every page is no-store.
    cacheable: bool = False


def redirect(location: str) -> Response:
    return Response(HTTPStatus.SEE_OTHER, b"", headers=[("Location", location)])


_COOKIE_VALUE = re.compile(r"^[A-Za-z0-9_-]{1,1024}$")


def parse_cookies(headers: Iterable[str]) -> dict[str, str]:
    """Cookie headers to a dict. The first value for a name wins, and only
    URL-safe token values are kept: every cookie this portal sets is one."""
    jar: dict[str, str] = {}
    for header in headers:
        for part in header.split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name and name not in jar and _COOKIE_VALUE.match(value):
                jar[name] = value
    return jar


_NEXT = re.compile(r"^/(?![/\\])[\x21-\x7e]{0,500}$")


def safe_next(value: str) -> str:
    """A path on this portal to go to after signing in, or "/". Anything that
    could leave the site (`//host`, `/\\host`, a scheme) falls back to "/"."""
    if isinstance(value, str) and _NEXT.match(value) and "\\" not in value:
        return value
    return "/"


def with_notice(location: str, key: str) -> str:
    if not key:
        return location
    return f"{location}{'&' if '?' in location else '?'}notice={quote(key)}"


def sign_in_location(path: str, query: Mapping[str, list[str]]) -> str:
    target = path + (f"?{urlencode(query, doseq=True)}" if query else "")
    target = safe_next(target)
    return "/sign-in" if target == "/" else f"/sign-in?next={quote(target, safe='')}"


# -- addresses ----------------------------------------------------------------

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def _address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        address = ipaddress.ip_address(text.strip().strip("[]"))
    except ValueError:
        return None
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def client_ip(peer: str, forwarded_for: str = "", *, trust_proxy: bool = False) -> str:
    """The address a request came from.

    X-Forwarded-For is honoured only with `trust_proxy` and only when the
    connection itself comes from this machine (a local reverse proxy). Its
    last entry is the one that proxy added; anything before it was written
    by the client and proves nothing.
    """
    address = _address(peer)
    if address is None:
        return peer
    if trust_proxy and address.is_loopback and forwarded_for:
        candidate = _address(forwarded_for.split(",")[-1])
        if candidate is not None:
            address = candidate
    return str(address)


def parse_networks(values: Iterable[str]) -> tuple[Network, ...]:
    networks = []
    for value in values:
        try:
            networks.append(ipaddress.ip_network(value.strip(), strict=False))
        except ValueError:
            raise ValueError(f"{value!r} is not a network such as 100.64.0.0/10") from None
    return tuple(networks)


def network_allowed(ip: str, networks: Sequence[Network]) -> bool:
    """True when no allowlist is set, or the address is inside one network."""
    if not networks:
        return True
    address = _address(ip)
    return address is not None and any(address in network for network in networks)


# -- settings -----------------------------------------------------------------

@dataclass(frozen=True)
class PortalSettings:
    """Everything a request needs to know about how the portal was started."""

    root: Path
    environ: Mapping[str, str] = field(default_factory=dict)
    mailer: Mailer | None = None
    #: Why sign-in is off, naming the missing settings; "" when mail works.
    mail_problem: str = ""
    google: GoogleConfig | None = None
    google_problem: str = ""
    google_http_post: HttpPost | None = None
    public_url: str = ""
    https_cookies: bool = False
    allowed_networks: tuple[Network, ...] = ()
    trust_proxy: bool = False
    host: str = "127.0.0.1"
    clock: Callable[[], datetime] | None = None
    password_iterations: int = PASSWORD_ITERATIONS
    #: One per server: it caches the connection page's results for a minute.
    connectivity: ConnectionChecker = field(default_factory=ConnectionChecker, compare=False, repr=False)

    @property
    def secure_cookies(self) -> bool:
        return self.https_cookies or self.public_url.startswith("https://")

    @property
    def public_host(self) -> str:
        return (urlsplit(self.public_url).hostname or "") if self.public_url else ""

    def now(self) -> datetime:
        return (self.clock or (lambda: datetime.now(timezone.utc)))()

    def origins_for(self, host_header: str) -> set[str]:
        """The origins a form may be posted from: this host as the browser
        named it, by either scheme, and the configured public URL."""
        origins = set()
        host = host_header.strip().lower()
        if host:
            origins.update({f"http://{host}", f"https://{host}"})
        if self.public_url:
            parts = urlsplit(self.public_url)
            origins.add(f"{parts.scheme}://{parts.netloc}".lower())
        return origins

    def host_allowed(self, host_header: str) -> bool:
        """With a public URL set, answer only to its name or a bare address.
        A page on another domain that re-points its DNS at this server (DNS
        rebinding) arrives with its own name in Host, and is refused."""
        if not self.public_url:
            return True
        name = urlsplit(f"//{host_header.strip()}").hostname or ""
        if not name or name == "localhost" or _address(name) is not None:
            return True
        return name == self.public_host


def security_headers(settings: PortalSettings, *, cacheable: bool = False) -> list[tuple[str, str]]:
    headers = [
        ("Content-Security-Policy", CONTENT_SECURITY_POLICY),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "same-origin"),
        ("X-Frame-Options", "DENY"),
        ("Cache-Control", "public, max-age=3600" if cacheable else "no-store"),
    ]
    if settings.secure_cookies:
        headers.append(("Strict-Transport-Security", "max-age=31536000"))
    return headers


def same_origin(request: Request, settings: PortalSettings) -> bool:
    """True when the browser says the form came from this portal. Browsers
    send Origin on every POST; Referer (kept same-origin by our
    Referrer-Policy) covers the few that do not. Neither present is a no."""
    allowed = settings.origins_for(request.header("Host"))
    origin = request.header("Origin").strip()
    if origin:
        return origin.lower().rstrip("/") in allowed
    referer = request.header("Referer").strip()
    if referer:
        parts = urlsplit(referer)
        return f"{parts.scheme}://{parts.netloc}".lower() in allowed
    return False


# -- the request context ------------------------------------------------------

class Context:
    """One request: who is asking, their session, and a store to ask about it."""

    def __init__(self, settings: PortalSettings, request: Request, store: AccountStore | None) -> None:
        self.settings = settings
        self.request = request
        self._store = store
        self.session: Session | None = None
        self.token: str | None = None
        self.set_cookies: list[str] = []
        self._center: DataCenter | None = None

    @property
    def store(self) -> AccountStore:
        if self._store is None:
            raise RuntimeError("this route was declared public and has no accounts store")
        return self._store

    @property
    def account(self) -> Account | None:
        return self.session.account if self.session is not None else None

    @property
    def full(self) -> bool:
        return self.session is not None and self.session.is_full

    @property
    def is_owner(self) -> bool:
        return self.full and self.store.is_owner(self.account)

    def today(self) -> date:
        return self.settings.now().astimezone(timezone.utc).date()

    def load_session(self) -> None:
        token = self.request.cookies.get(SESSION_COOKIE)
        if not token:
            return
        session = self.store.get_session(token)
        if session is None:
            self.set_cookies.append(clear_cookie(SESSION_COOKIE, secure=self.settings.secure_cookies))
            return
        self.session, self.token = session, token

    def start_session(self, new: NewSession) -> None:
        self.set_cookies.append(session_cookie(new, secure=self.settings.secure_cookies))

    def forget_session(self) -> None:
        """Clear the cookie; the caller has already ended the session."""
        self.session, self.token = None, None
        self.set_cookies.append(clear_cookie(SESSION_COOKIE, secure=self.settings.secure_cookies))

    def set_cookie(self, name: str, value: str, *, max_age: int | None = None) -> None:
        self.set_cookies.append(cookie_header(name, value, secure=self.settings.secure_cookies,
                                              max_age=max_age))

    def clear_cookie(self, name: str) -> None:
        self.set_cookies.append(clear_cookie(name, secure=self.settings.secure_cookies))

    def center(self) -> DataCenter:
        # One per request: SQLite connections belong to the thread that opened them.
        if self._center is None:
            self._center = DataCenter(root=self.settings.root)
        return self._center

    def close(self) -> None:
        if self._center is not None:
            self._center.close()
            self._center = None

    def google(self) -> GoogleSignIn | None:
        if self.settings.google is None:
            return None
        return GoogleSignIn(self.settings.google, self.store.secret,
                            http_post=self.settings.google_http_post)

    # -- forms ----------------------------------------------------------------

    def _csrf_cookie(self, *, create: bool) -> str:
        """The cookie this request's form token is tied to: the session's,
        or before sign-in a random pre-auth cookie (made here if missing)."""
        if self.token:
            return self.token
        preauth = self.request.cookies.get(PREAUTH_COOKIE, "")
        if not preauth and create:
            preauth = secrets.token_urlsafe(32)
            self.request.cookies[PREAUTH_COOKIE] = preauth
            self.set_cookie(PREAUTH_COOKIE, preauth, max_age=24 * 3600)
        return preauth

    def csrf_field(self) -> str:
        return hidden("csrf", self.store.csrf_token(self._csrf_cookie(create=True)))

    def csrf_ok(self) -> bool:
        cookie = self._csrf_cookie(create=False)
        return bool(cookie) and self.store.check_csrf(cookie, self.request.form.get("csrf"))

    def form_fields(self) -> str:
        """Hidden fields every signed-in form carries: the CSRF token, and the
        caller's own account number, which the guard checks has not been
        changed to someone else's."""
        own = hidden("account", self.account.id) if self.full and self.account else ""
        return self.csrf_field() + own

    # -- pages ----------------------------------------------------------------

    def chrome(self) -> tuple[Sequence[tuple[str, str]], str]:
        """The navigation and the signed-in corner for this caller."""
        if not self.full or self.account is None:
            return (), ""
        account = self.account
        sign_out = (f'<form method="post" action="/sign-out" class="inline">{self.form_fields()}'
                    '<button type="submit" class="link">Sign out</button></form>')
        who = f'<div class="who"><span>{esc(account.display_name)}</span>{sign_out}</div>'
        if not account.can_use_portal:
            return (), who
        nav = list(NAV) + list(MEMBER_NAV) + [("/account", "Account")]
        if self.is_owner:
            nav.append(("/owner", "Owner center"))
        return nav, who

    def page(self, title: str, body: str, *, subtitle: str = "", status: int = HTTPStatus.OK,
             bare: bool = False) -> Response:
        nav, user = ((), "") if bare else self.chrome()
        notice = NOTICES.get(self.request.query_value("notice"), "")
        return Response(status, page(title, body, subtitle=subtitle, nav=nav, user=user, notice=notice))


# -- routes and the guard -----------------------------------------------------

PUBLIC = "public"          # anyone, no accounts store needed (health check, stylesheet)
ANYONE = "anyone"          # anyone; the session is read for the page chrome
SIGNED_OUT = "signed-out"  # sign-in pages: a full session is sent home instead
PARTIAL = "partial"        # the second step: a session past the first factor only
SIGNED_IN = "signed-in"    # a full session, whatever the account's status
MEMBER = "member"          # a full session of an approved account, or the owner's
OWNER = "owner"            # the owner's full session
ACCESS_LEVELS = (PUBLIC, ANYONE, SIGNED_OUT, PARTIAL, SIGNED_IN, MEMBER, OWNER)

Handler = Callable[[Context, "re.Match[str]"], Response]

#: Paths any owner route might take. A route that is not owner-only must not
#: match one of them, whatever its pattern looks like.
_OWNER_PROBES = ("/owner", "/owner/requests", "/owner/accounts/1/block", "/owner/announcements")


@dataclass(frozen=True)
class Route:
    method: str
    pattern: str
    access: str
    handler: Handler = field(compare=False)
    regex: "re.Pattern[str]" = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.method not in ("GET", "POST"):
            raise ValueError(f"{self.pattern}: the portal serves GET and POST only")
        if self.access not in ACCESS_LEVELS:
            raise ValueError(f"{self.pattern}: unknown access level {self.access!r}")
        regex = re.compile(self.pattern)
        owner_path = self.pattern.startswith("/owner") or any(
            regex.fullmatch(probe) for probe in _OWNER_PROBES)
        if owner_path and self.access != OWNER:
            raise ValueError(f"{self.pattern}: every /owner route must be owner-only")
        if self.access == OWNER and not self.pattern.startswith("/owner"):
            raise ValueError(f"{self.pattern}: owner-only routes live under /owner")
        if "account" in regex.groupindex and self.access not in (SIGNED_IN, MEMBER):
            raise ValueError(f"{self.pattern}: a route naming an account needs a signed-in caller")
        object.__setattr__(self, "regex", regex)


def blocked_response(ctx: Context, review_after: date | None) -> Response:
    """All a blocked person is shown: that they are blocked, and the date."""
    when = review_after.isoformat() if review_after else "a date the owner sets"
    body = (f'<div class="doc"><p><strong>This account is blocked.</strong></p>'
            f"<p>Review date: {esc(when)}</p></div>")
    return ctx.page("This account is blocked", body, status=HTTPStatus.FORBIDDEN, bare=True)


def _deliberate(request: Request) -> bool:
    """Did the person mean to ask for this? A navigation another site
    started (Sec-Fetch-Site cross-site or same-site) does not count against
    them; a typed URL ("none"), our own pages, or a client that sends no such
    header (a script) does."""
    return request.header("Sec-Fetch-Site").strip().lower() in ("", "same-origin", "none")


def refuse(ctx: Context, *, reason: str, immediate: bool) -> Response:
    """Refuse a signed-in non-owner, recording a strike (or blocking at once)."""
    account = ctx.account
    assert account is not None
    if not immediate and not _deliberate(ctx.request):
        return ctx.page("Not available", '<p class="empty">This page is not available.</p>',
                        status=HTTPStatus.FORBIDDEN)
    outcome = ctx.store.record_strike(account.id, reason=reason, path=ctx.request.path,
                                      ip=ctx.request.ip, immediate=immediate)
    if outcome.blocked:
        ctx.forget_session()
        return blocked_response(ctx, outcome.review_after)
    body = ('<p class="error">This is not open to your account. The request has been recorded, '
            "and repeated attempts block the account.</p>"
            '<p><a href="/">Back to the overview</a></p>')
    return ctx.page("Not allowed", body, status=HTTPStatus.FORBIDDEN)


def throttle_probe(ctx: Context) -> None:
    """Count an anonymous request for something that needs sign-in (or for
    nothing at all) against its address; too many, and it is refused."""
    if not ctx.store.allow(f"probe:{ctx.request.ip}", PROBE_LIMIT, PROBE_WINDOW):
        raise HttpError(HTTPStatus.TOO_MANY_REQUESTS, "Too many requests. Wait a few minutes.",
                        retry_after=int(PROBE_WINDOW.total_seconds()))


def _refuse_anonymous(ctx: Context, route: Route) -> Response:
    request = ctx.request
    throttle_probe(ctx)
    if request.method == "POST":
        if route.access == OWNER:
            ctx.store.log_security_event("owner_probe", ip=request.ip,
                                         detail=f"POST {request.path} without a full session")
        raise HttpError(HTTPStatus.FORBIDDEN, "Sign in first.")
    if ctx.session is not None:  # past the first factor only
        return redirect("/sign-in/code")
    return redirect(sign_in_location(request.path, request.query))


def authorize(ctx: Context, route: Route, match: "re.Match[str]") -> Response | None:
    """The one gate in front of every handler. None means go ahead;
    anything else is the answer to send instead."""
    access = route.access
    if access == PUBLIC:
        return None
    session, request = ctx.session, ctx.request
    account = ctx.account if ctx.full else None
    is_post = request.method == "POST"
    owner = account is not None and ctx.store.is_owner(account)

    if account is not None:
        if account.status == "blocked":  # blocking ends sessions; this is the backstop
            ctx.store.end_session(ctx.token)
            ctx.forget_session()
            return blocked_response(ctx, account.review_after)
        # Every signed-in form carries the caller's own account number. The
        # server never acts on it; a different number means someone edited it.
        if is_post and not owner and request.form.has("account") \
                and request.form.get("account") != str(account.id):
            return refuse(ctx, reason="a form changed to act on another account", immediate=True)

    if access == ANYONE:
        return None
    if access == SIGNED_OUT:
        return redirect("/") if account is not None else None
    if access == PARTIAL:
        if session is None:
            return redirect("/sign-in")
        return redirect("/") if session.is_full else None

    if account is None:
        return _refuse_anonymous(ctx, route)

    if access == OWNER:
        if owner:
            return None
        return refuse(ctx, reason="owner action" if is_post else "owner page", immediate=is_post)

    named = match.groupdict().get("account")
    if named is not None and int(named) != account.id:
        if owner:
            raise NotFound("no such page")
        return refuse(ctx, reason="another person's account", immediate=is_post)
    if named is not None and is_post and not request.form.has("account"):
        raise HttpError(HTTPStatus.BAD_REQUEST, "The form is incomplete. Reload the page and try again.")

    if access == MEMBER and not account.can_use_portal:
        return redirect("/status")
    return None
