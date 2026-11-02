"""Email for the portal's sign-in: security codes, backup codes, owner alerts.

Every second sign-in step goes by email, so the portal cannot offer sign-in
until it can send mail. There are two ways to send:

SMTP (production)
    `SmtpMailer`, configured by environment variables. TLS is always on:
    STARTTLS on port 587 (`SDDU13_SMTP_TLS=starttls`, the default) or
    implicit TLS on port 465 (`SDDU13_SMTP_TLS=ssl`). There is no plaintext
    option. The server certificate is checked against the system's CA store.

        SDDU13_SMTP_HOST      the server, e.g. smtp.gmail.com        (required)
        SDDU13_SMTP_FROM      the sender address                      (required)
        SDDU13_SMTP_USER      login name     } set both, or neither for a
        SDDU13_SMTP_PASSWORD  login password } relay that needs no login
        SDDU13_SMTP_PORT      default 587 for starttls, 465 for ssl
        SDDU13_SMTP_TLS       "starttls" (default) or "ssl"

Outbox (development and tests)
    `OutboxMailer` writes each message as an `.eml` file into a directory
    (`--mail-outbox DIR` or `SDDU13_MAIL_OUTBOX`) instead of sending it. Those
    files hold the codes in plain text, which is the point of the mode, so it
    is for a machine only the developer uses; the files are created 0600.

When neither is configured, `mailer_from_env` raises `MailNotConfigured`
naming exactly which settings are missing. Nothing skips the second step
because mail is not set up.

Public API:

    mailer_from_env(environ=None, outbox=None) -> Mailer
        The configured mailer, or MailNotConfigured listing what is missing.
    missing_settings(environ=None) -> list[str]
        The settings SMTP still needs ([] when it is fully configured).
    smtp_settings_from_env(environ=None) -> SmtpSettings
        Parse the SMTP settings; raises MailNotConfigured when incomplete.
    Mailer.send(to, subject, body) -> None
        Send one plain-text message; raises MailError on failure.
    SmtpMailer.probe(timeout=5.0) -> None
        Connect and EHLO without sending anything (for a status page).
    code_email(purpose, code, *, minutes=10) -> (subject, body)
    backup_codes_email(codes) -> (subject, body)
    existing_account_email() -> (subject, body)
    owner_alert_email(headline, lines) -> (subject, body)

Nothing here logs a message body: the log line for a sent message carries
the recipient and subject only, and no subject contains a code.
"""

from __future__ import annotations

import logging
import os
import secrets
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .config import DATA_CENTER_NAME

log = logging.getLogger(__name__)

#: How long an emailed code lives; the wording of every code email uses it.
CODE_MINUTES = 10

IGNORE_LINE = "If you did not ask for this, ignore this email and tell the owner."

TLS_MODES = {"starttls": 587, "ssl": 465}

_PURPOSES = {
    "sign_in": ("Your sign-in code",
                "It is for signing in to the portal."),
    "verify_email": ("Confirm your email address",
                     "It is for confirming this email address for your portal access request."),
    "reset_password": ("Your password reset code",
                       "It is for resetting your portal password."),
}


class MailError(Exception):
    """A message could not be sent."""


class MailNotConfigured(MailError):
    """Neither SMTP nor an outbox is set up, so no code can be delivered."""

    def __init__(self, missing: list[str], problem: str = "") -> None:
        self.missing = list(missing)
        parts = ["Sign-in needs email, and email is not set up."]
        if problem:
            parts.append(problem)
        if self.missing:
            parts.append("Missing: " + ", ".join(self.missing) + ".")
        parts.append("Set them for SMTP, or use --mail-outbox DIR (SDDU13_MAIL_OUTBOX) "
                     "to write messages to files while testing.")
        super().__init__(" ".join(parts))


class Mailer(Protocol):
    """Anything that can deliver a plain-text message."""

    def send(self, to: str, subject: str, body: str) -> None: ...

    def describe(self) -> str: ...


