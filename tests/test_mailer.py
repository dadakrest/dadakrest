"""Tests for the portal's mailer: configuration, the outbox, SMTP and the messages."""

from __future__ import annotations

import contextlib
import email
import email.policy
import io
import logging
import smtplib
import ssl
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datacenter.mailer import (  # noqa: E402
    IGNORE_LINE,
    MailError,
    MailNotConfigured,
    OutboxMailer,
    SmtpMailer,
    SmtpSettings,
    backup_codes_email,
    code_email,
    existing_account_email,
    mailer_from_env,
    missing_settings,
    owner_alert_email,
    smtp_settings_from_env,
)


def read_outbox(directory: Path) -> list[email.message.EmailMessage]:
    return [email.message_from_bytes(path.read_bytes(), policy=email.policy.default)
            for path in sorted(directory.glob("*.eml"))]


class TestMessages(unittest.TestCase):
    def test_each_code_email_says_what_it_is_for_and_when_it_expires(self) -> None:
        purposes = {"sign_in": "signing in", "verify_email": "confirming this email address",
                    "reset_password": "resetting your portal password"}
        for purpose, phrase in purposes.items():
            with self.subTest(purpose=purpose):
                subject, body = code_email(purpose, "493017")
                self.assertIn("493017", body)
                self.assertIn(phrase, body)
                self.assertIn("expires in 10 minutes", body)
                self.assertIn(IGNORE_LINE, body)
                self.assertNotIn("493017", subject, "subjects show up in notifications and logs")

    def test_the_ignore_line_is_word_for_word(self) -> None:
        self.assertEqual(IGNORE_LINE, "If you did not ask for this, ignore this email and tell the owner.")

    def test_an_unknown_purpose_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            code_email("make_me_owner", "123456")

    def test_backup_codes_email_lists_every_code_and_says_to_print_them(self) -> None:
        codes = [f"K7QM-2XR{n}" for n in range(2, 10)]
        subject, body = backup_codes_email(codes)
        for code in codes:
            self.assertIn(code, body)
            self.assertNotIn(code, subject)
        self.assertIn("Print them", body)
        self.assertIn("off shared devices", body)
        self.assertIn(IGNORE_LINE, body)

    def test_code_and_backup_bodies_stay_plain_text(self) -> None:
        for _, body in (code_email("sign_in", "000111"), backup_codes_email(["ABCD-EFGH"]),
                        existing_account_email()):
            self.assertTrue(all(len(line) < 78 for line in body.splitlines()))

    def test_owner_alert_lists_its_lines(self) -> None:
        subject, body = owner_alert_email("An account was blocked automatically", ["Account: a@b.co"])
        self.assertIn("blocked automatically", subject)
        self.assertIn("- Account: a@b.co", body)
        self.assertIn("never shows", body)


class TestOutbox(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.outbox = Path(self._tmp.name) / "outbox"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_a_message_becomes_an_eml_file(self) -> None:
        mailer = OutboxMailer(self.outbox, sender="portal@example.com")
        subject, body = code_email("sign_in", "246810")
        mailer.send("person@example.com", subject, body)
        messages = read_outbox(self.outbox)
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message["To"], "person@example.com")
        self.assertEqual(message["From"], "portal@example.com")
        self.assertEqual(message["Subject"], subject)
        self.assertIn("246810", message.get_content())
        self.assertEqual(message.get_content_type(), "text/plain")

    def test_outbox_files_and_folder_are_private(self) -> None:
        OutboxMailer(self.outbox).send("person@example.com", "s", "b")
        self.assertEqual(stat.S_IMODE(self.outbox.stat().st_mode) & 0o077, 0)
        for path in self.outbox.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_every_message_gets_its_own_file(self) -> None:
        mailer = OutboxMailer(self.outbox)
        for n in range(5):
            mailer.send("person@example.com", f"s{n}", "b")
        self.assertEqual(len(list(self.outbox.glob("*.eml"))), 5)

    def test_a_newline_in_an_address_cannot_add_a_header(self) -> None:
        with self.assertRaises(MailError):
            OutboxMailer(self.outbox).send("a@example.com\r\nBcc: thief@example.com", "s", "b")
        self.assertFalse(self.outbox.exists() and any(self.outbox.iterdir()))

    def test_sending_logs_neither_the_code_nor_the_body(self) -> None:
        subject, body = code_email("sign_in", "975310")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        old_level = root.level
        root.addHandler(handler)
        root.setLevel(logging.DEBUG)
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                OutboxMailer(self.outbox).send("person@example.com", subject, body)
        finally:
            root.removeHandler(handler)
            root.setLevel(old_level)
        self.assertIn("person@example.com", stream.getvalue(), "the log should say something was sent")
        for captured in (stream.getvalue(), out.getvalue(), err.getvalue()):
            self.assertNotIn("975310", captured)


