"""The live portal's sign-in, over HTTP.

Each test runs a real server on 127.0.0.1 and drives it with http.client.
Mail goes to an outbox directory, Google is faked at its token endpoint, and
the accounts store's clock can be moved, so a four-month block can be tested
in a second. Every reply and every log line is kept, and one test checks
that no password, code, backup code, session token or OAuth secret ever
appears in any of them.
"""

from __future__ import annotations

import base64
import email
import email.policy
import http.client
import io
import json
import logging
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlencode, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datacenter import DATA_DIR, DataCenter, load_all  # noqa: E402
from datacenter.accounts import AccountStore, add_months  # noqa: E402
from datacenter.portal import WEB_ROUTES, build_server  # noqa: E402
from datacenter.portal_web import (  # noqa: E402
    CONTENT_SECURITY_POLICY,
    MEMBER as MEMBER_ACCESS,
    OWNER as OWNER_ACCESS,
    Route,
    client_ip,
    network_allowed,
    parse_networks,
    safe_next,
)

OWNER = "owner@example.com"
MEMBER = "pat@example.com"
GOOGLE_PERSON = "gale@example.com"
OWNER_PASSWORD = "owner horse battery staple"
PASSWORD = "correct horse battery"
NEW_PASSWORD = "a brand new passphrase"
CLIENT_ID = "1234-test.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-test-client-secret-0123456789"
AUTH_CODE = "4/0AauthorizationCodeFromGoogle-abcdefghijklmnop"
FAST = 1000  # PBKDF2 iterations; the real count is covered in test_accounts

SESSION = "sddu13_session"
PREAUTH = "sddu13_preauth"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(claims: dict) -> str:
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    return f"{header}.{b64url(json.dumps(claims).encode())}.signature"


class FakeGoogle:
    """Google's token endpoint: answers with an ID token for whoever it is told."""

    def __init__(self) -> None:
        self.nonce = ""
        self.sub = "google-sub-0001"
        self.email = GOOGLE_PERSON
        self.name = "Gale Google"
        self.requests: list[dict] = []

    def __call__(self, url: str, form: dict, timeout: float) -> dict:
        self.requests.append(dict(form))
        claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "azp": CLIENT_ID,
                  "sub": self.sub, "email": self.email, "email_verified": True, "name": self.name,
                  "exp": time.time() + 3600, "iat": time.time(), "nonce": self.nonce}
        return {"access_token": "ya29.access-token-secret", "id_token": make_token(claims)}


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


@dataclass
class Reply:
    status: int
    headers: http.client.HTTPMessage
    body: str

    @property
    def location(self) -> str:
        return self.headers.get("Location", "")


_CSRF = re.compile(r'name="csrf" value="([^"]+)"')


class Browser:
    """A cookie jar that talks to the test server. Not a real browser: it
    keeps Secure cookies over HTTP and sends Origin only when told to."""

    def __init__(self, case: "PortalCase") -> None:
        self.case = case
        self.cookies: dict[str, str] = {}

    def request(self, method: str, path: str, form: dict | None = None, *,
                headers: dict | None = None, origin: bool = True, raw: bytes | None = None) -> Reply:
        sent = {}
        if self.cookies:
            sent["Cookie"] = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
        body = raw
        if form is not None:
            body = urlencode(form).encode("utf-8")
        if body is not None:
            sent["Content-Type"] = "application/x-www-form-urlencoded"
            if origin:
                sent["Origin"] = self.case.origin
        sent.update(headers or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.case.port, timeout=15)
        try:
            connection.request(method, path, body=body, headers=sent)
            response = connection.getresponse()
            reply = Reply(response.status, response.headers, response.read().decode("utf-8"))
        finally:
            connection.close()
        for header in reply.headers.get_all("Set-Cookie") or []:
            name, _, rest = header.partition("=")
            value = rest.split(";", 1)[0]
            self.case.cookie_values.add(value)
            if "Max-Age=0" in header:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        self.case.replies.append(reply)
        return reply

    def get(self, path: str, **options) -> Reply:
        return self.request("GET", path, **options)

    def post(self, path: str, form: dict, **options) -> Reply:
        return self.request("POST", path, form, **options)

    def csrf(self, path: str) -> str:
        reply = self.get(path)
        if reply.status == 303 and reply.location.startswith("/"):
            reply = self.get(reply.location)
        found = _CSRF.search(reply.body)
        assert found, f"no form token on {path} ({reply.status})"
        return found.group(1).replace("&#x27;", "'")

    def submit(self, page: str, action: str, fields: dict, **options) -> Reply:
        """Load a page for its form token, then post the form."""
        return self.post(action, {**fields, "csrf": self.csrf(page)}, **options)


