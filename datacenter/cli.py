"""Command line interface: `python -m datacenter <command>`."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import sys
from pathlib import Path
from typing import Any

from .accounts import STATUSES, Account, AccountError, AccountStore, TooEarly, normalise_email
from .config import DATA_CENTER_NAME, DEFAULT_ROOT, VERSION
from .core import DataCenter
from .loader import DATA_DIR, DataFileError, validate
from .mailer import MailError, MailNotConfigured, mailer_from_env
from .plans import PlanError, cost_plan, plan_names
from .portal import DEFAULT_HOST, DEFAULT_PORT
from .seed import seed as seed_data


def _human_size(size: int) -> str:
    """Render a byte count. Divides in float and keeps one decimal above
    bytes, so 1536 reads as 1.5KB rather than being truncated to 1KB."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        # 1023.95 rather than 1024: a value that only reaches 1024 once it is
        # rounded to one decimal belongs to the next unit, not as "1024.0MB".
        if value < (1024 if unit == "B" else 1023.95) or unit == "GB":
            return f"{value:.0f}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{value:.1f}GB"


def _positive_int(text: str) -> int:
    """An argparse type for counts that are meaningless below one."""
    value = int(text)
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def _network(text: str) -> str:
    """An argparse type for --allow-network: a network such as 100.64.0.0/10."""
    try:
        return str(ipaddress.ip_network(text.strip(), strict=False))
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a network such as 100.64.0.0/10") from None


def _print_status(report: dict[str, Any]) -> None:
    print(f"{report['name']} v{report['version']}")
    print(f"root: {report['root']}")
    print(f"as of: {report['generated_at']}")
    print()
    header = f"{'database':<12} {'state':<14} {'size':>8}  rows"
    print(header)
    print("-" * len(header))
    for database in report["databases"]:
        if not database["provisioned"]:
            state = "not provisioned"
        else:
            state = "healthy" if database["healthy"] else "DEGRADED"
        rows = ", ".join(f"{table}={count}" for table, count in database["rows"].items())
        print(
            f"{database['key']:<12} {state:<14} "
            f"{_human_size(database['size_bytes']):>8}  {rows or '-'}"
        )


def _open(args: argparse.Namespace) -> DataCenter:
    return DataCenter(root=args.root)


