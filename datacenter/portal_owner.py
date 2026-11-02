"""The owner center: requests, members, blocks, activity, portal updates and a
security checklist.

Every route here is declared OWNER under /owner, and `portal_web.Route`
refuses an /owner route declared any other way. The guard runs before these
handlers, so none of them checks who is asking: a member who opens one of
these pages gets a strike, and one who posts to an action is blocked at once.

Nothing on these pages is a secret. The accounts store has no way to return
a password, a security code or a backup code, to the owner or to anyone; the
owner can approve, decline, remove, block, reset and sign people out.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from typing import Any

from .accounts import Account, AccountError
from .mailer import MailError, OutboxMailer
from .portal_web import (
    OWNER,
    OWNER_NEVER_SEES,
    Context,
    NotFound,
    Response,
    Route,
    error_box,
    esc,
    hidden,
    input_field,
    redirect,
    table,
    with_notice,
)

TABS = (
    ("/owner", "Overview"), ("/owner/requests", "Requests"), ("/owner/members", "Members"),
    ("/owner/blocked", "Blocked"), ("/owner/activity", "Activity"),
    ("/owner/announcements", "Portal updates"), ("/owner/checklist", "Security checklist"),
)

#: How old the last backup may be before the checklist asks for a new one.
BACKUP_FRESH = timedelta(days=7)


def _owner_page(ctx: Context, title: str, body: str, *, subtitle: str = "",
                status: int = HTTPStatus.OK) -> Response:
    tabs = "".join(f'<a href="{esc(path)}">{esc(label)}</a>' for path, label in TABS)
    footer = f'<p class="sub">{esc(OWNER_NEVER_SEES)}</p>'
    return ctx.page(title, f'<p class="tabs">{tabs}</p>{body}{footer}', subtitle=subtitle, status=status)


def _back(value: str) -> str:
    return value if value in {path for path, _ in TABS} else "/owner"


def _action(ctx: Context, account_id: int, action: str, label: str, back: str, *,
            reason: bool = False, danger: bool = False) -> str:
    """One owner action on one account, as its own small form."""
    reason_html = ('<input type="text" name="reason" placeholder="Reason" required maxlength="500">'
                   if reason else "")
    style = "danger" if danger else "secondary"
    return (f'<form method="post" action="/owner/accounts/{account_id}/{esc(action)}">'
            f"{ctx.csrf_field()}{hidden('back', back)}{reason_html}"
            f'<button type="submit" class="{style}">{esc(label)}</button></form>')


def _actions(*forms: str) -> str:
    return f'<div class="actions">{"".join(forms)}</div>'


def _who(account: Account) -> list[str]:
    return [esc(account.display_name), f"<code>{esc(account.email)}</code>"]


def _events_table(events: list[dict[str, Any]]) -> str:
    rows = [[esc(event["at"]), f'<code>{esc(event["kind"])}</code>', esc(event["email"] or "-"),
             f'<code>{esc(event["ip"] or "-")}</code>', esc(event["detail"] or "-")] for event in events]
    return table(["when", "what", "account", "address", "detail"], rows)


# -- pages ----------------------------------------------------------------------

def overview(ctx: Context, _match: re.Match[str]) -> Response:
    summary = ctx.store.owner_summary()
    cards = "".join(
        f'<a class="card" href="{esc(path)}"><div class="n">{count:,}</div><div class="l">{esc(label)}</div></a>'
        for count, label, path in (
            (summary["pending"], "requests waiting", "/owner/requests"),
            (summary["members"], "members with access", "/owner/members"),
            (summary["blocked"], "blocked", "/owner/blocked"),
            (summary["reviews_due"], "blocks due for review", "/owner/blocked"),
        ))
    body = (f'<div class="cards">{cards}</div>'
            "<h2>Recent security events</h2>"
            + _events_table(ctx.store.recent_security_events(limit=10))
            + '<p><a href="/owner/activity">All activity</a></p>')
    return _owner_page(ctx, "Owner center", body, subtitle="Who can use the portal, and what happened.")


def requests_page(ctx: Context, _match: re.Match[str]) -> Response:
    back = "/owner/requests"
    rows = [_who(account) + [
        esc(account.company or "-"), esc(account.request_reason or "-"),
        esc("Google" if account.google_linked else "password"), esc(account.created_at),
        _actions(_action(ctx, account.id, "approve", "Approve", back),
                 _action(ctx, account.id, "decline", "Decline", back),
                 _action(ctx, account.id, "block", "Block", back, reason=True, danger=True)),
    ] for account in ctx.store.pending_requests()]
    body = table(["name", "email", "company", "reason", "signs in with", "asked", ""], rows)
    return _owner_page(ctx, "Access requests", body,
                       subtitle="People who asked for access and confirmed their email address.")


def members_page(ctx: Context, _match: re.Match[str]) -> Response:
    back, store = "/owner/members", ctx.store
    rows = []
    for account in store.list_accounts():
        if store.is_owner(account) or account.status not in ("approved", "declined", "revoked", "unblocked"):
            continue
        if account.status == "approved":
            forms = (_action(ctx, account.id, "remove", "Remove access", back),
                     _action(ctx, account.id, "reset-password", "Email a password reset", back),
                     _action(ctx, account.id, "sign-out-everywhere", "Sign out everywhere", back))
        else:
            forms = (_action(ctx, account.id, "approve", "Give access", back),)
        forms += (_action(ctx, account.id, "block", "Block", back, reason=True, danger=True),)
        rows.append(_who(account) + [esc(account.status), esc(account.company or "-"),
                                     esc(account.decided_at or "-"), _actions(*forms)])
    body = table(["name", "email", "status", "company", "decided", ""], rows)
    return _owner_page(ctx, "Members", body,
                       subtitle="Resetting a password emails the person a reset code; you never see it.")


def blocked_list(ctx: Context, _match: re.Match[str]) -> Response:
    back, today = "/owner/blocked", ctx.today()
    rows = []
    for account in ctx.store.blocked_accounts():
        review = account.review_after.isoformat() if account.review_after else "-"
        if account.review_due(today):
            decide = _actions(_action(ctx, account.id, "unblock", "Unblock", back),
                              _action(ctx, account.id, "extend", "Keep blocked 4 more months", back))
        else:
            decide = esc(f"You can decide on {review}")
        rows.append(_who(account) + [esc(account.block_reason or "-"), esc(account.blocked_at or "-"),
                                     esc(review), decide])
    body = table(["name", "email", "reason", "blocked", "review date", ""], rows)
    return _owner_page(ctx, "Blocked", body, subtitle=(
        "A block lasts four calendar months and cannot be lifted before its review date. "
        "Unblocking does not restore access: the person may ask again."))


def activity_page(ctx: Context, _match: re.Match[str]) -> Response:
    body = _events_table(ctx.store.recent_security_events(limit=200))
    return _owner_page(ctx, "Activity", body,
                       subtitle="Sign-ins, requests, decisions, strikes and blocks, newest first.")


def announcements_page(ctx: Context, _match: re.Match[str], *, error: str = "",
                       values: dict[str, str] | None = None, status: int = HTTPStatus.OK) -> Response:
    values = values or {}
    rows = [[esc(item["created_at"]), esc(item["title"]), esc(item["body"]),
             f'<form method="post" action="/owner/announcements/{int(item["id"])}/delete">{ctx.csrf_field()}'
             '<button type="submit" class="secondary">Delete</button></form>']
            for item in ctx.store.list_announcements(limit=100)]
    body = (error_box(error)
            + f'<form method="post" action="/owner/announcements" class="stack">{ctx.csrf_field()}'
            + input_field("Title", "title", value=values.get("title", ""), extra='maxlength="200"')
            + input_field("What changed", "body", kind="textarea", value=values.get("body", ""),
                          extra='maxlength="5000"')
            + '<button type="submit">Post update</button></form>'
            + "<h2>Posted</h2>" + table(["posted", "title", "text", ""], rows))
    return _owner_page(ctx, "Portal updates", body, status=status,
                       subtitle="Short notes for everyone who uses the portal.")


def post_announcement(ctx: Context, match: re.Match[str]) -> Response:
    form = ctx.request.form
    values = {"title": form.get("title"), "body": form.get("body")}
    try:
        ctx.store.post_announcement(values["title"], values["body"])
    except AccountError as error:
        return announcements_page(ctx, match, error=str(error), values=values, status=HTTPStatus.BAD_REQUEST)
    return redirect("/owner/announcements?notice=posted")


def delete_announcement(ctx: Context, match: re.Match[str]) -> Response:
    if not ctx.store.delete_announcement(int(match["item"])):
        raise NotFound("no such update")
    return redirect("/owner/announcements?notice=deleted")


# -- the security checklist ---------------------------------------------------

def _last_backup(ctx: Context) -> datetime | None:
    """When `python -m datacenter backup` last ran, from the audit trail."""
    try:
        audit = ctx.center().db("audit")
        if not audit.path.exists():
            return None
        row = audit.query_one("SELECT occurred_at FROM events WHERE action = 'backup' ORDER BY id DESC LIMIT 1")
    except (OSError, sqlite3.Error):
        return None
    if row is None:
        return None
    return datetime.strptime(row["occurred_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def checklist(ctx: Context) -> list[tuple[str, str, str]]:
    """(state, item, detail) rows, where state is "ok", "check" or "bad"."""
    settings, store = ctx.settings, ctx.store
    items: list[tuple[str, str, str]] = []

    if settings.mailer is None:
        items.append(("bad", "Email for sign-in codes", settings.mail_problem))
    elif isinstance(settings.mailer, OutboxMailer):
        items.append(("check", "Email for sign-in codes",
                      "Development mode: messages are written to files, not sent. Set the SMTP "
                      "settings for real use."))
    else:
        items.append(("ok", "Email for sign-in codes", settings.mailer.describe()))

    if settings.google is not None:
        items.append(("ok", "Google sign-in", f"On. Redirect URI: {settings.google.redirect_uri}"))
    elif settings.google_problem:
        items.append(("bad", "Google sign-in", settings.google_problem))
    else:
        items.append(("check", "Google sign-in", "Not set up, so people sign in with a password. "
                      "Set SDDU13_GOOGLE_CLIENT_ID, SDDU13_GOOGLE_CLIENT_SECRET and SDDU13_PUBLIC_URL."))

    if settings.public_url.startswith("https://"):
        items.append(("ok", "HTTPS public URL", settings.public_url))
    else:
        items.append(("check", "HTTPS public URL", settings.public_url + " is not HTTPS." if settings.public_url
                      else "No public URL is set (--public-url or SDDU13_PUBLIC_URL)."))

    items.append(("ok", "Secure cookies", "Cookies are only sent over HTTPS, and HSTS is on.")
                 if settings.secure_cookies else
                 ("check", "Secure cookies", "Cookies are also sent over plain HTTP. Behind HTTPS, "
                  "start the portal with --https-cookies."))

    local = settings.host in ("127.0.0.1", "localhost", "::1")
    if settings.allowed_networks:
        items.append(("ok", "Network allowlist", "Only these networks are served: "
                      + ", ".join(str(network) for network in settings.allowed_networks)))
    elif local:
        items.append(("ok", "Network allowlist", "No allowlist, but the portal only listens on this machine."))
    else:
        items.append(("bad", "Network allowlist", f"Anyone who can reach {settings.host} gets the sign-in "
                      "page. Use --allow-network with your VPN's range, e.g. 100.64.0.0/10 for Tailscale."))

    if settings.trust_proxy:
        items.append(("check", "Reverse proxy", "X-Forwarded-For from a proxy on this machine is trusted. "
                      "Only use this with a proxy that sets it."))

    source = store.owner_email_source()
    items.append(("ok", "Owner email", f"Set ({'SDDU13_OWNER_EMAIL' if source == 'environment' else 'stored'}).")
                 if source else ("bad", "Owner email", "Not set: set SDDU13_OWNER_EMAIL."))

    last = _last_backup(ctx)
    if last is None:
        items.append(("bad", "Backups", "No backup on record. Run: python -m datacenter backup"))
    elif settings.now() - last > BACKUP_FRESH:
        items.append(("check", "Backups", f"The last backup was {last:%Y-%m-%d}, more than a week ago."))
    else:
        items.append(("ok", "Backups", f"Last backup {last:%Y-%m-%d %H:%M} UTC."))
    return items


def checklist_page(ctx: Context, _match: re.Match[str]) -> Response:
    labels = {"ok": "OK", "check": "Check", "bad": "Problem"}
    rows = [[f'<span class="{state}">{labels[state]}</span>', esc(item), esc(detail)]
            for state, item, detail in checklist(ctx)]
    return _owner_page(ctx, "Security checklist", table(["", "item", "detail"], rows),
                       subtitle="How this portal is set up right now.")


# -- actions on one account -----------------------------------------------------

def _do(ctx: Context, account: Account, action: str) -> str:
    """Carry out one owner action; return the notice for the page after."""
    store, form = ctx.store, ctx.request.form
    if action == "approve":
        store.approve(account.id)
        return "approved"
    if action == "decline":
        store.decline(account.id)
        return "declined"
    if action == "remove":
        store.remove(account.id)
        return "removed"
    if action == "block":
        store.block(account.id, form.get("reason"), ip=ctx.request.ip)
        return "blocked"
    if action == "unblock":
        store.unblock(account.id)
        return "unblocked"
    if action == "extend":
        store.extend_block(account.id)
        return "extended"
    if action == "reset-password":
        store.send_reset_code(account.id)
        return "reset-sent"
    if action == "sign-out-everywhere":
        store.end_all_sessions(account.id)
        return "sessions-ended"
    raise NotFound(f"no action {action}")


def account_action(ctx: Context, match: re.Match[str]) -> Response:
    account = ctx.store.get_account(int(match["target"]))
    if account is None:
        raise NotFound("no such account")
    back = _back(ctx.request.form.get("back"))
    try:
        notice = _do(ctx, account, match["action"])
    except AccountError as error:
        body = error_box(str(error)) + f'<p><a href="{esc(back)}">Back</a></p>'
        return _owner_page(ctx, "Not done", body, status=HTTPStatus.CONFLICT)
    except MailError:
        body = (error_box("The email could not be sent. The security checklist shows the mail settings.")
                + f'<p><a href="{esc(back)}">Back</a></p>')
        return _owner_page(ctx, "Not done", body, status=HTTPStatus.SERVICE_UNAVAILABLE)
    return redirect(with_notice(back, notice))


ROUTES: tuple[Route, ...] = (
    Route("GET", "/owner", OWNER, overview),
    Route("GET", "/owner/requests", OWNER, requests_page),
    Route("GET", "/owner/members", OWNER, members_page),
    Route("GET", "/owner/blocked", OWNER, blocked_list),
    Route("GET", "/owner/activity", OWNER, activity_page),
    Route("GET", "/owner/announcements", OWNER, announcements_page),
    Route("POST", "/owner/announcements", OWNER, post_announcement),
    Route("POST", r"/owner/announcements/(?P<item>[0-9]+)/delete", OWNER, delete_announcement),
    Route("GET", "/owner/checklist", OWNER, checklist_page),
    Route("POST", r"/owner/accounts/(?P<target>[0-9]+)/(?P<action>[a-z-]+)", OWNER, account_action),
)
