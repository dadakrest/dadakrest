"""The portal's data documentation, updates and connection pages, and the
account commands on the command line.

The pages are driven over HTTP with the helpers in test_portal_auth: a real
server on 127.0.0.1, mail in an outbox, Google faked. No connection check
touches the network here. Every probe is a stand-in, and some never answer,
to show that the page answers anyway.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_portal_auth as auth  # noqa: E402

from datacenter import DATABASES, DataCenter  # noqa: E402
from datacenter import portal_content  # noqa: E402
from datacenter.accounts import AccountStore  # noqa: E402
from datacenter.cli import build_parser, main  # noqa: E402
from datacenter.connectivity import OFF, OK, PROBLEM, ConnectionChecker, Probes  # noqa: E402
from datacenter.mailer import OutboxMailer, SmtpMailer, SmtpSettings  # noqa: E402
from datacenter.portal_content import UpdatesError, available_pdfs, load_updates  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CHECK_NAMES = ["Accounts store", "The seven databases", "Internet", "DNS", "Google sign-in", "Email server"]


class FakeNetwork:
    """Stands in for the network: records every probe, and can fail or hang
    any kind of probe ("tcp", "tls", "dns")."""

    def __init__(self, *, fail: tuple[str, ...] = (), hang: tuple[str, ...] = ()) -> None:
        self.fail, self.hang = set(fail), set(hang)
        self.calls: list[tuple[str, str]] = []
        self.release = threading.Event()

    def _probe(self, kind: str, target: str) -> None:
        self.calls.append((kind, target))
        if kind in self.hang:
            self.release.wait(30)
        if kind in self.fail:
            raise ConnectionRefusedError(f"refused by 10.9.8.7 while reaching {target}")

    def probes(self) -> Probes:
        return Probes(tcp=lambda host, port, timeout: self._probe("tcp", host),
                      tls=lambda host, port, timeout: self._probe("tls", host),
                      resolve=lambda name, timeout: self._probe("dns", name))


def provisioned_root(directory: str) -> Path:
    root = Path(directory) / "storage"
    center = DataCenter(root=root)
    center.provision()
    center.close()
    store = AccountStore(root, environ={}, mirror_audit=False)
    store.provision()
    store.close()
    return root


# -- the checks, without HTTP -----------------------------------------------------

class TestConnectionChecks(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = provisioned_root(self._tmp.name)

    def check(self, network: FakeNetwork, **options) -> dict[str, object]:
        checker = ConnectionChecker(network.probes(), timeout=options.pop("timeout", 1.0))
        self.addCleanup(network.release.set)
        results = checker.report(self.root, **options)
        self.assertEqual([result.name for result in results], CHECK_NAMES)
        return {result.name: result for result in results}

    def test_everything_passes_on_a_working_network(self) -> None:
        results = self.check(FakeNetwork())
        for name in ("Accounts store", "The seven databases", "Internet", "DNS"):
            self.assertEqual(results[name].state, OK, results[name].detail)
        self.assertEqual(results["Google sign-in"].state, OFF, "not configured, so not checked")
        self.assertEqual(results["Email server"].state, OFF)
        self.assertEqual(results["Internet"].label, "OK")
        self.assertIsNotNone(results["DNS"].checked_at.tzinfo)

    def test_google_is_checked_over_tls_only_when_configured(self) -> None:
        network = FakeNetwork()
        results = self.check(network, google_configured=True)
        self.assertEqual(results["Google sign-in"].state, OK)
        self.assertEqual(sorted(target for kind, target in network.calls if kind == "tls"),
                         ["accounts.google.com", "oauth2.googleapis.com"])

        network = FakeNetwork()
        self.check(network, google_configured=False)
        self.assertFalse([call for call in network.calls if call[0] == "tls"])

    def test_failures_are_problems_that_name_no_internal_detail(self) -> None:
        results = self.check(FakeNetwork(fail=("tcp", "dns", "tls")), google_configured=True)
        for name in ("Internet", "DNS", "Google sign-in"):
            self.assertEqual(results[name].state, PROBLEM)
            self.assertIn("ConnectionRefusedError", results[name].detail)
            self.assertNotIn("10.9.8.7", results[name].detail, "exception text is not shown")
        self.assertEqual(results["Accounts store"].state, OK, "local checks do not depend on the network")

    def test_one_working_internet_address_is_enough(self) -> None:
        network = FakeNetwork()
        calls = []

        def tcp(host: str, port: int, timeout: float) -> None:
            calls.append(host)
            if host == "1.1.1.1":
                raise TimeoutError()

        checker = ConnectionChecker(Probes(tcp=tcp, tls=network.probes().tls, resolve=network.probes().resolve))
        results = {result.name: result for result in checker.report(self.root)}
        self.assertEqual(results["Internet"].state, OK)
        self.assertEqual(calls, ["1.1.1.1", "8.8.8.8"])

    def test_a_probe_that_never_answers_cannot_hang_the_report(self) -> None:
        network = FakeNetwork(hang=("tcp", "dns", "tls"))
        started = time.monotonic()
        results = self.check(network, timeout=0.2, google_configured=True)
        self.assertLess(time.monotonic() - started, 3.0)
        for name in ("Internet", "DNS", "Google sign-in"):
            self.assertEqual(results[name].state, PROBLEM)
            self.assertIn("No answer within 0.2 seconds", results[name].detail)
        self.assertEqual(results["The seven databases"].state, OK)

    def test_an_unexpected_error_is_a_problem_not_a_crash(self) -> None:
        def broken(host: str, port: int, timeout: float) -> None:
            raise ValueError("not an OSError")

        checker = ConnectionChecker(Probes(tcp=broken, tls=broken, resolve=lambda name, timeout: None))
        results = {result.name: result for result in checker.report(self.root)}
        self.assertEqual(results["Internet"].state, PROBLEM)
        self.assertEqual(results["Internet"].detail, "The check failed (ValueError).")

    def test_results_are_reused_until_the_cache_expires(self) -> None:
        network = FakeNetwork()
        now = [100.0]
        checker = ConnectionChecker(network.probes(), ttl=60, monotonic=lambda: now[0])
        checker.report(self.root)
        first = len(network.calls)
        self.assertGreater(first, 0)
        now[0] += 59
        checker.report(self.root)
        self.assertEqual(len(network.calls), first, "a second visit within a minute probes nothing")
        now[0] += 2
        checker.report(self.root)
        self.assertEqual(len(network.calls), 2 * first)

    def test_missing_stores_are_reported(self) -> None:
        empty = Path(self._tmp.name) / "nothing-here"
        checker = ConnectionChecker(FakeNetwork().probes())
        results = {result.name: result for result in checker.report(empty)}
        self.assertEqual(results["Accounts store"].state, PROBLEM)
        self.assertEqual(results["The seven databases"].state, PROBLEM)
        self.assertIn("0 of 7 healthy", results["The seven databases"].detail)
        self.assertFalse(empty.exists(), "checking creates nothing")

    def test_the_email_server_is_greeted_and_never_sent_anything(self) -> None:
        calls: list[str] = []

        class FakeSmtp:
            def __init__(self, host: str, port: int, timeout: float = 0) -> None:
                calls.append(f"connect {host}:{port}")

            def __getattr__(self, name: str):
                return lambda *args, **kwargs: calls.append(name)

        mailer = SmtpMailer(SmtpSettings(host="smtp.example.com", port=587, sender="portal@example.com",
                                         user="portal@example.com", password="smtp-password-123"),
                            smtp_class=FakeSmtp)
        results = self.check(FakeNetwork(), mailer=mailer)
        self.assertEqual(results["Email server"].state, OK)
        self.assertEqual(calls, ["connect smtp.example.com:587", "ehlo", "starttls", "ehlo", "quit"])

        def unreachable(*args, **kwargs):
            raise ConnectionRefusedError("refused")

        mailer = SmtpMailer(mailer.settings, smtp_class=unreachable)
        results = self.check(FakeNetwork(), mailer=mailer)
        self.assertEqual(results["Email server"].state, PROBLEM)
        self.assertNotIn("smtp.example.com", results["Email server"].detail)

    def test_an_outbox_has_no_server_to_check(self) -> None:
        results = self.check(FakeNetwork(), mailer=OutboxMailer(Path(self._tmp.name) / "outbox"))
        self.assertEqual(results["Email server"].state, OFF)
        self.assertIn("Development mode", results["Email server"].detail)


# -- updates.json -------------------------------------------------------------------

class TestUpdatesFile(unittest.TestCase):
    def test_the_shipped_file_is_valid(self) -> None:
        updates = load_updates()
        self.assertTrue(updates["coding"])
        self.assertTrue(updates["data"])
        for kind, entries in updates.items():
            days = [entry["date"] for entry in entries]
            self.assertEqual(days, sorted(days, reverse=True), f"{kind} is newest first")
            for entry in entries:
                self.assertGreaterEqual(entry["date"], date(2026, 9, 19))
                self.assertTrue(entry["title"] and entry["summary"])
                for commit in entry["commits"]:
                    self.assertRegex(commit, r"^[0-9a-f]{7,40}$")

    def test_it_ships_with_the_package(self) -> None:
        pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(pyproject, r'datacenter = \[[^\]]*"updates\.json"')

    def test_a_file_in_the_wrong_shape_is_refused_by_name(self) -> None:
        good = {"date": "2026-11-02", "title": "A", "summary": "B", "commits": ["abcdef1"]}
        cases = {
            "not json": "{",
            "no data list": json.dumps({"coding": [good]}),
            "impossible date": json.dumps({"coding": [{**good, "date": "2026-13-01"}], "data": []}),
            "loose date": json.dumps({"coding": [{**good, "date": "20261102"}], "data": []}),
            "unknown key": json.dumps({"coding": [{**good, "author": "x"}], "data": []}),
            "bad commit": json.dumps({"coding": [], "data": [{**good, "commits": ["not-a-hash"]}]}),
            "empty title": json.dumps({"coding": [{**good, "title": " "}], "data": []}),
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "updates.json"
            for label, text in cases.items():
                with self.subTest(label):
                    path.write_text(text, encoding="utf-8")
                    with self.assertRaises(UpdatesError):
                        load_updates(path)
            with self.assertRaises(UpdatesError):
                load_updates(Path(directory) / "missing.json")


# -- the pages over HTTP ------------------------------------------------------------------

class ContentCase(auth.PortalCase):
    """A portal whose connection checks use a fake network."""

    hang: tuple[str, ...] = ()
    check_timeout = 1.0

    def setUp(self) -> None:
        self.network = FakeNetwork(hang=self.hang)
        self.checker = ConnectionChecker(self.network.probes(), timeout=self.check_timeout)
        super().setUp()
        self.addCleanup(self.network.release.set)

    def start_server(self, **options) -> None:
        options.setdefault("connectivity", self.checker)
        super().start_server(**options)

    def fetch_bytes(self, browser: auth.Browser, path: str) -> tuple[int, http.client.HTTPMessage, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            cookies = "; ".join(f"{name}={value}" for name, value in browser.cookies.items())
            connection.request("GET", path, headers={"Cookie": cookies})
            response = connection.getresponse()
            return response.status, response.headers, response.read()
        finally:
            connection.close()

    def owner_and_member(self) -> tuple[auth.Browser, auth.Browser]:
        owner = self.owner()
        return owner, self.member(owner)


PAGES = ("/data-docs", "/updates", "/connection")


class TestWhoSeesThePages(ContentCase):
    def test_signed_out_visitors_are_sent_to_sign_in(self) -> None:
        stranger = auth.Browser(self)
        for path in PAGES + ("/data-docs/pdf/fee-schedule.pdf",):
            with self.subTest(path=path):
                reply = stranger.get(path)
                self.assertEqual(reply.status, 303)
                self.assertTrue(reply.location.startswith("/sign-in?next="), reply.location)
        self.assertEqual(self.network.calls, [], "nobody signed out can start a connection check")

    def test_people_waiting_for_approval_see_only_their_status(self) -> None:
        self.owner()
        waiting = auth.Browser(self)
        self.register(waiting, auth.MEMBER, auth.PASSWORD)
        for path in PAGES:
            with self.subTest(path=path):
                self.assertEqual(waiting.get(path).location, "/status")

    def test_members_find_the_pages_in_the_navigation_and_the_owner_the_owner_center(self) -> None:
        owner, member = self.owner_and_member()
        page = member.get("/").body
        for path in PAGES:
            self.assertIn(f'href="{path}"', page)
        self.assertNotIn('href="/owner"', page)
        owner_page = owner.get("/").body
        self.assertIn('href="/owner"', owner_page)
        self.assertIn('href="/data-docs"', owner_page)
        for path in PAGES:
            with self.subTest(path=path):
                self.assertEqual(owner.get(path).status, 200)
                self.assertEqual(member.get(path).status, 200)


class TestDataDocumentation(ContentCase):
    def test_it_describes_the_live_schema_datasets_fees_plans_and_pdfs(self) -> None:
        _, member = self.owner_and_member()
        center = DataCenter(root=self.root)
        try:
            # Created after the server started: the page reads the files, not a copy.
            center.db("datasets").execute("CREATE TABLE scratch_notes (note TEXT NOT NULL)")
            datasets = {dataset["name"]: dataset["records"] for dataset in center.list_datasets()}
        finally:
            center.close()

        body = member.get("/data-docs").body
        for spec in DATABASES:
            self.assertIn(f'id="db-{spec.key}"', body)
            self.assertIn(f"<code>{spec.filename}</code>", body)
        self.assertIn("<h3>contact_channels", body)
        self.assertIn("<code>full_name</code>", body)
        self.assertIn("<h3>scratch_notes", body)
        self.assertIn("<code>note</code>", body)
        self.assertIn('>government_fees</a></td><td>', body)
        self.assertIn(f'<td class="num">{datasets["government_fees"]:,}</td>', body)
        self.assertIn('href="/plans/year-one-operating"', body)
        for pdf in available_pdfs():
            self.assertIn(f'href="/data-docs/pdf/{pdf.name}"', body)
        self.assertIn("separate accounts store", body)
        self.assertNotIn("password_hash", body, "nothing about sign-in records")
        secret = (self.root / "portal" / "secret.key").read_text().strip()
        self.assertNotIn(secret, body)

    def test_pdfs_are_served_to_members_and_nothing_else_is(self) -> None:
        _, member = self.owner_and_member()
        pdfs = available_pdfs()
        self.assertTrue(pdfs, "the checkout has PDFs in docs/pdf")
        status, headers, data = self.fetch_bytes(member, f"/data-docs/pdf/{pdfs[0].name}")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/pdf")
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

        for path in ("/data-docs/pdf/no-such-file.pdf", "/data-docs/pdf/..%2F..%2Fpyproject.pdf",
                     "/data-docs/pdf/../../pyproject.toml", "/data-docs/pdf/Fee-Schedule.pdf"):
            with self.subTest(path=path):
                self.assertEqual(self.fetch_bytes(member, path)[0], 404)

    def test_only_plain_pdf_files_in_the_folder_are_listed(self) -> None:
        _, member = self.owner_and_member()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "real.pdf").write_bytes(b"%PDF-1.4 real")
            (folder / "notes.txt").write_text("not a pdf")
            outside = folder.parent / f"outside-{os.getpid()}.txt"
            outside.write_text("must never be served")
            self.addCleanup(outside.unlink)
            (folder / "linked.pdf").symlink_to(outside)
            with mock.patch.object(portal_content, "PDF_DIR", folder):
                self.assertEqual([path.name for path in available_pdfs()], ["real.pdf"])
                self.assertEqual(self.fetch_bytes(member, "/data-docs/pdf/real.pdf")[2], b"%PDF-1.4 real")
                self.assertEqual(self.fetch_bytes(member, "/data-docs/pdf/linked.pdf")[0], 404)
            with mock.patch.object(portal_content, "PDF_DIR", folder / "missing"):
                self.assertIn("not on this server", member.get("/data-docs").body)


class TestUpdatesPage(ContentCase):
    def test_portal_coding_and_data_updates_are_shown(self) -> None:
        owner, member = self.owner_and_member()
        posted = owner.submit("/owner/announcements", "/owner/announcements", {
            "title": "New dashboards <script>alert(1)</script>", "body": "- one\n- two"})
        self.assertEqual(posted.status, 303)

        body = member.get("/updates").body
        for heading in ("Portal updates", "Coding updates", "Data updates"):
            self.assertIn(heading, body)
        self.assertIn("New dashboards &lt;script&gt;alert(1)&lt;/script&gt;", body)
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("<li>one</li>", body)
        updates = load_updates()
        self.assertIn(updates["coding"][0]["title"], body)
        self.assertIn(updates["data"][-1]["title"], body)
        self.assertIn("<code>dc7cf91</code>", body)

    def test_a_broken_updates_file_still_shows_the_portal_updates(self) -> None:
        owner, member = self.owner_and_member()
        owner.submit("/owner/announcements", "/owner/announcements", {"title": "Still here", "body": "Yes."})
        with tempfile.TemporaryDirectory() as directory:
            broken = Path(directory) / "updates.json"
            broken.write_text("{ not json", encoding="utf-8")
            with mock.patch.object(portal_content, "UPDATES_FILE", broken):
                reply = member.get("/updates")
        self.assertEqual(reply.status, 200)
        self.assertIn("could not be read", reply.body)
        self.assertIn("Still here", reply.body)


class TestConnectionPage(ContentCase):
    def test_each_check_is_shown_with_its_time_and_the_vpn_is_explained(self) -> None:
        _, member = self.owner_and_member()
        body = member.get("/connection").body
        self.assertIn("does not provide internet access and is not a VPN", body)
        for name in CHECK_NAMES:
            self.assertIn(f"<td>{name}</td>", body)
        self.assertIn('<span class="ok">OK</span>', body)
        self.assertIn("Development mode", body, "the test server mails to an outbox")
        self.assertRegex(body, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC")
        # The test environment configures Google, so its hosts were checked over TLS.
        self.assertIn(("tls", "accounts.google.com"), self.network.calls)

    def test_checks_are_reused_for_a_minute(self) -> None:
        _, member = self.owner_and_member()
        member.get("/connection")
        first = len(self.network.calls)
        member.get("/connection")
        self.assertEqual(len(self.network.calls), first)


class TestConnectionPageOnADeadNetwork(ContentCase):
    hang = ("tcp", "tls", "dns")
    check_timeout = 0.3

    def test_the_page_answers_even_when_nothing_does(self) -> None:
        _, member = self.owner_and_member()
        started = time.monotonic()
        reply = member.get("/connection")
        self.assertLess(time.monotonic() - started, 5.0)
        self.assertEqual(reply.status, 200)
        self.assertIn("No answer within 0.3 seconds", reply.body)
        self.assertIn('<span class="bad">Problem</span>', reply.body)


# -- the account commands -------------------------------------------------------------------

class TestAccountCommands(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "storage"
        self.outbox = Path(self._tmp.name) / "outbox"
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)
        for name in [name for name in os.environ if name.startswith("SDDU13_")]:
            del os.environ[name]

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--root", str(self.root), *argv])
        return code, out.getvalue(), err.getvalue()

    def store(self) -> AccountStore:
        store = AccountStore(self.root, mailer=OutboxMailer(self.outbox), password_iterations=auth.FAST,
                             mirror_audit=False)
        store.provision()
        self.addCleanup(store.close)
        return store

    def person(self, email: str, password: str = auth.PASSWORD) -> int:
        store = self.store()
        account = store.register_password_account(email, password, display_name=email.split("@")[0])
        return account.id

    def status(self, email: str) -> str:
        return self.store().find_account(email).status

    def test_portal_owner_sets_and_changes_the_owner(self) -> None:
        code, out, _ = self.run_cli("portal-owner", "First@Example.com")
        self.assertEqual(code, 0)
        self.assertIn("owner email stored: first@example.com", out)
        self.assertIn("No account uses that address yet", out)
        self.person("first@example.com")
        self.person("second@example.com")
        self.assertEqual(self.run_cli("portal-owner", "second@example.com")[0], 0)

        code, out, _ = self.run_cli("accounts")
        self.assertEqual(code, 0)
        rows = {line.split()[0]: line.split()[1] for line in out.splitlines()[1:]}
        self.assertEqual(rows, {"first@example.com": "member", "second@example.com": "owner"})
        self.assertEqual(self.status("second@example.com"), "approved")

    def test_portal_owner_says_when_the_environment_wins(self) -> None:
        os.environ["SDDU13_OWNER_EMAIL"] = "boss@example.com"
        code, _, err = self.run_cli("portal-owner", "other@example.com")
        self.assertEqual(code, 0)
        self.assertIn("SDDU13_OWNER_EMAIL is set to boss@example.com", err)
        self.assertEqual(self.run_cli("portal-owner", "not an address")[0], 1)

    def test_accounts_lists_people_and_never_a_secret(self) -> None:
        pat = self.person(auth.MEMBER)
        store = self.store()
        code_sent = store.issue_code(pat, "sign_in")
        backup = store.generate_backup_codes(pat)
        code, out, err = self.run_cli("accounts")
        self.assertEqual(code, 0)
        self.assertIn(auth.MEMBER, out)
        self.assertIn("pending (email not confirmed)", out)
        everything = out + err
        for secret in [auth.PASSWORD, code_sent, "pbkdf2", *backup,
                       (self.root / "portal" / "secret.key").read_text().strip()]:
            self.assertNotIn(secret, everything)
        self.assertIn("no accounts with status blocked", self.run_cli("accounts", "--status", "blocked")[1])

    def test_listing_before_the_portal_ever_ran_creates_nothing(self) -> None:
        code, _, err = self.run_cli("accounts")
        self.assertEqual(code, 0)
        self.assertIn("no accounts yet", err)
        self.assertFalse((self.root / "portal").exists())

    def test_block_then_unblock_only_after_the_review_date(self) -> None:
        self.person(auth.MEMBER)
        code, out, _ = self.run_cli("account-block", auth.MEMBER, "--reason", "Shared a password")
        self.assertEqual(code, 0)
        self.assertEqual(self.status(auth.MEMBER), "blocked")
        review = re.search(r"before (\d{4}-\d{2}-\d{2})", out).group(1)
        self.assertGreater(date.fromisoformat(review), datetime.now(timezone.utc).date())
        self.assertIn(f"review {review}", self.run_cli("accounts")[1])

        code, _, err = self.run_cli("account-unblock", auth.MEMBER)
        self.assertEqual(code, 1)
        self.assertIn(f"the review date is {review}", err)
        self.assertEqual(self.status(auth.MEMBER), "blocked")

        with contextlib.closing(sqlite3.connect(self.root / "portal" / "accounts.db")) as connection:
            with connection:  # the review date passes
                connection.execute("UPDATE accounts SET review_after = '2000-01-01' WHERE email = ?",
                                   (auth.MEMBER,))
        code, out, _ = self.run_cli("account-unblock", auth.MEMBER)
        self.assertEqual(code, 0)
        self.assertIn("Access is not restored", out)
        self.assertEqual(self.status(auth.MEMBER), "unblocked")

    def test_the_owner_cannot_be_blocked_and_unknown_people_are_named(self) -> None:
        self.run_cli("portal-owner", auth.OWNER)
        self.person(auth.OWNER)
        code, _, err = self.run_cli("account-block", auth.OWNER, "--reason", "test")
        self.assertEqual(code, 1)
        self.assertIn("never be blocked", err)
        code, _, err = self.run_cli("account-block", "nobody@example.com", "--reason", "test")
        self.assertEqual(code, 1)
        self.assertIn("no account uses nobody@example.com", err)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--root", str(self.root), "account-block", auth.OWNER])  # --reason is required

    def test_reset_emails_a_code_and_never_prints_it(self) -> None:
        self.person(auth.MEMBER)
        code, out, err = self.run_cli("account-reset", auth.MEMBER, "--mail-outbox", str(self.outbox))
        self.assertEqual(code, 0)
        self.assertIn("nobody else sees it", out)
        mails = sorted(self.outbox.glob("*.eml"))
        self.assertEqual(len(mails), 1)
        content = mails[0].read_text()
        self.assertIn("password reset code", content)
        sent = re.search(r"^ {4}(\d{6})$", content, re.MULTILINE).group(1)
        self.assertNotIn(sent, out + err)

    def test_reset_without_mail_names_what_is_missing(self) -> None:
        self.person(auth.MEMBER)
        code, out, err = self.run_cli("account-reset", auth.MEMBER)
        self.assertEqual(code, 1)
        self.assertIn("SDDU13_SMTP_HOST", err)
        self.assertFalse(self.outbox.exists())

    def test_the_portal_command_takes_the_live_options(self) -> None:
        args = build_parser().parse_args([
            "portal", "--mail-outbox", "outbox", "--allow-network", "100.64.0.0/10",
            "--allow-network", "fd7a:115c:a1e0::/48", "--trust-proxy", "--https-cookies",
            "--public-url", "https://portal.example.ts.net"])
        self.assertEqual(args.allow_network, ["100.64.0.0/10", "fd7a:115c:a1e0::/48"])
        self.assertTrue(args.trust_proxy and args.https_cookies)
        self.assertEqual(args.mail_outbox, "outbox")


if __name__ == "__main__":
    unittest.main()