def cmd_init(args: argparse.Namespace) -> int:
    with _open(args) as center:
        keys = center.provision()
    print(f"{DATA_CENTER_NAME}: provisioned {len(keys)} databases in {args.root}")
    for key in keys:
        print(f"  - {key}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    with _open(args) as center:
        report = center.status()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_status(report)
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    with _open(args) as center:
        center.provision()
        try:
            summary = seed_data(center, args.data)
        except DataFileError as error:
            print(f"data files rejected, nothing loaded:\n{error}", file=sys.stderr)
            return 1
        while center.run_next_job() is not None:
            pass
    print(f"loaded from {args.data}: " + ", ".join(f"{count} {name}" for name, count in summary.items()))
    return 0


def cmd_validate_data(args: argparse.Namespace) -> int:
    try:
        problems = validate(args.data)
    except DataFileError as error:
        problems = str(error).splitlines()
    if problems:
        print("\n".join(problems))
        print(f"{len(problems)} problem(s) in {args.data}")
        return 1
    print(f"data files in {args.data} are valid")
    return 0


def cmd_datasets(args: argparse.Namespace) -> int:
    with _open(args) as center:
        datasets = center.list_datasets()
    for dataset in datasets:
        fields = ", ".join(dataset["schema"]) or "-"
        print(f"#{dataset['id']:<4} {dataset['name']:<28} {dataset['records']:>6} records  [{fields}]")
    if not datasets:
        print("no datasets stored")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    with _open(args) as center:
        results = center.search(args.query, limit=args.limit)
    if not results:
        print("no matches")
        return 0
    for result in results:
        print(f"{result['score']:.4f}  #{result['document_id']:<4} {result['title']}")
    return 0


def cmd_add_document(args: argparse.Namespace) -> int:
    if args.file:
        try:
            body = Path(args.file).read_text(encoding="utf-8")
        except OSError as error:
            print(f"cannot read {args.file}: {error.strerror or error}", file=sys.stderr)
            return 1
        except UnicodeDecodeError:
            print(f"cannot read {args.file}: not valid UTF-8 text", file=sys.stderr)
            return 1
    else:
        body = args.body or ""

    with _open(args) as center:
        center.provision()
        document_id = center.add_document(
            args.title, body, source=args.source, tags=args.tag or []
        )
    print(f"stored document #{document_id}: {args.title}")
    return 0


def cmd_add_contact(args: argparse.Namespace) -> int:
    with _open(args) as center:
        center.provision()
        contact_id = center.add_contact(
            args.name,
            email=args.email,
            role=args.role,
            phone=args.phone,
            organization=args.organization,
        )
    print(f"stored contact #{contact_id}: {args.name}")
    return 0


def cmd_contacts(args: argparse.Namespace) -> int:
    with _open(args) as center:
        contacts = center.list_contacts()
    for contact in contacts:
        org = contact["organization"] or "-"
        print(f"#{contact['id']:<4} {contact['full_name']:<20} {org:<22} {contact['email'] or '-'}")
    if not contacts:
        print("no contacts stored")
    return 0


def cmd_documents(args: argparse.Namespace) -> int:
    with _open(args) as center:
        documents = center.list_documents()
    for document in documents:
        print(f"#{document['id']:<4} {document['title']:<36} {document['source'] or '-'}")
    if not documents:
        print("no documents stored")
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    with _open(args) as center:
        if args.run:
            result = center.run_next_job()
            print(json.dumps(result, indent=2) if result else "queue empty")
            return 0
        pending = center.pending_jobs()
    for job in pending:
        print(f"#{job['id']:<4} {job['kind']:<12} priority={job['priority']}")
    if not pending:
        print("queue empty")
    return 0


def _money(amount: float) -> str:
    return f"${amount:,.2f}"


FEE_TABLES = {
    "government": ("government_fees", ("fee_code", "jurisdiction", "name", "period", "amount_usd")),
    "service": ("service_fees", ("fee_code", "service", "unit", "unit_price_usd", "minimum_units")),
    "employment": ("employment_fees", ("fee_code", "role", "employment_type", "base_salary_usd", "benefits_usd_month")),
}


def cmd_fees(args: argparse.Namespace) -> int:
    chosen = [args.table] if args.table else list(FEE_TABLES)
    with _open(args) as center:
        for name in chosen:
            dataset, columns = FEE_TABLES[name]
            records = [record["payload"] for record in center.list_records(dataset)]
            print(f"{dataset} ({len(records)})")
            if not records:
                print("  no rows — run seed")
                continue
            for payload in records:
                cells = []
                for column in columns:
                    value = payload.get(column, "")
                    cells.append(_money(value) if column.endswith("_usd") else str(value))
                print("  " + "  ".join(f"{cell:<22}" if index == 1 else f"{cell:<14}"
                                       for index, cell in enumerate(cells)))
            print()
    return 0


def cmd_plans(args: argparse.Namespace) -> int:
    with _open(args) as center:
        names = plan_names(center)
        if not names:
            print("no business plans stored — run seed")
            return 0
        for name in names:
            plan = cost_plan(center, name)
            parts = ", ".join(f"{table.split('_')[0]} {_money(amount)}"
                              for table, amount in sorted(plan["subtotals"].items()))
            print(f"{name:<22} {plan['months']:>3} months  {_money(plan['total_usd']):>14}   {parts}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    with _open(args) as center:
        try:
            plan = cost_plan(center, args.name)
        except PlanError as error:
            print(error, file=sys.stderr)
            return 1

    if args.json:
        print(json.dumps(plan, indent=2))
        return 0

    print(f"{plan['plan']} — {plan['months']} months")
    print()
    header = f"{'code':<9} {'line':<28} {'basis':<24} {'amount':>14}"
    print(header)
    print("-" * len(header))
    for row in plan["lines"]:
        print(f"{row['fee_code']:<9} {row['line'][:28]:<28} {row['basis'][:24]:<24} {_money(row['amount_usd']):>14}")
    print("-" * len(header))
    for table, amount in sorted(plan["subtotals"].items()):
        print(f"{'':<9} {table:<28} {'':<24} {_money(amount):>14}")
    print(f"{'':<9} {'TOTAL':<28} {'':<24} {_money(plan['total_usd']):>14}")
    print(f"{'':<9} {'monthly average':<28} {'':<24} {_money(plan['monthly_average_usd']):>14}")
    return 0


def cmd_portal(args: argparse.Namespace) -> int:
    from .portal import serve

    try:
        serve(args.root, args.host, args.port, allow_remote=args.allow_remote,
              mail_outbox=args.mail_outbox, allow_network=args.allow_network or (),
              trust_proxy=args.trust_proxy, https_cookies=args.https_cookies, public_url=args.public_url)
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    except OSError as error:
        print(f"cannot serve on {args.host}:{args.port}: {error.strerror or error}", file=sys.stderr)
        return 1
    return 0


# -- portal accounts ------------------------------------------------------------
#
# These print emails, roles and statuses only. The accounts store has no way
# to return a password, a code or a backup code, so no command can show one.

def _store_or_explain(args: argparse.Namespace, **options: Any) -> AccountStore | None:
    """The accounts store, provisioned (which also applies SDDU13_OWNER_EMAIL),
    or None with the reason printed when there is none yet."""
    store = AccountStore(args.root, **options)
    if not store.path.exists():
        print(f"no accounts yet: the accounts store {store.path} is created when the portal first "
              "starts, or by portal-owner", file=sys.stderr)
        return None
    try:
        store.provision()
    except AccountError as error:  # an unusable SDDU13_OWNER_EMAIL or portal secret
        store.close()
        print(error, file=sys.stderr)
        return None
    return store


def _account_or_explain(store: AccountStore, email: str) -> Account | None:
    account = store.find_account(email)
    if account is None:
        print(f"no account uses {email}", file=sys.stderr)
    return account


def cmd_portal_owner(args: argparse.Namespace) -> int:
    store = AccountStore(args.root)
    try:
        address = normalise_email(args.email)
        store.provision()
        store.set_owner_email(address)
        account = store.find_account(address)
        effective = store.owner_email()
    except AccountError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        store.close()
    print(f"owner email stored: {address}")
    if effective != address:
        print(f"note: SDDU13_OWNER_EMAIL is set to {effective}, and it wins while it is set",
              file=sys.stderr)
    if account is None:
        print("No account uses that address yet. Ask for access with it on the portal: it is approved "
              "automatically, and still signs in with both steps.")
    return 0


def cmd_accounts(args: argparse.Namespace) -> int:
    store = _store_or_explain(args)
    if store is None:
        # No store yet is an empty list, not a failure.
        return 1 if AccountStore(args.root).path.exists() else 0
    try:
        accounts = store.list_accounts(args.status)
        owners = {account.id for account in accounts if store.is_owner(account)}
    finally:
        store.close()
    if not accounts:
        print("no accounts" + (f" with status {args.status}" if args.status else ""))
        return 0
    width = max(len("email"), *(len(account.email) for account in accounts))
    print(f"{'email':<{width}}  {'role':<6}  {'status':<10}  created")
    for account in accounts:
        role = "owner" if account.id in owners else "member"
        status = account.status
        if account.status == "blocked" and account.review_after:
            status += f" (review {account.review_after.isoformat()})"
        elif not account.email_verified:
            status += " (email not confirmed)"
        print(f"{account.email:<{width}}  {role:<6}  {status:<10}  {account.created_at}")
    return 0


def cmd_account_block(args: argparse.Namespace) -> int:
    store = _store_or_explain(args)
    if store is None:
        return 1
    try:
        account = _account_or_explain(store, args.email)
        if account is None:
            return 1
        review = store.block(account.id, args.reason)
    except AccountError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        store.close()
    print(f"{account.email} is blocked and signed out everywhere. The block cannot be lifted "
          f"before {review.isoformat()}.")
    return 0


def cmd_account_unblock(args: argparse.Namespace) -> int:
    store = _store_or_explain(args)
    if store is None:
        return 1
    try:
        account = _account_or_explain(store, args.email)
        if account is None:
            return 1
        store.unblock(account.id)
    except TooEarly as error:
        print(f"not unblocked: the review date is {error.review_after.isoformat()}, and a block cannot "
              "be lifted before it", file=sys.stderr)
        return 1
    except AccountError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        store.close()
    print(f"{account.email} is unblocked. Access is not restored: they may ask again, and you decide.")
    return 0


def cmd_account_reset(args: argparse.Namespace) -> int:
    try:
        mailer = mailer_from_env(os.environ, outbox=args.mail_outbox)
    except MailNotConfigured as error:
        print(error, file=sys.stderr)
        return 1
    store = _store_or_explain(args, mailer=mailer)
    if store is None:
        return 1
    try:
        account = _account_or_explain(store, args.email)
        if account is None:
            return 1
        if account.status == "blocked":
            print(f"{account.email} is blocked; a reset would not let them in", file=sys.stderr)
            return 1
        store.send_reset_code(account.id)
        store.wait_for_mail()
    except MailError as error:
        print(f"the reset code was not sent: {error}", file=sys.stderr)
        return 1
    except AccountError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        store.close()
    print(f"A password reset code was emailed to {account.email}. It expires in 10 minutes, and "
          "nobody else sees it, you included.")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    with _open(args) as center:
        results = center.verify()
    for key, healthy in results.items():
        print(f"{key:<12} {'ok' if healthy else 'FAILED'}")
    return 0 if all(results.values()) else 1


def cmd_backup(args: argparse.Namespace) -> int:
    with _open(args) as center:
        target = center.backup(args.destination)
    print(f"backup written to {target}")
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    with _open(args) as center:
        events = center.recent_events(limit=args.limit)
    for event in events:
        print(f"{event['occurred_at']}  {event['database']:<11} {event['action']:<18} {event['detail']}")
    if not events:
        print("no events recorded")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="datacenter",
        description=f"{DATA_CENTER_NAME} - seven-database AI data center.",
    )
    parser.add_argument("--version", action="version", version=f"{DATA_CENTER_NAME} {VERSION}")
    parser.add_argument(
        "--root",
        default=str(DEFAULT_ROOT),
        help=f"where the databases live (default: {DEFAULT_ROOT})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the seven databases").set_defaults(func=cmd_init)

    status = sub.add_parser("status", help="health and size of every database")
    status.add_argument("--json", action="store_true", help="machine-readable output")
    status.set_defaults(func=cmd_status)

    seed = sub.add_parser("seed", help="load (or refill) the data center from its data files")
    seed.add_argument("--data", default=str(DATA_DIR), help=f"folder of JSON data files (default: {DATA_DIR})")
    seed.set_defaults(func=cmd_seed)

    validate_data = sub.add_parser("validate-data", help="check the data files without loading them")
    validate_data.add_argument("--data", default=str(DATA_DIR))
    validate_data.set_defaults(func=cmd_validate_data)

    search = sub.add_parser("search", help="semantic search over stored documents")
    search.add_argument("query")
    search.add_argument("--limit", type=_positive_int, default=5)
    search.set_defaults(func=cmd_search)

    add_document = sub.add_parser("add-document", help="store and index a document")
    add_document.add_argument("title")
    add_document.add_argument("--body", default="")
    add_document.add_argument("--file", help="read the body from this file instead")
    add_document.add_argument("--source", default="")
    add_document.add_argument("--tag", action="append", help="repeatable")
    add_document.set_defaults(func=cmd_add_document)

    add_contact = sub.add_parser("add-contact", help="store a contact")
    add_contact.add_argument("name")
    add_contact.add_argument("--email", default="")
    add_contact.add_argument("--role", default="")
    add_contact.add_argument("--phone", default="")
    add_contact.add_argument("--organization", default="")
    add_contact.set_defaults(func=cmd_add_contact)

    sub.add_parser("contacts", help="list stored contacts").set_defaults(func=cmd_contacts)
    sub.add_parser("documents", help="list stored documents").set_defaults(func=cmd_documents)
    sub.add_parser("datasets", help="list stored datasets").set_defaults(func=cmd_datasets)

    fees = sub.add_parser("fees", help="show the government, service and employment fee tables")
    fees.add_argument("--table", choices=sorted(FEE_TABLES), help="just one of them")
    fees.set_defaults(func=cmd_fees)

    sub.add_parser("plans", help="list business plans with their totals").set_defaults(func=cmd_plans)

    portal = sub.add_parser("portal", help="serve the data center as a web portal with sign-in")
    portal.add_argument("--host", default=DEFAULT_HOST, help=f"default {DEFAULT_HOST}")
    portal.add_argument("--port", type=_positive_int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")
    portal.add_argument("--allow-remote", action="store_true",
                        help="permit a non-local --host: everyone who can reach it gets the sign-in page")
    portal.add_argument("--allow-network", action="append", type=_network, metavar="CIDR",
                        help="serve only clients in this network, e.g. 100.64.0.0/10 for Tailscale; "
                             "repeatable; others get 403")
    portal.add_argument("--trust-proxy", action="store_true",
                        help="take the client address from X-Forwarded-For set by a reverse proxy "
                             "on this machine")
    portal.add_argument("--https-cookies", action="store_true",
                        help="the portal is reached over HTTPS: mark cookies Secure and send HSTS")
    portal.add_argument("--public-url", metavar="URL",
                        help="where people reach the portal, e.g. https://portal.example.ts.net "
                             "(default: SDDU13_PUBLIC_URL)")
    portal.add_argument("--mail-outbox", metavar="DIR",
                        help="write sign-in emails as files in DIR instead of sending them, for testing "
                             "(default: SDDU13_MAIL_OUTBOX)")
    portal.set_defaults(func=cmd_portal)

    portal_owner = sub.add_parser("portal-owner", help="set or change the portal owner's email")
    portal_owner.add_argument("email")
    portal_owner.set_defaults(func=cmd_portal_owner)

    accounts = sub.add_parser("accounts", help="list portal accounts: email, role, status, created")
    accounts.add_argument("--status", choices=STATUSES, help="only accounts with this status")
    accounts.set_defaults(func=cmd_accounts)

    account_block = sub.add_parser("account-block", help="block a portal account for four months")
    account_block.add_argument("email")
    account_block.add_argument("--reason", required=True, help="why; the owner center shows it")
    account_block.set_defaults(func=cmd_account_block)

    account_unblock = sub.add_parser("account-unblock",
                                     help="lift a block whose review date has come (access is not restored)")
    account_unblock.add_argument("email")
    account_unblock.set_defaults(func=cmd_account_unblock)

    account_reset = sub.add_parser("account-reset", help="email a password reset code to an account")
    account_reset.add_argument("email")
    account_reset.add_argument("--mail-outbox", metavar="DIR",
                               help="write the email as a file in DIR instead of sending it "
                                    "(default: SDDU13_MAIL_OUTBOX, else SMTP)")
    account_reset.set_defaults(func=cmd_account_reset)

    plan = sub.add_parser("plan", help="cost one business plan line by line")
    plan.add_argument("name")
    plan.add_argument("--json", action="store_true", help="machine-readable output")
    plan.set_defaults(func=cmd_plan)

    jobs = sub.add_parser("jobs", help="show the queue, or run the next job")
    jobs.add_argument("--run", action="store_true", help="run the next pending job")
    jobs.set_defaults(func=cmd_jobs)

    sub.add_parser("verify", help="integrity-check every database").set_defaults(func=cmd_verify)

    backup = sub.add_parser("backup", help="back up all seven databases")
    backup.add_argument("destination", nargs="?", default="backups")
    backup.set_defaults(func=cmd_backup)

    events = sub.add_parser("events", help="show the audit trail")
    events.add_argument("--limit", type=_positive_int, default=20)
    events.set_defaults(func=cmd_events)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        try:
            code = int(args.func(args))
        except FileNotFoundError as error:
            # Raised by the engine when a command reads a database that was
            # never provisioned; reads do not create, so say what to run.
            print(error, file=sys.stderr)
            print(f"run: python -m datacenter --root {args.root} init", file=sys.stderr)
            return 1
        # Most commands print less than one buffer of output, so nothing has
        # reached the pipe yet: flush here, where the handler below can still
        # catch the error, rather than leaving it to the interpreter's flush
        # at exit, which would report it as 'Exception ignored' and exit 120.
        sys.stdout.flush()
        return code
    except BrokenPipeError:
        # Output was piped into something that stopped reading, e.g. `| head`.
        # Redirect stdout to devnull so the interpreter's flush at exit does
        # not raise the same error again on the way out.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
