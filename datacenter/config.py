"""Static configuration for the data center.

Everything that describes *what* the data center is made of lives here, so the
engine, the schema and the CLI all agree on one source of truth.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Product name. Used in CLI banners, backup manifests and the audit trail.
DATA_CENTER_NAME = "Software da Database User 13"

#: Short machine-safe slug derived from the product name.
DATA_CENTER_SLUG = "software-da-database-user-13"

VERSION = "1.0.0"

#: Default on-disk location of the provisioned databases. Override with the
#: SDDU13_ROOT environment variable or `DataCenter(root=...)`.
DEFAULT_ROOT = Path(os.environ.get("SDDU13_ROOT", Path.cwd() / "storage"))

#: The space the local embedder hashes tokens into. It is deliberately huge:
#: an embedding is stored as a sparse map of the buckets it actually uses, so a
#: wide space costs nothing and makes two different words sharing a bucket a
#: ~2**-63 event rather than a ~1/4096 one. Collisions used to be filtered by a
#: minimum score, which cannot work — see DataCenter.search.
BUCKET_SPACE = 2**63 - 1


@dataclass(frozen=True)
class DatabaseSpec:
    """Describes one of the seven databases in the data center."""

    key: str
    title: str
    purpose: str
    tables: tuple[str, ...] = field(default_factory=tuple)

    @property
    def filename(self) -> str:
        return f"{self.key}.db"


#: The seven databases. Order is the provisioning order and the display order.
DATABASES: tuple[DatabaseSpec, ...] = (
    DatabaseSpec(
        key="contacts",
        title="Contacts",
        purpose="People and organizations the business works with.",
        tables=("organizations", "contacts", "contact_channels"),
    ),
    DatabaseSpec(
        key="documents",
        title="Documents",
        purpose="Documents, their text and their metadata.",
        tables=("documents", "document_tags"),
    ),
    DatabaseSpec(
        key="datasets",
        title="Datasets",
        purpose="Structured data records grouped into named datasets.",
        tables=("datasets", "records"),
    ),
    DatabaseSpec(
        key="models",
        title="Model registry",
        purpose="AI models available to the data center and their versions.",
        tables=("models", "model_versions"),
    ),
    DatabaseSpec(
        key="embeddings",
        title="Embeddings",
        purpose="Vector index used for semantic search over documents.",
        tables=("embeddings",),
    ),
    DatabaseSpec(
        key="jobs",
        title="Jobs",
        purpose="Queue of processing work: ingest, embed, export.",
        tables=("jobs", "job_runs"),
    ),
    DatabaseSpec(
        key="audit",
        title="Audit",
        purpose="Append-only log of everything that happens in the data center.",
        tables=("events",),
    ),
)

DATABASE_KEYS: tuple[str, ...] = tuple(spec.key for spec in DATABASES)


def get_spec(key: str) -> DatabaseSpec:
    """Return the spec for `key`, or raise a helpful error."""
    for spec in DATABASES:
        if spec.key == key:
            return spec
    known = ", ".join(DATABASE_KEYS)
    raise KeyError(f"unknown database {key!r} (available: {known})")
