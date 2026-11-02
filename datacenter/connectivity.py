"""Connection checks for the portal's connection status page.

The portal is a web server. It does not give anyone internet access and it
is not a VPN: these checks only ask what the server itself can reach, so the
owner can tell "the portal is down" apart from "the server lost its
internet" or "the email server stopped answering". A VPN such as Tailscale
or WireGuard is what puts people on the same private network as the server;
see docs/PORTAL.md.

What is checked, in order:

    accounts store       <root>/portal/accounts.db is readable and complete
    the seven databases  every data database is provisioned and passes
                         SQLite's integrity check
    internet             a TCP connection to a well-known address on port 443
    DNS                  a well-known name resolves
    Google sign-in       a verified TLS connection to Google's sign-in hosts,
                         only when Google sign-in is configured
    email server         connect and EHLO (and STARTTLS), never send, only
                         when SMTP is configured

The hosts are fixed here and never taken from a request, and the results are
cached for `ttl` seconds (60 by default), so opening the page again and
again cannot turn the portal into a scanner or flood anything.

Every check runs on its own thread with one shared deadline. A DNS lookup
has no timeout of its own, so the deadline is what guarantees the page
answers within `timeout` seconds (plus a moment) whatever the network does;
a check still running then is reported as a problem.

Public API:

    ConnectionChecker(probes=None, *, timeout=3.0, ttl=60.0,
                      internet=INTERNET_TARGETS, dns_names=DNS_NAMES)
        .report(root, *, mailer=None, google_configured=False)
            -> list[CheckResult]   cached for ttl seconds
    Probes(tcp, tls, resolve)      the network calls, replaceable in tests
    CheckResult                    name, state (OK, PROBLEM, OFF), detail,
                                   checked_at
"""

from __future__ import annotations

import socket
import sqlite3
import ssl
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .accounts import AccountStore
from .config import DATABASES
from .core import DataCenter
from .google_signin import HOSTS as GOOGLE_HOSTS
from .mailer import MailError, OutboxMailer

OK = "ok"
PROBLEM = "problem"
OFF = "off"
STATE_LABELS = {OK: "OK", PROBLEM: "Problem", OFF: "Not checked"}

#: Addresses, not names, so a DNS failure does not also read as "no internet".
INTERNET_TARGETS: tuple[tuple[str, int], ...] = (("1.1.1.1", 443), ("8.8.8.8", 443))
DNS_NAMES: tuple[str, ...] = ("example.com",)

PROBE_TIMEOUT = 3.0
CHECK_TTL = 60.0
#: How much longer than `timeout` the page waits for a check thread before
#: calling it unanswered: the probes' own socket timeouts should fire first.
_GRACE = 0.5


# -- probes ---------------------------------------------------------------------

def tcp_connect(host: str, port: int, timeout: float) -> None:
    """Open a TCP connection and close it again. Raises OSError on failure."""
    with socket.create_connection((host, port), timeout=timeout):
        pass


def tls_connect(host: str, port: int, timeout: float) -> None:
    """Complete a TLS handshake with the certificate verified for `host`."""
    context = ssl.create_default_context()
    with socket.create_connection((host, port), timeout=timeout) as raw:
        with context.wrap_socket(raw, server_hostname=host):
            pass


def resolve_name(name: str, _timeout: float) -> None:
    """Resolve a name. getaddrinfo takes no timeout; the checker's deadline
    covers a lookup that never returns."""
    if not socket.getaddrinfo(name, 443, type=socket.SOCK_STREAM):
        raise OSError(f"no addresses for {name}")


@dataclass(frozen=True)
class Probes:
    """The network calls a check makes. Tests replace them, so no test
    needs a network and none can hang on one."""

    tcp: Callable[[str, int, float], None] = tcp_connect
    tls: Callable[[str, int, float], None] = tls_connect
    resolve: Callable[[str, float], None] = resolve_name


@dataclass(frozen=True)
class CheckResult:
    name: str
    state: str
    detail: str
    checked_at: datetime

    @property
    def label(self) -> str:
        return STATE_LABELS[self.state]


Outcome = tuple[str, str]  # (state, detail)


def _failure(error: BaseException) -> str:
    # The exception's type only: its text can carry addresses and paths,
    # and the page is open to every member.
    return type(error).__name__


# -- the checker ------------------------------------------------------------------