class PortalCase(unittest.TestCase):
    """A seeded data center copied per test, a server, an outbox and a clock."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._template = tempfile.TemporaryDirectory()
        cls.template_root = Path(cls._template.name) / "storage"
        center = DataCenter(root=cls.template_root)
        center.provision()
        load_all(center, DATA_DIR)
        center.close()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._template.cleanup()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "storage"
        shutil.copytree(self.template_root, self.root)
        self.outbox = Path(self._tmp.name) / "outbox"
        self.clock = Clock(datetime.now(timezone.utc).replace(microsecond=0))
        self.google = FakeGoogle()
        self.replies: list[Reply] = []
        self.cookie_values: set[str] = set()
        self.passwords = {OWNER_PASSWORD, PASSWORD, NEW_PASSWORD}

        # Everything the server logs, by either route, is kept for inspection.
        self.log = io.StringIO()
        stderr = mock.patch.object(sys, "stderr", self.log)
        stderr.start()
        self.addCleanup(stderr.stop)
        handler = logging.StreamHandler(self.log)
        package = logging.getLogger("datacenter")
        previous = package.level
        package.setLevel(logging.DEBUG)
        package.addHandler(handler)
        self.addCleanup(package.removeHandler, handler)
        self.addCleanup(package.setLevel, previous)

        self.server = None
        self.start_server()

    def tearDown(self) -> None:
        self.stop_server()
        for thread in threading.enumerate():
            if thread.name == "portal-mail":
                thread.join(10)
        self._tmp.cleanup()

    def environ(self, **extra: str) -> dict[str, str]:
        environ = {"SDDU13_OWNER_EMAIL": OWNER, "SDDU13_GOOGLE_CLIENT_ID": CLIENT_ID,
                   "SDDU13_GOOGLE_CLIENT_SECRET": CLIENT_SECRET, "SDDU13_PUBLIC_URL": "http://localhost"}
        environ.update(extra)
        return environ

    def start_server(self, **options) -> None:
        self.stop_server()
        settings = dict(environ=self.environ(), mail_outbox=self.outbox, google_http_post=self.google,
                        clock=self.clock, password_iterations=FAST)
        settings.update(options)
        self.server = build_server(self.root, host="127.0.0.1", port=0, quiet=False, **settings)
        self.port = self.server.server_address[1]
        self.origin = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop_server(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.thread.join(timeout=5)
            self.server.server_close()
            self.server = None

    # -- mail -------------------------------------------------------------------

    def mails(self, to: str | None = None) -> list[email.message.EmailMessage]:
        if not self.outbox.exists():
            return []
        found = [email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
                 for path in sorted(self.outbox.glob("*.eml"))]
        return [message for message in found if to is None or message["To"] == to]

    def count(self, to: str) -> int:
        return len(self.mails(to))

    def next_mail(self, to: str, before: int, timeout: float = 10.0) -> email.message.EmailMessage:
        """The newest mail to `to` once there are more than `before` (some go
        out on a background thread)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            mails = self.mails(to)
            if len(mails) > before:
                return mails[-1]
            time.sleep(0.02)
        self.fail(f"no new mail to {to}")

    @staticmethod
    def code_in(message: email.message.EmailMessage) -> str:
        found = re.search(r"^ {4}(\d{6})$", message.get_content(), re.MULTILINE)
        assert found, "no code in the mail"
        return found.group(1)

    @staticmethod
    def backup_codes_in(message: email.message.EmailMessage) -> list[str]:
        return re.findall(r"^ {4}([A-Z0-9]{4}-[A-Z0-9]{4})$", message.get_content(), re.MULTILINE)

    def backup_codes(self, to: str) -> list[str]:
        sets = [codes for codes in map(self.backup_codes_in, self.mails(to)) if codes]
        self.assertTrue(sets, f"no backup codes mailed to {to}")
        return sets[-1]

    # -- people -----------------------------------------------------------------

    def account_id(self, address: str) -> int:
        store = AccountStore(self.root, environ=self.environ(), mirror_audit=False)
        try:
            account = store.find_account(address)
        finally:
            store.close()
        self.assertIsNotNone(account, address)
        return account.id

    def account_status(self, address: str) -> str:
        store = AccountStore(self.root, environ=self.environ(), mirror_audit=False)
        try:
            return store.find_account(address).status
        finally:
            store.close()

    def register(self, browser: Browser, address: str, password: str, name: str = "Pat Person") -> Reply:
        """Ask for access with a password and confirm the address from the outbox."""
        before = self.count(address)
        reply = browser.submit("/request-access", "/request-access", {
            "name": name, "email": address, "company": "Acme", "reason": "Quarterly reports",
            "password": password, "confirm": password})
        self.assertEqual(reply.status, 200, reply.body)
        self.assertIn("Check your email", reply.body)
        code = self.code_in(self.next_mail(address, before))
        token = _CSRF.search(reply.body).group(1)
        return browser.post("/request-access/confirm",
                            {"email": address, "code": code, "password": password, "csrf": token})

    def first_factor(self, browser: Browser, address: str, password: str, next_path: str = "") -> Reply:
        page = "/sign-in" + (f"?next={next_path}" if next_path else "")
        fields = {"email": address, "password": password}
        if next_path:
            fields["next"] = next_path
        return browser.submit(page, "/sign-in", fields)

    def sign_in(self, browser: Browser, address: str, password: str, *, code: str | None = None) -> Reply:
        before = self.count(address)
        reply = self.first_factor(browser, address, password)
        self.assertEqual(reply.status, 303, reply.body)
        self.assertTrue(reply.location.startswith("/sign-in/code"), reply.location)
        if code is None:
            code = self.code_in(self.next_mail(address, before))
        return browser.submit("/sign-in/code", "/sign-in/code", {"code": code})

    def owner(self) -> Browser:
        browser = Browser(self)
        reply = self.register(browser, OWNER, OWNER_PASSWORD, name="Olive Owner")
        self.assertEqual(reply.status, 303)
        self.assertTrue(reply.location.startswith("/?") or reply.location == "/", reply.location)
        return browser

    def owner_action(self, owner: Browser, address: str, action: str, back: str = "/owner/members",
                     **fields: str) -> Reply:
        return owner.submit(back, f"/owner/accounts/{self.account_id(address)}/{action}",
                            {"back": back, **fields})

    def member(self, owner: Browser, address: str = MEMBER, name: str = "Pat Person") -> Browser:
        browser = Browser(self)
        reply = self.register(browser, address, PASSWORD, name=name)
        self.assertTrue(reply.location.startswith("/status"), reply.location)
        approved = self.owner_action(owner, address, "approve", back="/owner/requests")
        self.assertEqual(approved.status, 303, approved.body)
        return browser

    def assertSignedOut(self, browser: Browser) -> None:  # noqa: N802 - unittest style
        reply = browser.get("/")
        self.assertEqual(reply.status, 303)
        self.assertEqual(reply.location, "/sign-in")


# -- asking for access ----------------------------------------------------------

class TestRequestAccess(PortalCase):
    def test_a_password_request_is_confirmed_by_email_then_approved_by_the_owner(self) -> None:
        owner = self.owner()
        self.assertTrue(self.backup_codes(OWNER), "the owner's first sign-in sends backup codes")

        pat = Browser(self)
        reply = self.register(pat, MEMBER, PASSWORD)
        self.assertTrue(reply.location.startswith("/status"))
        self.assertEqual(pat.get("/documents").location, "/status")
        status = pat.get("/status")
        self.assertIn("Your request is with the owner", status.body)
        self.assertNotIn('href="/documents"', status.body, "pending people see their status and nothing else")

        requests = owner.get("/owner/requests")
        self.assertIn(MEMBER, requests.body)
        self.assertIn("Quarterly reports", requests.body)
        approved = self.owner_action(owner, MEMBER, "approve", back="/owner/requests")
        self.assertEqual(approved.location, "/owner/requests?notice=approved")

        overview = pat.get("/")
        self.assertEqual(overview.status, 200)
        self.assertIn("Seven databases", overview.body)

    def test_the_owner_is_approved_automatically_but_still_confirms_the_address(self) -> None:
        browser = Browser(self)
        reply = browser.submit("/request-access", "/request-access", {
            "name": "Olive", "email": OWNER, "password": OWNER_PASSWORD, "confirm": OWNER_PASSWORD})
        self.assertIn("Check your email", reply.body)
        self.assertSignedOut(browser)
        self.assertEqual(self.account_status(OWNER), "approved")

    def test_a_taken_address_gets_the_same_page_and_its_holder_an_email(self) -> None:
        owner = self.owner()
        self.member(owner)
        before = self.count(MEMBER)
        fresh = Browser(self).submit("/request-access", "/request-access", {
            "name": "Someone", "email": "free@example.com", "password": PASSWORD, "confirm": PASSWORD})
        taken = Browser(self).submit("/request-access", "/request-access", {
            "name": "Someone", "email": MEMBER, "password": PASSWORD, "confirm": PASSWORD})

        def scrub(reply: Reply) -> str:
            return _CSRF.sub("", reply.body).replace("free@example.com", "X").replace(MEMBER, "X")

        self.assertEqual((fresh.status, scrub(fresh)), (taken.status, scrub(taken)))
        notice = self.next_mail(MEMBER, before)
        self.assertIn("already has an account", notice.get_content())

    def test_an_unconfirmed_request_is_not_shown_to_the_owner(self) -> None:
        owner = self.owner()
        Browser(self).submit("/request-access", "/request-access", {
            "name": "Quiet", "email": "quiet@example.com", "password": PASSWORD, "confirm": PASSWORD})
        self.assertNotIn("quiet@example.com", owner.get("/owner/requests").body)

    def test_a_wrong_confirmation_code_is_refused(self) -> None:
        browser = Browser(self)
        reply = browser.submit("/request-access", "/request-access", {
            "name": "Pat", "email": MEMBER, "password": PASSWORD, "confirm": PASSWORD})
        code = self.code_in(self.next_mail(MEMBER, 0))
        wrong = "000000" if code != "000000" else "111111"
        reply = browser.post("/request-access/confirm", {"email": MEMBER, "code": wrong, "password": PASSWORD,
                                                         "csrf": _CSRF.search(reply.body).group(1)})
        self.assertEqual(reply.status, 400)
        self.assertIn("That code is not right", reply.body)
        self.assertSignedOut(browser)

    def test_mismatched_and_short_passwords_are_refused(self) -> None:
        browser = Browser(self)
        reply = browser.submit("/request-access", "/request-access", {
            "name": "Pat", "email": MEMBER, "password": PASSWORD, "confirm": PASSWORD + "x"})
        self.assertIn("not the same", reply.body)
        reply = browser.submit("/request-access", "/request-access", {
            "name": "Pat", "email": MEMBER, "password": "too short", "confirm": "too short"})
        self.assertEqual(reply.status, 400)
        self.assertIn("at least 12 characters", reply.body)
        self.assertEqual(self.count(MEMBER), 0)