def _message(sender: str, to: str, subject: str, body: str) -> EmailMessage:
    """Build the message. EmailMessage refuses CR or LF in a header, which is
    what stops an address carrying a second header along with it."""
    message = EmailMessage()
    try:
        message["From"] = sender
        message["To"] = to
        message["Subject"] = subject
    except ValueError as error:
        raise MailError(f"refusing to build a message: {error}") from error
    message["Date"] = formatdate(localtime=False)
    message["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(body)
    return message


# -- outbox ---------------------------------------------------------------------

class OutboxMailer:
    """Writes each message to `<directory>/<time>-<random>.eml` instead of sending."""

    def __init__(self, directory: Path | str, sender: str = "portal@localhost") -> None:
        self.directory = Path(directory)
        self.sender = sender

    def send(self, to: str, subject: str, body: str) -> None:
        message = _message(self.sender, to, subject, body)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path = self.directory / f"{stamp}-{secrets.token_hex(4)}.eml"
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(message.as_bytes())
        except OSError as error:
            raise MailError(f"cannot write to the mail outbox {self.directory}: "
                            f"{error.strerror or error}") from error
        log.info("mail to %s written to the outbox: %s", to, subject)

    def describe(self) -> str:
        return f"outbox {self.directory} (development mode: messages are files, not sent)"


# -- SMTP -------------------------------------------------------------------------

@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    sender: str
    tls: str = "starttls"
    user: str = ""
    password: str = field(default="", repr=False)


class SmtpMailer:
    """Sends through an SMTP server over TLS, logging in when a user is set."""

    def __init__(self, settings: SmtpSettings, *, timeout: float = 20.0,
                 smtp_class: Callable[..., Any] | None = None,
                 smtp_ssl_class: Callable[..., Any] | None = None) -> None:
        if settings.tls not in TLS_MODES:
            raise MailError(f"unknown TLS mode {settings.tls!r}")
        self.settings = settings
        self.timeout = timeout
        # Injectable so tests can stand in for a server.
        self._smtp_class = smtp_class or smtplib.SMTP
        self._smtp_ssl_class = smtp_ssl_class or smtplib.SMTP_SSL

    def _connect(self, timeout: float) -> Any:
        context = ssl.create_default_context()
        settings = self.settings
        if settings.tls == "ssl":
            client = self._smtp_ssl_class(settings.host, settings.port,
                                          timeout=timeout, context=context)
        else:
            client = self._smtp_class(settings.host, settings.port, timeout=timeout)
        try:
            client.ehlo()
            if settings.tls == "starttls":
                client.starttls(context=context)
                client.ehlo()
        except BaseException:
            _quit(client)  # the socket is open even though the handshake failed
            raise
        return client

    def send(self, to: str, subject: str, body: str) -> None:
        message = _message(self.settings.sender, to, subject, body)
        client = None
        try:
            client = self._connect(self.timeout)
            if self.settings.user:
                client.login(self.settings.user, self.settings.password)
            client.send_message(message)
        except smtplib.SMTPAuthenticationError as error:
            raise MailError(f"the SMTP server at {self.settings.host} refused the login "
                            f"(code {error.smtp_code})") from None
        except (smtplib.SMTPException, OSError) as error:
            # Name the failure, not the message: the exception text never
            # carries the body, and the password is never part of it.
            raise MailError(f"could not send mail through {self.settings.host}:"
                            f"{self.settings.port}: {type(error).__name__}") from None
        finally:
            _quit(client)
        log.info("mail to %s sent: %s", to, subject)

    def probe(self, timeout: float = 5.0) -> None:
        """Connect, say EHLO (and STARTTLS when configured), then hang up."""
        client = None
        try:
            client = self._connect(timeout)
        except (smtplib.SMTPException, OSError) as error:
            raise MailError(f"cannot reach {self.settings.host}:{self.settings.port}: "
                            f"{type(error).__name__}") from None
        finally:
            _quit(client)

    def describe(self) -> str:
        return f"SMTP {self.settings.host}:{self.settings.port} ({self.settings.tls})"


def _quit(client: Any) -> None:
    if client is None:
        return
    try:
        client.quit()
    except (smtplib.SMTPException, OSError):
        try:
            client.close()
        except OSError:
            pass


# -- configuration ------------------------------------------------------------------

def _env(environ: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if environ is None else environ


def missing_settings(environ: Mapping[str, str] | None = None) -> list[str]:
    """The SMTP settings still needed, in the order the docs list them."""
    env = _env(environ)
    missing = [name for name in ("SDDU13_SMTP_HOST", "SDDU13_SMTP_FROM")
               if not env.get(name, "").strip()]
    user = env.get("SDDU13_SMTP_USER", "").strip()
    password = env.get("SDDU13_SMTP_PASSWORD", "")
    if user and not password:
        missing.append("SDDU13_SMTP_PASSWORD")
    if password and not user:
        missing.append("SDDU13_SMTP_USER")
    return missing


def smtp_settings_from_env(environ: Mapping[str, str] | None = None) -> SmtpSettings:
    env = _env(environ)
    missing = missing_settings(env)
    if missing:
        raise MailNotConfigured(missing)

    tls = env.get("SDDU13_SMTP_TLS", "").strip().lower() or "starttls"
    if tls not in TLS_MODES:
        raise MailNotConfigured([], f"SDDU13_SMTP_TLS must be 'starttls' or 'ssl', not {tls!r}.")
    port_text = env.get("SDDU13_SMTP_PORT", "").strip()
    try:
        port = int(port_text) if port_text else TLS_MODES[tls]
    except ValueError:
        raise MailNotConfigured([], f"SDDU13_SMTP_PORT must be a number, not {port_text!r}.") from None
    if not 0 < port < 65536:
        raise MailNotConfigured([], f"SDDU13_SMTP_PORT {port} is not a valid port.")

    return SmtpSettings(
        host=env["SDDU13_SMTP_HOST"].strip(),
        port=port,
        sender=env["SDDU13_SMTP_FROM"].strip(),
        tls=tls,
        user=env.get("SDDU13_SMTP_USER", "").strip(),
        password=env.get("SDDU13_SMTP_PASSWORD", ""),
    )


def mailer_from_env(environ: Mapping[str, str] | None = None,
                    outbox: Path | str | None = None) -> OutboxMailer | SmtpMailer:
    """The mailer the portal should use.

    An outbox (the argument, then SDDU13_MAIL_OUTBOX) wins over SMTP, so a
    developer machine never sends real mail by accident.
    """
    env = _env(environ)
    directory = outbox or env.get("SDDU13_MAIL_OUTBOX", "").strip()
    if directory:
        return OutboxMailer(directory, sender=env.get("SDDU13_SMTP_FROM", "").strip()
                            or "portal@localhost")
    return SmtpMailer(smtp_settings_from_env(env))


# -- messages -----------------------------------------------------------------------

def code_email(purpose: str, code: str, *, minutes: int = CODE_MINUTES) -> tuple[str, str]:
    """Subject and body for an emailed security code. The code is in the body only."""
    if purpose not in _PURPOSES:
        raise ValueError(f"unknown code purpose {purpose!r}")
    subject, what_for = _PURPOSES[purpose]
    # Lines stay under 78 characters so the message goes out as plain text.
    body = (
        f"Your {DATA_CENTER_NAME} security code is:\n\n"
        f"    {code}\n\n"
        f"{what_for}\n"
        f"It expires in {minutes} minutes and works once.\n"
        "Nobody from the portal will ever ask you for this code.\n\n"
        f"{IGNORE_LINE}\n"
    )
    return f"{subject} - {DATA_CENTER_NAME}", body


def backup_codes_email(codes: list[str]) -> tuple[str, str]:
    """Subject and body for a new set of backup codes."""
    listing = "\n".join(f"    {code}" for code in codes)
    body = (
        f"Here are your new {DATA_CENTER_NAME} backup codes:\n\n"
        f"{listing}\n\n"
        "Each code works once, in place of an emailed sign-in code.\n"
        "Asking for a new set cancels every code in this one.\n\n"
        "Print them and keep them somewhere safe, off shared devices.\n"
        "Delete this email once you have them.\n\n"
        f"{IGNORE_LINE}\n"
    )
    return f"Your backup codes - {DATA_CENTER_NAME}", body


def existing_account_email() -> tuple[str, str]:
    """Sent instead of a code when someone asks for access with an address
    that already has an account, so the request page never has to say so."""
    body = (
        f"Someone asked for access to the {DATA_CENTER_NAME} portal\n"
        "with this email address, but it already has an account.\n\n"
        "If it was you, sign in instead, or use \"Forgot password\"\n"
        "on the sign-in page.\n\n"
        f"{IGNORE_LINE}\n"
    )
    return f"About your access request - {DATA_CENTER_NAME}", body


def owner_alert_email(headline: str, lines: list[str]) -> tuple[str, str]:
    """A notice to the owner, e.g. that an account was blocked automatically."""
    detail = "\n".join(f"- {line}" for line in lines)
    body = (
        f"{headline}\n\n{detail}\n\n"
        "Review it in the owner center. The portal never shows anyone's\n"
        "password, codes or backup codes, including to you.\n"
    )
    return f"Portal alert: {headline} - {DATA_CENTER_NAME}", body
