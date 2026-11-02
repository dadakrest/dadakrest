"""Accounts, sign-in and the rules around them, for the live portal.

The store is its own SQLite file, `<root>/portal/accounts.db`, next to the
seven data databases rather than one of them: the data center still has
exactly seven databases, and backing up or inspecting the data never touches
anyone's sign-in records. The directory is 0700 and the file 0600.

What it never holds in plain text: passwords (PBKDF2-HMAC-SHA256), emailed
codes and backup codes (HMAC-SHA256 under the server secret), session tokens
(SHA-256) and anything from a Google sign-in flow (only a hash of the flow
key; the state, nonce and PKCE verifier are derived from it, see
`google_signin`). Nothing here returns a password hash either: an `Account`
says whether a password is set, never what it is. Nobody, the owner
included, can read a password or a code; the owner can reset, revoke, block
and approve.

The server secret is generated once into `<root>/portal/secret.key` (0600),
or taken from SDDU13_PORTAL_SECRET (at least 32 characters). Losing it voids
every outstanding code, backup code and Google flow, and nothing else.

The owner is the account whose email is SDDU13_OWNER_EMAIL or, when that is
not set, the email stored with `set_owner_email` (the `portal-owner` CLI
command). The owner is approved automatically, still signs in with both
steps, and can never be blocked.

Sign-in is two steps, always:

    1. a first factor: `authenticate_password`, or a Google identity passed
       to `account_for_google` / `create_google_account`;
       then `begin_sign_in` gives a 'partial' session (15 minutes);
    2. `send_code` emails a 6-digit code, and `finish_sign_in` checks it, or
       a backup code, and swaps the partial session for a 'full' one with a
       new token. Only a full session of an approved account (or the
       owner's) may see data; see `Account.can_use_portal`.

Public API. Module functions:

    hash_password(password, *, iterations=PASSWORD_ITERATIONS) -> str
    verify_password(password, encoded) -> bool       constant-time
    needs_rehash(encoded, iterations=PASSWORD_ITERATIONS) -> bool
    check_password_strength(password) -> None        InvalidInput if too short/long
    normalise_email(email) -> str                    InvalidInput if not an address
    add_months(day, months) -> date                  same day, or the month's last
    load_secret(root, environ=None) -> bytes         creates secret.key once
    cookie_header(name, value, *, secure, max_age=None) -> str
    session_cookie(new_session, *, secure) -> str    Set-Cookie value for a session
    clear_cookie(name, *, secure) -> str             Set-Cookie value that deletes it

AccountStore(root, *, mailer=None, environ=None, clock=None,
             password_iterations=PASSWORD_ITERATIONS, mirror_audit=True):

  lifecycle
    provision() -> None                     create the file, tables and secret
    close() -> None
    healthy() -> bool                       integrity check, for a status page
    backup_to(destination) -> Path          SQLite online backup
    purge_expired() -> dict[str, int]       drop expired sessions, codes, flows

  owner
    owner_email() -> str | None
    owner_email_source() -> str | None      "environment", "store" or None
    set_owner_email(email) -> None
    is_owner(account) -> bool

  accounts
    get_account(account_id) -> Account | None
    find_account(email) -> Account | None
    list_accounts(status=None) -> list[Account]
    pending_requests() -> list[Account]     pending and email confirmed
    blocked_accounts() -> list[Account]
    owner_summary() -> dict                 counts for the owner center
    register_password_account(email, password, *, display_name, company="",
                              reason="", ip="") -> Account
    account_for_google(sub, email, *, ip="") -> Account | None
    create_google_account(sub, email, *, display_name, company="", reason="",
                          ip="") -> Account
    update_profile(account_id, *, display_name) -> Account
    request_again(account_id, reason="") -> Account   'unblocked' -> 'pending'

  owner decisions
    approve(account_id) -> Account
    decline(account_id) -> Account
    remove(account_id) -> Account           'approved' -> 'revoked', signs out
    block(account_id, reason, *, ip="", automatic=False) -> date
                                            returns the review date
    unblock(account_id) -> Account          TooEarly before the review date
    extend_block(account_id) -> date        TooEarly before the review date
    send_reset_code(account_id) -> None     email a reset code to them

  sign-in
    note_sign_in_attempt(*, ip, email=None) -> None    Throttled when over
    authenticate_password(email, password, *, ip="") -> Account | None
    begin_sign_in(account_id, *, method, ip="", user_agent="") -> NewSession
    finish_sign_in(token, code, *, purpose="sign_in") -> SecondStep
    get_session(token) -> Session | None    checks expiry, refreshes last_seen
    end_session(token) -> None
    end_all_sessions(account_id, *, keep_token=None) -> int
    list_sessions(account_id) -> list[dict] no tokens
    csrf_token(cookie_value) -> str         tied to that cookie (session or pre-auth)
    check_csrf(cookie_value, submitted) -> bool

  codes
    issue_code(account_id, purpose) -> str  the caller must email it; prefer send_code
    send_code(account_id, purpose, *, background=False) -> None
    notify_existing_account(email) -> None  background notice instead of "taken"
    wait_for_mail(timeout=30.0) -> None     join background deliveries
    verify_code(account_id, purpose, code) -> CodeCheck
    generate_backup_codes(account_id) -> list[str]     voids the old set
    send_backup_codes(account_id) -> None   generate, email, then store
    ensure_backup_codes(account_id) -> bool sends a set when none are left
    use_backup_code(account_id, code) -> CodeCheck
    backup_codes_remaining(account_id) -> int

  passwords
    change_password(account_id, current, new, *, keep_token=None) -> bool
    request_password_reset(email, *, ip="") -> None    says nothing either way;
                                            mails in the background, so it takes
                                            as long whether the address exists
    reset_password(email, code, new_password, *, ip="") -> CodeCheck

  strikes, throttling, Google flows
    record_strike(account_id, *, reason, path="", ip="", immediate=False)
        -> StrikeOutcome                    3 in 24 hours, or one immediate, blocks
    account_strikes(account_id) -> list[dict]
    allow(key, limit, window) -> bool       fixed-window throttle for anything
    begin_oauth_flow() -> str               a single-use key for the pre-auth cookie
    consume_oauth_flow(flow_key) -> bool

  announcements and events
    post_announcement(title, body) -> int
    delete_announcement(announcement_id) -> bool
    list_announcements(limit=50) -> list[dict]
    log_security_event(kind, *, account_id=None, ip="", detail="") -> None
    recent_security_events(limit=50, account_id=None) -> list[dict]

Records (frozen dataclasses; none carries a secret in its repr):

    Account        id, email, display_name, role, status, email_verified,
                   has_password, google_linked, company, request_reason,
                   created_at, decided_at, blocked_at, review_after (date),
                   block_reason; .is_owner, .can_use_portal, .review_due(today)
    NewSession     token, stage, expires_at, csrf_token (token and CSRF hidden from repr)
    Session        account, stage, method, created_at, last_seen, expires_at,
                   ip, user_agent; .is_full
    CodeCheck      ok, reason ("ok", "wrong", "expired", "locked", "none",
                   "throttled"); truthy only when ok
    SecondStep     check, session (None unless the second step passed)
    StrikeOutcome  blocked, strikes (in the last 24 hours), review_after

Errors, all AccountError, with messages safe to show: InvalidInput,
EmailTaken, UnknownAccount, WrongState, OwnerProtected, ConfigError,
TooEarly (.review_after) and Throttled (.retry_after, in seconds).

A store holds one SQLite connection, which belongs to the thread that opened
it: the threaded portal opens one per request, as it does with DataCenter.
Multi-step changes run in `BEGIN IMMEDIATE` transactions, so two requests
cannot both spend the same code.
"""

from __future__ import annotations

import base64
import calendar
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .core import DataCenter
from .mailer import (
    MailError,
    MailNotConfigured,
    Mailer,
    backup_codes_email,
    code_email,
    existing_account_email,
    owner_alert_email,
)

# -- policy -------------------------------------------------------------------------

PASSWORD_ITERATIONS = 600_000
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 1024
SALT_BYTES = 16
_HASH_SCHEME = "pbkdf2_sha256"

CODE_PURPOSES = ("sign_in", "verify_email", "reset_password")
CODE_LIFETIME = timedelta(minutes=10)
CODE_ATTEMPTS = 5
CODES_PER_HOUR = 5

