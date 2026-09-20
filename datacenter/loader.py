"""Load the data center from JSON files.

The data lives in files, not in code: `datacenter/data/*.json` is the source of
truth, and `load_all` pours it into the seven databases. Loading is a refill,
not an append — every record carries a natural key (an organization's name, a
contact's email, a document's external_id, a dataset's name plus the record's
canonical payload, a model's name and version) so running it again updates what
changed and leaves the rest alone.

Each kind of data is one JSON array. It can live in a single file
(`documents.json`) or be split across a folder of part files
(`documents/*.json`, loaded in name order) — handy for a knowledge base
organised by topic. Both forms can coexist; the single file loads first.

File formats (all files optional):

organizations.json
    [{"name", "industry", "website"}]
contacts.json
    [{"full_name", "email", "role", "phone", "organization", "notes",
      "channels": [{"channel", "handle", "is_primary"}]}]
documents.json
    [{"external_id", "title", "body", "source", "mime_type", "owner_email",
      "tags": [str]}]
datasets.json
    [{"name", "description", "schema": {field: type}, "records": [{...}]}]
models.json
    [{"name", "provider", "task", "description",
      "versions": [{"version", "dimensions", "is_active"}]}]
jobs.json
    [{"kind", "payload", "priority"}]

Contacts are fictional by construction: the validator rejects any email whose
domain is not a reserved one (`.example`, `.test`, `.invalid`, `.localhost`,
`example.com/net/org`), so real people's addresses cannot end up in the files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .core import DataCenter

DATA_DIR = Path(__file__).parent / "data"

FILES: tuple[str, ...] = (
    "organizations.json",
    "contacts.json",
    "documents.json",
    "datasets.json",
    "models.json",
    "jobs.json",
)

#: Domains reserved for documentation and testing (RFC 2606 / RFC 6761).
#: Any contact email must end in one of these, so the data set is always
#: demonstrably fictional.
FICTIONAL_DOMAIN_RE = re.compile(
    r"@([a-z0-9-]+\.)*(example|test|invalid|localhost)\Z|"
    r"@([a-z0-9-]+\.)*example\.(com|net|org)\Z"
)

#: \A and \Z rather than ^ and $: in Python those also match around a
#: trailing newline, so "ada@x.example\n" would pass as a second, distinct
#: contact.
_EMAIL_RE = re.compile(r"\A[^@\s]+@[^@\s]+\Z")


class DataFileError(ValueError):
    """Raised when a data file does not match the documented format."""


def _read_array(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise DataFileError(f"{label}: not valid UTF-8 ({error})") from error
    except OSError as error:
        raise DataFileError(f"{label}: cannot be read ({error})") from error

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise DataFileError(f"{label}: not valid JSON ({error})") from error
    if not isinstance(payload, list):
        raise DataFileError(f"{label}: top level must be a JSON array")
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise DataFileError(f"{label}[{index}]: each entry must be an object")
    return payload


def part_files(data_dir: Path, name: str) -> list[Path]:
    """The files that make up one kind of data: `name.json` if present, then
    every `name/*.json` part in name order."""
    data_dir = Path(data_dir)
    stem = name[: -len(".json")] if name.endswith(".json") else name
    paths: list[Path] = []
    single = data_dir / f"{stem}.json"
    if single.exists():
        paths.append(single)
    folder = data_dir / stem
    if folder.is_dir():
        paths.extend(sorted(path for path in folder.glob("*.json") if path.is_file()))
    return paths


def read_sources(data_dir: Path, name: str) -> list[tuple[str, dict[str, Any]]]:
    """Every entry of one kind of data, each paired with a label naming the
    file it came from and its index inside that file.

    A message built from the label points at the entry someone can actually
    find, which a running index across concatenated part files does not.
    """
    data_dir = Path(data_dir)
    entries: list[tuple[str, dict[str, Any]]] = []
    for path in part_files(data_dir, name):
        relative = str(path.relative_to(data_dir))
        for index, item in enumerate(_read_array(path, relative)):
            entries.append((f"{relative}[{index}]", item))
    return entries


def read_file(data_dir: Path, name: str) -> list[dict[str, Any]]:
    """Read one kind of data across its single file and part files; nothing
    on disk is an empty list."""
    return [item for _, item in read_sources(Path(data_dir), name)]


# -- validation ---------------------------------------------------------------

#: What each field is allowed to be. A value of the wrong type either crashes
#: the validator or, worse, reaches SQLite and is coerced there, so the shape
#: is checked before anything else is asked of it.
_TYPES: dict[str, dict[str, tuple[tuple[type, ...], str]]] = {
    "organizations": {
        "name": ((str,), "a string"),
        "industry": ((str,), "a string"),
        "website": ((str,), "a string"),
    },
    "contacts": {
        "full_name": ((str,), "a string"),
        "email": ((str,), "a string"),
        "role": ((str,), "a string"),
        "phone": ((str,), "a string"),
        "organization": ((str,), "a string"),
        "notes": ((str,), "a string"),
        "channels": ((list,), "a list"),
    },
    "documents": {
        "external_id": ((str,), "a string"),
        "title": ((str,), "a string"),
        "body": ((str,), "a string"),
        "source": ((str,), "a string"),
        "mime_type": ((str,), "a string"),
        "owner_email": ((str,), "a string"),
        "tags": ((list,), "a list"),
    },
    "datasets": {
        "name": ((str,), "a string"),
        "description": ((str,), "a string"),
        "schema": ((dict,), "an object of field -> type"),
        "records": ((list,), "a list"),
    },
    "models": {
        "name": ((str,), "a string"),
        "provider": ((str,), "a string"),
        "task": ((str,), "a string"),
        "description": ((str,), "a string"),
        "versions": ((list,), "a list"),
    },
    "jobs": {
        "kind": ((str,), "a string"),
        "payload": ((dict,), "an object"),
        "priority": ((int,), "a whole number"),
    },
}


def _shape(label: str, kind: str, item: dict[str, Any]) -> list[str]:
    """Check every field of one entry against _TYPES."""
    problems: list[str] = []
    for key, (kinds, what) in _TYPES[kind].items():
        if key not in item or item[key] is None:
            continue
        value = item[key]
        # bool is a subclass of int, and "priority": true is not a number.
        if isinstance(value, bool) and bool not in kinds:
            problems.append(f"{label}: {key!r} must be {what}")
        elif not isinstance(value, kinds):
            problems.append(f"{label}: {key!r} must be {what}")
    return problems


def _flag(label: str, item: dict[str, Any], key: str) -> list[str]:
    """A true/false field must actually be a JSON boolean.

    Without this, "false" and "no" are truthy strings and set the flag.
    """
    if key in item and item[key] is not None and not isinstance(item[key], bool):
        return [f"{label}: {key!r} must be true or false"]
    return []


def _require(label: str, item: dict[str, Any], *keys: str) -> list[str]:
    return [f"{label}: missing or empty {key!r}" for key in keys if not item.get(key)]


def _unique(entries: list[tuple[str, dict[str, Any]]], key: str) -> list[str]:
    """Report entries that share a natural key.

    Only string values are compared: anything else has already been reported
    by _shape, and comparing Python values would miss that SQLite stores 1 and
    "1" in a TEXT column as the same key, letting one entry overwrite another.
    """
    seen: dict[str, str] = {}
    problems: list[str] = []
    for label, item in entries:
        value = item.get(key)
        if not isinstance(value, str):
            continue
        if value in seen:
            problems.append(f"{label}: duplicate {key} {value!r} (first at {seen[value]})")
        else:
            seen[value] = label
    return problems


def _check_email(label: str, email: str) -> list[str]:
    if not _EMAIL_RE.match(email):
        return [f"{label}: {email!r} is not an email address"]
    if not FICTIONAL_DOMAIN_RE.search(email.lower()):
        return [
            f"{label}: {email!r} must use a reserved domain "
            "(.example, .test, .invalid, example.com) so the data stays fictional"
        ]
    return []


def validate(data_dir: Path | str = DATA_DIR) -> list[str]:
    """Return every problem found in the data files. Empty means valid."""
    data_dir = Path(data_dir)
    # A typo in --data used to validate clean and then seed nothing.
    if not data_dir.exists():
        return [f"{data_dir}: no such data directory"]
    if not data_dir.is_dir():
        return [f"{data_dir}: is not a directory"]

    problems: list[str] = []

    organizations = read_sources(data_dir, "organizations.json")
    for label, org in organizations:
        shape = _shape(label, "organizations", org)
        problems += shape
        if not shape:
            problems += _require(label, org, "name")
    problems += _unique(organizations, "name")
    org_names = {org["name"] for _, org in organizations if isinstance(org.get("name"), str)}

    contacts = read_sources(data_dir, "contacts.json")
    for label, contact in contacts:
        shape = _shape(label, "contacts", contact)
        problems += shape
        if shape:
            continue
        problems += _require(label, contact, "full_name", "email")
        if contact.get("email"):
            problems += _check_email(label, contact["email"])
        organization = contact.get("organization")
        if organization and organization not in org_names:
            problems.append(
                f"{label}: organization {organization!r} is not in organizations.json"
            )
        for channel_index, channel in enumerate(contact.get("channels") or []):
            channel_label = f"{label}.channels[{channel_index}]"
            if not isinstance(channel, dict) or not channel.get("channel") or not channel.get("handle"):
                problems.append(f"{channel_label}: needs 'channel' and 'handle'")
                continue
            problems += _flag(channel_label, channel, "is_primary")
    problems += _unique(contacts, "email")

    documents = read_sources(data_dir, "documents.json")
    for label, document in documents:
        shape = _shape(label, "documents", document)
        problems += shape
        if shape:
            continue
        problems += _require(label, document, "external_id", "title", "body")
        if not all(isinstance(tag, str) for tag in document.get("tags") or []):
            problems.append(f"{label}: 'tags' must be a list of strings")
        owner = document.get("owner_email")
        if owner:
            problems += _check_email(label, owner)
    problems += _unique(documents, "external_id")

    datasets = read_sources(data_dir, "datasets.json")
    for label, dataset in datasets:
        shape = _shape(label, "datasets", dataset)
        problems += shape
        if shape:
            continue
        problems += _require(label, dataset, "name")
        schema = dataset.get("schema") or {}
        for record_index, record in enumerate(dataset.get("records") or []):
            record_label = f"{label}.records[{record_index}]"
            if not isinstance(record, dict):
                problems.append(f"{record_label}: must be an object")
                continue
            unknown = set(record) - set(schema)
            if schema and unknown:
                problems.append(
                    f"{record_label}: fields not in schema: " + ", ".join(sorted(unknown))
                )
    problems += _unique(datasets, "name")

    models = read_sources(data_dir, "models.json")
    for label, model in models:
        shape = _shape(label, "models", model)
        problems += shape
        if shape:
            continue
        problems += _require(label, model, "name", "provider", "task")
        versions = model.get("versions") or []
        if not versions:
            problems.append(f"{label}: needs at least one entry in 'versions'")
        for version_index, version in enumerate(versions):
            version_label = f"{label}.versions[{version_index}]"
            if not isinstance(version, dict) or not version.get("version"):
                problems.append(f"{version_label}: needs 'version'")
                continue
            if not isinstance(version["version"], str):
                problems.append(f"{version_label}: 'version' must be a string")
            dimensions = version.get("dimensions")
            if dimensions is not None and (isinstance(dimensions, bool) or not isinstance(dimensions, int)):
                problems.append(f"{version_label}: 'dimensions' must be a whole number")
            problems += _flag(version_label, version, "is_active")
    problems += _unique(models, "name")

    jobs = read_sources(data_dir, "jobs.json")
    for label, job in jobs:
        shape = _shape(label, "jobs", job)
        problems += shape
        if not shape:
            problems += _require(label, job, "kind")

    return problems


# -- loading ------------------------------------------------------------------

def load_all(center: DataCenter, data_dir: Path | str = DATA_DIR) -> dict[str, int]:
    """Validate the data files, then load every one of them. Returns counts.

    Raises DataFileError before writing anything if a file is malformed, so a
    bad refill never leaves the data center half-updated.
    """
    data_dir = Path(data_dir)
    problems = validate(data_dir)
    if problems:
        raise DataFileError("\n".join(problems))

    counts = {key: 0 for key in ("organizations", "contacts", "documents", "records", "models", "jobs")}

    with center.transaction():
        _load(center, data_dir, counts)

    center.log(
        "audit", "load_data", detail=f"{data_dir}: " + ", ".join(f"{k}={v}" for k, v in counts.items())
    )
    return counts


def _load(center: DataCenter, data_dir: Path, counts: dict[str, int]) -> None:
    """Write every data file into the data center. Called inside a transaction
    by load_all, so an error part-way through keeps none of it."""

    for org in read_file(data_dir, "organizations.json"):
        center.add_organization(
            org["name"], industry=org.get("industry", ""), website=org.get("website", "")
        )
        counts["organizations"] += 1

    for contact in read_file(data_dir, "contacts.json"):
        center.add_contact(
            contact["full_name"],
            email=contact["email"],
            role=contact.get("role", ""),
            phone=contact.get("phone", ""),
            organization=contact.get("organization") or None,
            notes=contact.get("notes", ""),
            channels=contact.get("channels") or [],
        )
        counts["contacts"] += 1

    for model in read_file(data_dir, "models.json"):
        for version in model["versions"]:
            center.register_model(
                model["name"],
                provider=model["provider"],
                task=model["task"],
                version=version["version"],
                dimensions=version.get("dimensions"),
                description=model.get("description", ""),
                activate=bool(version.get("is_active", False)),
            )
        counts["models"] += 1

    for document in read_file(data_dir, "documents.json"):
        center.add_document(
            document["title"],
            document["body"],
            external_id=document["external_id"],
            source=document.get("source", ""),
            mime_type=document.get("mime_type", "text/plain"),
            owner_email=document.get("owner_email", ""),
            tags=document.get("tags") or [],
        )
        counts["documents"] += 1

    for dataset in read_file(data_dir, "datasets.json"):
        center.create_dataset(
            dataset["name"],
            description=dataset.get("description", ""),
            schema=dataset.get("schema") or {},
        )
        # Replaced wholesale, not appended to: an edited record would
        # otherwise be stored beside the version it was meant to correct.
        counts["records"] += center.replace_records(
            dataset["name"], dataset.get("records") or []
        )

    for job in read_file(data_dir, "jobs.json"):
        payload = job.get("payload") or {}
        if not center.has_job(job["kind"], payload):
            center.submit_job(job["kind"], payload, priority=int(job.get("priority", 100)))
            counts["jobs"] += 1