# -- Google ---------------------------------------------------------------------

class TestGoogle(PortalCase):
    def start_google(self, browser: Browser) -> str:
        reply = browser.get("/auth/google/start")
        self.assertEqual(reply.status, 303)
        target = urlsplit(reply.location)
        self.assertEqual(f"{target.scheme}://{target.netloc}{target.path}",
                         "https://accounts.google.com/o/oauth2/v2/auth")
        query = {key: values[0] for key, values in parse_qs(target.query).items()}
        self.assertEqual(query["code_challenge_method"], "S256")
        self.assertEqual(query["redirect_uri"], "http://localhost/auth/google/callback")
        self.assertIn("sddu13_google", browser.cookies)
        self.google.nonce = query["nonce"]
        return query["state"]

    def callback(self, browser: Browser, state: str) -> Reply:
        return browser.get(f"/auth/google/callback?{urlencode({'state': state, 'code': AUTH_CODE})}")

    def test_asking_for_access_with_google(self) -> None:
        owner = self.owner()
        gale = Browser(self)
        reply = self.callback(gale, self.start_google(gale))
        self.assertEqual(reply.status, 200, reply.body)
        self.assertIn(GOOGLE_PERSON, reply.body)
        self.assertIn("Gale Google", reply.body)
        sent = self.google.requests[-1]
        self.assertEqual(sent["code"], AUTH_CODE)
        self.assertEqual(sent["client_secret"], CLIENT_SECRET)
        self.assertTrue(sent["code_verifier"])

        before = self.count(GOOGLE_PERSON)
        reply = gale.post("/auth/google/request", {
            "name": "Gale", "company": "Gusts Ltd", "reason": "Forecasts",
            "csrf": _CSRF.search(reply.body).group(1)})
        self.assertEqual(reply.location, "/sign-in/code")
        code = self.code_in(self.next_mail(GOOGLE_PERSON, before))
        reply = gale.submit("/sign-in/code", "/sign-in/code", {"code": code})
        self.assertTrue(reply.location.startswith("/status"), reply.location)
        requests = owner.get("/owner/requests").body
        self.assertIn(GOOGLE_PERSON, requests)
        self.assertIn("Google", requests)

    def test_google_signs_in_an_existing_account_and_still_asks_for_the_code(self) -> None:
        owner = self.owner()
        self.member(owner)
        self.google.email = MEMBER
        browser = Browser(self)
        before = self.count(MEMBER)
        reply = self.callback(browser, self.start_google(browser))
        self.assertEqual(reply.location, "/sign-in/code")
        self.assertEqual(browser.get("/documents").location, "/sign-in/code", "one step is not enough")
        code = self.code_in(self.next_mail(MEMBER, before))
        reply = browser.submit("/sign-in/code", "/sign-in/code", {"code": code})
        self.assertEqual(reply.status, 303)
        self.assertEqual(browser.get("/documents").status, 200)

    def test_a_callback_works_once(self) -> None:
        browser = Browser(self)
        state = self.start_google(browser)
        flow = browser.cookies["sddu13_google"]
        self.assertEqual(self.callback(browser, state).status, 200)
        browser.cookies["sddu13_google"] = flow
        replay = self.callback(browser, state)
        self.assertEqual(replay.status, 400)
        self.assertIn("expired or was already used", replay.body)

    def test_a_callback_from_another_browser_is_refused(self) -> None:
        state = self.start_google(Browser(self))
        other = Browser(self)
        self.start_google(other)
        reply = self.callback(other, state)
        self.assertEqual(reply.status, 400)
        self.assertEqual(self.google.requests, [], "Google is never asked with the wrong state")

    def test_the_request_form_cannot_be_forged_without_google(self) -> None:
        browser = Browser(self)
        browser.cookies["sddu13_google"] = "A" * 43 + b64url(json.dumps(
            {"sub": "x", "email": "forged@example.com", "name": "F", "exp": 9999999999}).encode())
        reply = browser.submit("/sign-in", "/auth/google/request", {"name": "Forged"})
        self.assertEqual(reply.status, 400)
        self.assertEqual(self.count("forged@example.com"), 0)

    def test_without_google_settings_the_button_is_hidden(self) -> None:
        self.start_server(environ={"SDDU13_OWNER_EMAIL": OWNER})
        page = Browser(self).get("/sign-in")
        self.assertIn("Google sign-in is not set up yet", page.body)
        self.assertNotIn("/auth/google/start", page.body)
        self.assertEqual(Browser(self).get("/auth/google/start").status, 404)


# -- the second step ------------------------------------------------------------

