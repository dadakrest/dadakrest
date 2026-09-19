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
    r"@([a-z0-9-]+\.)*(example|test|invalid|localhost)$|"
    r"@([a-z0-9-]+\.)*example\.(com|net|org)$"
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+$")


class DataFileError(ValueError):
    """Raised when a data file does not match the documented format."""


def _read_array(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
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


def read_file(data_dir: Path, name: str) -> list[dict[str, Any]]:
    """Read one kind of data across its single file and part files; nothing
    on disk is an empty list."""
    data_dir = Path(data_dir)
    items: list[dict[str, Any]] = []
    for path in part_files(data_dir, name):
        items.extend(_read_array(path, str(path.relative_to(data_dir))))
    return items


# -- validation ---------------------------------------------------------------

def _require(name: str, index: int, item: dict[str, Any], *keys: str) -> list[str]:
    return [
        f"{name}[{index}]: missing or empty {key!r}"
        for key in keys
        if not item.get(key)
    ]


def _unique(name: str, items: list[dict[str, Any]], key: str) -> list[str]:
    seen: dict[Any, int] = {}
    problems: list[str] = []
    for index, item in enumerate(items):
        value = item.get(key)
        if value in seen:
            problems.append(f"{name}[{index}]: duplicate {key} {value!r} (first at [{seen[value]}])")
        else:
            seen[value] = index
    return problems


def _check_email(name: str, index: int, email: str) -> list[str]:
    if not _EMAIL_RE.match(email):
        return [f"{name}[{index}]: {email!r} is not an email address"]
    if not FICTIONAL_DOMAIN_RE.search(email.lower()):
        return [
            f"{name}[{index}]: {email!r} must use a reserved domain "
            "(.example, .test, .invalid, example.com) so the data stays fictional"
        ]
    return []


def validate(data_dir: Path | str = DATA_DIR) -> list[str]:
    """Return every problem found in the data files. Empty means valid."""
    data_dir = Path(data_dir)
    problems: list[str] = []

    organizations = read_file(data_dir, "organizations.json")
    for index, org in enumerate(organizations):
        problems += _require("organizations", index, org, "name")
    problems += _unique("organizations", organizations, "name")
    org_names = {org.get("name") for org in organizations}

    contacts = read_file(data_dir, "contacts.json")
    for index, contact in enumerate(contacts):
        problems += _require("contacts", index, contact, "full_name", "email")
        if contact.get("email"):
            problems += _check_email("contacts", index, str(contact["email"]))
        organization = contact.get("organization")
        if organization and organization not in org_names:
            problems.append(
                f"contacts[{index}]: organization {organization!r} is not in organizations.json"
            )
        for channel_index, channel in enumerate(contact.get("channels") or []):
            if not isinstance(channel, dict) or not channel.get("channel") or not channel.get("handle"):
                problems.append(
                    f"contacts[{index}].channels[{channel_index}]: needs 'channel' and 'handle'"
                )
    problems += _unique("contacts", contacts, "email")

    documents = read_file(data_dir, "documents.json")
    for index, document in enumerate(documents):
        problems += _require("documents", index, document, "external_id", "title", "body")
        tags = document.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            problems.append(f"documents[{index}]: 'tags' must be a list of strings")
        owner = document.get("owner_email")
        if owner:
            problems += _check_email("documents", index, str(owner))
    problems += _unique("documents", documents, "external_id")

    datasets = read_file(data_dir, "datasets.json")
    for index, dataset in enumerate(datasets):
        problems += _require("datasets", index, dataset, "name")
        schema = dataset.get("schema") or {}
        records = dataset.get("records") or []
        if not isinstance(schema, dict):
            problems.append(f"datasets[{index}]: 'schema' must be an object of field -> type")
            schema = {}
        if not isinstance(records, list):
            problems.append(f"datasets[{index}]: 'records' must be a list")
            records = []
        for record_index, record in enumerate(records):
            if not isinstance(record, dict):
                problems.append(f"datasets[{index}].records[{record_index}]: must be an object")
                continue
            unknown = set(record) - set(schema)
            if schema and unknown:
                problems.append(
                    f"datasets[{index}].records[{record_index}]: fields not in schema: "
                    + ", ".join(sorted(unknown))
                )
    problems += _unique("datasets", datasets, "name")

    models = read_file(data_dir, "models.json")
    for index, model in enumerate(models):
        problems += _require("models", index, model, "name", "provider", "task")
        versions = model.get("versions") or []
        if not versions:
            problems.append(f"models[{index}]: needs at least one entry in 'versions'")
        for version_index, version in enumerate(versions):
            if not isinstance(version, dict) or not version.get("version"):
                problems.append(f"models[{index}].versions[{version_index}]: needs 'version'")
    problems += _unique("models", models, "name")

    jobs = read_file(data_dir, "jobs.json")
    for index, job in enumerate(jobs):
        problems += _require("jobs", index, job, "kind")
        if job.get("payload") is not None and not isinstance(job["payload"], dict):
            problems.append(f"jobs[{index}]: 'payload' must be an object")

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
        for record in dataset.get("records") or []:
            center.add_record(dataset["name"], record)
            counts["records"] += 1

    for job in read_file(data_dir, "jobs.json"):
        payload = job.get("payload") or {}
        if not center.has_job(job["kind"], payload):
            center.submit_job(job["kind"], payload, priority=int(job.get("priority", 100)))
            counts["jobs"] += 1

    center.log("audit", "load_data", detail=f"{data_dir}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    return counts
