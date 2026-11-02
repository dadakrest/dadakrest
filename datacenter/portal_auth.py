"""Sign-in, access requests, password resets, the status page and the account page.

Every route is declared in `ROUTES` with an access level, and the guard in
`portal_web.authorize` runs before any handler here. So a PARTIAL handler
always has a session past the first factor, and a MEMBER handler a full
session of an approved account (or the owner's) acting on its own account.

Sign-in is two steps, always: a password or Google first, then a 6-digit
code emailed to the account's own address, or one backup code. Without a way
to send mail, every page here says what is missing instead of offering a
form; the second step is never skipped.

Messages never say whether an address has an account. A wrong email and a
wrong password read the same; asking for access with a taken address shows
the same page as with a free one (the holder is told by email instead); and
a password reset says "if that address has an account" either way.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from http import HTTPStatus
from urllib.parse import quote

from .accounts import (
    SIGN_IN_LIMIT,
    SIGN_IN_WINDOW,
    Account,
    EmailTaken,
    InvalidInput,
    NewSession,
    Throttled,
    WrongState,
    normalise_email,
)
from .google_signin import GoogleIdentity, GoogleSignInError
from .mailer import MailError
from .portal_web import (
    ANYONE,
    MEMBER,
    OWNER_NEVER_SEES,
    PARTIAL,
    SIGNED_IN,
    SIGNED_OUT,
    Context,
    HttpError,
    NotFound,
    Response,
    Route,
    blocked_response,
    error_box,
    esc,
    hidden,
    input_field,
    redirect,
    safe_next,
    table,
    with_notice,
)

#: Holds the Google flow key during a sign-in, then (for a new person) the
#: signed identity Google confirmed, while they fill in the request form.
GOOGLE_COOKIE = "sddu13_google"
GOOGLE_COOKIE_AGE = 600

MAIL_FAILED = ("We could not send the email just now. Try again in a few minutes; "
               "if it keeps failing, tell the owner.")
NO_MATCH = "That email and password do not match."

CODE_ERRORS = {
    "wrong": "That code is not right.",
    "expired": "That code has expired. Ask for a new one.",
    "locked": "Too many wrong tries for that code. Ask for a new one.",
    "none": "There is no code waiting. Ask for a new one.",
    "throttled": "Too many backup code tries. Wait 15 minutes, or use the emailed code.",
}

STATUS_TEXT = {
    "pending": ("Your request is with the owner",
                "The owner has not decided yet. You can use the portal once your request is approved."),
    "declined": ("Your request was declined", "The owner declined your request for access."),
    "revoked": ("Your access was removed", "The owner removed your access to the portal."),
    "unblocked": ("You can ask again",
                  "Your account was unblocked. Access is not restored automatically: ask again "
                  "below, and the owner will decide."),
}


# -- helpers ------------------------------------------------------------------

def _mail_off(ctx: Context) -> Response | None:
    """Sign-in needs mail for its second step. Without it, say what is missing."""
    problem = ctx.settings.mail_problem
    if not problem:
        return None
    body = (f'<p class="error">Sign-in is turned off. {esc(problem)}</p>'
            '<p class="sub">The second sign-in step is a code sent by email, and it is never skipped.</p>')
    return ctx.page("Sign-in is not available", body, status=HTTPStatus.SERVICE_UNAVAILABLE, bare=True)


def _throttled(response: Response, error: Throttled) -> Response:
    response.headers.append(("Retry-After", str(error.retry_after)))
    return response


def _google_link(ctx: Context, label: str) -> str:
    if ctx.settings.google is None:
        return '<p class="sub">Google sign-in is not set up yet.</p>'
    return f'<p><a class="button secondary" href="/auth/google/start">{esc(label)}</a></p>'


def _shown_email(value: str) -> str:
    """What someone typed in an email box, to put back in the form, but only
    if it is an address: people type passwords there, and no page shows one."""
    try:
        normalise_email(value)
    except InvalidInput:
        return ""
    return value.strip()


def _purpose(account: Account) -> str:
    """An address nobody has confirmed yet is confirmed by the same code."""
    return "sign_in" if account.email_verified else "verify_email"


def _code_location(target: str) -> str:
    return "/sign-in/code" if target == "/" else f"/sign-in/code?next={quote(target, safe='')}"


def _user_agent(ctx: Context) -> str:
    return ctx.request.header("User-Agent")


def start_second_step(ctx: Context, account: Account, *, method: str, target: str = "/") -> Response:
    """The first factor passed: open a partial session and email the code.

    A blocked account goes no further than the notice. Nothing is mailed to
    it, and no session is made.
    """
    if account.status == "blocked":
        return blocked_response(ctx, account.review_after)
    store = ctx.store
    if ctx.token:
        store.end_session(ctx.token)
    new = store.begin_sign_in(account.id, method=method, ip=ctx.request.ip, user_agent=_user_agent(ctx))
    notice = ""
    try:
        store.send_code(account.id, _purpose(account))
    except Throttled:
        notice = "code-not-sent"  # the last code, or a backup code, still works
    except MailError:
        store.end_session(new.token)
        raise HttpError(HTTPStatus.SERVICE_UNAVAILABLE, MAIL_FAILED) from None
    ctx.start_session(new)
    return redirect(with_notice(_code_location(target), notice))


def _backup_codes_after_sign_in(ctx: Context, account: Account | None) -> str:
    """After a full sign-in, an account with no backup codes left is sent a set."""
    if account is None:
        return ""
    try:
        return "backup-sent" if ctx.store.ensure_backup_codes(account.id) else ""
    except Throttled:
        return "backup-later"
    except MailError:
        return "backup-failed"


def _signed_in(ctx: Context, account_id: int, new: NewSession, target: str) -> Response:
    """Both steps passed: set the full session and send the person on."""
    ctx.start_session(new)
    account = ctx.store.get_account(account_id)
    notice = _backup_codes_after_sign_in(ctx, account)
    if account is None or not account.can_use_portal:
        target = "/status"
    return redirect(with_notice(target, notice))


# -- sign in ------------------------------------------------------------------

def sign_in_page(ctx: Context, _match: re.Match[str], *, error: str = "", email: str = "",
                 status: int = HTTPStatus.OK) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    target = safe_next(ctx.request.query_value("next") or ctx.request.form.get("next"))
    body = (error_box(error)
            + f'<form method="post" action="/sign-in" class="stack">{ctx.csrf_field()}{hidden("next", target)}'
            + input_field("Email", "email", kind="email", value=email, autocomplete="username")
            + input_field("Password", "password", kind="password", autocomplete="current-password")
            + '<button type="submit">Continue</button></form>'
            + _google_link(ctx, "Sign in with Google")
            + '<p class="sub">Whichever way you start, we then email a 6-digit code to your address. '
              "One of your backup codes works in its place.</p>"
            + '<p><a href="/forgot-password">Forgot your password?</a> · '
              '<a href="/reset-password">I have a reset code</a> · '
              '<a href="/request-access">Ask for access</a></p>')
    return ctx.page("Sign in", body, status=status, bare=True)


def sign_in(ctx: Context, match: re.Match[str]) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    form = ctx.request.form
    email = form.get("email")
    try:
        account = ctx.store.authenticate_password(email, form.get("password"), ip=ctx.request.ip)
    except Throttled as error:
        return _throttled(sign_in_page(ctx, match, error=str(error), email=_shown_email(email),
                                       status=HTTPStatus.TOO_MANY_REQUESTS), error)
    if account is None:
        return sign_in_page(ctx, match, error=NO_MATCH, email=_shown_email(email),
                            status=HTTPStatus.UNAUTHORIZED)
    return start_second_step(ctx, account, method="password", target=safe_next(form.get("next")))


def code_page(ctx: Context, _match: re.Match[str], *, error: str = "",
              status: int = HTTPStatus.OK) -> Response:
    account = ctx.account
    assert account is not None
    target = safe_next(ctx.request.query_value("next") or ctx.request.form.get("next"))
    if account.email_verified:
        intro = (f"We emailed a 6-digit code to <strong>{esc(account.email)}</strong>. "
                 "Enter it, or one of your backup codes. The emailed code expires in 10 minutes.")
        code = input_field("Code", "code", hint="6 digits, or a backup code", autocomplete="one-time-code",
                           extra='maxlength="20" autofocus')
    else:
        intro = (f"To confirm your email address, enter the 6-digit code we emailed to "
                 f"<strong>{esc(account.email)}</strong>. It expires in 10 minutes.")
        code = input_field("Code", "code", hint="6 digits", autocomplete="one-time-code",
                           extra='inputmode="numeric" maxlength="12" autofocus')
    body = (error_box(error) + f"<p>{intro}</p>"
            + f'<form method="post" action="/sign-in/code" class="stack">{ctx.csrf_field()}{hidden("next", target)}'
            + code + '<button type="submit">Sign in</button></form>'
            + '<div class="actions">'
            + f'<form method="post" action="/sign-in/resend">{ctx.csrf_field()}{hidden("next", target)}'
              '<button type="submit" class="secondary">Email me a new code</button></form>'
            + f'<form method="post" action="/sign-out">{ctx.csrf_field()}'
              '<button type="submit" class="secondary">Start again</button></form>'
            + "</div>")
    return ctx.page("Enter your code", body, status=status, bare=True)


def code_submit(ctx: Context, match: re.Match[str]) -> Response:
    account = ctx.account
    assert account is not None and ctx.token
    step = ctx.store.finish_sign_in(ctx.token, ctx.request.form.get("code"), purpose=_purpose(account))
    if not step:
        reason = step.check.reason
        status = HTTPStatus.TOO_MANY_REQUESTS if reason == "throttled" else HTTPStatus.BAD_REQUEST
        return code_page(ctx, match, error=CODE_ERRORS.get(reason, CODE_ERRORS["wrong"]), status=status)
    assert step.session is not None
    return _signed_in(ctx, account.id, step.session, safe_next(ctx.request.form.get("next")))


def code_resend(ctx: Context, match: re.Match[str]) -> Response:
    account = ctx.account
    assert account is not None
    target = safe_next(ctx.request.form.get("next"))
    try:
        ctx.store.send_code(account.id, _purpose(account))
    except Throttled:
        return redirect(with_notice(_code_location(target), "code-not-sent"))
    except MailError:
        return code_page(ctx, match, error=MAIL_FAILED, status=HTTPStatus.SERVICE_UNAVAILABLE)
    return redirect(with_notice(_code_location(target), "code-sent"))


def sign_out(ctx: Context, _match: re.Match[str]) -> Response:
    if ctx.token:
        ctx.store.end_session(ctx.token)
    ctx.forget_session()
    return redirect("/sign-in?notice=signed-out")


# -- asking for access with a password ----------------------------------------

def request_page(ctx: Context, _match: re.Match[str], *, error: str = "",
                 values: dict[str, str] | None = None, status: int = HTTPStatus.OK) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    values = values or {}
    body = (error_box(error)
            + "<p>Ask the owner for access. Once you confirm your email address, the owner sees "
              "your request and decides.</p>"
            + _google_link(ctx, "Ask with Google")
            + f'<form method="post" action="/request-access" class="stack">{ctx.csrf_field()}'
            + input_field("Your name", "name", value=values.get("name", ""), autocomplete="name",
                          extra='maxlength="100"')
            + input_field("Email", "email", kind="email", value=values.get("email", ""), autocomplete="email")
            + input_field("Company", "company", value=values.get("company", ""), required=False,
                          autocomplete="organization", extra='maxlength="200"')
            + input_field("Why you need access", "reason", kind="textarea", value=values.get("reason", ""),
                          required=False, extra='maxlength="2000"')
            + input_field("Password", "password", kind="password", autocomplete="new-password",
                          hint="at least 12 characters", extra='minlength="12"')
            + input_field("Password again", "confirm", kind="password", autocomplete="new-password")
            + '<button type="submit">Ask for access</button></form>'
            + '<p><a href="/sign-in">Already have an account? Sign in</a></p>')
    return ctx.page("Ask for access", body, status=status, bare=True)


def confirm_page(ctx: Context, *, email: str, error: str = "", status: int = HTTPStatus.OK) -> Response:
    body = (error_box(error)
            + "<p>If this address can be used for a new request, we have emailed a 6-digit code to it. "
              "It expires in 10 minutes. Enter it with the password you chose to confirm the address; "
              "the owner sees your request once it is confirmed.</p>"
            + f'<form method="post" action="/request-access/confirm" class="stack">{ctx.csrf_field()}'
            + input_field("Email", "email", kind="email", value=email, autocomplete="username")
            + input_field("Code", "code", hint="6 digits", autocomplete="one-time-code",
                          extra='inputmode="numeric" maxlength="12"')
            + input_field("Password", "password", kind="password", autocomplete="current-password")
            + '<button type="submit">Confirm</button></form>')
    return ctx.page("Check your email", body, status=status, bare=True)


def request_submit(ctx: Context, match: re.Match[str]) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    form, store = ctx.request.form, ctx.store
    address = form.get("email")
    values = {key: form.get(key) for key in ("name", "company", "reason")}
    values["email"] = _shown_email(address)
    password = form.get("password")
    if not values["name"].strip():
        return request_page(ctx, match, error="Enter your name.", values=values, status=HTTPStatus.BAD_REQUEST)
    if password != form.get("confirm"):
        return request_page(ctx, match, error="The two passwords are not the same.", values=values,
                            status=HTTPStatus.BAD_REQUEST)
    try:
        account = store.register_password_account(
            address, password, display_name=values["name"], company=values["company"],
            reason=values["reason"], ip=ctx.request.ip)
    except EmailTaken:
        # Same page as a free address; the holder hears about it by email.
        store.notify_existing_account(address)
    except InvalidInput as error:
        return request_page(ctx, match, error=str(error), values=values, status=HTTPStatus.BAD_REQUEST)
    except Throttled as error:
        return _throttled(request_page(ctx, match, error=str(error), values=values,
                                       status=HTTPStatus.TOO_MANY_REQUESTS), error)
    else:
        try:
            # In the background, like the notice above, so both take as long.
            store.send_code(account.id, "verify_email", background=True)
        except Throttled:
            pass
    return confirm_page(ctx, email=values["email"])


def request_confirm(ctx: Context, _match: re.Match[str]) -> Response:
    """Both factors in one form: the password chosen, and the emailed code."""
    off = _mail_off(ctx)
    if off:
        return off
    form, store = ctx.request.form, ctx.store
    email = form.get("email")
    try:
        account = store.authenticate_password(email, form.get("password"), ip=ctx.request.ip)
    except Throttled as error:
        return _throttled(confirm_page(ctx, email=_shown_email(email), error=str(error),
                                       status=HTTPStatus.TOO_MANY_REQUESTS), error)
    if account is None:
        return confirm_page(ctx, email=_shown_email(email), error="That email, password and code do not match.",
                            status=HTTPStatus.BAD_REQUEST)
    if account.email_verified or account.status == "blocked":
        return start_second_step(ctx, account, method="password")
    new = store.begin_sign_in(account.id, method="password", ip=ctx.request.ip, user_agent=_user_agent(ctx))
    step = store.finish_sign_in(new.token, form.get("code"), purpose="verify_email")
    if not step or step.session is None:
        store.end_session(new.token)
        return confirm_page(ctx, email=_shown_email(email),
                            error=CODE_ERRORS.get(step.check.reason, CODE_ERRORS["wrong"]),
                            status=HTTPStatus.BAD_REQUEST)
    if ctx.token:
        store.end_session(ctx.token)
    return _signed_in(ctx, account.id, step.session, "/")


# -- Google -------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _ticket_mac(ctx: Context, payload: str) -> str:
    digest = hmac.new(ctx.store.secret, b"google-ticket:" + payload.encode("ascii"), hashlib.sha256)
    return _b64url(digest.digest())  # always 43 characters


def _ticket(ctx: Context, identity: GoogleIdentity) -> str:
    """The identity Google confirmed, signed with the server secret, so the
    request form can be filled in without trusting anything the browser says."""
    expires = int(ctx.settings.now().timestamp()) + GOOGLE_COOKIE_AGE
    claims = {"sub": identity.sub, "email": identity.email, "name": identity.name, "exp": expires}
    payload = _b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    return _ticket_mac(ctx, payload) + payload


def _read_ticket(ctx: Context, value: str) -> GoogleIdentity | None:
    mac, payload = value[:43], value[43:]
    if not payload or not hmac.compare_digest(mac.encode("ascii"), _ticket_mac(ctx, payload).encode("ascii")):
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, binascii.Error):
        return None
    if not isinstance(claims, dict) or not isinstance(claims.get("exp"), int) \
            or claims["exp"] <= ctx.settings.now().timestamp():
        return None
    if not all(isinstance(claims.get(key), str) for key in ("sub", "email", "name")):
        return None
    return GoogleIdentity(sub=claims["sub"], email=claims["email"], name=claims["name"])


def _google_failed(ctx: Context, message: str) -> Response:
    body = (f'<p class="error">Google sign-in did not work: {esc(message)}.</p>'
            '<p><a href="/auth/google/start">Try Google again</a> · <a href="/sign-in">Sign in another way</a></p>')
    return ctx.page("Google sign-in", body, status=HTTPStatus.BAD_REQUEST, bare=True)


def google_start(ctx: Context, _match: re.Match[str]) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    google = ctx.google()
    if google is None:
        body = ('<p class="sub">Google sign-in is not set up yet.</p>'
                '<p><a href="/sign-in">Sign in with your email address</a></p>')
        return ctx.page("Google sign-in", body, status=HTTPStatus.NOT_FOUND, bare=True)
    # Starting a flow writes a row; cap it like any other sign-in attempt.
    if not ctx.store.allow(f"google_start:{ctx.request.ip}", 2 * SIGN_IN_LIMIT, SIGN_IN_WINDOW):
        raise HttpError(HTTPStatus.TOO_MANY_REQUESTS, "Too many sign-in attempts. Try again in a few minutes.",
                        retry_after=int(SIGN_IN_WINDOW.total_seconds()))
    flow_key, url = google.begin(ctx.store)
    ctx.set_cookie(GOOGLE_COOKIE, flow_key, max_age=GOOGLE_COOKIE_AGE)
    return redirect(url)


def google_request_page(ctx: Context, identity: GoogleIdentity, *, error: str = "",
                        values: dict[str, str] | None = None, status: int = HTTPStatus.OK) -> Response:
    values = values or {}
    body = (error_box(error)
            + f"<p>Google confirmed you as <strong>{esc(identity.email)}</strong>. Tell the owner who you "
              "are; they decide whether you get access.</p>"
            + f'<form method="post" action="/auth/google/request" class="stack">{ctx.csrf_field()}'
            + input_field("Your name", "name", value=values.get("name", identity.name), extra='maxlength="100"')
            + input_field("Company", "company", value=values.get("company", ""), required=False,
                          extra='maxlength="200"')
            + input_field("Why you need access", "reason", kind="textarea", value=values.get("reason", ""),
                          required=False, extra='maxlength="2000"')
            + '<button type="submit">Ask for access</button></form>'
            + '<p class="sub">Next, we email a 6-digit code to that address to finish signing you in.</p>')
    return ctx.page("Ask for access", body, status=status, bare=True)


def google_callback(ctx: Context, _match: re.Match[str]) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    google = ctx.google()
    if google is None:
        raise NotFound("Google sign-in is not set up")
    store, ip = ctx.store, ctx.request.ip
    flow_key = ctx.request.cookies.get(GOOGLE_COOKIE, "")
    ctx.clear_cookie(GOOGLE_COOKIE)
    try:
        identity = google.complete(store, flow_key, ctx.request.query)
    except GoogleSignInError as error:
        store.log_security_event("google_failed", ip=ip, detail=str(error))
        return _google_failed(ctx, str(error))
    try:
        store.note_sign_in_attempt(ip=ip, email=identity.email)
    except Throttled as error:
        raise HttpError(HTTPStatus.TOO_MANY_REQUESTS, str(error), retry_after=error.retry_after) from None

    account = store.account_for_google(identity.sub, identity.email, ip=ip)
    if account is not None:
        return start_second_step(ctx, account, method="google")
    if store.find_account(identity.email) is not None:
        # The person proved they hold this address, so saying so tells them nothing new.
        return _google_failed(ctx, "this email address already has an account linked to a different "
                                   "Google account. Sign in with your password, or ask the owner")
    ctx.set_cookie(GOOGLE_COOKIE, _ticket(ctx, identity), max_age=GOOGLE_COOKIE_AGE)
    return google_request_page(ctx, identity)


def google_request(ctx: Context, _match: re.Match[str]) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    identity = _read_ticket(ctx, ctx.request.cookies.get(GOOGLE_COOKIE, ""))
    if identity is None:
        return _google_failed(ctx, "this Google sign-in has expired; start it again")
    form = ctx.request.form
    values = {key: form.get(key) for key in ("name", "company", "reason")}
    if not values["name"].strip():
        return google_request_page(ctx, identity, error="Enter your name.", values=values,
                                   status=HTTPStatus.BAD_REQUEST)
    try:
        account = ctx.store.create_google_account(
            identity.sub, identity.email, display_name=values["name"], company=values["company"],
            reason=values["reason"], ip=ctx.request.ip)
    except EmailTaken:
        ctx.clear_cookie(GOOGLE_COOKIE)
        return _google_failed(ctx, "this Google account or address already has an account; sign in instead")
    except InvalidInput as error:
        return google_request_page(ctx, identity, error=str(error), values=values,
                                   status=HTTPStatus.BAD_REQUEST)
    ctx.clear_cookie(GOOGLE_COOKIE)
    return start_second_step(ctx, account, method="google")


# -- forgotten passwords --------------------------------------------------------

def forgot_page(ctx: Context, _match: re.Match[str], *, error: str = "", email: str = "",
                status: int = HTTPStatus.OK) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    body = (error_box(error)
            + "<p>Enter your email address. If it has an account, we email a reset code to it.</p>"
            + f'<form method="post" action="/forgot-password" class="stack">{ctx.csrf_field()}'
            + input_field("Email", "email", kind="email", value=email, autocomplete="username")
            + '<button type="submit">Email me a reset code</button></form>'
            + '<p><a href="/reset-password">I already have a reset code</a> · <a href="/sign-in">Sign in</a></p>')
    return ctx.page("Forgot your password", body, status=status, bare=True)


def forgot_submit(ctx: Context, match: re.Match[str]) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    email = ctx.request.form.get("email")
    try:
        ctx.store.request_password_reset(email, ip=ctx.request.ip)
    except Throttled as error:
        return _throttled(forgot_page(ctx, match, error=str(error), email=_shown_email(email),
                                      status=HTTPStatus.TOO_MANY_REQUESTS), error)
    return reset_page(ctx, match, email=_shown_email(email), sent=True)


def reset_page(ctx: Context, _match: re.Match[str], *, email: str = "", sent: bool = False,
               error: str = "", status: int = HTTPStatus.OK) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    intro = ("If that address has an account, we have emailed a reset code to it. It expires in 10 minutes. "
             if sent else "")
    body = (error_box(error)
            + f"<p>{intro}Enter the code and choose a new password. Changing it signs you out everywhere.</p>"
            + f'<form method="post" action="/reset-password" class="stack">{ctx.csrf_field()}'
            + input_field("Email", "email", kind="email", value=email, autocomplete="username")
            + input_field("Reset code", "code", hint="6 digits", autocomplete="one-time-code",
                          extra='inputmode="numeric" maxlength="12"')
            + input_field("New password", "password", kind="password", autocomplete="new-password",
                          hint="at least 12 characters", extra='minlength="12"')
            + input_field("New password again", "confirm", kind="password", autocomplete="new-password")
            + '<button type="submit">Change my password</button></form>')
    return ctx.page("Reset your password", body, status=status, bare=True)


def reset_submit(ctx: Context, match: re.Match[str]) -> Response:
    off = _mail_off(ctx)
    if off:
        return off
    form = ctx.request.form
    email, password = form.get("email"), form.get("password")
    shown = _shown_email(email)
    if password != form.get("confirm"):
        return reset_page(ctx, match, email=shown, error="The two passwords are not the same.",
                          status=HTTPStatus.BAD_REQUEST)
    try:
        check = ctx.store.reset_password(email, form.get("code"), password, ip=ctx.request.ip)
    except InvalidInput as error:
        return reset_page(ctx, match, email=shown, error=str(error), status=HTTPStatus.BAD_REQUEST)
    except Throttled as error:
        return _throttled(reset_page(ctx, match, email=shown, error=str(error),
                                     status=HTTPStatus.TOO_MANY_REQUESTS), error)
    if not check:
        return reset_page(ctx, match, email=shown, error="That code is not right or has expired. "
                          "Ask for a new one.", status=HTTPStatus.BAD_REQUEST)
    if ctx.token:
        ctx.store.end_session(ctx.token)
        ctx.forget_session()
    body = ("<p>Your password is changed, and every session was signed out.</p>"
            '<p><a class="button" href="/sign-in">Sign in</a></p>')
    return ctx.page("Password changed", body, bare=True)


# -- status, for anyone signed in who cannot use the portal yet ---------------

def status_page(ctx: Context, _match: re.Match[str], *, error: str = "",
                status: int = HTTPStatus.OK) -> Response:
    account = ctx.account
    assert account is not None
    if account.can_use_portal:
        return redirect("/")
    title, text = STATUS_TEXT.get(account.status, ("Your access", "The owner decides who can use the portal."))
    body = error_box(error) + f"<p>{esc(text)}</p>" + table(["name", "email", "status"], [[
        esc(account.display_name), f"<code>{esc(account.email)}</code>", esc(account.status)]])
    if account.status == "unblocked":
        body += (f'<form method="post" action="/status/request-again" class="stack">{ctx.form_fields()}'
                 + input_field("Why you need access", "reason", kind="textarea", required=False,
                               extra='maxlength="2000"')
                 + '<button type="submit">Ask for access again</button></form>')
    return ctx.page(title, body, status=status)


def request_again(ctx: Context, match: re.Match[str]) -> Response:
    account = ctx.account
    assert account is not None
    try:
        ctx.store.request_again(account.id, ctx.request.form.get("reason"))
    except WrongState as error:
        return status_page(ctx, match, error=str(error), status=HTTPStatus.CONFLICT)
    return redirect("/status?notice=request-sent")


# -- the account page ---------------------------------------------------------

def account_home(ctx: Context, _match: re.Match[str]) -> Response:
    assert ctx.account is not None
    return redirect(f"/account/{ctx.account.id}")


def account_page(ctx: Context, _match: re.Match[str], *, error: str = "",
                 status: int = HTTPStatus.OK) -> Response:
    store = ctx.store
    assert ctx.account is not None
    account = store.get_account(ctx.account.id) or ctx.account
    base = f"/account/{account.id}"
    methods = ", ".join(name for name, on in (("password", account.has_password),
                                              ("Google", account.google_linked)) if on) or "-"
    details = table(["", ""], [
        ["Name", esc(account.display_name)],
        ["Email", f"<code>{esc(account.email)}</code>"],
        ["Status", esc("owner" if ctx.is_owner else account.status)],
        ["Signs in with", esc(methods)],
        ["Member since", esc(account.created_at)],
    ])
    sessions = [[esc(row["created_at"]), esc(row["last_seen"]), esc(row["method"] or "-"),
                 f'<code>{esc(row["ip"] or "-")}</code>', esc((row["user_agent"] or "-")[:80])]
                for row in store.list_sessions(account.id)]
    remaining = store.backup_codes_remaining(account.id)
    if account.has_password:
        password = (f'<form method="post" action="{base}/password" class="stack">{ctx.form_fields()}'
                    + input_field("Current password", "current", kind="password",
                                  autocomplete="current-password")
                    + input_field("New password", "password", kind="password", autocomplete="new-password",
                                  hint="at least 12 characters", extra='minlength="12"')
                    + input_field("New password again", "confirm", kind="password", autocomplete="new-password")
                    + '<button type="submit">Change password</button></form>')
    else:
        password = "<p>You sign in with Google, so there is no password to change.</p>"
    body = (error_box(error) + details
            + f'<form method="post" action="{base}/name" class="stack">{ctx.form_fields()}'
            + input_field("Name", "name", value=account.display_name, extra='maxlength="100"')
            + '<button type="submit" class="secondary">Save name</button></form>'
            + "<h2>Backup codes</h2>"
            + f"<p>You have {remaining} unused backup code{'s' if remaining != 1 else ''}. Each works once, in "
              "place of an emailed code. A new set is emailed to your own address and replaces the old one.</p>"
            + f'<form method="post" action="{base}/backup-codes">{ctx.form_fields()}'
              '<button type="submit" class="secondary">Email me new backup codes</button></form>'
            + "<h2>Password</h2>" + password
            + "<h2>Where you are signed in</h2>"
            + table(["started", "last seen", "method", "address", "browser"], sessions)
            + f'<div class="actions"><form method="post" action="{base}/sign-out-everywhere">{ctx.form_fields()}'
              '<button type="submit" class="secondary">Sign out everywhere</button></form>'
            + f'<form method="post" action="/sign-out">{ctx.form_fields()}'
              '<button type="submit" class="secondary">Sign out</button></form></div>'
            + f'<p class="sub">{esc(OWNER_NEVER_SEES)}</p>')
    return ctx.page("Your account", body, status=status)


def account_name(ctx: Context, match: re.Match[str]) -> Response:
    assert ctx.account is not None
    try:
        ctx.store.update_profile(ctx.account.id, display_name=ctx.request.form.get("name"))
    except InvalidInput as error:
        return account_page(ctx, match, error=str(error), status=HTTPStatus.BAD_REQUEST)
    return redirect(f"/account/{ctx.account.id}?notice=name-saved")


def account_backup_codes(ctx: Context, match: re.Match[str]) -> Response:
    assert ctx.account is not None
    try:
        ctx.store.send_backup_codes(ctx.account.id)
    except Throttled as error:
        return _throttled(account_page(ctx, match, error=str(error), status=HTTPStatus.TOO_MANY_REQUESTS), error)
    except MailError:
        return account_page(ctx, match, error=MAIL_FAILED, status=HTTPStatus.SERVICE_UNAVAILABLE)
    return redirect(f"/account/{ctx.account.id}?notice=backup-sent")


def account_password(ctx: Context, match: re.Match[str]) -> Response:
    assert ctx.account is not None
    form = ctx.request.form
    if form.get("password") != form.get("confirm"):
        return account_page(ctx, match, error="The two new passwords are not the same.",
                            status=HTTPStatus.BAD_REQUEST)
    try:
        changed = ctx.store.change_password(ctx.account.id, form.get("current"), form.get("password"),
                                            keep_token=ctx.token)
    except InvalidInput as error:
        return account_page(ctx, match, error=str(error), status=HTTPStatus.BAD_REQUEST)
    except Throttled as error:
        return _throttled(account_page(ctx, match, error=str(error), status=HTTPStatus.TOO_MANY_REQUESTS), error)
    if not changed:
        return account_page(ctx, match, error="Your current password is not right.",
                            status=HTTPStatus.BAD_REQUEST)
    return redirect(f"/account/{ctx.account.id}?notice=password-changed")


def account_sign_out_everywhere(ctx: Context, _match: re.Match[str]) -> Response:
    assert ctx.account is not None
    ctx.store.end_all_sessions(ctx.account.id)
    ctx.forget_session()
    return redirect("/sign-in?notice=signed-out-everywhere")


ROUTES: tuple[Route, ...] = (
    Route("GET", "/sign-in", SIGNED_OUT, sign_in_page),
    Route("POST", "/sign-in", SIGNED_OUT, sign_in),
    Route("GET", "/sign-in/code", PARTIAL, code_page),
    Route("POST", "/sign-in/code", PARTIAL, code_submit),
    Route("POST", "/sign-in/resend", PARTIAL, code_resend),
    Route("POST", "/sign-out", ANYONE, sign_out),
    Route("GET", "/request-access", SIGNED_OUT, request_page),
    Route("POST", "/request-access", SIGNED_OUT, request_submit),
    Route("POST", "/request-access/confirm", SIGNED_OUT, request_confirm),
    Route("GET", "/auth/google/start", SIGNED_OUT, google_start),
    Route("GET", "/auth/google/callback", SIGNED_OUT, google_callback),
    Route("POST", "/auth/google/request", SIGNED_OUT, google_request),
    Route("GET", "/forgot-password", SIGNED_OUT, forgot_page),
    Route("POST", "/forgot-password", SIGNED_OUT, forgot_submit),
    Route("GET", "/reset-password", SIGNED_OUT, reset_page),
    Route("POST", "/reset-password", SIGNED_OUT, reset_submit),
    Route("GET", "/status", SIGNED_IN, status_page),
    Route("POST", "/status/request-again", SIGNED_IN, request_again),
    Route("GET", "/account", MEMBER, account_home),
    Route("GET", r"/account/(?P<account>[0-9]+)", MEMBER, account_page),
    Route("POST", r"/account/(?P<account>[0-9]+)/name", MEMBER, account_name),
    Route("POST", r"/account/(?P<account>[0-9]+)/backup-codes", MEMBER, account_backup_codes),
    Route("POST", r"/account/(?P<account>[0-9]+)/password", MEMBER, account_password),
    Route("POST", r"/account/(?P<account>[0-9]+)/sign-out-everywhere", MEMBER, account_sign_out_everywhere),
)