class TestSecondStep(PortalCase):
    def test_a_backup_code_works_once_in_place_of_the_emailed_code(self) -> None:
        self.owner()
        codes = self.backup_codes(OWNER)
        self.assertEqual(len(codes), 10)
        browser = Browser(self)
        reply = self.sign_in(browser, OWNER, OWNER_PASSWORD, code=codes[0].lower().replace("-", " "))
        self.assertEqual(reply.status, 303)
        self.assertEqual(browser.get("/owner").status, 200)

        again = Browser(self)
        reply = self.sign_in(again, OWNER, OWNER_PASSWORD, code=codes[0])
        self.assertEqual(reply.status, 400)
        self.assertIn("That code is not right", reply.body)
        reply = again.submit("/sign-in/code", "/sign-in/code", {"code": codes[1]})
        self.assertEqual(reply.status, 303)

    def test_five_wrong_codes_lock_the_code_until_a_new_one_is_sent(self) -> None:
        self.owner()
        browser = Browser(self)
        before = self.count(OWNER)
        self.first_factor(browser, OWNER, OWNER_PASSWORD)
        code = self.code_in(self.next_mail(OWNER, before))
        wrong = "000000" if code != "000000" else "111111"
        for attempt in range(5):
            reply = browser.submit("/sign-in/code", "/sign-in/code", {"code": wrong})
            self.assertEqual(reply.status, 400)
        self.assertIn("Too many wrong tries", reply.body)
        reply = browser.submit("/sign-in/code", "/sign-in/code", {"code": code})
        self.assertIn("Too many wrong tries", reply.body, "the right code no longer works either")

        before = self.count(OWNER)
        reply = browser.submit("/sign-in/code", "/sign-in/resend", {})
        self.assertEqual(reply.location, "/sign-in/code?notice=code-sent")
        fresh = self.code_in(self.next_mail(OWNER, before))
        self.assertEqual(browser.submit("/sign-in/code", "/sign-in/code", {"code": fresh}).status, 303)

    def test_an_emailed_code_expires_after_ten_minutes(self) -> None:
        self.owner()
        browser = Browser(self)
        before = self.count(OWNER)
        self.first_factor(browser, OWNER, OWNER_PASSWORD)
        code = self.code_in(self.next_mail(OWNER, before))
        self.clock.advance(minutes=11)
        reply = browser.submit("/sign-in/code", "/sign-in/code", {"code": code})
        self.assertIn("That code has expired", reply.body)

    def test_data_pages_need_both_steps_and_an_approved_account(self) -> None:
        anonymous = Browser(self)
        self.assertEqual(anonymous.get("/documents").location, "/sign-in?next=%2Fdocuments")
        self.assertEqual(anonymous.get("/documents/1").location, "/sign-in?next=%2Fdocuments%2F1")

        owner = self.owner()
        pat = Browser(self)
        self.register(pat, MEMBER, PASSWORD)
        self.assertEqual(pat.get("/fees").location, "/status", "pending")
        self.owner_action(owner, MEMBER, "approve", back="/owner/requests")

        later = Browser(self)
        before = self.count(MEMBER)
        reply = self.first_factor(later, MEMBER, PASSWORD, next_path="/plans")
        self.assertEqual(reply.location, "/sign-in/code?next=%2Fplans")
        self.assertEqual(later.get("/plans").location, "/sign-in/code", "the first step alone shows nothing")
        reply = later.submit("/sign-in/code?next=%2Fplans", "/sign-in/code",
                             {"code": self.code_in(self.next_mail(MEMBER, before)), "next": "/plans"})
        self.assertEqual(reply.location, "/plans")
        for path in ("/", "/search?q=leave", "/documents", "/contacts", "/datasets", "/fees", "/plans",
                     "/events", "/plans/year-one-operating"):
            with self.subTest(path=path):
                self.assertEqual(later.get(path).status, 200)

    def test_sign_in_never_redirects_off_the_site(self) -> None:
        for target in ("//evil.example/", "https://evil.example/", "/\\evil.example", "/ok\r\nX: y"):
            with self.subTest(target=target):
                self.assertEqual(safe_next(target), "/")
        self.assertEqual(safe_next("/documents/3"), "/documents/3")

    def test_a_password_typed_into_the_email_box_is_not_shown_back(self) -> None:
        self.owner()
        for page, action, fields in (
                ("/sign-in", "/sign-in", {"password": "x"}),
                ("/forgot-password", "/forgot-password", {}),
                ("/reset-password", "/reset-password", {"code": "1", "password": "x", "confirm": "y"}),
                ("/request-access", "/request-access", {"name": "Pat", "password": "x", "confirm": "y"})):
            with self.subTest(action=action):
                reply = Browser(self).submit(page, action, {"email": OWNER_PASSWORD, **fields})
                self.assertNotIn(OWNER_PASSWORD, reply.body)
        reply = self.first_factor(Browser(self), "Owner@Example.com", "wrong password here")
        self.assertIn('value="Owner@Example.com"', reply.body, "a real address is kept for retyping")

    def test_a_wrong_password_and_an_unknown_address_read_the_same(self) -> None:
        self.owner()
        wrong = self.first_factor(Browser(self), OWNER, "not the password at all")
        unknown = self.first_factor(Browser(self), "nobody@example.com", "not the password at all")
        self.assertEqual(wrong.status, 401)
        self.assertEqual(unknown.status, 401)
        self.assertIn("That email and password do not match.", wrong.body)
        self.assertEqual(_CSRF.sub("", wrong.body).replace(OWNER, "X"),
                         _CSRF.sub("", unknown.body).replace("nobody@example.com", "X"))


# -- throttling -----------------------------------------------------------------

class TestThrottling(PortalCase):
    def test_password_attempts_are_limited_to_ten_per_quarter_hour(self) -> None:
        self.owner()
        self.clock.advance(minutes=16)  # a fresh window: registering counted too
        browser = Browser(self)
        for attempt in range(10):
            self.assertEqual(self.first_factor(browser, OWNER, f"wrong password {attempt:02d}").status, 401)
        reply = self.first_factor(browser, OWNER, OWNER_PASSWORD)
        self.assertEqual(reply.status, 429)
        self.assertIn("Too many attempts", reply.body)
        self.assertTrue(reply.headers["Retry-After"])
        self.clock.advance(minutes=16)
        self.assertEqual(self.first_factor(browser, OWNER, OWNER_PASSWORD).status, 303)

    def test_anonymous_probing_is_throttled_not_blocked(self) -> None:
        browser = Browser(self)
        for _ in range(60):
            self.assertEqual(browser.get("/owner").status, 303)
        reply = browser.get("/owner")
        self.assertEqual(reply.status, 429)
        self.assertEqual(browser.get("/sign-in").status, 200, "the sign-in page itself stays open")


# -- forms ----------------------------------------------------------------------

