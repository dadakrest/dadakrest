"""Tests for the portal's accounts store: every sign-in rule that does not need HTTP."""

from __future__ import annotations

import base64
import contextlib
import email
import email.policy
import hashlib
import io
import logging
import re
import stat
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datacenter import DataCenter  # noqa: E402
from datacenter.accounts import (  # noqa: E402
    BACKUP_ALPHABET,
    PASSWORD_ITERATIONS,
    SESSION_COOKIE,
    AccountStore,
    ConfigError,
    EmailTaken,
    InvalidInput,
    OwnerProtected,
    Throttled,
    TooEarly,
    WrongState,
    add_months,
    check_password_strength,
    clear_cookie,
    cookie_header,
    hash_password,
    load_secret,
    needs_rehash,
    normalise_email,
    session_cookie,
    verify_password,
)
from datacenter.mailer import MailError, MailNotConfigured, OutboxMailer  # noqa: E402

OWNER = "owner@example.com"
PASSWORD = "correct horse battery"
FAST = 1000  # PBKDF2 iterations for tests; the real count is checked once below


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


class BrokenMailer:
    def send(self, to: str, subject: str, body: str) -> None:
        raise MailError("the SMTP server is down")

    def describe(self) -> str:
        return "broken"


class StoreTestCase(unittest.TestCase):
    """A fresh store per test, with an outbox for mail and a clock to move."""

    environ: dict[str, str] = {"SDDU13_OWNER_EMAIL": OWNER}

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "storage"
        self.outbox = Path(self._tmp.name) / "outbox"
        self.clock = Clock(datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc))
        self.store = self.open_store()
        self.store.provision()

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def open_store(self, **overrides: object) -> AccountStore:
        options: dict[str, object] = dict(mailer=OutboxMailer(self.outbox), environ=self.environ,
                                          clock=self.clock, password_iterations=FAST,
                                          mirror_audit=False)
        options.update(overrides)
        return AccountStore(self.root, **options)

    # -- mail ------------------------------------------------------------------

    def mails(self) -> list[email.message.EmailMessage]:
        self.store.wait_for_mail()
        if not self.outbox.exists():
            return []
        return [email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
                for path in sorted(self.outbox.glob("*.eml"))]

    def last_mail(self, to: str | None = None) -> email.message.EmailMessage:
        mails = [m for m in self.mails() if to is None or m["To"] == to]
        self.assertTrue(mails, f"no mail to {to}")
        return mails[-1]

    def last_code(self, to: str | None = None) -> str:
        match = re.search(r"^ {4}(\d{6})$", self.last_mail(to).get_content(), re.MULTILINE)
        self.assertIsNotNone(match, "no code in the last mail")
        return match.group(1)

    def last_backup_codes(self, to: str | None = None) -> list[str]:
        return re.findall(r"^ {4}([A-Z0-9]{4}-[A-Z0-9]{4})$", self.last_mail(to).get_content(), re.MULTILINE)

    # -- people ----------------------------------------------------------------

    def register(self, address: str = "pat@example.com", *, confirm: bool = True, ip: str = "10.0.0.1"):
        account = self.store.register_password_account(address, PASSWORD, display_name="Pat",
                                                       company="Acme", reason="work", ip=ip)
        if confirm:
            code = self.store.issue_code(account.id, "verify_email")
            self.assertTrue(self.store.verify_code(account.id, "verify_email", code))
        return self.store.get_account(account.id)

    def member(self, address: str = "pat@example.com"):
        account = self.register(address)
        return self.store.approve(account.id)

    def owner(self):
        return self.register(OWNER)

    def full_session(self, account):
        partial = self.store.begin_sign_in(account.id, method="password", ip="10.0.0.1")
        code = self.store.issue_code(account.id, "sign_in")
        step = self.store.finish_sign_in(partial.token, code)
        self.assertTrue(step)
        return step.session

    def dump(self) -> str:
        return "\n".join(self.store.connection.iterdump())


# -- storage -------------------------------------------------------------------------