class FakeSMTP:
    """Records what the mailer does; can be told to fail at one step."""

    instances: list["FakeSMTP"] = []
    fail_at: str = ""

    def __init__(self, host: str, port: int, timeout: float = 0, context: object = None) -> None:
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls: list[str] = []
        self.sent: list[object] = []
        FakeSMTP.instances.append(self)

    def _step(self, name: str) -> None:
        self.calls.append(name)
        if FakeSMTP.fail_at == name:
            if name == "login":
                raise smtplib.SMTPAuthenticationError(535, b"5.7.8 bad credentials")
            raise smtplib.SMTPServerDisconnected("gone")

    def ehlo(self) -> None:
        self._step("ehlo")

    def starttls(self, context: object = None) -> None:
        self.tls_context = context
        self._step("starttls")

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)
        self._step("login")

    def send_message(self, message: object) -> None:
        self._step("send")
        self.sent.append(message)

    def set_debuglevel(self, level: int) -> None:
        self._step("debug")

    def quit(self) -> None:
        self._step("quit")

    def close(self) -> None:
        self.calls.append("close")


class TestSmtp(unittest.TestCase):
    def setUp(self) -> None:
        FakeSMTP.instances = []
        FakeSMTP.fail_at = ""
        self.settings = SmtpSettings(host="smtp.example.com", port=587, sender="portal@example.com",
                                     tls="starttls", user="portal@example.com",
                                     password="hunter2-app-password")

    def mailer(self, settings: SmtpSettings | None = None) -> SmtpMailer:
        return SmtpMailer(settings or self.settings, smtp_class=FakeSMTP, smtp_ssl_class=FakeSMTP)

    def test_starttls_comes_before_the_login(self) -> None:
        self.mailer().send("person@example.com", "subject", "body 123456")
        client = FakeSMTP.instances[0]
        self.assertEqual(client.calls, ["ehlo", "starttls", "ehlo", "login", "send", "quit"])
        self.assertEqual((client.host, client.port), ("smtp.example.com", 587))
        self.assertIsNotNone(client.tls_context)
        self.assertEqual(client.login_args, ("portal@example.com", "hunter2-app-password"))
        self.assertNotIn("debug", client.calls, "debug output would print the conversation")

    def test_implicit_tls_uses_the_ssl_client(self) -> None:
        settings = SmtpSettings(host="smtp.example.com", port=465, sender="p@example.com", tls="ssl")
        self.mailer(settings).send("person@example.com", "subject", "body")
        client = FakeSMTP.instances[0]
        self.assertIsNotNone(client.context, "SMTP_SSL must get a verifying context")
        self.assertEqual(client.calls, ["ehlo", "send", "quit"], "no login without a user")

    def test_the_certificate_is_verified(self) -> None:
        self.mailer().send("person@example.com", "subject", "body")
        context = FakeSMTP.instances[0].tls_context
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_failures_become_mail_errors_without_the_password_or_body(self) -> None:
        for step in ("starttls", "login", "send"):
            with self.subTest(step=step):
                FakeSMTP.fail_at = step
                with self.assertRaises(MailError) as raised:
                    self.mailer().send("person@example.com", "subject", "the code is 864209")
                message = str(raised.exception)
                self.assertNotIn("hunter2", message)
                self.assertNotIn("864209", message)
                self.assertIsNone(raised.exception.__cause__)
                self.assertIn("quit", FakeSMTP.instances[-1].calls, "the connection is closed")

    def test_probe_connects_without_sending(self) -> None:
        self.mailer().probe()
        client = FakeSMTP.instances[0]
        self.assertNotIn("send", client.calls)
        self.assertNotIn("login", client.calls)
        self.assertIn("ehlo", client.calls)

    def test_probe_reports_an_unreachable_server(self) -> None:
        FakeSMTP.fail_at = "ehlo"
        with self.assertRaises(MailError):
            self.mailer().probe()

    def test_the_password_is_not_in_the_settings_repr(self) -> None:
        self.assertNotIn("hunter2", repr(self.settings))
        self.assertNotIn("hunter2", self.mailer().describe())

    def test_plaintext_smtp_is_not_an_option(self) -> None:
        with self.assertRaises(MailError):
            SmtpMailer(SmtpSettings(host="h", port=25, sender="p@example.com", tls="none"))