class TestForms(PortalCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner_browser = self.owner()
        self.pat = self.member(self.owner_browser)
        self.pat_id = self.account_id(MEMBER)

    def rename(self, browser: Browser, fields: dict, **options) -> Reply:
        return browser.post(f"/account/{self.pat_id}/name", fields, **options)

    def name_of(self, address: str) -> str:
        store = AccountStore(self.root, environ=self.environ(), mirror_audit=False)
        try:
            return store.find_account(address).display_name
        finally:
            store.close()

    def test_a_post_without_the_form_token_is_refused(self) -> None:
        reply = self.rename(self.pat, {"name": "Changed", "account": str(self.pat_id)})
        self.assertEqual(reply.status, 403)
        self.assertIn("This form has expired", reply.body)
        self.assertEqual(self.name_of(MEMBER), "Pat Person")
        self.assertEqual(self.account_status(MEMBER), "approved", "a refused form is never a strike")

    def test_another_sessions_token_is_refused(self) -> None:
        stolen = self.owner_browser.csrf("/owner")
        reply = self.rename(self.pat, {"name": "Changed", "account": str(self.pat_id), "csrf": stolen})
        self.assertEqual(reply.status, 403)
        self.assertEqual(self.name_of(MEMBER), "Pat Person")

    def test_a_cross_origin_post_is_refused_even_with_a_good_token(self) -> None:
        token = self.pat.csrf("/account")
        fields = {"name": "Changed", "account": str(self.pat_id), "csrf": token}
        reply = self.rename(self.pat, fields, headers={"Origin": "http://evil.example"})
        self.assertEqual(reply.status, 403)
        self.assertIn("did not come from this portal", reply.body)
        self.assertEqual(self.rename(self.pat, fields, origin=False).status, 403, "no Origin and no Referer")
        self.assertEqual(self.name_of(MEMBER), "Pat Person")
        reply = self.rename(self.pat, fields, origin=False, headers={"Referer": f"{self.origin}/account"})
        self.assertEqual(reply.status, 303, "a same-origin Referer will do")
        self.assertEqual(self.name_of(MEMBER), "Changed")

    def test_a_forged_post_to_an_owner_action_does_not_block_the_member(self) -> None:
        reply = self.pat.post(f"/owner/accounts/{self.pat_id}/approve", {"back": "/owner"},
                              headers={"Origin": "http://evil.example"})
        self.assertEqual(reply.status, 403)
        self.assertEqual(self.account_status(MEMBER), "approved")
        self.assertEqual(self.pat.get("/").status, 200)

    def test_bodies_are_limited_and_must_be_plain_forms(self) -> None:
        token = self.pat.csrf("/account")
        big = urlencode({"name": "x" * 70_000, "csrf": token}).encode()
        self.assertEqual(self.pat.request("POST", f"/account/{self.pat_id}/name", raw=big).status, 413)
        reply = self.pat.request("POST", f"/account/{self.pat_id}/name", raw=b"{}",
                                 headers={"Content-Type": "application/json"})
        self.assertEqual(reply.status, 415)
        doubled = f"csrf={token}&csrf={token}&name=Two&account={self.pat_id}".encode()
        self.assertEqual(self.pat.request("POST", f"/account/{self.pat_id}/name", raw=doubled).status, 400)
        self.assertEqual(self.name_of(MEMBER), "Pat Person")


# -- strikes and automatic blocks -----------------------------------------------

class TestStrikes(PortalCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner_browser = self.owner()
        self.pat = self.member(self.owner_browser)
        self.pat_id = self.account_id(MEMBER)
        self.owner_mail = self.count(OWNER)

    def assertBlockedNotice(self, reply: Reply) -> None:  # noqa: N802
        self.assertEqual(reply.status, 403)
        self.assertIn("This account is blocked", reply.body)
        self.assertIn("Review date: ", reply.body)
        self.assertNotIn('href="/documents"', reply.body)

    def assertOwnerAlerted(self) -> None:  # noqa: N802
        alert = self.next_mail(OWNER, self.owner_mail)
        self.assertIn("blocked automatically", alert["Subject"])
        self.assertIn(MEMBER, alert.get_content())

    def test_owner_pages_give_strikes_and_the_third_blocks(self) -> None:
        first = self.pat.get("/owner")
        self.assertEqual(first.status, 403)
        self.assertIn("The request has been recorded", first.body)
        self.assertEqual(self.pat.get("/owner/members").status, 403)
        self.assertEqual(self.account_status(MEMBER), "approved")
        self.assertBlockedNotice(self.pat.get("/owner/blocked"))
        self.assertEqual(self.account_status(MEMBER), "blocked")
        self.assertOwnerAlerted()
        self.assertSignedOut(self.pat)

    def test_posting_an_owner_action_blocks_at_once_and_emails_the_owner(self) -> None:
        owner_id = self.account_id(OWNER)
        reply = self.pat.submit("/account", f"/owner/accounts/{owner_id}/block", {"reason": "mine now"})
        self.assertBlockedNotice(reply)
        self.assertEqual(self.account_status(MEMBER), "blocked")
        self.assertEqual(self.account_status(OWNER), "approved")
        self.assertOwnerAlerted()
        self.assertSignedOut(self.pat)

    def test_a_form_changed_to_act_on_someone_else_blocks_at_once(self) -> None:
        owner_id = self.account_id(OWNER)
        reply = self.pat.submit("/account", f"/account/{self.pat_id}/name",
                                {"name": "Hijack", "account": str(owner_id)})
        self.assertBlockedNotice(reply)
        self.assertOwnerAlerted()
        self.assertEqual(self.owner_browser.get(f"/account/{owner_id}").status, 200)
        self.assertIn("Olive Owner", self.owner_browser.get(f"/account/{owner_id}").body)

    def test_a_tampered_account_path_on_a_post_blocks_at_once(self) -> None:
        owner_id = self.account_id(OWNER)
        reply = self.pat.submit("/account", f"/account/{owner_id}/sign-out-everywhere",
                                {"account": str(self.pat_id)})
        self.assertBlockedNotice(reply)
        self.assertEqual(self.owner_browser.get("/owner").status, 200, "the owner was not signed out")

    def test_another_persons_account_page_is_a_strike(self) -> None:
        owner_id = self.account_id(OWNER)
        self.assertEqual(self.pat.get(f"/account/{owner_id}").status, 403)
        self.assertEqual(self.pat.get(f"/account/{self.pat_id}").status, 200)
        self.assertEqual(self.owner_browser.get(f"/account/{self.pat_id}").status, 404,
                         "the owner uses the owner center, not other people's pages")
        store = AccountStore(self.root, environ=self.environ(), mirror_audit=False)
        try:
            self.assertEqual(len(store.account_strikes(self.pat_id)), 1)
        finally:
            store.close()

    def test_links_from_another_site_are_not_strikes(self) -> None:
        for _ in range(4):
            reply = self.pat.get("/owner", headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual(reply.status, 403)
        self.assertEqual(self.account_status(MEMBER), "approved")
        self.assertEqual(self.pat.get("/").status, 200)

    def test_the_owner_is_never_struck_and_cannot_be_blocked(self) -> None:
        owner_id = self.account_id(OWNER)
        reply = self.owner_browser.submit("/owner/members", f"/owner/accounts/{owner_id}/block",
                                          {"reason": "test", "back": "/owner/members"})
        self.assertEqual(reply.status, 409)
        self.assertIn("never be blocked", reply.body)
        self.assertEqual(self.account_status(OWNER), "approved")

    def test_every_owner_route_is_owner_only_and_lives_under_owner(self) -> None:
        for route in WEB_ROUTES:
            if route.pattern.startswith("/owner"):
                self.assertEqual(route.access, OWNER_ACCESS, route.pattern)
        with self.assertRaises(ValueError):
            Route("GET", "/owner/secret", MEMBER_ACCESS, lambda ctx, match: None)
        with self.assertRaises(ValueError):
            Route("GET", r"/(?P<anything>.+)", MEMBER_ACCESS, lambda ctx, match: None)
        with self.assertRaises(ValueError):
            Route("GET", "/elsewhere", OWNER_ACCESS, lambda ctx, match: None)


# -- blocks the owner places ------------------------------------------------------

class TestBlocking(PortalCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner_browser = self.owner()
        self.pat = self.member(self.owner_browser)

    def block(self) -> Reply:
        return self.owner_action(self.owner_browser, MEMBER, "block", reason="Shared their password")

    def test_a_blocked_member_sees_only_the_notice_and_their_sessions_end(self) -> None:
        second = Browser(self)
        self.sign_in(second, MEMBER, PASSWORD)
        self.assertEqual(second.get("/").status, 200)

        self.assertEqual(self.block().location, "/owner/members?notice=blocked")
        self.assertSignedOut(self.pat)
        self.assertSignedOut(second)

        before = self.count(MEMBER)
        reply = self.first_factor(Browser(self), MEMBER, PASSWORD)
        self.assertEqual(reply.status, 403)
        self.assertIn("This account is blocked", reply.body)
        self.assertIn(f"Review date: {add_months(self.clock.now.date(), 4).isoformat()}", reply.body)
        self.assertNotIn("<nav>", reply.body)
        self.assertEqual(self.count(MEMBER), before, "nothing is mailed to a blocked account")

    def test_unblocking_is_refused_before_the_review_date(self) -> None:
        self.block()
        page = self.owner_browser.get("/owner/blocked")
        self.assertIn("Shared their password", page.body)
        self.assertIn("You can decide on ", page.body)
        self.assertNotIn("/unblock", page.body)

        reply = self.owner_action(self.owner_browser, MEMBER, "unblock", back="/owner/blocked")
        self.assertEqual(reply.status, 409)
        self.assertIn("You can decide on ", reply.body)
        self.assertEqual(self.account_status(MEMBER), "blocked")
        reply = self.owner_action(self.owner_browser, MEMBER, "approve", back="/owner/blocked")
        self.assertEqual(reply.status, 409, "approving cannot get round the block")

        self.clock.advance(days=124)
        owner = Browser(self)
        self.sign_in(owner, OWNER, OWNER_PASSWORD)
        page = owner.get("/owner/blocked")
        self.assertIn("Keep blocked 4 more months", page.body)
        reply = self.owner_action(owner, MEMBER, "unblock", back="/owner/blocked")
        self.assertEqual(reply.location, "/owner/blocked?notice=unblocked")
        self.assertEqual(self.account_status(MEMBER), "unblocked")

        pat = Browser(self)
        reply = self.sign_in(pat, MEMBER, PASSWORD)
        self.assertTrue(reply.location.startswith("/status"))
        self.assertEqual(pat.get("/documents").location, "/status", "access is not restored")
        self.assertIn("ask again", pat.get("/status").body)
        reply = pat.submit("/status", "/status/request-again", {"reason": "Sorry"})
        self.assertEqual(reply.location, "/status?notice=request-sent")
        self.assertIn(MEMBER, owner.get("/owner/requests").body)

    def test_an_overdue_block_can_be_extended(self) -> None:
        self.block()
        self.clock.advance(days=124)
        owner = Browser(self)
        self.sign_in(owner, OWNER, OWNER_PASSWORD)
        reply = self.owner_action(owner, MEMBER, "extend", back="/owner/blocked")
        self.assertEqual(reply.location, "/owner/blocked?notice=extended")
        self.assertIn("You can decide on ", owner.get("/owner/blocked").body)


# -- the account page -----------------------------------------------------------

class TestAccountPage(PortalCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner_browser = self.owner()
        self.pat = self.member(self.owner_browser)
        self.pat_id = self.account_id(MEMBER)
        self.pat.get("/")  # the first full sign-in already mailed a set

    def account_post(self, browser: Browser, action: str, **fields: str) -> Reply:
        return browser.submit("/account", f"/account/{self.pat_id}/{action}",
                              {"account": str(self.pat_id), **fields})

    def test_the_page_shows_the_person_and_never_a_secret(self) -> None:
        self.assertEqual(self.pat.get("/account").location, f"/account/{self.pat_id}")
        page = self.pat.get(f"/account/{self.pat_id}")
        for expected in ("Pat Person", MEMBER, "approved", "10 unused backup codes",
                         "Email me new backup codes", "Sign out everywhere", "Change password",
                         "Nobody can see a password"):
            self.assertIn(expected, page.body)

    def test_new_backup_codes_replace_the_old_set(self) -> None:
        old = self.backup_codes(MEMBER)
        reply = self.account_post(self.pat, "backup-codes")
        self.assertEqual(reply.location, f"/account/{self.pat_id}?notice=backup-sent")
        new = self.backup_codes(MEMBER)
        self.assertNotEqual(old, new)
        self.assertIn("Print them", self.mails(MEMBER)[-1].get_content())
        self.assertEqual(self.sign_in(Browser(self), MEMBER, PASSWORD, code=old[0]).status, 400)
        self.assertEqual(self.sign_in(Browser(self), MEMBER, PASSWORD, code=new[0]).status, 303)

    def test_changing_the_password_needs_the_current_one(self) -> None:
        reply = self.account_post(self.pat, "password", current="not my password!",
                                  password=NEW_PASSWORD, confirm=NEW_PASSWORD)
        self.assertEqual(reply.status, 400)
        self.assertIn("current password is not right", reply.body)
        other = Browser(self)
        self.sign_in(other, MEMBER, PASSWORD)
        reply = self.account_post(self.pat, "password", current=PASSWORD,
                                  password=NEW_PASSWORD, confirm=NEW_PASSWORD)
        self.assertEqual(reply.location, f"/account/{self.pat_id}?notice=password-changed")
        self.assertEqual(self.pat.get("/").status, 200, "this session stays")
        self.assertSignedOut(other)
        self.assertEqual(self.first_factor(Browser(self), MEMBER, PASSWORD).status, 401)
        self.assertEqual(self.first_factor(Browser(self), MEMBER, NEW_PASSWORD).status, 303)

    def test_sign_out_and_sign_out_everywhere(self) -> None:
        other = Browser(self)
        self.sign_in(other, MEMBER, PASSWORD)
        reply = self.account_post(self.pat, "sign-out-everywhere")
        self.assertEqual(reply.location, "/sign-in?notice=signed-out-everywhere")
        self.assertSignedOut(self.pat)
        self.assertSignedOut(other)

        again = Browser(self)
        self.sign_in(again, MEMBER, PASSWORD)
        reply = again.submit("/", "/sign-out", {"account": str(self.pat_id)})
        self.assertEqual(reply.location, "/sign-in?notice=signed-out")
        self.assertSignedOut(again)

    def test_a_forgotten_password_is_reset_by_email_and_signs_out_everywhere(self) -> None:
        before = self.count(MEMBER)
        stranger = Browser(self)
        reply = stranger.submit("/forgot-password", "/forgot-password", {"email": MEMBER})
        unknown = Browser(self).submit("/forgot-password", "/forgot-password", {"email": "no@example.com"})
        self.assertEqual(_CSRF.sub("", reply.body).replace(MEMBER, "X"),
                         _CSRF.sub("", unknown.body).replace("no@example.com", "X"))
        code = self.code_in(self.next_mail(MEMBER, before))
        token = _CSRF.search(reply.body).group(1)
        reply = stranger.post("/reset-password", {"email": MEMBER, "code": code, "password": NEW_PASSWORD,
                                                  "confirm": NEW_PASSWORD, "csrf": token})
        self.assertEqual(reply.status, 200)
        self.assertIn("Your password is changed", reply.body)
        self.assertSignedOut(self.pat)
        self.assertEqual(self.sign_in(Browser(self), MEMBER, NEW_PASSWORD).status, 303)

    def test_the_owner_can_email_a_reset_code_but_never_sees_it(self) -> None:
        before = self.count(MEMBER)
        reply = self.owner_action(self.owner_browser, MEMBER, "reset-password")
        self.assertEqual(reply.location, "/owner/members?notice=reset-sent")
        code = self.code_in(self.next_mail(MEMBER, before))
        self.assertNotIn(code, self.owner_browser.get("/owner/members").body)
        self.assertNotIn(code, self.owner_browser.get("/owner/activity").body)

    def test_the_owner_can_remove_access_and_sign_someone_out(self) -> None:
        reply = self.owner_action(self.owner_browser, MEMBER, "sign-out-everywhere")
        self.assertEqual(reply.location, "/owner/members?notice=sessions-ended")
        self.assertSignedOut(self.pat)
        self.sign_in(self.pat, MEMBER, PASSWORD)
        reply = self.owner_action(self.owner_browser, MEMBER, "remove")
        self.assertEqual(reply.location, "/owner/members?notice=removed")
        self.assertSignedOut(self.pat)
        self.sign_in(self.pat, MEMBER, PASSWORD)
        self.assertEqual(self.pat.get("/").location, "/status")
        self.assertIn("Your access was removed", self.pat.get("/status").body)


# -- the owner center -----------------------------------------------------------

class TestOwnerCenter(PortalCase):
    def test_every_owner_page_opens_and_says_secrets_are_never_shown(self) -> None:
        owner = self.owner()
        self.member(owner)
        for path in ("/owner", "/owner/requests", "/owner/members", "/owner/blocked", "/owner/activity",
                     "/owner/announcements", "/owner/checklist"):
            with self.subTest(path=path):
                reply = owner.get(path)
                self.assertEqual(reply.status, 200)
                self.assertIn("Nobody can see a password, a security code or a backup code", reply.body)
        self.assertIn('href="/owner"', owner.get("/").body, "the owner's navigation links the center")

    def test_portal_updates_are_posted_and_deleted(self) -> None:
        owner = self.owner()
        reply = owner.submit("/owner/announcements", "/owner/announcements",
                             {"title": "New fees table", "body": "The <b>2027</b> fees are in."})
        self.assertEqual(reply.location, "/owner/announcements?notice=posted")
        page = owner.get("/owner/announcements").body
        self.assertIn("The &lt;b&gt;2027&lt;/b&gt; fees are in.", page)
        item = re.search(r"/owner/announcements/(\d+)/delete", page).group(1)
        reply = owner.submit("/owner/announcements", f"/owner/announcements/{item}/delete", {})
        self.assertEqual(reply.location, "/owner/announcements?notice=deleted")
        self.assertNotIn("New fees table", owner.get("/owner/announcements").body)

    def test_the_checklist_describes_this_setup(self) -> None:
        owner = self.owner()
        body = owner.get("/owner/checklist").body
        for expected in ("Development mode", "Google sign-in", "is not HTTPS", "Network allowlist",
                         "only listens on this machine", "Owner email", "No backup on record"):
            self.assertIn(expected, body)
        with DataCenter(root=self.root) as center:
            center.backup(Path(self._tmp.name) / "backups")
        self.assertIn("Last backup", owner.get("/owner/checklist").body)

    def test_activity_lists_security_events(self) -> None:
        owner = self.owner()
        body = owner.get("/owner/activity").body
        self.assertIn("sign_in", body)
        self.assertIn("account_requested", body)


# -- headers, networks and configuration ---------------------------------------

class TestHeadersAndNetworks(PortalCase):
    def test_every_response_carries_the_security_headers(self) -> None:
        owner = self.owner()
        replies = [Browser(self).get("/sign-in"), Browser(self).get("/healthz"), Browser(self).get("/nope"),
                   Browser(self).get("/documents"), owner.get("/"), owner.get("/owner")]
        for reply in replies:
            with self.subTest(status=reply.status):
                self.assertEqual(reply.headers["Content-Security-Policy"], CONTENT_SECURITY_POLICY)
                self.assertEqual(reply.headers["Content-Security-Policy"],
                                 "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'")
                self.assertEqual(reply.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(reply.headers["Referrer-Policy"], "same-origin")
                self.assertEqual(reply.headers["X-Frame-Options"], "DENY")
                self.assertEqual(reply.headers["Cache-Control"], "no-store")
                self.assertIsNone(reply.headers["Strict-Transport-Security"])
        for cookie in owner_cookies(self.replies):
            self.assertIn("HttpOnly", cookie)
            self.assertIn("SameSite=Lax", cookie)
            self.assertIn("Path=/", cookie)

    def test_pages_need_no_inline_script_or_style(self) -> None:
        owner = self.owner()
        account = f"/account/{self.account_id(OWNER)}"
        for path in ("/", "/owner", "/owner/members", account, "/documents/1"):
            body = owner.get(path).body
            with self.subTest(path=path):
                self.assertIn('<link rel="stylesheet" href="/portal.css">', body)
                self.assertNotIn("<script", body)
                self.assertNotIn("<style", body)
                self.assertNotIn(" style=", body)
                self.assertIsNone(re.search(r"<[^>]*\son[a-z]+=", body))
        css = Browser(self).get("/portal.css")
        self.assertEqual(css.status, 200)
        self.assertTrue(css.headers["Content-Type"].startswith("text/css"))

    def test_https_cookies_add_secure_and_hsts(self) -> None:
        self.start_server(https_cookies=True)
        reply = Browser(self).get("/sign-in")
        self.assertEqual(reply.headers["Strict-Transport-Security"], "max-age=31536000")
        cookies = reply.headers.get_all("Set-Cookie")
        self.assertTrue(cookies)
        for cookie in cookies:
            self.assertIn("; Secure", cookie)

    def test_the_health_check_is_public_and_says_nothing_else(self) -> None:
        reply = Browser(self).get("/healthz")
        self.assertEqual((reply.status, reply.body), (200, '{"status":"ok"}'))
        self.assertIsNone(reply.headers["Set-Cookie"])

    def test_allow_network_turns_away_other_addresses(self) -> None:
        self.start_server(allow_network=["10.0.0.0/8", "fd7a:115c:a1e0::/48"])
        for path in ("/healthz", "/sign-in", "/"):
            with self.subTest(path=path):
                reply = Browser(self).get(path)
                self.assertEqual(reply.status, 403)
                self.assertIn("not open to your network", reply.body)
        self.start_server(allow_network=["127.0.0.0/8"])
        self.assertEqual(Browser(self).get("/healthz").status, 200)

    def test_forwarded_addresses_count_only_with_trust_proxy(self) -> None:
        self.start_server(allow_network=["10.0.0.0/8"])
        forwarded = {"X-Forwarded-For": "10.1.2.3"}
        self.assertEqual(Browser(self).get("/healthz", headers=forwarded).status, 403)
        self.start_server(allow_network=["10.0.0.0/8"], trust_proxy=True)
        self.assertEqual(Browser(self).get("/healthz", headers=forwarded).status, 200)
        spoofed = {"X-Forwarded-For": "10.1.2.3, 192.0.2.1"}
        self.assertEqual(Browser(self).get("/healthz", headers=spoofed).status, 403,
                         "only the entry the local proxy added counts")

    def test_client_addresses(self) -> None:
        self.assertEqual(client_ip("192.0.2.5", "10.0.0.1", trust_proxy=True), "192.0.2.5",
                         "a forwarded address from a remote peer is ignored")
        self.assertEqual(client_ip("127.0.0.1", "203.0.113.9, 10.0.0.7", trust_proxy=True), "10.0.0.7")
        self.assertEqual(client_ip("127.0.0.1", "not-an-address", trust_proxy=True), "127.0.0.1")
        self.assertEqual(client_ip("::ffff:10.0.0.9"), "10.0.0.9")
        networks = parse_networks(["100.64.0.0/10"])
        self.assertTrue(network_allowed("100.101.102.103", networks))
        self.assertFalse(network_allowed("192.0.2.1", networks))
        self.assertFalse(network_allowed("garbage", networks))
        self.assertTrue(network_allowed("192.0.2.1", ()))
        with self.assertRaises(ValueError):
            parse_networks(["not a network"])

    def test_a_host_that_is_not_the_public_url_is_refused(self) -> None:
        reply = Browser(self).get("/sign-in", headers={"Host": "rebound.evil.example"})
        self.assertEqual(reply.status, 400)
        self.assertEqual(Browser(self).get("/sign-in", headers={"Host": "localhost"}).status, 200)

    def test_without_mail_sign_in_is_off_and_says_what_is_missing(self) -> None:
        self.start_server(environ={"SDDU13_OWNER_EMAIL": OWNER}, mail_outbox=None)
        for path in ("/sign-in", "/request-access", "/forgot-password"):
            with self.subTest(path=path):
                reply = Browser(self).get(path)
                self.assertEqual(reply.status, 503)
                self.assertIn("SDDU13_SMTP_HOST", reply.body)
                self.assertIn("SDDU13_SMTP_FROM", reply.body)
                self.assertNotIn('name="password"', reply.body)
        self.assertEqual(Browser(self).get("/documents").location, "/sign-in?next=%2Fdocuments")

    def test_get_only_paths_refuse_other_methods(self) -> None:
        self.assertEqual(Browser(self).get("/sign-out").status, 405)
        self.assertEqual(Browser(self).request("PUT", "/sign-in", raw=b"x").status, 501)


def owner_cookies(replies: list[Reply]) -> list[str]:
    return [cookie for reply in replies for cookie in reply.headers.get_all("Set-Cookie") or []]


# -- nothing secret leaks ---------------------------------------------------------

class TestNothingSecretLeaks(PortalCase):
    def test_no_secret_appears_in_any_page_redirect_or_log_line(self) -> None:
        owner = self.owner()
        pat = self.member(owner)
        pat_id = self.account_id(MEMBER)

        # A backup-code sign-in, new backup codes, a password change.
        self.sign_in(Browser(self), MEMBER, PASSWORD, code=self.backup_codes(MEMBER)[0])
        pat.submit("/account", f"/account/{pat_id}/backup-codes", {"account": str(pat_id)})
        pat.submit("/account", f"/account/{pat_id}/password",
                   {"account": str(pat_id), "current": PASSWORD, "password": NEW_PASSWORD,
                    "confirm": NEW_PASSWORD})
        # A forgotten password, reset by email, and an owner-sent reset.
        before = self.count(MEMBER)
        stranger = Browser(self)
        reply = stranger.submit("/forgot-password", "/forgot-password", {"email": MEMBER})
        code = self.code_in(self.next_mail(MEMBER, before))
        stranger.post("/reset-password", {"email": MEMBER, "code": code, "password": PASSWORD,
                                          "confirm": PASSWORD, "csrf": _CSRF.search(reply.body).group(1)})
        self.owner_action(owner, MEMBER, "reset-password")
        # Wrong codes, wrong passwords, and a Google request with its own code.
        self.clock.advance(minutes=16)  # a fresh sign-in window; every attempt above counted
        self.first_factor(Browser(self), MEMBER, "a wrong password typed")
        browser = Browser(self)
        reply = browser.get("/auth/google/start")
        query = {key: values[0] for key, values in parse_qs(urlsplit(reply.location).query).items()}
        self.google.nonce = query["nonce"]
        callback = urlencode({"state": query["state"], "code": AUTH_CODE})
        reply = browser.get(f"/auth/google/callback?{callback}")
        before = self.count(GOOGLE_PERSON)
        browser.post("/auth/google/request", {"name": "Gale", "csrf": _CSRF.search(reply.body).group(1)})
        browser.submit("/sign-in/code", "/sign-in/code", {"code": "999999"})
        code = self.code_in(self.next_mail(GOOGLE_PERSON, before))
        browser.submit("/sign-in/code", "/sign-in/code", {"code": code})
        # Every owner page, after all of that.
        for path in ("/owner", "/owner/requests", "/owner/members", "/owner/blocked", "/owner/activity",
                     "/owner/checklist", f"/account/{self.account_id(OWNER)}"):
            owner.get(path)
        for thread in threading.enumerate():
            if thread.name == "portal-mail":
                thread.join(10)

        secrets_seen: set[str] = set(self.passwords) | {CLIENT_SECRET, AUTH_CODE, "ya29.access-token-secret"}
        for message in self.mails():
            content = message.get_content()
            secrets_seen.update(re.findall(r"^ {4}(\d{6})$", content, re.MULTILINE))
            secrets_seen.update(re.findall(r"^ {4}([A-Z0-9]{4}-[A-Z0-9]{4})$", content, re.MULTILINE))
        sessions = {value for value in self.cookie_values if len(value) >= 40}
        secrets_seen.update(sessions)
        secrets_seen.add((self.root / "portal" / "secret.key").read_text().strip())
        self.assertGreater(len(secrets_seen), 30, "the flow above should have produced plenty of secrets")

        def leaks(text: str) -> list[str]:
            return [secret for secret in secrets_seen
                    if re.search(rf"(?<![A-Za-z0-9]){re.escape(secret)}(?![A-Za-z0-9])", text)]

        self.assertGreater(len(self.replies), 30)
        for reply in self.replies:
            visible = reply.body + "\n" + "\n".join(
                f"{name}: {value}" for name, value in reply.headers.items()
                if name.lower() not in ("set-cookie", "content-length"))
            self.assertEqual(leaks(visible), [], f"a secret in a {reply.status} reply")
        log = self.log.getvalue()
        self.assertIn('"GET /auth/google/callback"', log, "the callback was logged, without its query")
        self.assertEqual(leaks(log), [], "a secret in the server log")


if __name__ == "__main__":
    unittest.main()