class ConnectionChecker:
    """Runs the checks with a deadline and remembers the answer for `ttl`."""

    def __init__(self, probes: Probes | None = None, *, timeout: float = PROBE_TIMEOUT,
                 ttl: float = CHECK_TTL, internet: tuple[tuple[str, int], ...] = INTERNET_TARGETS,
                 dns_names: tuple[str, ...] = DNS_NAMES,
                 monotonic: Callable[[], float] = time.monotonic,
                 now: Callable[[], datetime] | None = None) -> None:
        self.probes = probes or Probes()
        self.timeout = timeout
        self.ttl = ttl
        self.internet = internet
        self.dns_names = dns_names
        self._monotonic = monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.Lock()
        self._cached: list[CheckResult] | None = None
        self._cached_at = float("-inf")

    def report(self, root: Path | str, *, mailer: Any = None,
               google_configured: bool = False) -> list[CheckResult]:
        """The latest results, running the checks when the cache has expired.
        Callers that arrive while a run is in progress wait for it rather
        than starting their own."""
        with self._lock:
            if self._cached is not None and self._monotonic() - self._cached_at < self.ttl:
                return list(self._cached)
            results = self._run(self._plan(Path(root), mailer, google_configured))
            self._cached, self._cached_at = results, self._monotonic()
            return list(results)

    # -- the checks -------------------------------------------------------------

    def _plan(self, root: Path, mailer: Any,
              google_configured: bool) -> list[tuple[str, Callable[[], Outcome]]]:
        plan: list[tuple[str, Callable[[], Outcome]]] = [
            ("Accounts store", lambda: self._accounts_store(root)),
            ("The seven databases", lambda: self._databases(root)),
            ("Internet", self._internet),
            ("DNS", self._dns),
        ]
        if google_configured:
            plan.append(("Google sign-in", self._google))
        else:
            plan.append(("Google sign-in", lambda: (OFF, "Not set up, so not checked.")))
        plan.append(("Email server", lambda: self._email(mailer)))
        return plan

    @staticmethod
    def _accounts_store(root: Path) -> Outcome:
        store = AccountStore(root, environ={}, mirror_audit=False)
        try:
            healthy = store.healthy()
        except (OSError, sqlite3.Error) as error:
            return PROBLEM, f"Could not be read ({_failure(error)})."
        finally:
            store.close()
        if healthy:
            return OK, "Readable, complete and passes SQLite's integrity check."
        return PROBLEM, "Missing, damaged or incomplete."

    @staticmethod
    def _databases(root: Path) -> Outcome:
        center = DataCenter(root=root)
        try:
            report = center.status()
        except (OSError, sqlite3.Error) as error:
            return PROBLEM, f"Could not be read ({_failure(error)})."
        finally:
            center.close()
        unhealthy = [database["key"] for database in report["databases"] if not database["healthy"]]
        if not unhealthy:
            return OK, f"All {len(DATABASES)} are provisioned and pass SQLite's integrity check."
        return PROBLEM, (f"{len(DATABASES) - len(unhealthy)} of {len(DATABASES)} healthy. "
                         f"Not healthy: {', '.join(unhealthy)}.")

    def _internet(self) -> Outcome:
        each = self.timeout / max(1, len(self.internet))
        failures = []
        for host, port in self.internet:
            try:
                self.probes.tcp(host, port, each)
            except OSError as error:
                failures.append(_failure(error))
                continue
            return OK, f"Opened a connection to {host} on port {port}."
        targets = " or ".join(host for host, _ in self.internet)
        return PROBLEM, f"Could not open a connection to {targets} on port 443 ({', '.join(failures)})."

    def _dns(self) -> Outcome:
        for name in self.dns_names:
            try:
                self.probes.resolve(name, self.timeout)
            except OSError as error:
                return PROBLEM, f"Could not look up {name} ({_failure(error)})."
        return OK, f"Looked up {', '.join(self.dns_names)}."

    def _google(self) -> Outcome:
        each = self.timeout / max(1, len(GOOGLE_HOSTS))
        for host in GOOGLE_HOSTS:
            try:
                self.probes.tls(host, 443, each)
            except (OSError, ssl.SSLError) as error:
                return PROBLEM, f"Could not reach {host} over verified TLS ({_failure(error)})."
        return OK, f"Reached {' and '.join(GOOGLE_HOSTS)} over TLS with valid certificates."

    def _email(self, mailer: Any) -> Outcome:
        if mailer is None:
            return OFF, "Email is not set up, so sign-in is off. The owner's security checklist says what is missing."
        if isinstance(mailer, OutboxMailer):
            return OFF, "Development mode: messages are written to files, so there is no server to reach."
        probe = getattr(mailer, "probe", None)
        if not callable(probe):
            return OFF, "This kind of mailer cannot be checked."
        try:
            probe(self.timeout)
        except (MailError, OSError):
            # MailError's text names the host; the page is for every member.
            return PROBLEM, "The email server did not answer. Sign-in codes cannot be sent until it does."
        return OK, "The email server answered (connect and EHLO). Nothing was sent."

    # -- running them -------------------------------------------------------------

    def _run(self, plan: list[tuple[str, Callable[[], Outcome]]]) -> list[CheckResult]:
        checked_at = self._now().astimezone(timezone.utc).replace(microsecond=0)
        running = []
        for name, check in plan:
            box: dict[str, Any] = {}
            thread = threading.Thread(target=_capture, args=(check, box), name="portal-check", daemon=True)
            thread.start()
            running.append((name, thread, box))

        # Real time, whatever clock the cache uses: this is what bounds the page.
        deadline = time.monotonic() + self.timeout + _GRACE
        results = []
        for name, thread, box in running:
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                outcome: Outcome = (PROBLEM, f"No answer within {self.timeout:g} seconds.")
            elif "error" in box:
                outcome = (PROBLEM, f"The check failed ({_failure(box['error'])}).")
            else:
                outcome = box["outcome"]
            results.append(CheckResult(name, outcome[0], outcome[1], checked_at))
        return results


def _capture(check: Callable[[], Outcome], box: dict[str, Any]) -> None:
    try:
        box["outcome"] = check()
    except Exception as error:  # reported as a problem, never raised into the page
        box["error"] = error