class TestConfiguration(unittest.TestCase):
    def test_nothing_set_names_what_is_missing(self) -> None:
        with self.assertRaises(MailNotConfigured) as raised:
            mailer_from_env({})
        self.assertEqual(raised.exception.missing, ["SDDU13_SMTP_HOST", "SDDU13_SMTP_FROM"])
        self.assertIn("SDDU13_SMTP_HOST", str(raised.exception))
        self.assertIn("--mail-outbox", str(raised.exception))

    def test_a_user_needs_a_password_and_the_other_way_round(self) -> None:
        base = {"SDDU13_SMTP_HOST": "smtp.example.com", "SDDU13_SMTP_FROM": "p@example.com"}
        self.assertEqual(missing_settings({**base, "SDDU13_SMTP_USER": "u"}), ["SDDU13_SMTP_PASSWORD"])
        self.assertEqual(missing_settings({**base, "SDDU13_SMTP_PASSWORD": "p"}), ["SDDU13_SMTP_USER"])
        self.assertEqual(missing_settings(base), [])

    def test_ports_default_by_tls_mode(self) -> None:
        base = {"SDDU13_SMTP_HOST": "smtp.example.com", "SDDU13_SMTP_FROM": "p@example.com"}
        self.assertEqual(smtp_settings_from_env(base).port, 587)
        self.assertEqual(smtp_settings_from_env(base).tls, "starttls")
        self.assertEqual(smtp_settings_from_env({**base, "SDDU13_SMTP_TLS": "ssl"}).port, 465)
        self.assertEqual(smtp_settings_from_env({**base, "SDDU13_SMTP_PORT": "2525"}).port, 2525)

    def test_bad_values_are_named(self) -> None:
        base = {"SDDU13_SMTP_HOST": "smtp.example.com", "SDDU13_SMTP_FROM": "p@example.com"}
        for extra, word in (({"SDDU13_SMTP_TLS": "none"}, "SDDU13_SMTP_TLS"),
                            ({"SDDU13_SMTP_PORT": "smtp"}, "SDDU13_SMTP_PORT"),
                            ({"SDDU13_SMTP_PORT": "70000"}, "SDDU13_SMTP_PORT")):
            with self.subTest(extra=extra):
                with self.assertRaises(MailNotConfigured) as raised:
                    smtp_settings_from_env({**base, **extra})
                self.assertIn(word, str(raised.exception))

    def test_full_settings_give_an_smtp_mailer(self) -> None:
        mailer = mailer_from_env({"SDDU13_SMTP_HOST": "smtp.gmail.com", "SDDU13_SMTP_FROM": "p@example.com",
                                  "SDDU13_SMTP_USER": "p@example.com", "SDDU13_SMTP_PASSWORD": "app-pw"})
        self.assertIsInstance(mailer, SmtpMailer)
        self.assertEqual(mailer.settings.password, "app-pw")

    def test_an_outbox_wins_over_smtp(self) -> None:
        env = {"SDDU13_SMTP_HOST": "smtp.gmail.com", "SDDU13_SMTP_FROM": "p@example.com",
               "SDDU13_MAIL_OUTBOX": "/tmp/from-env"}
        self.assertIsInstance(mailer_from_env(env), OutboxMailer)
        self.assertEqual(mailer_from_env(env).directory, Path("/tmp/from-env"))
        self.assertEqual(mailer_from_env(env, outbox="/tmp/from-flag").directory, Path("/tmp/from-flag"))
        self.assertIsInstance(mailer_from_env({}, outbox="/tmp/x"), OutboxMailer)


if __name__ == "__main__":
    unittest.main()