class TestStorage(StoreTestCase):
    def test_the_store_is_its_own_file_and_the_data_center_keeps_seven(self) -> None:
        with DataCenter(root=self.root) as center:
            center.provision()
            report = center.status()
        self.assertEqual(len(report["databases"]), 7)
        self.assertNotIn("accounts", [db["key"] for db in report["databases"]])
        self.assertTrue((self.root / "portal" / "accounts.db").exists())
        self.assertFalse((self.root / "accounts.db").exists())

    def test_the_folder_file_and_secret_are_private(self) -> None:
        portal = self.root / "portal"
        self.assertEqual(stat.S_IMODE(portal.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((portal / "accounts.db").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((portal / "secret.key").stat().st_mode), 0o600)

    def test_the_secret_is_made_once_and_reused(self) -> None:
        first = load_secret(self.root, {})
        self.assertGreaterEqual(len(first), 32)
        self.assertEqual(load_secret(self.root, {}), first)
        other = self.open_store()
        self.assertEqual(other.secret, first)
        other.close()

    def test_a_loose_secret_file_is_tightened(self) -> None:
        path = self.root / "portal" / "secret.key"
        path.chmod(0o644)
        load_secret(self.root, {})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_the_environment_secret_wins_and_must_be_long(self) -> None:
        value = "x" * 40
        self.assertEqual(load_secret(self.root, {"SDDU13_PORTAL_SECRET": value}), value.encode())
        with self.assertRaises(ConfigError):
            load_secret(self.root, {"SDDU13_PORTAL_SECRET": "too-short"})

    def test_a_damaged_secret_file_is_reported_not_replaced(self) -> None:
        path = self.root / "portal" / "secret.key"
        path.write_text("not hex at all")
        with self.assertRaises(ConfigError):
            load_secret(self.root, {})
        self.assertEqual(path.read_text(), "not hex at all")

    def test_provisioning_twice_is_harmless_and_healthy(self) -> None:
        account = self.register()
        self.store.provision()
        self.assertTrue(self.store.healthy())
        self.assertEqual(self.store.get_account(account.id).email, "pat@example.com")

    def test_an_unprovisioned_store_refuses_to_read(self) -> None:
        store = AccountStore(Path(self._tmp.name) / "elsewhere", environ={})
        with self.assertRaises(FileNotFoundError):
            store.list_accounts()
        self.assertFalse(store.healthy())

    def test_a_backup_is_a_private_copy(self) -> None:
        self.register()
        target = self.store.backup_to(Path(self._tmp.name) / "backups" / "accounts.db")
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        copy = AccountStore(self.root, environ={})
        copy.path = target
        self.assertEqual([a.email for a in copy.list_accounts()], ["pat@example.com"])
        copy.close()


# -- passwords -----------------------------------------------------------------------

class TestPasswords(StoreTestCase):
    def test_the_stored_format_and_default_strength(self) -> None:
        encoded = hash_password(PASSWORD)
        scheme, iterations, salt, digest = encoded.split("$")
        self.assertEqual(scheme, "pbkdf2_sha256")
        self.assertEqual(int(iterations), PASSWORD_ITERATIONS)
        self.assertEqual(PASSWORD_ITERATIONS, 600_000)
        self.assertEqual(len(base64.b64decode(salt)), 16)
        self.assertEqual(len(base64.b64decode(digest)), 32)
        self.assertTrue(verify_password(PASSWORD, encoded))

    def test_salts_differ_and_wrong_passwords_fail(self) -> None:
        first, second = hash_password(PASSWORD, iterations=FAST), hash_password(PASSWORD, iterations=FAST)
        self.assertNotEqual(first, second)
        self.assertTrue(verify_password(PASSWORD, first))
        self.assertFalse(verify_password(PASSWORD + "!", first))
        self.assertFalse(verify_password("", first))

    def test_malformed_hashes_never_match(self) -> None:
        good = hash_password(PASSWORD, iterations=FAST)
        scheme, iterations, salt, _ = good.split("$")
        for encoded in (None, "", "plain-text-password", f"{scheme}${iterations}${salt}$",
                        f"md5${iterations}${salt}$AAAA", f"{scheme}$0${salt}$AAAA", good + "$extra"):
            with self.subTest(encoded=encoded):
                self.assertFalse(verify_password(PASSWORD, encoded))
                self.assertTrue(needs_rehash(encoded))

    def test_twelve_characters_is_the_minimum(self) -> None:
        with self.assertRaises(InvalidInput):
            check_password_strength("x" * 11)
        check_password_strength("x" * 12)
        with self.assertRaises(InvalidInput):
            check_password_strength("x" * 1025)
        with self.assertRaises(InvalidInput):
            self.store.register_password_account("short@example.com", "x" * 11, display_name="S")

    def test_the_same_password_typed_two_ways_matches(self) -> None:
        composed, decomposed = "café au lait 2026", "café au lait 2026"
        self.assertTrue(verify_password(decomposed, hash_password(composed, iterations=FAST)))

    def test_a_weaker_stored_hash_is_upgraded_at_sign_in(self) -> None:
        account = self.register()
        stronger = self.open_store(password_iterations=FAST * 2)
        try:
            self.assertIsNotNone(stronger.authenticate_password("pat@example.com", PASSWORD, ip="10.0.0.2"))
            stored = stronger.connection.execute(
                "SELECT password_hash FROM accounts WHERE id = ?", (account.id,)).fetchone()[0]
            self.assertEqual(int(stored.split("$")[1]), FAST * 2)
            self.assertTrue(verify_password(PASSWORD, stored))
            self.assertFalse(needs_rehash(stored, FAST * 2))
        finally:
            stronger.close()

    def test_a_current_hash_is_left_alone(self) -> None:
        self.register()
        before = self.dump()
        self.store.authenticate_password("pat@example.com", PASSWORD)
        stored = re.findall(r"pbkdf2_sha256\$[^']+", self.dump())
        self.assertEqual(stored, re.findall(r"pbkdf2_sha256\$[^']+", before))

    def test_every_failure_looks_the_same(self) -> None:
        self.register()
        self.store.create_google_account("g-1", "google-only@example.com", display_name="G")
        for address, password in (("nobody@example.com", PASSWORD), ("pat@example.com", "wrong password!"),
                                  ("google-only@example.com", PASSWORD), ("", PASSWORD), ("not an email", "")):
            with self.subTest(address=address):
                self.assertIsNone(self.store.authenticate_password(address, password, ip="10.0.0.3"))

    def test_the_right_password_signs_in_whatever_the_case_of_the_email(self) -> None:
        account = self.register()
        self.assertEqual(self.store.authenticate_password(" PAT@Example.com ", PASSWORD).id, account.id)

    def test_an_account_never_carries_its_hash(self) -> None:
        account = self.register()
        self.assertTrue(account.has_password)
        self.assertNotIn("pbkdf2", repr(account))
        self.assertFalse(hasattr(account, "password_hash"))

    def test_a_failed_sign_in_does_not_record_what_was_typed(self) -> None:
        self.store.authenticate_password("my secret passphrase", "x", ip="10.0.0.4")
        self.assertNotIn("my secret passphrase", self.dump())


class TestEmails(unittest.TestCase):
    def test_addresses_are_normalised_and_checked(self) -> None:
        self.assertEqual(normalise_email("  Pat.Smith+portal@Example.COM "), "pat.smith+portal@example.com")
        for bad in ("", "pat", "pat@", "@example.com", "pat@example", "a,b@example.com", "pat smith@example.com",
                    "<pat@example.com>", "pat@example.com\r\nBcc: x@example.com", '"pat"@example.com',
                    "a@b@example.com", "x" * 250 + "@example.com"):
            with self.subTest(bad=bad):
                with self.assertRaises(InvalidInput):
                    normalise_email(bad)


# -- emailed codes -------------------------------------------------------------------

class TestEmailedCodes(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.account = self.register()

    def test_a_code_is_six_digits_and_only_its_hmac_is_stored(self) -> None:
        code = self.store.issue_code(self.account.id, "sign_in")
        self.assertRegex(code, r"^\d{6}$")
        self.assertNotIn(f"'{code}'", self.dump())
        stored = self.store.connection.execute(
            "SELECT code_hash FROM email_codes ORDER BY id DESC LIMIT 1").fetchone()[0]
        self.assertRegex(stored, r"^[0-9a-f]{64}$")

    def test_a_code_works_once(self) -> None:
        code = self.store.issue_code(self.account.id, "sign_in")
        self.assertTrue(self.store.verify_code(self.account.id, "sign_in", code))
        again = self.store.verify_code(self.account.id, "sign_in", code)
        self.assertFalse(again)
        self.assertEqual(again.reason, "none")

    def test_spaces_and_a_dash_are_forgiven(self) -> None:
        code = self.store.issue_code(self.account.id, "sign_in")
        self.assertTrue(self.store.verify_code(self.account.id, "sign_in", f" {code[:3]}-{code[3:]} "))

    def test_a_code_lasts_ten_minutes(self) -> None:
        code = self.store.issue_code(self.account.id, "sign_in")
        self.clock.advance(minutes=9, seconds=59)
        self.assertTrue(self.store.verify_code(self.account.id, "sign_in", code))

        code = self.store.issue_code(self.account.id, "sign_in")
        self.clock.advance(minutes=10)
        result = self.store.verify_code(self.account.id, "sign_in", code)
        self.assertFalse(result)
        self.assertEqual(result.reason, "expired")

    def test_five_attempts_per_code(self) -> None:
        code = self.store.issue_code(self.account.id, "sign_in")
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(4):
            self.assertEqual(self.store.verify_code(self.account.id, "sign_in", wrong).reason, "wrong")
        self.assertTrue(self.store.verify_code(self.account.id, "sign_in", code), "the fifth try may succeed")

        code = self.store.issue_code(self.account.id, "sign_in")
        wrong = "000000" if code != "000000" else "111111"
        results = [self.store.verify_code(self.account.id, "sign_in", wrong).reason for _ in range(5)]
        self.assertEqual(results, ["wrong"] * 4 + ["locked"])
        self.assertEqual(self.store.verify_code(self.account.id, "sign_in", code).reason, "locked")

    def test_a_new_code_voids_the_old_one(self) -> None:
        old = self.store.issue_code(self.account.id, "sign_in")
        new = self.store.issue_code(self.account.id, "sign_in")
        if old != new:
            self.assertFalse(self.store.verify_code(self.account.id, "sign_in", old))
        self.assertTrue(self.store.verify_code(self.account.id, "sign_in", new))

    def test_a_new_code_also_unlocks_after_too_many_attempts(self) -> None:
        code = self.store.issue_code(self.account.id, "sign_in")
        wrong = "000000" if code != "000000" else "111111"
        for _ in range(5):
            self.store.verify_code(self.account.id, "sign_in", wrong)
        fresh = self.store.issue_code(self.account.id, "sign_in")
        self.assertTrue(self.store.verify_code(self.account.id, "sign_in", fresh))

    def test_codes_are_kept_to_their_purpose_and_account(self) -> None:
        other = self.register("sam@example.com")
        code = self.store.issue_code(self.account.id, "reset_password")
        self.assertFalse(self.store.verify_code(self.account.id, "sign_in", code))
        self.assertFalse(self.store.verify_code(other.id, "reset_password", code))
        self.assertTrue(self.store.verify_code(self.account.id, "reset_password", code))

    def test_at_most_five_codes_an_hour(self) -> None:
        self.clock.advance(hours=1)  # past the code that confirmed the address
        for _ in range(5):
            self.store.issue_code(self.account.id, "sign_in")
            self.clock.advance(minutes=1)
        with self.assertRaises(Throttled) as raised:
            self.store.issue_code(self.account.id, "reset_password")
        self.assertGreater(raised.exception.retry_after, 0)
        self.clock.advance(minutes=56)
        self.store.issue_code(self.account.id, "sign_in")

    def test_send_code_mails_the_accounts_own_address(self) -> None:
        self.store.send_code(self.account.id, "sign_in")
        mail = self.last_mail()
        self.assertEqual(mail["To"], "pat@example.com")
        self.assertIn("expires in 10 minutes", mail.get_content())
        self.assertTrue(self.store.verify_code(self.account.id, "sign_in", self.last_code()))

    def test_a_working_code_confirms_the_address(self) -> None:
        account = self.register("new@example.com", confirm=False)
        self.assertFalse(account.email_verified)
        self.store.send_code(account.id, "verify_email")
        self.store.verify_code(account.id, "verify_email", self.last_code("new@example.com"))
        self.assertTrue(self.store.get_account(account.id).email_verified)

    def test_no_mailer_means_no_code_rather_than_no_second_step(self) -> None:
        store = self.open_store(mailer=None)
        try:
            with self.assertRaises(MailNotConfigured):
                store.send_code(self.account.id, "sign_in")
        finally:
            store.close()

    def test_a_code_that_could_not_be_mailed_is_voided(self) -> None:
        store = self.open_store(mailer=BrokenMailer())
        try:
            with self.assertRaises(MailError):
                store.send_code(self.account.id, "sign_in")
            self.assertEqual(store.verify_code(self.account.id, "sign_in", "123456").reason, "none")
        finally:
            store.close()

    def test_an_unknown_purpose_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.store.issue_code(self.account.id, "make_me_owner")


# -- backup codes ----------------------------------------------------------------------

class TestBackupCodes(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.account = self.register()

    def test_ten_unambiguous_codes(self) -> None:
        codes = self.store.generate_backup_codes(self.account.id)
        self.assertEqual(len(codes), 10)
        self.assertEqual(len(set(codes)), 10)
        for code in codes:
            self.assertRegex(code, r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
            self.assertTrue(set(code.replace("-", "")) <= set(BACKUP_ALPHABET))
        for ambiguous in "01OIL":
            self.assertNotIn(ambiguous, BACKUP_ALPHABET)
        self.assertEqual(self.store.backup_codes_remaining(self.account.id), 10)

    def test_each_code_works_once(self) -> None:
        codes = self.store.generate_backup_codes(self.account.id)
        self.assertTrue(self.store.use_backup_code(self.account.id, codes[0]))
        self.assertFalse(self.store.use_backup_code(self.account.id, codes[0]))
        self.assertTrue(self.store.use_backup_code(self.account.id, codes[1]))
        self.assertEqual(self.store.backup_codes_remaining(self.account.id), 8)

    def test_case_spaces_and_the_dash_do_not_matter(self) -> None:
        codes = self.store.generate_backup_codes(self.account.id)
        self.assertTrue(self.store.use_backup_code(self.account.id, codes[0].lower()))
        self.assertTrue(self.store.use_backup_code(self.account.id, " " + codes[1].replace("-", "") + " "))

    def test_a_new_set_voids_the_old_one(self) -> None:
        old = self.store.generate_backup_codes(self.account.id)
        new = self.store.generate_backup_codes(self.account.id)
        self.assertFalse(self.store.use_backup_code(self.account.id, old[0]))
        self.assertTrue(self.store.use_backup_code(self.account.id, new[0]))
        self.assertEqual(self.store.backup_codes_remaining(self.account.id), 9)

    def test_codes_belong_to_one_account(self) -> None:
        other = self.register("sam@example.com")
        codes = self.store.generate_backup_codes(self.account.id)
        self.assertFalse(self.store.use_backup_code(other.id, codes[0]))

    def test_only_hmacs_are_stored(self) -> None:
        codes = self.store.generate_backup_codes(self.account.id)
        dump = self.dump()
        for code in codes:
            self.assertNotIn(code, dump)
            self.assertNotIn(code.replace("-", ""), dump)

    def test_guessing_is_limited(self) -> None:
        codes = self.store.generate_backup_codes(self.account.id)
        for _ in range(5):
            self.assertEqual(self.store.use_backup_code(self.account.id, "AAAA-AAAA").reason, "wrong")
        self.assertEqual(self.store.use_backup_code(self.account.id, codes[0]).reason, "throttled")
        self.clock.advance(minutes=15)
        self.assertTrue(self.store.use_backup_code(self.account.id, codes[0]))

    def test_sending_mails_the_set_to_the_account_and_then_stores_it(self) -> None:
        old = self.store.generate_backup_codes(self.account.id)
        self.store.send_backup_codes(self.account.id)
        mail = self.last_mail()
        self.assertEqual(mail["To"], "pat@example.com")
        mailed = self.last_backup_codes()
        self.assertEqual(len(mailed), 10)
        self.assertIn("Print them", mail.get_content())
        self.assertFalse(self.store.use_backup_code(self.account.id, old[0]))
        self.assertTrue(self.store.use_backup_code(self.account.id, mailed[0]))

    def test_a_set_that_could_not_be_mailed_leaves_the_old_one_working(self) -> None:
        old = self.store.generate_backup_codes(self.account.id)
        store = self.open_store(mailer=BrokenMailer())
        try:
            with self.assertRaises(MailError):
                store.send_backup_codes(self.account.id)
            self.assertTrue(store.use_backup_code(self.account.id, old[0]))
        finally:
            store.close()

    def test_a_set_is_sent_only_when_none_are_left(self) -> None:
        self.assertTrue(self.store.ensure_backup_codes(self.account.id))
        self.assertFalse(self.store.ensure_backup_codes(self.account.id))
        self.assertEqual(len([m for m in self.mails() if "backup codes" in m["Subject"]]), 1)

    def test_at_most_three_sets_an_hour(self) -> None:
        for _ in range(3):
            self.store.send_backup_codes(self.account.id)
        with self.assertRaises(Throttled):
            self.store.send_backup_codes(self.account.id)


# -- sessions --------------------------------------------------------------------------

class TestSessions(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.account = self.member()

    def test_the_first_factor_only_gives_a_partial_session(self) -> None:
        partial = self.store.begin_sign_in(self.account.id, method="password")
        self.assertEqual(partial.stage, "partial")
        session = self.store.get_session(partial.token)
        self.assertFalse(session.is_full)
        self.assertEqual(session.account.id, self.account.id)

    def test_the_second_step_swaps_in_a_full_session_with_a_new_token(self) -> None:
        partial = self.store.begin_sign_in(self.account.id, method="password", ip="10.0.0.9",
                                           user_agent="Firefox")
        self.store.send_code(self.account.id, "sign_in")
        step = self.store.finish_sign_in(partial.token, self.last_code())
        self.assertTrue(step)
        self.assertEqual(step.session.stage, "full")
        self.assertNotEqual(step.session.token, partial.token)
        self.assertIsNone(self.store.get_session(partial.token), "the partial token must stop working")
        session = self.store.get_session(step.session.token)
        self.assertTrue(session.is_full)
        self.assertEqual((session.ip, session.user_agent, session.method), ("10.0.0.9", "Firefox", "password"))

    def test_a_wrong_code_gives_no_session(self) -> None:
        partial = self.store.begin_sign_in(self.account.id, method="password")
        code = self.store.issue_code(self.account.id, "sign_in")
        step = self.store.finish_sign_in(partial.token, "000000" if code != "000000" else "111111")
        self.assertFalse(step)
        self.assertIsNone(step.session)
        self.assertEqual(step.check.reason, "wrong")
        self.assertFalse(self.store.get_session(partial.token).is_full)

    def test_a_backup_code_can_be_the_second_step(self) -> None:
        codes = self.store.generate_backup_codes(self.account.id)
        partial = self.store.begin_sign_in(self.account.id, method="google")
        step = self.store.finish_sign_in(partial.token, codes[3].lower())
        self.assertTrue(step)
        self.assertEqual(self.store.backup_codes_remaining(self.account.id), 9)

    def test_a_backup_code_cannot_confirm_an_address(self) -> None:
        codes = self.store.generate_backup_codes(self.account.id)
        partial = self.store.begin_sign_in(self.account.id, method="register")
        self.assertFalse(self.store.finish_sign_in(partial.token, codes[0], purpose="verify_email"))
        with self.assertRaises(ValueError):
            self.store.finish_sign_in(partial.token, "123456", purpose="reset_password")

    def test_a_full_session_cannot_be_finished_again(self) -> None:
        full = self.full_session(self.account)
        code = self.store.issue_code(self.account.id, "sign_in")
        self.assertFalse(self.store.finish_sign_in(full.token, code))

    def test_another_accounts_code_does_not_finish_a_sign_in(self) -> None:
        other = self.member("sam@example.com")
        partial = self.store.begin_sign_in(self.account.id, method="password")
        code = self.store.issue_code(other.id, "sign_in")
        self.assertFalse(self.store.finish_sign_in(partial.token, code))

    def test_a_partial_session_lasts_fifteen_minutes(self) -> None:
        partial = self.store.begin_sign_in(self.account.id, method="password")
        self.clock.advance(minutes=14, seconds=59)
        self.assertIsNotNone(self.store.get_session(partial.token))
        self.clock.advance(seconds=1)
        self.assertIsNone(self.store.get_session(partial.token))

    def test_two_idle_hours_end_a_session(self) -> None:
        full = self.full_session(self.account)
        self.clock.advance(hours=1, minutes=59)
        self.assertIsNotNone(self.store.get_session(full.token), "activity moves the idle clock")
        self.clock.advance(hours=1, minutes=59)
        self.assertIsNotNone(self.store.get_session(full.token))
        self.clock.advance(hours=2)
        self.assertIsNone(self.store.get_session(full.token))
        self.assertIsNone(self.store.get_session(full.token), "and it stays gone")

    def test_twelve_hours_end_a_session_however_busy(self) -> None:
        full = self.full_session(self.account)
        for _ in range(11):
            self.clock.advance(hours=1)
            self.assertIsNotNone(self.store.get_session(full.token))
        self.clock.advance(minutes=59, seconds=59)
        self.assertIsNotNone(self.store.get_session(full.token))
        self.clock.advance(seconds=1)
        self.assertIsNone(self.store.get_session(full.token))

    def test_tokens_are_stored_only_as_hashes(self) -> None:
        full = self.full_session(self.account)
        dump = self.dump()
        self.assertNotIn(full.token, dump)
        self.assertIn(hashlib.sha256(full.token.encode()).hexdigest(), dump)
        self.assertNotIn(full.token, repr(full))
        self.assertGreaterEqual(len(full.token), 43)

    def test_nonsense_tokens_find_nothing(self) -> None:
        for token in (None, "", "x" * 500, "not-a-token"):
            with self.subTest(token=token):
                self.assertIsNone(self.store.get_session(token))

    def test_signing_out_and_signing_out_everywhere(self) -> None:
        sessions = [self.full_session(self.account) for _ in range(3)]
        self.store.end_session(sessions[0].token)
        self.assertIsNone(self.store.get_session(sessions[0].token))
        self.assertEqual(self.store.end_all_sessions(self.account.id, keep_token=sessions[1].token), 1)
        self.assertIsNotNone(self.store.get_session(sessions[1].token))
        self.assertIsNone(self.store.get_session(sessions[2].token))
        self.store.end_all_sessions(self.account.id)
        self.assertIsNone(self.store.get_session(sessions[1].token))

    def test_the_session_list_has_no_tokens(self) -> None:
        full = self.full_session(self.account)
        listed = self.store.list_sessions(self.account.id)
        self.assertEqual(len(listed), 1)
        self.assertNotIn(full.token, repr(listed))
        self.assertNotIn("token_hash", listed[0])

    def test_csrf_tokens_are_tied_to_one_cookie(self) -> None:
        first, second = self.full_session(self.account), self.full_session(self.account)
        self.assertEqual(first.csrf_token, self.store.csrf_token(first.token))
        self.assertTrue(self.store.check_csrf(first.token, first.csrf_token))
        self.assertFalse(self.store.check_csrf(first.token, second.csrf_token))
        self.assertFalse(self.store.check_csrf(first.token, ""))
        self.assertFalse(self.store.check_csrf("", first.csrf_token))
        self.assertFalse(self.store.check_csrf(first.token, first.csrf_token[:-1] + "A"
                                               if first.csrf_token[-1] != "A" else first.csrf_token[:-1] + "B"))
        self.assertNotIn(first.token, first.csrf_token)

    def test_cookies_are_httponly_lax_and_secure_over_https(self) -> None:
        full = self.full_session(self.account)
        plain = session_cookie(full, secure=False)
        self.assertTrue(plain.startswith(f"{SESSION_COOKIE}={full.token}; "))
        for attribute in ("HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=43200"):
            self.assertIn(attribute, plain)
        self.assertNotIn("Secure", plain)
        self.assertIn("Secure", session_cookie(full, secure=True))
        partial = self.store.begin_sign_in(self.account.id, method="password")
        self.assertIn("Max-Age=900", session_cookie(partial, secure=True))
        cleared = clear_cookie(SESSION_COOKIE, secure=True)
        self.assertIn(f"{SESSION_COOKIE}=;", cleared)
        self.assertIn("Max-Age=0", cleared)
        with self.assertRaises(ValueError):
            cookie_header(SESSION_COOKIE, "a; Domain=evil.example.com", secure=True)


# -- blocking ----------------------------------------------------------------------------

class TestBlockDates(unittest.TestCase):
    def test_four_calendar_months_on_the_same_day_or_the_months_last(self) -> None:
        cases = {
            date(2027, 1, 31): date(2027, 5, 31),
            date(2027, 8, 31): date(2027, 12, 31),
            date(2026, 10, 31): date(2027, 2, 28),
            date(2027, 10, 31): date(2028, 2, 29),   # into a leap February
            date(2026, 11, 30): date(2027, 3, 30),
            date(2028, 2, 29): date(2028, 6, 29),    # from a leap day
            date(2026, 12, 31): date(2027, 4, 30),   # year rollover, shorter month
            date(2026, 9, 15): date(2027, 1, 15),
            date(2026, 11, 2): date(2027, 3, 2),
        }
        for start, expected in cases.items():
            with self.subTest(start=start):
                self.assertEqual(add_months(start, 4), expected)

    def test_other_month_counts(self) -> None:
        self.assertEqual(add_months(date(2027, 1, 31), 1), date(2027, 2, 28))
        self.assertEqual(add_months(date(2027, 1, 31), 12), date(2028, 1, 31))
        self.assertEqual(add_months(date(2027, 1, 31), 0), date(2027, 1, 31))


class TestBlocking(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner_account = self.owner()
        self.account = self.member()

    def test_a_block_lasts_four_months_and_signs_out_everywhere(self) -> None:
        full = self.full_session(self.account)
        code = self.store.issue_code(self.account.id, "reset_password")
        review = self.store.block(self.account.id, "Shared their screen with a stranger")
        self.assertEqual(review, date(2027, 3, 2))
        account = self.store.get_account(self.account.id)
        self.assertEqual(account.status, "blocked")
        self.assertEqual(account.review_after, review)
        self.assertEqual(account.blocked_at, "2026-11-02T09:00:00Z")
        self.assertEqual(account.block_reason, "Shared their screen with a stranger")
        self.assertFalse(account.can_use_portal)
        self.assertIsNone(self.store.get_session(full.token))
        self.assertFalse(self.store.verify_code(self.account.id, "reset_password", code))

    def test_the_review_date_follows_the_short_month_rule(self) -> None:
        self.clock.now = datetime(2027, 10, 31, 23, 30, tzinfo=timezone.utc)
        self.assertEqual(self.store.block(self.account.id, "reason"), date(2028, 2, 29))

    def test_a_block_cannot_be_lifted_before_its_review_date(self) -> None:
        review = self.store.block(self.account.id, "reason")
        self.clock.now = datetime.combine(review - timedelta(days=1), datetime.max.time(), timezone.utc)
        with self.assertRaises(TooEarly) as raised:
            self.store.unblock(self.account.id)
        self.assertEqual(raised.exception.review_after, review)
        self.assertEqual(str(raised.exception), "You can decide on 2027-03-02.")
        with self.assertRaises(TooEarly):
            self.store.extend_block(self.account.id)
        self.assertEqual(self.store.get_account(self.account.id).status, "blocked")

    def test_approving_cannot_get_round_the_review_date(self) -> None:
        self.store.block(self.account.id, "reason")
        with self.assertRaises(WrongState):
            self.store.approve(self.account.id)

    def test_unblocking_on_the_review_date_does_not_restore_access(self) -> None:
        review = self.store.block(self.account.id, "reason")
        self.clock.now = datetime.combine(review, datetime.min.time(), timezone.utc)
        self.assertTrue(self.store.get_account(self.account.id).review_due(self.clock.now.date()))
        account = self.store.unblock(self.account.id)
        self.assertEqual(account.status, "unblocked")
        self.assertFalse(account.can_use_portal)
        account = self.store.request_again(self.account.id, "I would like back in")
        self.assertEqual(account.status, "pending")
        self.assertEqual(self.store.approve(self.account.id).status, "approved")

    def test_keeping_a_block_adds_four_months_from_the_day_decided(self) -> None:
        review = self.store.block(self.account.id, "reason")
        self.clock.now = datetime(2027, 3, 31, 12, tzinfo=timezone.utc)
        self.assertGreater(self.clock.now.date(), review)
        self.assertEqual(self.store.extend_block(self.account.id), date(2027, 7, 31))
        with self.assertRaises(TooEarly):
            self.store.unblock(self.account.id)

    def test_the_owner_can_never_be_blocked(self) -> None:
        with self.assertRaises(OwnerProtected):
            self.store.block(self.owner_account.id, "reason")
        self.store.connection.execute("UPDATE accounts SET role = 'member' WHERE id = ?", (self.owner_account.id,))
        with self.assertRaises(OwnerProtected, msg="the owner email alone protects the account"):
            self.store.block(self.owner_account.id, "reason")
        with self.assertRaises(OwnerProtected):
            self.store.remove(self.owner_account.id)

    def test_a_block_needs_a_reason_and_happens_once(self) -> None:
        with self.assertRaises(InvalidInput):
            self.store.block(self.account.id, "   ")
        self.store.block(self.account.id, "reason")
        with self.assertRaises(WrongState):
            self.store.block(self.account.id, "again")

    def test_only_a_blocked_account_can_be_unblocked(self) -> None:
        with self.assertRaises(WrongState):
            self.store.unblock(self.account.id)
        with self.assertRaises(WrongState):
            self.store.request_again(self.account.id)

    def test_blocked_people_are_listed_with_due_reviews_counted(self) -> None:
        other = self.member("sam@example.com")
        self.store.block(self.account.id, "first")
        self.clock.advance(days=40)
        self.store.block(other.id, "second")
        self.assertEqual([a.id for a in self.store.blocked_accounts()], [self.account.id, other.id])
        self.assertEqual(self.store.owner_summary()["reviews_due"], 0)
        self.clock.now = datetime(2027, 3, 2, tzinfo=timezone.utc)
        self.assertEqual(self.store.owner_summary()["reviews_due"], 1)


# -- strikes ------------------------------------------------------------------------------

class TestStrikes(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.owner()
        self.account = self.member()

    def strike(self, **options: object):
        options.setdefault("reason", "asked for an owner page")
        options.setdefault("path", "/owner")
        options.setdefault("ip", "10.0.0.7")
        return self.store.record_strike(self.account.id, **options)

    def test_three_strikes_in_a_day_block_and_tell_the_owner(self) -> None:
        self.assertEqual(self.strike().strikes, 1)
        self.clock.advance(hours=10)
        second = self.strike()
        self.assertFalse(second.blocked)
        self.assertEqual(second.strikes, 2)
        self.assertEqual([m for m in self.mails() if m["To"] == OWNER and "alert" in m["Subject"]], [])
        self.clock.advance(hours=13, minutes=59)
        third = self.strike(path="/owner/members")
        self.assertTrue(third.blocked)
        self.assertEqual(third.review_after, add_months(self.clock.now.date(), 4))
        account = self.store.get_account(self.account.id)
        self.assertEqual(account.status, "blocked")
        self.assertIn("Automatic", account.block_reason)
        alert = self.last_mail(OWNER)
        self.assertIn("blocked automatically", alert["Subject"])
        self.assertIn("pat@example.com", alert.get_content())
        self.assertIn("/owner/members", alert.get_content())

    def test_strikes_more_than_a_day_apart_do_not_add_up(self) -> None:
        self.strike()
        self.clock.advance(hours=12)
        self.strike()
        self.clock.advance(hours=12)
        outcome = self.strike()
        self.assertFalse(outcome.blocked, "the first strike is exactly 24 hours old")
        self.assertEqual(outcome.strikes, 2)
        self.assertEqual(self.store.get_account(self.account.id).status, "approved")

    def test_an_owner_action_or_tampered_form_blocks_at_once(self) -> None:
        outcome = self.strike(reason="POST to an owner action", path="/owner/requests/1/approve",
                              immediate=True)
        self.assertTrue(outcome.blocked)
        self.assertEqual(self.store.get_account(self.account.id).status, "blocked")
        self.assertIn("blocked automatically", self.last_mail(OWNER)["Subject"])

    def test_the_owner_collects_no_strikes(self) -> None:
        owner = self.store.find_account(OWNER)
        outcome = self.store.record_strike(owner.id, reason="r", immediate=True)
        self.assertFalse(outcome.blocked)
        self.assertEqual(self.store.get_account(owner.id).status, "approved")

    def test_a_strike_without_an_account_is_recorded_and_blocks_nothing(self) -> None:
        outcome = self.store.record_strike(None, reason="probing", path="/owner", ip="203.0.113.5")
        self.assertFalse(outcome.blocked)
        kinds = [e["kind"] for e in self.store.recent_security_events()]
        self.assertIn("strike", kinds)

    def test_an_auto_block_without_mail_is_still_a_block_and_says_so(self) -> None:
        store = self.open_store(mailer=None)
        try:
            outcome = store.record_strike(self.account.id, reason="r", immediate=True)
            self.assertTrue(outcome.blocked)
            self.assertIn("owner_alert_unsent", [e["kind"] for e in store.recent_security_events()])
        finally:
            store.close()

    def test_strikes_are_listed_for_the_owner(self) -> None:
        self.strike(path="/owner/blocked")
        listed = self.store.account_strikes(self.account.id)
        self.assertEqual(listed[0]["path"], "/owner/blocked")
        self.assertEqual(listed[0]["ip"], "10.0.0.7")


# -- throttling ----------------------------------------------------------------------------

class TestThrottling(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.register()
        self.clock.advance(minutes=15)  # registering counted as an attempt

    def test_ten_sign_in_attempts_per_address_in_fifteen_minutes(self) -> None:
        for n in range(10):
            self.store.authenticate_password(f"person{n}@example.com", "wrong password!", ip="203.0.113.9")
        with self.assertRaises(Throttled) as raised:
            self.store.authenticate_password("pat@example.com", PASSWORD, ip="203.0.113.9")
        self.assertLessEqual(raised.exception.retry_after, 15 * 60)
        self.assertIn("Try again in", str(raised.exception))
        self.assertIsNotNone(self.store.authenticate_password("pat@example.com", PASSWORD, ip="203.0.113.10"))
        self.clock.advance(minutes=15)
        self.assertIsNotNone(self.store.authenticate_password("pat@example.com", PASSWORD, ip="203.0.113.9"))

    def test_ten_sign_in_attempts_per_email_from_anywhere(self) -> None:
        for n in range(10):
            self.store.authenticate_password("pat@example.com", "wrong password!", ip=f"198.51.100.{n}")
        with self.assertRaises(Throttled):
            self.store.authenticate_password("PAT@example.com", PASSWORD, ip="198.51.100.200")

    def test_google_attempts_count_too(self) -> None:
        for _ in range(10):
            self.store.note_sign_in_attempt(ip="192.0.2.1", email="g@example.com")
        with self.assertRaises(Throttled):
            self.store.note_sign_in_attempt(ip="192.0.2.1")

    def test_a_generic_window(self) -> None:
        window = timedelta(minutes=1)
        self.assertTrue(all(self.store.allow("probe:192.0.2.7", 3, window) for _ in range(3)))
        self.assertFalse(self.store.allow("probe:192.0.2.7", 3, window))
        self.assertTrue(self.store.allow("probe:192.0.2.8", 3, window))
        self.clock.advance(minutes=1)
        self.assertTrue(self.store.allow("probe:192.0.2.7", 3, window))

    def test_keys_are_not_kept_in_plain_text(self) -> None:
        self.store.note_sign_in_attempt(ip="192.0.2.1", email="typed-password-here@example.com")
        self.assertNotIn("typed-password-here", self.dump())


# -- owner and decisions -----------------------------------------------------------------------

class TestOwnerAndDecisions(StoreTestCase):
    def test_the_owner_is_approved_from_the_start(self) -> None:
        owner = self.register(OWNER, confirm=False)
        self.assertEqual((owner.role, owner.status), ("owner", "approved"))
        self.assertTrue(self.store.is_owner(owner))
        self.assertEqual(self.store.owner_email_source(), "environment")

    def test_the_owner_email_can_be_stored_and_moved(self) -> None:
        store = self.open_store(environ={})
        try:
            self.assertIsNone(store.owner_email())
            first = store.register_password_account("first@example.com", PASSWORD, display_name="F")
            self.assertEqual(first.role, "member")
            store.set_owner_email("First@Example.com")
            self.assertEqual(store.owner_email_source(), "store")
            self.assertTrue(store.is_owner(store.get_account(first.id)))
            second = store.register_password_account("second@example.com", PASSWORD, display_name="S")
            store.set_owner_email("second@example.com")
            self.assertEqual(store.get_account(first.id).role, "member")
            self.assertEqual(store.get_account(second.id).role, "owner")
            self.assertEqual(store.get_account(second.id).status, "approved")
        finally:
            store.close()

    def test_the_environment_wins_over_the_stored_owner(self) -> None:
        self.store.set_owner_email("someone-else@example.com")
        self.assertEqual(self.store.owner_email(), OWNER)

    def test_a_bad_owner_setting_is_reported(self) -> None:
        store = self.open_store(environ={"SDDU13_OWNER_EMAIL": "not an address"})
        try:
            with self.assertRaises(ConfigError):
                store.owner_email()
        finally:
            store.close()

    def test_a_new_request_is_pending_until_the_owner_decides(self) -> None:
        account = self.register()
        self.assertEqual(account.status, "pending")
        self.assertFalse(account.can_use_portal)
        self.assertEqual([a.id for a in self.store.pending_requests()], [account.id])
        self.assertEqual(self.store.approve(account.id).status, "approved")
        self.assertTrue(self.store.get_account(account.id).can_use_portal)

    def test_an_unconfirmed_address_is_not_a_request_yet(self) -> None:
        account = self.register(confirm=False)
        self.assertEqual(self.store.pending_requests(), [])
        with self.assertRaises(WrongState):
            self.store.approve(account.id)

    def test_decline_and_remove(self) -> None:
        declined = self.register("dee@example.com")
        self.assertEqual(self.store.decline(declined.id).status, "declined")
        with self.assertRaises(WrongState):
            self.store.remove(declined.id)
        member = self.member()
        full = self.full_session(member)
        self.assertEqual(self.store.remove(member.id).status, "revoked")
        self.assertIsNone(self.store.get_session(full.token))

    def test_an_email_has_one_account(self) -> None:
        self.register()
        with self.assertRaises(EmailTaken):
            self.store.register_password_account("PAT@example.com", PASSWORD, display_name="Again")

    def test_the_summary_counts(self) -> None:
        self.owner()
        self.member("a@example.com")
        self.member("b@example.com")
        self.register("c@example.com")
        self.register("d@example.com", confirm=False)
        blocked = self.member("e@example.com")
        self.store.block(blocked.id, "reason")
        self.assertEqual(self.store.owner_summary(),
                         {"pending": 1, "members": 2, "blocked": 1, "reviews_due": 0})

    def test_names_and_reasons_lose_control_characters(self) -> None:
        account = self.store.register_password_account(
            "x@example.com", PASSWORD, display_name="Eve‮\r\nAdmin", reason="line one\r\nline two\x00")
        self.assertEqual(account.display_name, "Eve   Admin")
        self.assertEqual(account.request_reason, "line one\nline two")


# -- Google accounts ---------------------------------------------------------------------------

class TestGoogleAccounts(StoreTestCase):
    def test_a_google_request_is_confirmed_and_pending(self) -> None:
        account = self.store.create_google_account("g-100", "gee@example.com", display_name="Gee",
                                                   company="Acme", reason="work")
        self.assertTrue(account.email_verified)
        self.assertTrue(account.google_linked)
        self.assertFalse(account.has_password)
        self.assertEqual(account.status, "pending")
        self.assertEqual(self.store.account_for_google("g-100", "whatever@example.com").id, account.id)

    def test_the_owner_can_use_google(self) -> None:
        account = self.store.create_google_account("g-owner", OWNER, display_name="Owner")
        self.assertEqual((account.role, account.status), ("owner", "approved"))

    def test_an_unknown_identity_has_no_account(self) -> None:
        self.assertIsNone(self.store.account_for_google("g-404", "nobody@example.com"))

    def test_google_links_to_a_confirmed_password_account(self) -> None:
        account = self.register()
        linked = self.store.account_for_google("g-200", "pat@example.com")
        self.assertEqual(linked.id, account.id)
        self.assertTrue(linked.google_linked)
        self.assertTrue(linked.has_password)

    def test_google_takes_over_an_address_nobody_confirmed(self) -> None:
        squatter = self.register(confirm=False)
        linked = self.store.account_for_google("g-300", "pat@example.com")
        self.assertEqual(linked.id, squatter.id)
        self.assertFalse(linked.has_password, "whoever typed this address in never proved it")
        self.assertTrue(linked.email_verified)
        self.assertIsNone(self.store.authenticate_password("pat@example.com", PASSWORD))

    def test_a_second_google_account_cannot_claim_a_linked_address(self) -> None:
        self.store.create_google_account("g-400", "gee@example.com", display_name="Gee")
        self.assertIsNone(self.store.account_for_google("g-401", "gee@example.com"))
        with self.assertRaises(EmailTaken):
            self.store.create_google_account("g-402", "gee@example.com", display_name="Gee")


# -- passwords: change and reset -------------------------------------------------------------------

class TestPasswordChanges(StoreTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.account = self.member()

    def test_a_reset_by_email_code_signs_out_everywhere(self) -> None:
        sessions = [self.full_session(self.account) for _ in range(2)]
        self.store.request_password_reset("pat@example.com", ip="10.0.0.5")
        code = self.last_code("pat@example.com")
        self.assertIn("resetting", self.last_mail().get_content())
        result = self.store.reset_password("pat@example.com", code, "a brand new passphrase", ip="10.0.0.5")
        self.assertTrue(result)
        for session in sessions:
            self.assertIsNone(self.store.get_session(session.token))
        self.assertIsNone(self.store.authenticate_password("pat@example.com", PASSWORD))
        self.assertIsNotNone(self.store.authenticate_password("pat@example.com", "a brand new passphrase"))

    def test_a_short_new_password_does_not_spend_the_code(self) -> None:
        self.store.request_password_reset("pat@example.com")
        code = self.last_code()
        with self.assertRaises(InvalidInput):
            self.store.reset_password("pat@example.com", code, "short")
        self.assertTrue(self.store.reset_password("pat@example.com", code, "long enough now!"))

    def test_an_unknown_address_gets_no_mail_and_no_hint(self) -> None:
        self.store.request_password_reset("nobody@example.com")
        self.store.request_password_reset("not even an address")
        self.assertEqual([m for m in self.mails() if "reset" in m["Subject"]], [])
        result = self.store.reset_password("nobody@example.com", "123456", "long enough now!")
        self.assertEqual(result.reason, "none")

    def test_a_wrong_reset_code_changes_nothing(self) -> None:
        self.store.request_password_reset("pat@example.com")
        code = self.last_code()
        self.assertFalse(self.store.reset_password("pat@example.com", "000000" if code != "000000" else "1" * 6,
                                                   "long enough now!"))
        self.assertIsNotNone(self.store.authenticate_password("pat@example.com", PASSWORD))

    def test_a_reset_request_does_not_wait_for_the_mail_server(self) -> None:
        release = threading.Event()
        sent: list[str] = []

        class SlowMailer:
            def send(self, to: str, subject: str, body: str) -> None:
                release.wait(10)
                sent.append(to)

            def describe(self) -> str:
                return "slow"

        store = self.open_store(mailer=SlowMailer())
        try:
            store.request_password_reset("pat@example.com")
            self.assertEqual(sent, [], "the request returned before the mail went")
            release.set()
            store.wait_for_mail()
            self.assertEqual(sent, ["pat@example.com"])
        finally:
            release.set()
            store.close()

    def test_a_reset_mail_that_fails_in_the_background_voids_its_code(self) -> None:
        store = self.open_store(mailer=BrokenMailer())
        try:
            store.request_password_reset("pat@example.com")
            store.wait_for_mail()
            self.assertEqual(store.verify_code(self.account.id, "reset_password", "123456").reason, "none")
            self.assertIn("mail_failed", [e["kind"] for e in store.recent_security_events()])
        finally:
            store.close()

    def test_asking_again_with_a_taken_address_tells_its_holder(self) -> None:
        for _ in range(4):
            self.store.notify_existing_account("PAT@example.com")
        self.store.notify_existing_account("nobody@example.com")
        notices = [m for m in self.mails() if "access request" in m["Subject"]]
        self.assertEqual([m["To"] for m in notices], ["pat@example.com"] * 3, "three an hour at most")

    def test_the_owner_can_send_a_reset_code_but_never_sees_it(self) -> None:
        self.store.send_reset_code(self.account.id)
        self.assertEqual(self.last_mail()["To"], "pat@example.com")
        events = repr(self.store.recent_security_events())
        self.assertNotIn(self.last_code(), events)

    def test_changing_needs_the_current_password_and_signs_out_the_rest(self) -> None:
        here, elsewhere = self.full_session(self.account), self.full_session(self.account)
        self.assertFalse(self.store.change_password(self.account.id, "not my password", "another passphrase"))
        self.assertTrue(self.store.change_password(self.account.id, PASSWORD, "another passphrase",
                                                   keep_token=here.token))
        self.assertIsNotNone(self.store.get_session(here.token))
        self.assertIsNone(self.store.get_session(elsewhere.token))
        self.assertIsNotNone(self.store.authenticate_password("pat@example.com", "another passphrase"))

    def test_a_google_only_account_has_no_current_password_to_change(self) -> None:
        google = self.store.create_google_account("g-1", "gee@example.com", display_name="Gee")
        self.assertFalse(self.store.change_password(google.id, "", "another passphrase"))


# -- announcements, events and the audit trail ---------------------------------------------------------

class TestAnnouncementsAndEvents(StoreTestCase):
    def test_portal_updates_can_be_posted_listed_and_deleted(self) -> None:
        first = self.store.post_announcement("Maintenance", "The portal is down Sunday 9-10.")
        self.clock.advance(hours=1)
        second = self.store.post_announcement("New page", "The data documentation page is live.")
        self.assertEqual([a["id"] for a in self.store.list_announcements()], [second, first])
        self.assertTrue(self.store.delete_announcement(first))
        self.assertFalse(self.store.delete_announcement(first))
        self.assertEqual([a["id"] for a in self.store.list_announcements()], [second])
        with self.assertRaises(InvalidInput):
            self.store.post_announcement("  ", "body")

    def test_important_events_reach_the_audit_trail_without_personal_details(self) -> None:
        with DataCenter(root=self.root) as center:
            center.provision()
        store = self.open_store(mirror_audit=True)
        try:
            account = store.register_password_account("pat@example.com", PASSWORD, display_name="Pat")
            store.block(account.id, "private reason")
        finally:
            store.close()
        with DataCenter(root=self.root) as center:
            events = [e for e in center.recent_events(limit=10) if e["action"].startswith("portal_")]
        actions = [e["action"] for e in events]
        self.assertIn("portal_block", actions)
        self.assertIn("portal_account_requested", actions)
        for event in events:
            self.assertEqual(event["detail"], f"account #{account.id}")
            self.assertEqual(event["actor"], "portal")

    def test_the_mirror_does_not_need_the_data_center(self) -> None:
        store = self.open_store(mirror_audit=True)
        try:
            account = store.register_password_account("pat@example.com", PASSWORD, display_name="Pat")
            store.block(account.id, "reason")
        finally:
            store.close()
        self.assertFalse((self.root / "audit.db").exists(), "mirroring must not create a database")

    def test_no_secret_is_stored_logged_or_printed(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root_logger = logging.getLogger()
        old_level = root_logger.level
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.DEBUG)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                account = self.store.register_password_account("pat@example.com", PASSWORD,
                                                                display_name="Pat", ip="10.0.0.1")
                partial = self.store.begin_sign_in(account.id, method="register")
                self.store.send_code(account.id, "verify_email")
                verify_code = self.last_code()
                step = self.store.finish_sign_in(partial.token, verify_code, purpose="verify_email")
                self.store.ensure_backup_codes(account.id)
                backup = self.last_backup_codes()
                self.store.request_password_reset("pat@example.com")
                reset_code = self.last_code()
                self.store.reset_password("pat@example.com", reset_code, "a different passphrase")
        finally:
            root_logger.removeHandler(handler)
            root_logger.setLevel(old_level)

        secrets_used = [PASSWORD, "a different passphrase", partial.token, step.session.token,
                        verify_code, reset_code, *backup]
        dump = self.dump()
        captured = stream.getvalue() + out.getvalue() + err.getvalue()
        events = repr(self.store.recent_security_events(limit=500))
        for secret in secrets_used:
            self.assertNotIn(secret, captured)
            self.assertNotIn(secret, events)
            if len(secret) > 6:
                self.assertNotIn(secret, dump)
            else:
                self.assertNotIn(f"'{secret}'", dump)


# -- housekeeping ---------------------------------------------------------------------------------------

class TestPurge(StoreTestCase):
    def test_expired_things_and_unconfirmed_accounts_go(self) -> None:
        kept = self.member()
        unconfirmed = self.register("ghost@example.com", confirm=False)
        full = self.full_session(kept)
        flow = self.store.begin_oauth_flow()
        self.clock.advance(hours=24, seconds=1)
        removed = self.store.purge_expired()
        self.assertEqual(removed["sessions"], 1)
        self.assertEqual(removed["flows"], 1)
        self.assertEqual(removed["unconfirmed_accounts"], 1)
        self.assertIsNone(self.store.get_account(unconfirmed.id))
        self.assertIsNotNone(self.store.get_account(kept.id))
        self.assertIsNone(self.store.get_session(full.token))
        self.assertFalse(self.store.consume_oauth_flow(flow))

    def test_a_blocked_account_is_never_purged(self) -> None:
        account = self.register(confirm=False)
        self.store.block(account.id, "reason")
        self.clock.advance(days=2)
        self.store.purge_expired()
        self.assertIsNotNone(self.store.get_account(account.id), "purging must not lift a block")


if __name__ == "__main__":
    unittest.main()