BACKUP_CODE_COUNT = 10
#: No 0/O, 1/I/L: a code read off paper should not be ambiguous.
BACKUP_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
#: Backup codes have no per-code counter (a wrong guess matches no row), so
#: guesses are limited per account instead.
BACKUP_ATTEMPTS = 5
BACKUP_WINDOW = timedelta(minutes=15)
BACKUP_SETS_PER_HOUR = 3

SESSION_COOKIE = "sddu13_session"
PREAUTH_COOKIE = "sddu13_preauth"
SESSION_IDLE = timedelta(hours=2)
SESSION_ABSOLUTE = timedelta(hours=12)
#: Long enough to read an emailed code (10 minutes) and type it.
PARTIAL_LIFETIME = timedelta(minutes=15)

SIGN_IN_LIMIT = 10
SIGN_IN_WINDOW = timedelta(minutes=15)

BLOCK_MONTHS = 4
STRIKE_LIMIT = 3
STRIKE_WINDOW = timedelta(hours=24)

FLOW_LIFETIME = timedelta(minutes=10)
#: An address nobody confirmed is released after this, so typing someone
#: else's email into the request form cannot hold it.
UNVERIFIED_LIFETIME = timedelta(hours=24)

ROLES = ("owner", "member")
STATUSES = ("pending", "approved", "declined", "revoked", "blocked", "unblocked")

#: Security events also written to the audit database's trail. Only the
#: account id goes there: the audit page is readable by every member.
MIRRORED_EVENTS = frozenset({
    "account_requested", "approved", "declined", "removed", "block", "auto_block",
    "unblock", "block_extended", "owner_changed", "password_reset",
})

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    email          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    display_name   TEXT NOT NULL DEFAULT '',
    password_hash  TEXT,
    google_sub     TEXT UNIQUE,
    role           TEXT NOT NULL DEFAULT 'member' CHECK (role IN ('owner', 'member')),
    status         TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
                   ('pending', 'approved', 'declined', 'revoked', 'blocked', 'unblocked')),
    email_verified INTEGER NOT NULL DEFAULT 0,
    request_reason TEXT NOT NULL DEFAULT '',
    company        TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    decided_at     TEXT,
    blocked_at     TEXT,
    review_after   TEXT,
    block_reason   TEXT
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    stage      TEXT NOT NULL CHECK (stage IN ('partial', 'full')),
    method     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    ip         TEXT NOT NULL DEFAULT '',
    user_agent TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions(account_id);
CREATE TABLE IF NOT EXISTS email_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    purpose    TEXT NOT NULL CHECK (purpose IN ('sign_in', 'verify_email', 'reset_password')),
    code_hash  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    used_at    TEXT,
    voided_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_email_codes_account ON email_codes(account_id, purpose);
CREATE TABLE IF NOT EXISTS backup_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    code_hash  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    used_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_backup_codes_account ON backup_codes(account_id);
CREATE TABLE IF NOT EXISTS strikes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    ip         TEXT NOT NULL DEFAULT '',
    at         TEXT NOT NULL,
    reason     TEXT NOT NULL,
    path       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_strikes_account ON strikes(account_id, at);
