"""Command line interface: `python -m datacenter <command>`."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .config import DATA_CENTER_NAME, DEFAULT_ROOT, VERSION
from .core import DataCenter
from .loader import DATA_DIR, DataFileError, validate
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