CREATE TABLE IF NOT EXISTS throttle (
    key          TEXT PRIMARY KEY,
    window_start TEXT NOT NULL,
    count        INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_flows (
    key_hash   TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at    TEXT
);
CREATE TABLE IF NOT EXISTS announcements (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS security_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    ip         TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_security_events_at ON security_events(at);
"""

TABLES = ("accounts", "announcements", "backup_codes", "email_codes", "oauth_flows",
          "security_events", "sessions", "settings", "strikes", "throttle")


# -- errors -------------------------------------------------------------------------

class AccountError(Exception):
    """Base for everything the store refuses. Messages are safe to show."""


class InvalidInput(AccountError, ValueError):
    """An email, password or other field that cannot be accepted."""


class EmailTaken(AccountError):
    """An account already uses this email address."""


class UnknownAccount(AccountError, LookupError):
    """No account has that id."""


class WrongState(AccountError):
    """The account's status does not allow this change."""


class OwnerProtected(AccountError):
    """The change would lock the owner out, which is never allowed."""


class ConfigError(AccountError):
    """The server secret or owner email setting is unusable."""


class TooEarly(AccountError):
    """A block's review date has not come yet."""

    def __init__(self, review_after: date) -> None:
        self.review_after = review_after
        super().__init__(f"You can decide on {review_after.isoformat()}.")


class Throttled(AccountError):
    """Too many attempts in the current window."""

    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, int(retry_after))
        minutes = -(-self.retry_after // 60)
        unit = "minute" if minutes == 1 else "minutes"
        super().__init__(f"Too many attempts. Try again in {minutes} {unit}.")


# -- time ---------------------------------------------------------------------------

_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_TIME_FORMAT)


def _parse(text: str) -> datetime:
    return datetime.strptime(text, _TIME_FORMAT).replace(tzinfo=timezone.utc)


def add_months(day: date, months: int) -> date:
    """The same day of the month `months` later, or that month's last day
    when it is shorter: Oct 31 + 4 months is the end of February."""
    index = day.month - 1 + months
    year, month = day.year + index // 12, index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


# -- passwords ----------------------------------------------------------------------

def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _password_bytes(password: str) -> bytes:
    # NFKC, so the same password typed on two keyboards hashes the same.
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def check_password_strength(password: str) -> None:
    if not isinstance(password, str):
        raise InvalidInput("Enter a password.")
    length = len(unicodedata.normalize("NFKC", password))
    if length < PASSWORD_MIN_LENGTH:
        raise InvalidInput(f"Use a password of at least {PASSWORD_MIN_LENGTH} characters.")
    if length > PASSWORD_MAX_LENGTH:
        raise InvalidInput(f"Use a password of at most {PASSWORD_MAX_LENGTH} characters.")


def hash_password(password: str, *, iterations: int = PASSWORD_ITERATIONS) -> str:
    """`pbkdf2_sha256$<iterations>$<salt_b64>$<hash_b64>` with a fresh salt."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", _password_bytes(password), salt, iterations)
    return f"{_HASH_SCHEME}${iterations}${_b64(salt)}${_b64(digest)}"


def _parse_hash(encoded: str | None) -> tuple[int, bytes, bytes] | None:
    try:
        scheme, iterations, salt, digest = (encoded or "").split("$")
        parsed = int(iterations), base64.b64decode(salt, validate=True), \
            base64.b64decode(digest, validate=True)
    except (ValueError, TypeError):
        return None
    # A short stored digest must not turn into an easy match.
    if scheme != _HASH_SCHEME or parsed[0] < 1 or len(parsed[1]) < SALT_BYTES or len(parsed[2]) != 32:
        return None
    return parsed


def verify_password(password: str, encoded: str | None) -> bool:
    parsed = _parse_hash(encoded)
    if parsed is None or not isinstance(password, str):
        return False
    iterations, salt, expected = parsed
    actual = hashlib.pbkdf2_hmac("sha256", _password_bytes(password), salt, iterations)
    return hmac.compare_digest(actual, expected)


def needs_rehash(encoded: str | None, iterations: int = PASSWORD_ITERATIONS) -> bool:
    parsed = _parse_hash(encoded)
    return parsed is None or parsed[0] < iterations


_DUMMY_HASHES: dict[int, str] = {}


def _dummy_hash(iterations: int) -> str:
    """A hash to check against when there is no account, so an unknown email
    takes as long to refuse as a wrong password."""
    if iterations not in _DUMMY_HASHES:
        _DUMMY_HASHES[iterations] = hash_password(secrets.token_urlsafe(16), iterations=iterations)
    return _DUMMY_HASHES[iterations]


# -- text ---------------------------------------------------------------------------

_EMAIL = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")
#: Control characters, and the bidirectional overrides that can make a name
#: in the owner's list display as something else.
_CONTROL = re.compile(r"[\x00-\x1f\x7f‪-‮⁦-⁩]")
_CONTROL_KEEP_NEWLINES = re.compile(r"[\x00-\x09\x0b-\x1f\x7f‪-‮⁦-⁩]")


def normalise_email(email: str) -> str:
    """Lower-cased and checked. Commas, quotes and angle brackets are refused,
    so an address can never turn into two recipients in a mail header."""
    value = (email or "").strip().lower() if isinstance(email, str) else ""
    if len(value) > 254 or not _EMAIL.match(value):
        raise InvalidInput("Enter a valid email address.")
    return value


def _line(value: Any, limit: int) -> str:
    return _CONTROL.sub(" ", "" if value is None else str(value)).strip()[:limit]


def _text(value: Any, limit: int) -> str:
    text = ("" if value is None else str(value)).replace("\r\n", "\n").replace("\r", "\n")
    return _CONTROL_KEEP_NEWLINES.sub(" ", text).strip()[:limit]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# -- secret and cookies ---------------------------------------------------------------

def load_secret(root: Path | str, environ: Mapping[str, str] | None = None) -> bytes:
    """The server secret: SDDU13_PORTAL_SECRET, or `<root>/portal/secret.key`,
    created with 0600 permissions the first time it is needed."""
    env = os.environ if environ is None else environ
    configured = env.get("SDDU13_PORTAL_SECRET", "")
    if configured:
        if len(configured) < 32:
            raise ConfigError("SDDU13_PORTAL_SECRET must be at least 32 characters.")
        return configured.encode("utf-8")

    path = Path(root) / "portal" / "secret.key"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        # Written aside and linked into place: link fails if another process
        # got there first, and nobody ever reads a half-written key.
        scratch = path.with_name(f".secret.{secrets.token_hex(8)}")
        descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(secrets.token_hex(32) + "\n")
        try:
            os.link(scratch, path)
        except FileExistsError:
            pass
        finally:
            scratch.unlink()
    if path.stat().st_mode & 0o077:
        os.chmod(path, 0o600)
    try:
        key = bytes.fromhex(path.read_text(encoding="ascii").strip())
    except (ValueError, UnicodeDecodeError):
        raise ConfigError(f"{path} is not a portal secret; move it aside to make a new one") from None
    if len(key) < 32:
        raise ConfigError(f"{path} is too short to be a portal secret")
    return key


_COOKIE_VALUE = re.compile(r"^[A-Za-z0-9_-]*$")


def cookie_header(name: str, value: str, *, secure: bool, max_age: int | None = None) -> str:
    """A Set-Cookie value: HttpOnly, SameSite=Lax, Path=/, Secure over HTTPS."""
    if not _COOKIE_VALUE.match(name) or not _COOKIE_VALUE.match(value):
        raise ValueError("cookie names and values here are URL-safe tokens")
    parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
    if max_age is not None:
        parts.append(f"Max-Age={int(max_age)}")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def session_cookie(new_session: "NewSession", *, secure: bool) -> str:
    life = PARTIAL_LIFETIME if new_session.stage == "partial" else SESSION_ABSOLUTE
    return cookie_header(SESSION_COOKIE, new_session.token, secure=secure,
                         max_age=int(life.total_seconds()))


def clear_cookie(name: str, *, secure: bool) -> str:
    return cookie_header(name, "", secure=secure, max_age=0)


# -- records ------------------------------------------------------------------------

@dataclass(frozen=True)
class Account:
    """An account as the portal may see it: no password hash, no Google subject."""

    id: int
    email: str
    display_name: str
    role: str
    status: str
    email_verified: bool
    has_password: bool
    google_linked: bool
    company: str
    request_reason: str
    created_at: str
    decided_at: str | None
    blocked_at: str | None
    review_after: date | None
    block_reason: str | None

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def can_use_portal(self) -> bool:
        """May see the data pages (with a full session)."""
        return self.is_owner or self.status == "approved"

    def review_due(self, today: date) -> bool:
        return self.status == "blocked" and self.review_after is not None and today >= self.review_after


def _account(row: sqlite3.Row) -> Account:
    return Account(
        id=int(row["id"]),
        email=row["email"],
        display_name=row["display_name"],
        role=row["role"],
        status=row["status"],
        email_verified=bool(row["email_verified"]),
        has_password=row["password_hash"] is not None,
        google_linked=row["google_sub"] is not None,
        company=row["company"],
        request_reason=row["request_reason"],
        created_at=row["created_at"],
        decided_at=row["decided_at"],
        blocked_at=row["blocked_at"],
        review_after=date.fromisoformat(row["review_after"]) if row["review_after"] else None,
        block_reason=row["block_reason"],
    )


@dataclass(frozen=True)
class NewSession:
    """A session just created. The token goes into the cookie and nowhere else."""

    token: str = field(repr=False)
    stage: str
    expires_at: str
    csrf_token: str = field(repr=False)


@dataclass(frozen=True)
class Session:
    account: Account
    stage: str
    method: str
    created_at: str
    last_seen: str
    expires_at: str
    ip: str
    user_agent: str

    @property
    def is_full(self) -> bool:
        return self.stage == "full"


@dataclass(frozen=True)
class CodeCheck:
    """The outcome of checking a code. True only when it was accepted.

    reason: "ok", "wrong", "expired", "locked" (too many attempts: ask for a
    new code), "none" (no code waiting) or "throttled" (backup codes).
    """

    ok: bool
    reason: str

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class SecondStep:
    check: CodeCheck
    session: NewSession | None

    def __bool__(self) -> bool:
        return self.session is not None


@dataclass(frozen=True)
class StrikeOutcome:
    blocked: bool
    strikes: int
    review_after: date | None


# -- the store ------------------------------------------------------------------------

class AccountStore:
    """The accounts database and every rule that does not need HTTP."""

    def __init__(
        self,
        root: Path | str,
        *,
        mailer: Mailer | None = None,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
        password_iterations: int = PASSWORD_ITERATIONS,
        mirror_audit: bool = True,
    ) -> None:
        self.root = Path(root)
        self.path = self.root / "portal" / "accounts.db"
        self.mailer = mailer
        self.environ: Mapping[str, str] = os.environ if environ is None else environ
        self.clock = clock or _utc_now
        self.password_iterations = password_iterations
        self.mirror_audit = mirror_audit
        self._connection: sqlite3.Connection | None = None
        self._secret: bytes | None = None
        self._in_transaction = False
        self._deliveries: list[threading.Thread] = []

    # -- lifecycle ------------------------------------------------------------

    def provision(self) -> None:
        """Create the store, its tables and the server secret. Idempotent."""
        directory = self.path.parent
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        if not self.path.exists():
            os.close(os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600))
        connection = self._open(create=True)
        connection.executescript(SCHEMA)
        os.chmod(self.path, 0o600)
        self._secret = load_secret(self.root, self.environ)
        self._sync_owner()

    def _open(self, *, create: bool) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        if not create and not self.path.exists():
            raise FileNotFoundError(f"the accounts store is not provisioned at {self.path}")
        connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
        except BaseException:
            connection.close()
            raise
        self._connection = connection
        return connection

    @property
    def connection(self) -> sqlite3.Connection:
        return self._open(create=False)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "AccountStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        """BEGIN IMMEDIATE: take the write lock up front, so a read-then-write
        (spend a code, count strikes) cannot interleave with another request.
        Nesting joins the outer transaction."""
        if self._in_transaction:
            yield
            return
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        self._in_transaction = True
        try:
            yield
            connection.execute("COMMIT")
        except BaseException:
            # SQLite rolls back by itself after some errors; a second ROLLBACK
            # would raise and hide the error that mattered.
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            self._in_transaction = False

    def _rows(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def _row(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def _run(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def _now(self) -> datetime:
        # Whole seconds, as stored, so a value read back compares equal.
        return self.clock().astimezone(timezone.utc).replace(microsecond=0)

    def healthy(self) -> bool:
        if not self.path.exists():
            return False
        try:
            row = self._row("PRAGMA integrity_check")
            tables = {r["name"] for r in self._rows("SELECT name FROM sqlite_master WHERE type = 'table'")}
        except sqlite3.DatabaseError:
            return False
        return bool(row) and row[0] == "ok" and set(TABLES).issubset(tables)

    def backup_to(self, destination: Path | str) -> Path:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self.connection.backup(target)
        finally:
            target.close()
        os.chmod(destination, 0o600)
        return destination

    def purge_expired(self) -> dict[str, int]:
        now = self._now()
        removed: dict[str, int] = {}
        with self._transaction():
            removed["sessions"] = self._run(
                "DELETE FROM sessions WHERE expires_at <= ? OR last_seen <= ?",
                (_iso(now), _iso(now - SESSION_IDLE))).rowcount
            # Kept a day, not ten minutes: the hourly limit counts them.
            removed["codes"] = self._run(
                "DELETE FROM email_codes WHERE created_at < ?", (_iso(now - timedelta(days=1)),)).rowcount
            removed["flows"] = self._run(
                "DELETE FROM oauth_flows WHERE expires_at <= ?", (_iso(now),)).rowcount
            removed["throttle"] = self._run(
                "DELETE FROM throttle WHERE window_start < ?", (_iso(now - timedelta(days=1)),)).rowcount
            removed["unconfirmed_accounts"] = self._run(
                "DELETE FROM accounts WHERE email_verified = 0 AND status IN ('pending', 'approved') "
                "AND created_at < ?", (_iso(now - UNVERIFIED_LIFETIME),)).rowcount
        return removed

    # -- secret -------------------------------------------------------------

    @property
    def secret(self) -> bytes:
        if self._secret is None:
            self._secret = load_secret(self.root, self.environ)
        return self._secret

    def _hmac(self, label: str, value: str) -> str:
        return hmac.new(self.secret, f"{label}:{value}".encode("utf-8"), hashlib.sha256).hexdigest()

    def csrf_token(self, cookie_value: str) -> str:
        """The form token for whoever holds this cookie. Derived, not stored:
        it is an HMAC of the cookie's hash, so it is tied to that one cookie
        and reveals nothing about it."""
        digest = hmac.new(self.secret, f"csrf:{_sha256(cookie_value or '')}".encode("ascii"),
                          hashlib.sha256).digest()
        return _b64url(digest)

    def check_csrf(self, cookie_value: str, submitted: str) -> bool:
        if not cookie_value or not submitted or not isinstance(submitted, str):
            return False
        return hmac.compare_digest(self.csrf_token(cookie_value).encode("ascii"),
                                   submitted.encode("utf-8"))

    # -- owner --------------------------------------------------------------

    def owner_email(self) -> str | None:
        configured = self.environ.get("SDDU13_OWNER_EMAIL", "").strip()
        if configured:
            try:
                return normalise_email(configured)
            except InvalidInput:
                raise ConfigError("SDDU13_OWNER_EMAIL is not a valid email address.") from None
        row = self._row("SELECT value FROM settings WHERE key = 'owner_email'")
        return row["value"] if row else None

    def owner_email_source(self) -> str | None:
        if self.environ.get("SDDU13_OWNER_EMAIL", "").strip():
            return "environment"
        return "store" if self._row("SELECT 1 FROM settings WHERE key = 'owner_email'") else None

    def set_owner_email(self, email: str) -> None:
        """Store the owner's address. SDDU13_OWNER_EMAIL, when set, still wins."""
        address = normalise_email(email)
        self._run("INSERT INTO settings (key, value) VALUES ('owner_email', ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (address,))
        self._sync_owner()
        owner = self.find_account(address)
        self.log_security_event("owner_changed", account_id=owner.id if owner else None,
                                detail="owner email set")

    def _sync_owner(self) -> None:
        """Make the role column agree with the configured owner email."""
        owner = self.owner_email()
        now = _iso(self._now())
        with self._transaction():
            self._run("UPDATE accounts SET role = 'member' WHERE role = 'owner' AND email != ?",
                      (owner or "",))
            if owner:
                # The owner can never be blocked, so naming a blocked account
                # as owner lifts the block.
                self._run(
                    "UPDATE accounts SET role = 'owner', status = 'approved', "
                    "decided_at = COALESCE(decided_at, ?), blocked_at = NULL, "
                    "review_after = NULL, block_reason = NULL WHERE email = ?",
                    (now, owner))

    def is_owner(self, account: Account | None) -> bool:
        return account is not None and account.role == "owner" and account.email == self.owner_email()

    # -- accounts -----------------------------------------------------------

    def get_account(self, account_id: int) -> Account | None:
        row = self._row("SELECT * FROM accounts WHERE id = ?", (account_id,))
        return _account(row) if row else None

    def _require_account(self, account_id: int) -> Account:
        account = self.get_account(account_id)
        if account is None:
            raise UnknownAccount(f"No account #{account_id}.")
        return account

    def find_account(self, email: str) -> Account | None:
        if not isinstance(email, str) or not email.strip():
            return None
        row = self._row("SELECT * FROM accounts WHERE email = ?", (email.strip().lower(),))
        return _account(row) if row else None

    def list_accounts(self, status: str | None = None) -> list[Account]:
        if status is None:
            rows = self._rows("SELECT * FROM accounts ORDER BY created_at, id")
        else:
            rows = self._rows("SELECT * FROM accounts WHERE status = ? ORDER BY created_at, id", (status,))
        return [_account(row) for row in rows]

    def pending_requests(self) -> list[Account]:
        """Requests the owner can decide: an unconfirmed address is not one yet."""
        rows = self._rows("SELECT * FROM accounts WHERE status = 'pending' AND email_verified = 1 "
                          "ORDER BY created_at, id")
        return [_account(row) for row in rows]

    def blocked_accounts(self) -> list[Account]:
        rows = self._rows("SELECT * FROM accounts WHERE status = 'blocked' ORDER BY review_after, id")
        return [_account(row) for row in rows]

    def owner_summary(self) -> dict[str, int]:
        today = self._now().date().isoformat()

        def count(where: str, params: Sequence[Any] = ()) -> int:
            return int(self._row(f"SELECT COUNT(*) AS n FROM accounts WHERE {where}", params)["n"])

        return {
            "pending": count("status = 'pending' AND email_verified = 1"),
            "members": count("status = 'approved' AND role = 'member'"),
            "blocked": count("status = 'blocked'"),
            "reviews_due": count("status = 'blocked' AND review_after <= ?", (today,)),
        }

    def _insert_account(self, values: dict[str, Any]) -> int:
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = self._run(f"INSERT INTO accounts ({columns}) VALUES ({placeholders})",
                           tuple(values.values()))
        return int(cursor.lastrowid)

    def _new_account_values(self, address: str, display_name: str, company: str,
                            reason: str) -> dict[str, Any]:
        now = _iso(self._now())
        is_owner = address == self.owner_email()
        return {
            "email": address,
            "display_name": _line(display_name, 100) or address.partition("@")[0],
            "role": "owner" if is_owner else "member",
            "status": "approved" if is_owner else "pending",
            "decided_at": now if is_owner else None,
            "company": _line(company, 200),
            "request_reason": _text(reason, 2000),
            "created_at": now,
        }

    def register_password_account(self, email: str, password: str, *, display_name: str,
                                  company: str = "", reason: str = "", ip: str = "") -> Account:
        """Create a password account, 'pending' (the owner's is approved).

        Its address is unconfirmed until a 'verify_email' code comes back;
        until then the owner does not see the request, and after a day an
        unconfirmed account is purged.
        """
        address = normalise_email(email)
        check_password_strength(password)
        self.note_sign_in_attempt(ip=ip, email=address)
        values = self._new_account_values(address, display_name, company, reason)
        # Hashed before the lookup, so a taken address takes as long as a free one.
        values["password_hash"] = hash_password(password, iterations=self.password_iterations)
        with self._transaction():
            if self._row("SELECT 1 FROM accounts WHERE email = ?", (address,)):
                raise EmailTaken("An account already uses this email address.")
            account_id = self._insert_account(values)
        self.log_security_event("account_requested", account_id=account_id, ip=ip, detail="password")
        return self._require_account(account_id)

    def account_for_google(self, sub: str, email: str, *, ip: str = "") -> Account | None:
        """The account a verified Google identity signs in to, or None.

        The first Google sign-in to an address with an account links the two.
        If nobody ever confirmed that address, its password was set by someone
        who could not read its mail, so the link drops that password.
        """
        sub = _line(sub, 255)
        if not sub:
            return None
        row = self._row("SELECT * FROM accounts WHERE google_sub = ?", (sub,))
        if row is not None:
            return _account(row)

        address = (email or "").strip().lower() if isinstance(email, str) else ""
        detail = ""
        with self._transaction():
            # Read inside the transaction, so two callbacks cannot both link.
            row = self._row("SELECT * FROM accounts WHERE email = ?", (address,)) if address else None
            if row is None:
                return None
            if row["google_sub"] is None:
                if row["email_verified"]:
                    self._run("UPDATE accounts SET google_sub = ? WHERE id = ?", (sub, row["id"]))
                    detail = "Google linked"
                else:
                    self._run("UPDATE accounts SET google_sub = ?, email_verified = 1, "
                              "password_hash = NULL WHERE id = ?", (sub, row["id"]))
                    detail = "Google linked; the unconfirmed password was removed"
        if not detail:
            self.log_security_event("google_mismatch", account_id=row["id"], ip=ip,
                                    detail="a different Google account used this address")
            return None
        self.log_security_event("google_linked", account_id=row["id"], ip=ip, detail=detail)
        return self._require_account(int(row["id"]))

    def create_google_account(self, sub: str, email: str, *, display_name: str,
                              company: str = "", reason: str = "", ip: str = "") -> Account:
        """A new account for a Google identity. Google confirmed the address."""
        address = normalise_email(email)
        sub = _line(sub, 255)
        if not sub:
            raise InvalidInput("Google did not identify the account.")
        values = self._new_account_values(address, display_name, company, reason)
        values.update(google_sub=sub, email_verified=1)
        with self._transaction():
            if self._row("SELECT 1 FROM accounts WHERE email = ? OR google_sub = ?", (address, sub)):
                raise EmailTaken("An account already uses this email address.")
            account_id = self._insert_account(values)
        self.log_security_event("account_requested", account_id=account_id, ip=ip, detail="google")
        return self._require_account(account_id)

    def update_profile(self, account_id: int, *, display_name: str) -> Account:
        name = _line(display_name, 100)
        if not name:
            raise InvalidInput("Enter a name.")
        self._require_account(account_id)
        self._run("UPDATE accounts SET display_name = ? WHERE id = ?", (name, account_id))
        return self._require_account(account_id)

    def request_again(self, account_id: int, reason: str = "") -> Account:
        """An unblocked person asks for access again; the owner decides anew."""
        with self._transaction():
            account = self._require_account(account_id)
            if account.status != "unblocked":
                raise WrongState("Only an unblocked account can ask for access again.")
            self._run("UPDATE accounts SET status = 'pending', request_reason = ?, decided_at = NULL "
                      "WHERE id = ?", (_text(reason, 2000) or account.request_reason, account_id))
        self.log_security_event("access_requested_again", account_id=account_id)
        return self._require_account(account_id)

    # -- owner decisions ------------------------------------------------------

    def _decide(self, account_id: int, status: str, allowed: tuple[str, ...], event: str,
                *, sign_out: bool = False) -> Account:
        with self._transaction():
            account = self._require_account(account_id)
            if self._is_owner_account(account):
                raise OwnerProtected("The owner account's access cannot be changed.")
            if account.status not in allowed:
                raise WrongState(f"An account that is {account.status} cannot be {status}.")
            if status == "approved" and not account.email_verified:
                raise WrongState("This person has not confirmed their email address yet.")
            self._run("UPDATE accounts SET status = ?, decided_at = ? WHERE id = ?",
                      (status, _iso(self._now()), account_id))
            if sign_out:
                self._run("DELETE FROM sessions WHERE account_id = ?", (account_id,))
        self.log_security_event(event, account_id=account_id)
        return self._require_account(account_id)

    def approve(self, account_id: int) -> Account:
        # Never from 'blocked': that would get round the review date.
        return self._decide(account_id, "approved",
                            ("pending", "declined", "revoked", "unblocked"), "approved")

    def decline(self, account_id: int) -> Account:
        return self._decide(account_id, "declined", ("pending",), "declined")

    def remove(self, account_id: int) -> Account:
        return self._decide(account_id, "revoked", ("approved",), "removed", sign_out=True)

    def _is_owner_account(self, account: Account) -> bool:
        return account.is_owner or account.email == self.owner_email()

    def block(self, account_id: int, reason: str, *, ip: str = "", automatic: bool = False) -> date:
        """Block for four calendar months and sign them out everywhere.
        Returns the review date, before which the block cannot be lifted."""
        reason = _line(reason, 500)
        if not reason:
            raise InvalidInput("Give a reason for the block.")
        now = self._now()
        review = add_months(now.date(), BLOCK_MONTHS)
        with self._transaction():
            account = self._require_account(account_id)
            if self._is_owner_account(account):
                raise OwnerProtected("The owner account can never be blocked.")
            if account.status == "blocked":
                raise WrongState("This account is already blocked.")
            self._run(
                "UPDATE accounts SET status = 'blocked', blocked_at = ?, review_after = ?, "
                "block_reason = ?, decided_at = ? WHERE id = ?",
                (_iso(now), review.isoformat(), reason, _iso(now), account_id))
            self._run("DELETE FROM sessions WHERE account_id = ?", (account_id,))
            self._run("UPDATE email_codes SET voided_at = ? WHERE account_id = ? "
                      "AND used_at IS NULL AND voided_at IS NULL", (_iso(now), account_id))
        self.log_security_event("auto_block" if automatic else "block", account_id=account_id,
                                ip=ip, detail=f"until at least {review.isoformat()}: {reason}")
        return review

    def _blocked_past_review(self, account_id: int) -> Account:
        account = self._require_account(account_id)
        if account.status != "blocked" or account.review_after is None:
            raise WrongState("This account is not blocked.")
        if self._now().date() < account.review_after:
            raise TooEarly(account.review_after)
        return account

    def unblock(self, account_id: int) -> Account:
        """Lift a block whose review date has come. Access is not restored:
        the person may ask again, and the owner decides."""
        with self._transaction():
            self._blocked_past_review(account_id)
            self._run("UPDATE accounts SET status = 'unblocked', decided_at = ? WHERE id = ?",
                      (_iso(self._now()), account_id))
        self.log_security_event("unblock", account_id=account_id)
        return self._require_account(account_id)

    def extend_block(self, account_id: int) -> date:
        """Keep a block whose review date has come for four more months from today."""
        review = add_months(self._now().date(), BLOCK_MONTHS)
        with self._transaction():
            self._blocked_past_review(account_id)
            self._run("UPDATE accounts SET review_after = ?, decided_at = ? WHERE id = ?",
                      (review.isoformat(), _iso(self._now()), account_id))
        self.log_security_event("block_extended", account_id=account_id,
                                detail=f"until at least {review.isoformat()}")
        return review

    def send_reset_code(self, account_id: int) -> None:
        """The owner's "reset password": a reset code goes to the person."""
        self.send_code(account_id, "reset_password")
        self.log_security_event("reset_sent", account_id=account_id, detail="by the owner")

    # -- throttling -----------------------------------------------------------

    def _hit(self, key: str, limit: int, window: timedelta) -> int:
        """Count one attempt against `key`; 0 if allowed, else seconds to wait.
        Keys are stored as HMACs, so a password typed into an email box by
        mistake is not kept here in plain text."""
        stored = self._hmac("throttle", key)
        now = self._now()
        with self._transaction():
            row = self._row("SELECT window_start, count FROM throttle WHERE key = ?", (stored,))
            if row is None or now - _parse(row["window_start"]) >= window:
                self._run("INSERT INTO throttle (key, window_start, count) VALUES (?, ?, 1) "
                          "ON CONFLICT(key) DO UPDATE SET window_start = excluded.window_start, "
                          "count = 1", (stored, _iso(now)))
                return 0 if limit >= 1 else int(window.total_seconds())
            count = int(row["count"]) + 1
            self._run("UPDATE throttle SET count = ? WHERE key = ?", (count, stored))
            if count <= limit:
                return 0
            return max(1, int((_parse(row["window_start"]) + window - now).total_seconds()))

    def allow(self, key: str, limit: int, window: timedelta) -> bool:
        """Count an attempt against any key; False once `limit` is passed in
        the window. For throttling unauthenticated probing by address."""
        return self._hit(key, limit, window) == 0

    def note_sign_in_attempt(self, *, ip: str, email: str | None = None) -> None:
        """Count a password or Google sign-in attempt by address and by email.
        Raises Throttled past SIGN_IN_LIMIT in SIGN_IN_WINDOW for either."""
        waits = [self._hit(f"sign_in:ip:{ip}", SIGN_IN_LIMIT, SIGN_IN_WINDOW)]
        if email:
            waits.append(self._hit(f"sign_in:email:{email.strip().lower()}",
                                   SIGN_IN_LIMIT, SIGN_IN_WINDOW))
        wait = max(waits)
        if wait:
            self.log_security_event("throttled", ip=ip, detail="sign-in attempts")
            raise Throttled(wait)

    # -- passwords ----------------------------------------------------------

    def authenticate_password(self, email: str, password: str, *, ip: str = "") -> Account | None:
        """The first factor. None for any failure, with no hint which part was
        wrong; an unknown email takes as long as a wrong password."""
        self.note_sign_in_attempt(ip=ip, email=email if isinstance(email, str) else None)
        address = email.strip().lower() if isinstance(email, str) else ""
        row = self._row("SELECT * FROM accounts WHERE email = ?", (address,)) if address else None
        stored = row["password_hash"] if row is not None else None
        matched = verify_password(password, stored or _dummy_hash(self.password_iterations))
        if row is None or stored is None or not matched:
            # The typed email is not recorded: people type passwords into it.
            self.log_security_event("sign_in_failed", account_id=row["id"] if row is not None else None,
                                    ip=ip, detail="password")
            return None
        if needs_rehash(stored, self.password_iterations):
            self._run("UPDATE accounts SET password_hash = ? WHERE id = ? AND password_hash = ?",
                      (hash_password(password, iterations=self.password_iterations), row["id"], stored))
        return _account(row)

    def _set_password(self, account_id: int, password: str, *, keep_token: str | None) -> None:
        check_password_strength(password)
        encoded = hash_password(password, iterations=self.password_iterations)
        with self._transaction():
            self._run("UPDATE accounts SET password_hash = ? WHERE id = ?", (encoded, account_id))
            self._run("DELETE FROM sessions WHERE account_id = ? AND token_hash != ?",
                      (account_id, _sha256(keep_token) if keep_token else ""))

    def change_password(self, account_id: int, current: str, new: str, *,
                        keep_token: str | None = None) -> bool:
        """Needs the current password. Every other session is signed out."""
        check_password_strength(new)
        wait = self._hit(f"change_password:{account_id}", SIGN_IN_LIMIT, SIGN_IN_WINDOW)
        if wait:
            raise Throttled(wait)
        row = self._row("SELECT password_hash FROM accounts WHERE id = ?", (account_id,))
        stored = row["password_hash"] if row is not None else None
        if not verify_password(current, stored or _dummy_hash(self.password_iterations)) or stored is None:
            self.log_security_event("password_change_failed", account_id=account_id if row else None)
            return False
        self._set_password(account_id, new, keep_token=keep_token)
        self.log_security_event("password_changed", account_id=account_id)
        return True

    def request_password_reset(self, email: str, *, ip: str = "") -> None:
        """Email a reset code if the address has an account. Returns the same
        way whether it does or not; only the attempt limit can raise."""
        self.note_sign_in_attempt(ip=ip, email=email if isinstance(email, str) else None)
        account = self.find_account(email)
        if account is None:
            return
        try:
            self.send_code(account.id, "reset_password", background=True)
        except Throttled:
            return  # recorded as a security event; the page says the same either way

    def reset_password(self, email: str, code: str, new_password: str, *, ip: str = "") -> CodeCheck:
        """Set a new password with an emailed reset code. Signs out everywhere.
        The password is checked first, so a too-short one does not spend the code."""
        check_password_strength(new_password)
        self.note_sign_in_attempt(ip=ip, email=email if isinstance(email, str) else None)
        account = self.find_account(email)
        if account is None:
            return CodeCheck(False, "none")
        check = self.verify_code(account.id, "reset_password", code)
        if not check:
            self.log_security_event("code_failed", account_id=account.id, ip=ip,
                                    detail=f"reset_password: {check.reason}")
            return check
        self._set_password(account.id, new_password, keep_token=None)
        self.log_security_event("password_reset", account_id=account.id, ip=ip)
        return check

    # -- sessions -------------------------------------------------------------

    def _create_session(self, account_id: int, stage: str, method: str, ip: str,
                        user_agent: str) -> NewSession:
        token = secrets.token_urlsafe(32)
        now = self._now()
        expires = now + (PARTIAL_LIFETIME if stage == "partial" else SESSION_ABSOLUTE)
        self._run(
            "INSERT INTO sessions (token_hash, account_id, stage, method, created_at, expires_at, "
            "last_seen, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (_sha256(token), account_id, stage, _line(method, 20), _iso(now), _iso(expires),
             _iso(now), _line(ip, 64), _line(user_agent, 300)))
        return NewSession(token=token, stage=stage, expires_at=_iso(expires),
                          csrf_token=self.csrf_token(token))

    def begin_sign_in(self, account_id: int, *, method: str, ip: str = "",
                      user_agent: str = "") -> NewSession:
        """A partial session after the first factor (or after registering)."""
        self._require_account(account_id)
        return self._create_session(account_id, "partial", method, ip, user_agent)

    def get_session(self, token: str | None) -> Session | None:
        """The live session for a cookie value, or None. Expired sessions are
        deleted on sight; a live one has its last_seen moved to now."""
        if not token or not isinstance(token, str) or len(token) > 200:
            return None
        token_hash = _sha256(token)
        row = self._row("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,))
        if row is None:
            return None
        now = self._now()
        if now >= _parse(row["expires_at"]) or now - _parse(row["last_seen"]) >= SESSION_IDLE:
            self._run("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))
            return None
        account = self.get_account(int(row["account_id"]))
        if account is None:
            return None
        self._run("UPDATE sessions SET last_seen = ? WHERE token_hash = ?", (_iso(now), token_hash))
        return Session(account=account, stage=row["stage"], method=row["method"],
                       created_at=row["created_at"], last_seen=_iso(now),
                       expires_at=row["expires_at"], ip=row["ip"], user_agent=row["user_agent"])

    def finish_sign_in(self, token: str, code: str, *, purpose: str = "sign_in") -> SecondStep:
        """The second step. Six digits are checked as the emailed code; anything
        else as a backup code (sign-in only). On success the partial session is
        replaced by a full one with a new token."""
        if purpose not in ("sign_in", "verify_email"):
            raise ValueError(f"a sign-in cannot finish with a {purpose} code")
        session = self.get_session(token)
        if session is None or session.stage != "partial":
            return SecondStep(CodeCheck(False, "none"), None)
        account_id = session.account.id
        typed = code if isinstance(code, str) else ""
        if re.fullmatch(r"[0-9]{6}", re.sub(r"[\s-]", "", typed)) or purpose != "sign_in":
            check = self.verify_code(account_id, purpose, typed)
            via = "emailed code"
        else:
            check = self.use_backup_code(account_id, typed)
            via = "backup code"
        if not check:
            self.log_security_event("code_failed", account_id=account_id, ip=session.ip,
                                    detail=f"{purpose} {via}: {check.reason}")
            return SecondStep(check, None)
        with self._transaction():
            self._run("DELETE FROM sessions WHERE token_hash = ?", (_sha256(token),))
            new = self._create_session(account_id, "full", session.method, session.ip,
                                       session.user_agent)
        self.log_security_event("sign_in", account_id=account_id, ip=session.ip,
                                detail=f"{session.method or 'unknown'} + {via}")
        return SecondStep(check, new)

    def end_session(self, token: str | None) -> None:
        if token:
            self._run("DELETE FROM sessions WHERE token_hash = ?", (_sha256(token),))

    def end_all_sessions(self, account_id: int, *, keep_token: str | None = None) -> int:
        count = self._run("DELETE FROM sessions WHERE account_id = ? AND token_hash != ?",
                          (account_id, _sha256(keep_token) if keep_token else "")).rowcount
        self.log_security_event("sessions_revoked", account_id=account_id,
                                detail=f"{count} signed out")
        return count

    def list_sessions(self, account_id: int) -> list[dict[str, Any]]:
        rows = self._rows("SELECT stage, method, created_at, last_seen, expires_at, ip, user_agent "
                          "FROM sessions WHERE account_id = ? ORDER BY last_seen DESC", (account_id,))
        return [dict(row) for row in rows]

    # -- emailed codes --------------------------------------------------------

    def _code_hash(self, account_id: int, purpose: str, code: str) -> str:
        return self._hmac("code", f"{account_id}:{purpose}:{code}")

    def issue_code(self, account_id: int, purpose: str) -> str:
        """A new 6-digit code, voiding the last one for the same purpose.
        Only its HMAC is stored. At most CODES_PER_HOUR per account per hour."""
        if purpose not in CODE_PURPOSES:
            raise ValueError(f"unknown code purpose {purpose!r}")
        code = f"{secrets.randbelow(1_000_000):06d}"
        now = self._now()
        hour_ago = _iso(now - timedelta(hours=1))
        with self._transaction():
            self._require_account(account_id)
            recent = self._row("SELECT COUNT(*) AS n, MIN(created_at) AS first FROM email_codes "
                               "WHERE account_id = ? AND created_at > ?", (account_id, hour_ago))
            throttled = recent["n"] >= CODES_PER_HOUR
            if throttled:
                wait = int((_parse(recent["first"]) + timedelta(hours=1) - now).total_seconds())
            else:
                self._run("UPDATE email_codes SET voided_at = ? WHERE account_id = ? AND purpose = ? "
                          "AND used_at IS NULL AND voided_at IS NULL", (_iso(now), account_id, purpose))
                self._run("INSERT INTO email_codes (account_id, purpose, code_hash, created_at, expires_at) "
                          "VALUES (?, ?, ?, ?, ?)",
                          (account_id, purpose, self._code_hash(account_id, purpose, code),
                           _iso(now), _iso(now + CODE_LIFETIME)))
        if throttled:
            self.log_security_event("throttled", account_id=account_id,
                                    detail=f"{purpose} code not sent: {CODES_PER_HOUR} in the last hour")
            raise Throttled(wait)
        return code

    def _require_mailer(self) -> Mailer:
        if self.mailer is None:
            raise MailNotConfigured([], "The accounts store was opened without a mailer.")
        return self.mailer

    def send_code(self, account_id: int, purpose: str, *, background: bool = False) -> None:
        """Issue a code and email it to the account's own address.

        `background` hands the sending to a thread and returns at once. Use it
        wherever an attacker could compare response times: SMTP takes seconds,
        so a page that waits for it says "this address has an account".
        """
        mailer = self._require_mailer()
        account = self._require_account(account_id)
        code = self.issue_code(account_id, purpose)
        subject, body = code_email(purpose, code)
        if background:
            self._deliver_later(account_id, purpose, account.email, subject, body)
            return
        try:
            mailer.send(account.email, subject, body)
        except MailError:
            self._mail_failed(account_id, purpose)
            raise

    def _mail_failed(self, account_id: int, purpose: str | None) -> None:
        if purpose:
            self._run("UPDATE email_codes SET voided_at = ? WHERE account_id = ? AND purpose = ? "
                      "AND used_at IS NULL AND voided_at IS NULL",
                      (_iso(self._now()), account_id, purpose))
        self.log_security_event("mail_failed", account_id=account_id,
                                detail=f"{purpose} code" if purpose else "notice")

    def _deliver_later(self, account_id: int, purpose: str | None, to: str, subject: str,
                       body: str) -> None:
        mailer = self._require_mailer()

        def deliver() -> None:
            try:
                mailer.send(to, subject, body)
            except MailError:
                # This thread cannot use the request's connection; open its own.
                store = AccountStore(self.root, environ=self.environ, clock=self.clock,
                                     mirror_audit=self.mirror_audit)
                try:
                    store._mail_failed(account_id, purpose)
                finally:
                    store.close()

        thread = threading.Thread(target=deliver, name="portal-mail", daemon=True)
        self._deliveries.append(thread)
        thread.start()

    def wait_for_mail(self, timeout: float = 30.0) -> None:
        """Wait for mail handed to background threads (tests, and a CLI that
        is about to exit)."""
        for thread in self._deliveries:
            thread.join(timeout)
        self._deliveries = [thread for thread in self._deliveries if thread.is_alive()]

    def notify_existing_account(self, email: str) -> None:
        """Someone asked for access with an address that has an account: tell
        its holder, in the background, at most three times an hour. The
        request page then reads the same whether the address was free or not."""
        account = self.find_account(email)
        if account is None or not self.allow(f"notice:{account.id}", 3, timedelta(hours=1)):
            return
        subject, body = existing_account_email()
        self._deliver_later(account.id, None, account.email, subject, body)

    def verify_code(self, account_id: int, purpose: str, code: str) -> CodeCheck:
        """Check an emailed code: 10 minutes, 5 attempts, once. A code that
        works also confirms the address it was sent to."""
        typed = re.sub(r"[\s-]", "", code) if isinstance(code, str) else ""
        now = self._now()
        with self._transaction():
            row = self._row(
                "SELECT id, code_hash, expires_at, attempts FROM email_codes WHERE account_id = ? "
                "AND purpose = ? AND used_at IS NULL AND voided_at IS NULL ORDER BY id DESC LIMIT 1",
                (account_id, purpose))
            if row is None:
                return CodeCheck(False, "none")
            if now >= _parse(row["expires_at"]):
                return CodeCheck(False, "expired")
            if row["attempts"] >= CODE_ATTEMPTS:
                return CodeCheck(False, "locked")
            attempts = int(row["attempts"]) + 1
            expected = self._code_hash(account_id, purpose, typed)
            if hmac.compare_digest(expected, row["code_hash"]):
                self._run("UPDATE email_codes SET attempts = ?, used_at = ? WHERE id = ?",
                          (attempts, _iso(now), row["id"]))
                self._run("UPDATE accounts SET email_verified = 1 WHERE id = ?", (account_id,))
                return CodeCheck(True, "ok")
            self._run("UPDATE email_codes SET attempts = ? WHERE id = ?", (attempts, row["id"]))
            return CodeCheck(False, "locked" if attempts >= CODE_ATTEMPTS else "wrong")

    # -- backup codes ---------------------------------------------------------

    def _backup_hash(self, account_id: int, code: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9]", "", code).upper()
        return self._hmac("backup", f"{account_id}:{cleaned}")

    @staticmethod
    def _new_backup_codes() -> list[str]:
        codes: set[str] = set()
        while len(codes) < BACKUP_CODE_COUNT:
            raw = "".join(secrets.choice(BACKUP_ALPHABET) for _ in range(8))
            codes.add(f"{raw[:4]}-{raw[4:]}")
        return sorted(codes)

    def _store_backup_codes(self, account_id: int, codes: list[str]) -> None:
        now = _iso(self._now())
        with self._transaction():
            self._require_account(account_id)
            self._run("DELETE FROM backup_codes WHERE account_id = ?", (account_id,))
            for code in codes:
                self._run("INSERT INTO backup_codes (account_id, code_hash, created_at) VALUES (?, ?, ?)",
                          (account_id, self._backup_hash(account_id, code), now))
        self.log_security_event("backup_codes_generated", account_id=account_id,
                                detail=f"{len(codes)} codes; the old set no longer works")

    def generate_backup_codes(self, account_id: int) -> list[str]:
        """A new set of ten, replacing the old set. Only HMACs are stored."""
        codes = self._new_backup_codes()
        self._store_backup_codes(account_id, codes)
        return codes

    def send_backup_codes(self, account_id: int) -> None:
        """Email a new set to the account's own address, then store it. In
        that order: if the mail fails, the old set still works."""
        mailer = self._require_mailer()
        account = self._require_account(account_id)
        wait = self._hit(f"backup_set:{account_id}", BACKUP_SETS_PER_HOUR, timedelta(hours=1))
        if wait:
            raise Throttled(wait)
        codes = self._new_backup_codes()
        subject, body = backup_codes_email(codes)
        try:
            mailer.send(account.email, subject, body)
        except MailError:
            self.log_security_event("mail_failed", account_id=account_id, detail="backup codes")
            raise
        self._store_backup_codes(account_id, codes)

    def ensure_backup_codes(self, account_id: int) -> bool:
        """After a full sign-in: email a set if the account has none left."""
        if self.backup_codes_remaining(account_id):
            return False
        self.send_backup_codes(account_id)
        return True

    def backup_codes_remaining(self, account_id: int) -> int:
        row = self._row("SELECT COUNT(*) AS n FROM backup_codes WHERE account_id = ? AND used_at IS NULL",
                        (account_id,))
        return int(row["n"])

    def use_backup_code(self, account_id: int, code: str) -> CodeCheck:
        """Spend one backup code. Case, spaces and the dash do not matter."""
        if self._hit(f"backup:{account_id}", BACKUP_ATTEMPTS, BACKUP_WINDOW):
            return CodeCheck(False, "throttled")
        candidate = self._backup_hash(account_id, code if isinstance(code, str) else "")
        with self._transaction():
            rows = self._rows("SELECT id, code_hash FROM backup_codes WHERE account_id = ? "
                              "AND used_at IS NULL", (account_id,))
            match = None
            for row in rows:  # every row compared, match or not
                if hmac.compare_digest(row["code_hash"], candidate) and match is None:
                    match = row["id"]
            if match is None:
                return CodeCheck(False, "wrong")
            self._run("UPDATE backup_codes SET used_at = ? WHERE id = ?", (_iso(self._now()), match))
        self.log_security_event("backup_code_used", account_id=account_id,
                                detail=f"{self.backup_codes_remaining(account_id)} left")
        return CodeCheck(True, "ok")

    # -- strikes --------------------------------------------------------------

    def record_strike(self, account_id: int | None, *, reason: str, path: str = "", ip: str = "",
                      immediate: bool = False) -> StrikeOutcome:
        """Record a request the person had no right to make.

        STRIKE_LIMIT strikes within STRIKE_WINDOW block the account; so does a
        single `immediate` strike (a POST to an owner action, or a tampered
        form acting on someone else). Every automatic block emails the owner.
        Without an account (`account_id` None) the strike is recorded for the
        owner to see and nothing is blocked; throttle that case instead.
        """
        reason, path, ip = _line(reason, 300), _line(path, 300), _line(ip, 64)
        account = self.get_account(account_id) if account_id is not None else None
        if account_id is not None and account is None:
            raise UnknownAccount(f"No account #{account_id}.")
        if account is not None and self._is_owner_account(account):
            return StrikeOutcome(False, 0, None)  # the owner may go anywhere

        now = self._now()
        with self._transaction():
            self._run("INSERT INTO strikes (account_id, ip, at, reason, path) VALUES (?, ?, ?, ?, ?)",
                      (account_id, ip, _iso(now), reason, path))
            strikes = 0
            if account_id is not None:
                strikes = int(self._row(
                    "SELECT COUNT(*) AS n FROM strikes WHERE account_id = ? AND at > ?",
                    (account_id, _iso(now - STRIKE_WINDOW)))["n"])
        self.log_security_event("strike", account_id=account_id, ip=ip, detail=f"{reason} ({path})")

        if account is None:
            return StrikeOutcome(False, 0, None)
        if account.status == "blocked":
            return StrikeOutcome(True, strikes, account.review_after)
        if not immediate and strikes < STRIKE_LIMIT:
            return StrikeOutcome(False, strikes, None)

        why = ("an owner-only action or a form changed to act on someone else" if immediate
               else f"{strikes} strikes within 24 hours")
        try:
            review = self.block(account.id, f"Automatic: {why}. Last: {reason}", ip=ip, automatic=True)
        except WrongState:
            # A request racing this one blocked the account first.
            current = self._require_account(account.id)
            return StrikeOutcome(True, strikes, current.review_after)
        self._alert_owner("An account was blocked automatically", [
            f"Account: {account.email} ({account.display_name})",
            f"Why: {why}",
            f"Last request: {reason} at {path or '-'} from {ip or 'an unknown address'}",
            f"It stays blocked until at least {review.isoformat()}; "
            "you can decide then whether to unblock it or keep it blocked.",
        ])
        return StrikeOutcome(True, strikes, review)

    def account_strikes(self, account_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._rows("SELECT at, ip, reason, path FROM strikes WHERE account_id = ? "
                          "ORDER BY id DESC LIMIT ?", (account_id, limit))
        return [dict(row) for row in rows]

    def _alert_owner(self, headline: str, lines: list[str]) -> bool:
        owner = self.owner_email()
        if self.mailer is None or not owner:
            self.log_security_event("owner_alert_unsent", detail=f"{headline}: no mailer or owner email")
            return False
        subject, body = owner_alert_email(headline, lines)
        try:
            self.mailer.send(owner, subject, body)
        except MailError:
            self.log_security_event("owner_alert_failed", detail=headline)
            return False
        self.log_security_event("owner_alerted", detail=headline)
        return True

    # -- Google sign-in flows -------------------------------------------------

    def begin_oauth_flow(self) -> str:
        """A random key for the pre-auth cookie. Only its hash is kept."""
        key = secrets.token_urlsafe(32)
        now = self._now()
        self._run("INSERT INTO oauth_flows (key_hash, created_at, expires_at) VALUES (?, ?, ?)",
                  (_sha256(key), _iso(now), _iso(now + FLOW_LIFETIME)))
        return key

    def consume_oauth_flow(self, flow_key: str) -> bool:
        """True once for a live flow; never again."""
        if not flow_key or not isinstance(flow_key, str):
            return False
        now = self._now()
        with self._transaction():
            cursor = self._run("UPDATE oauth_flows SET used_at = ? WHERE key_hash = ? "
                               "AND used_at IS NULL AND expires_at > ?",
                               (_iso(now), _sha256(flow_key), _iso(now)))
            return cursor.rowcount == 1

    # -- announcements --------------------------------------------------------

    def post_announcement(self, title: str, body: str) -> int:
        title, body = _line(title, 200), _text(body, 5000)
        if not title or not body:
            raise InvalidInput("An update needs a title and some text.")
        cursor = self._run("INSERT INTO announcements (title, body, created_at) VALUES (?, ?, ?)",
                           (title, body, _iso(self._now())))
        self.log_security_event("announcement_posted", detail=title)
        return int(cursor.lastrowid)

    def delete_announcement(self, announcement_id: int) -> bool:
        deleted = self._run("DELETE FROM announcements WHERE id = ?", (announcement_id,)).rowcount == 1
        if deleted:
            self.log_security_event("announcement_deleted", detail=f"#{announcement_id}")
        return deleted

    def list_announcements(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._rows("SELECT id, title, body, created_at FROM announcements "
                          "ORDER BY created_at DESC, id DESC LIMIT ?", (limit,))
        return [dict(row) for row in rows]

    # -- security events ------------------------------------------------------

    def log_security_event(self, kind: str, *, account_id: int | None = None, ip: str = "",
                           detail: str = "") -> None:
        """Record what happened. Callers pass no secrets; nothing here adds one."""
        self._run("INSERT INTO security_events (at, account_id, ip, kind, detail) VALUES (?, ?, ?, ?, ?)",
                  (_iso(self._now()), account_id, _line(ip, 64), _line(kind, 40), _line(detail, 500)))
        if self.mirror_audit and kind in MIRRORED_EVENTS:
            self._mirror(kind, account_id)

    def _mirror(self, kind: str, account_id: int | None) -> None:
        """Best effort: copy the event to the data center's audit trail when
        that database exists. A missing audit database is not the store's
        problem, and the event is already recorded here."""
        center = DataCenter(root=self.root)
        try:
            if center.db("audit").path.exists():
                who = f"account #{account_id}" if account_id is not None else "portal"
                center.log("audit", f"portal_{kind}", actor="portal", detail=who)
        except (OSError, sqlite3.Error):
            pass
        finally:
            center.close()

    def recent_security_events(self, limit: int = 50,
                               account_id: int | None = None) -> list[dict[str, Any]]:
        if account_id is None:
            rows = self._rows("SELECT e.id, e.at, e.account_id, a.email, e.ip, e.kind, e.detail "
                              "FROM security_events e LEFT JOIN accounts a ON a.id = e.account_id "
                              "ORDER BY e.id DESC LIMIT ?", (limit,))
        else:
            rows = self._rows("SELECT e.id, e.at, e.account_id, a.email, e.ip, e.kind, e.detail "
                              "FROM security_events e LEFT JOIN accounts a ON a.id = e.account_id "
                              "WHERE e.account_id = ? ORDER BY e.id DESC LIMIT ?", (account_id, limit))
        return [dict(row) for row in rows]
