"""The data center itself: provisions and operates the seven databases."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import vectors
from .config import (
    BUCKET_SPACE,
    DATABASES,
    DATA_CENTER_NAME,
    DEFAULT_ROOT,
    VERSION,
    get_spec,
)
from .engine import Database


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DataCenter:
    """Facade over the seven databases.

    The databases stay separate files — each one owns its own domain — and this
    class is the single place that knows how they fit together: provisioning
    order, cross-database operations (embedding a document, running a job) and
    the audit trail that records all of it.
    """

    name = DATA_CENTER_NAME
    version = VERSION

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else Path(DEFAULT_ROOT)
        self._databases = {
            spec.key: Database(spec, self.root) for spec in DATABASES
        }

    # -- lifecycle ----------------------------------------------------------

    def provision(self) -> list[str]:
        """Create every database and apply its schema. Idempotent."""
        self.root.mkdir(parents=True, exist_ok=True)
        provisioned: list[str] = []
        migrations: list[tuple[str, int]] = []
        for spec in DATABASES:
            migrated = self._databases[spec.key].provision()
            if migrated:
                migrations.append((spec.key, migrated))
            provisioned.append(spec.key)

        # Logged only now: the audit database is provisioned last, so there is
        # nowhere to write an event until the whole loop has run.
        for key, migrated in migrations:
            rows = "row" if migrated == 1 else "rows"
            self.log(key, "migrate", detail=f"{migrated} {rows} removed to meet the current schema")
        self.log("audit", "provision", detail=f"{len(provisioned)} databases ready")
        return provisioned

    @contextmanager
    def transaction(self) -> "Iterator[DataCenter]":
        """Run a block with every database held in a transaction.

        If the block raises, none of its writes are kept — including the audit
        events, because nothing happened. Each database commits separately, so
        this protects against an error part-way through, not against the
        process being killed between commits.
        """
        with ExitStack() as stack:
            for database in self._databases.values():
                stack.enter_context(database.transaction())
            yield self

    def db(self, key: str) -> Database:
        get_spec(key)  # raises a helpful KeyError for unknown keys
        return self._databases[key]

    @property
    def databases(self) -> dict[str, Database]:
        return dict(self._databases)

    def close(self) -> None:
        for database in self._databases.values():
            database.close()

    def __enter__(self) -> "DataCenter":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- audit --------------------------------------------------------------

    def log(self, database: str, action: str, *, actor: str = "system", detail: str = "") -> int:
        """Append an event to the audit database."""
        return self._databases["audit"].insert(
            "events",
            {
                "occurred_at": _utc_now(),
                "database": database,
                "action": action,
                "actor": actor,
                "detail": detail,
            },
        )

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._databases["audit"].query(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in rows]

    # -- contacts -----------------------------------------------------------

    def add_organization(self, name: str, industry: str = "", website: str = "") -> int:
        """Create or update an organization.

        Only the details actually passed are written, so naming an
        organization to attach a contact to it does not blank out the
        industry and website an earlier call recorded.
        """
        values: dict[str, Any] = {"name": name}
        if industry:
            values["industry"] = industry
        if website:
            values["website"] = website
        org_id = self.db("contacts").upsert(
            "organizations", values, conflict="name", keep=("created_at",)
        )
        self.log("contacts", "add_organization", detail=name)
        return org_id

    def add_contact(
        self,
        full_name: str,
        *,
        email: str = "",
        role: str = "",
        phone: str = "",
        organization: str | None = None,
        notes: str = "",
        channels: list[dict[str, Any]] | None = None,
    ) -> int:
        """Store a contact. A contact with an email is keyed on it, so loading
        the same person twice updates the one row instead of adding a second,
        and the channels passed replace the ones that contact already had.
        Leaving `channels` out keeps them, so naming a contact to correct a
        phone number does not drop the handles a data file gave them.
        """
        organization_id = None
        if organization:
            organization_id = self.add_organization(organization)

        contacts = self.db("contacts")
        values = {
            "organization_id": organization_id,
            "full_name": full_name,
            "role": role,
            "email": email or None,
            "phone": phone,
            "notes": notes,
            "created_at": _utc_now(),
        }
        if email:
            contact_id = contacts.upsert("contacts", values, conflict="email", keep=("created_at",))
        else:
            contact_id = contacts.insert("contacts", values)

        # Keyed contacts refill their channels: an edited handle replaces the
        # old one instead of sitting beside it, the way document tags do.
        if email and channels is not None:
            contacts.execute("DELETE FROM contact_channels WHERE contact_id = ?", (contact_id,))

        for channel in channels or []:
            contacts.execute(
                "INSERT INTO contact_channels (contact_id, channel, handle, is_primary) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(contact_id, channel, handle) "
                "DO UPDATE SET is_primary = excluded.is_primary",
                (contact_id, channel["channel"], channel["handle"], int(bool(channel.get("is_primary")))),
            )

        self.log("contacts", "add_contact", detail=full_name)
        return contact_id

    def list_contacts(self) -> list[dict[str, Any]]:
        rows = self.db("contacts").query(
            "SELECT c.id, c.full_name, c.role, c.email, c.phone, o.name AS organization "
            "FROM contacts c LEFT JOIN organizations o ON o.id = c.organization_id "
            "ORDER BY c.full_name"
        )
        return [dict(row) for row in rows]

    # -- documents ----------------------------------------------------------

    def add_document(
        self,
        title: str,
        body: str,
        *,
        external_id: str = "",
        source: str = "",
        mime_type: str = "text/plain",
        owner_email: str = "",
        tags: list[str] | None = None,
        embed: bool = True,
    ) -> int:
        """Store a document and, by default, index it for semantic search.

        A document with an `external_id` is keyed on it: loading it again
        updates the existing row (and replaces its tags) rather than adding a
        duplicate, which is what makes a data refill safe to repeat.
        """
        checksum = hashlib.sha256(body.encode("utf-8")).hexdigest()
        documents = self.db("documents")
        values = {
            "external_id": external_id or None,
            "title": title,
            "body": body,
            "source": source,
            "mime_type": mime_type or "text/plain",
            "checksum": checksum,
            "owner_email": owner_email,
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
        }
        if external_id:
            document_id = documents.upsert(
                "documents", values, conflict="external_id", keep=("created_at",)
            )
            documents.execute("DELETE FROM document_tags WHERE document_id = ?", (document_id,))
        else:
            document_id = documents.insert("documents", values)

        for tag in tags or []:
            documents.execute(
                "INSERT OR IGNORE INTO document_tags (document_id, tag) VALUES (?, ?)",
                (document_id, tag),
            )

        self.log("documents", "add_document", detail=title)
        if embed:
            self.index_document(document_id)
        return document_id

    def get_document(self, document_id: int) -> dict[str, Any] | None:
        row = self.db("documents").query_one(
            "SELECT * FROM documents WHERE id = ?", (document_id,)
        )
        return dict(row) if row else None

    def list_documents(self) -> list[dict[str, Any]]:
        rows = self.db("documents").query(
            "SELECT id, external_id, title, source, owner_email, created_at "
            "FROM documents ORDER BY id"
        )
        return [dict(row) for row in rows]

    def document_tags(self, document_id: int) -> list[str]:
        rows = self.db("documents").query(
            "SELECT tag FROM document_tags WHERE document_id = ? ORDER BY tag", (document_id,)
        )
        return [row["tag"] for row in rows]

    # -- datasets -----------------------------------------------------------

    def create_dataset(
        self, name: str, description: str = "", schema: dict[str, str] | None = None
    ) -> int:
        """Create or update a dataset. Passing no description or schema keeps
        whatever the existing dataset already has."""
        datasets = self.db("datasets")
        values: dict[str, Any] = {"name": name}
        if description:
            values["description"] = description
        if schema is not None:
            values["schema_json"] = json.dumps(schema, sort_keys=True)
        dataset_id = datasets.upsert("datasets", values, conflict="name")
        self.log("datasets", "create_dataset", detail=name)
        return dataset_id

    def add_record(self, dataset: str, payload: dict[str, Any]) -> int:
        """Append a record to a dataset, creating the dataset if needed.

        Payloads are stored as canonical JSON, so the identical record loaded
        twice is recognised and the existing row's id is returned.
        """
        dataset_id = self.create_dataset(dataset)
        datasets = self.db("datasets")
        canonical = json.dumps(payload, sort_keys=True)
        cursor = datasets.execute(
            "INSERT OR IGNORE INTO records (dataset_id, payload, ingested_at) VALUES (?, ?, ?)",
            (dataset_id, canonical, _utc_now()),
        )
        if cursor.rowcount == 1:
            record_id = int(cursor.lastrowid)
        else:
            row = datasets.query_one(
                "SELECT id FROM records WHERE dataset_id = ? AND payload = ?",
                (dataset_id, canonical),
            )
            record_id = int(row["id"])
        self.log("datasets", "add_record", detail=dataset)
        return record_id

    def replace_records(self, dataset: str, payloads: list[dict[str, Any]]) -> int:
        """Make a dataset's records exactly `payloads`, and return how many.

        A refill rather than an append: an edited record replaces the version
        before it instead of sitting beside it, and a record dropped from the
        source is dropped here too. Records added through `add_record` to a
        dataset that a data file also defines do not survive this.
        """
        dataset_id = self.create_dataset(dataset)
        datasets = self.db("datasets")
        canonical = [json.dumps(payload, sort_keys=True) for payload in payloads]

        for payload in canonical:
            datasets.execute(
                "INSERT OR IGNORE INTO records (dataset_id, payload, ingested_at) "
                "VALUES (?, ?, ?)",
                (dataset_id, payload, _utc_now()),
            )

        if canonical:
            placeholders = ", ".join("?" for _ in canonical)
            cursor = datasets.execute(
                f"DELETE FROM records WHERE dataset_id = ? AND payload NOT IN ({placeholders})",
                (dataset_id, *canonical),
            )
        else:
            cursor = datasets.execute(
                "DELETE FROM records WHERE dataset_id = ?", (dataset_id,)
            )
        removed = max(cursor.rowcount, 0)

        self.log(
            "datasets",
            "replace_records",
            detail=f"{dataset}: {len(canonical)} kept, {removed} removed",
        )
        return len(canonical)

    def list_datasets(self) -> list[dict[str, Any]]:
        rows = self.db("datasets").query(
            "SELECT d.id, d.name, d.description, d.schema_json, COUNT(r.id) AS records "
            "FROM datasets d LEFT JOIN records r ON r.dataset_id = d.id "
            "GROUP BY d.id ORDER BY d.name"
        )
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"],
                "schema": json.loads(row["schema_json"]) if row["schema_json"] else {},
                "records": row["records"],
            }
            for row in rows
        ]

    def list_records(self, dataset: str) -> list[dict[str, Any]]:
        rows = self.db("datasets").query(
            "SELECT r.id, r.payload, r.ingested_at FROM records r "
            "JOIN datasets d ON d.id = r.dataset_id WHERE d.name = ? ORDER BY r.id",
            (dataset,),
        )
        return [
            {"id": row["id"], "payload": json.loads(row["payload"]), "ingested_at": row["ingested_at"]}
            for row in rows
        ]

    # -- model registry -----------------------------------------------------

    def register_model(
        self,
        name: str,
        *,
        provider: str,
        task: str,
        version: str,
        dimensions: int | None = None,
        description: str = "",
        activate: bool = True,
    ) -> int:
        models = self.db("models")
        model_id = models.upsert(
            "models",
            {"name": name, "provider": provider, "task": task, "description": description},
            conflict="name",
        )
        models.execute(
            "INSERT INTO model_versions (model_id, version, dimensions, is_active) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(model_id, version) DO UPDATE SET "
            "dimensions = excluded.dimensions, is_active = excluded.is_active",
            (model_id, version, dimensions, int(activate)),
        )
        self.log("models", "register_model", detail=f"{name}@{version}")
        return model_id

    def list_models(self) -> list[dict[str, Any]]:
        rows = self.db("models").query(
            "SELECT m.name, m.provider, m.task, v.version, v.dimensions, v.is_active "
            "FROM models m JOIN model_versions v ON v.model_id = m.id ORDER BY m.name, v.version"
        )
        return [dict(row) for row in rows]

    # -- embeddings and search ----------------------------------------------

    def index_document(self, document_id: int, model: str = vectors.LOCAL_MODEL_NAME) -> int:
        """Compute and store the embedding for one document."""
        document = self.get_document(document_id)
        if document is None:
            raise KeyError(f"no document with id {document_id}")

        vector = vectors.embed(f"{document['title']}\n{document['body']}")
        embedding_id = self.db("embeddings").upsert(
            "embeddings",
            {
                "document_id": document_id,
                "model": model,
                # The bucket space the vector was built in, not its length:
                # two embeddings are only comparable when they hash into the
                # same space, and swapping in another embedder must show up
                # here or the column says nothing.
                "dimensions": BUCKET_SPACE,
                "vector": vectors.dumps(vector),
                "created_at": _utc_now(),
            },
            conflict="document_id, model",
        )
        self.log("embeddings", "index_document", detail=f"document {document_id}")
        return embedding_id

    def reindex(self, model: str = vectors.LOCAL_MODEL_NAME) -> int:
        """Re-embed every document. Returns the number indexed."""
        documents = self.db("documents").query("SELECT id FROM documents ORDER BY id")
        for row in documents:
            self.index_document(int(row["id"]), model=model)
        return len(documents)

    #: No floor by default. An absolute cosine cannot separate a collision
    #: from a match: a one-word query scores 1/sqrt(N) against a document of N
    #: distinct words, so any floor high enough to reject a collision in a
    #: short document also rejects a real hit in a long one. Collisions are
    #: made negligible in the embedder instead (see config.BUCKET_SPACE).
    #: Callers can still pass `min_score` to ask for a stronger overlap.
    min_score = 0.0

    def search(
        self,
        query: str,
        *,
        limit: int = 5,
        model: str = vectors.LOCAL_MODEL_NAME,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search across indexed documents, best match first.

        A document that shares nothing with the query is never a hit, whatever
        floor the caller asks for: a score of exactly zero means no term in
        common, not a weak match.
        """
        if limit is None or limit < 1:
            raise ValueError("limit must be at least 1")

        threshold = self.min_score if min_score is None else min_score
        query_vector = vectors.embed(query)
        # The bucket space is part of the match: vectors hashed into a
        # different space are not comparable to this query.
        rows = self.db("embeddings").query(
            "SELECT document_id, vector FROM embeddings WHERE model = ? AND dimensions = ?",
            (model, BUCKET_SPACE),
        )
        if not rows:
            self._require_current_index(model, BUCKET_SPACE)

        scored: list[tuple[float, int]] = []
        unreadable = 0
        for row in rows:
            try:
                stored = vectors.loads(row["vector"])
            except (ValueError, KeyError, TypeError):
                # A corrupt blob is one lost document, not a failed search.
                unreadable += 1
                continue
            score = vectors.similarity(query_vector, stored)
            if score > 0.0 and score >= threshold:
                scored.append((score, int(row["document_id"])))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        if unreadable:
            self.log("embeddings", "search_skip", detail=f"{unreadable} unreadable vectors")

        results: list[dict[str, Any]] = []
        for score, document_id in scored[:limit]:
            document = self.get_document(document_id) or {}
            results.append(
                {
                    "document_id": document_id,
                    "score": round(score, 4),
                    "title": document.get("title", "(missing document)"),
                    "source": document.get("source", ""),
                }
            )

        self.log("embeddings", "search", detail=f"{query!r} -> {len(results)} hits")
        return results

    def _require_current_index(self, model: str, dimensions: int) -> None:
        """Refuse to pass off a stale index as an empty one.

        Changing `BUCKET_SPACE` (or the embedder behind it) leaves every
        stored vector outside the space filter, so search would quietly answer
        "no matches" for every query until the index is rebuilt.
        """
        row = self.db("embeddings").query_one(
            "SELECT COUNT(*) AS n FROM embeddings WHERE model = ?", (model,)
        )
        if row and row["n"]:
            raise RuntimeError(
                f"{row['n']} embeddings for {model!r} were built in a bucket space "
                f"other than {dimensions}; run reindex()"
            )

    def _stale_embeddings(self) -> int:
        """Stored vectors whose bucket space is not the one the embedder uses now.

        They are excluded from every search until `reindex()` rebuilds them,
        and nothing else in a health report would show it.
        """
        embeddings = self._databases["embeddings"]
        if not embeddings.path.exists() or "embeddings" not in embeddings.tables():
            return 0
        row = embeddings.query_one(
            "SELECT COUNT(*) AS n FROM embeddings WHERE dimensions != ?", (BUCKET_SPACE,)
        )
        return int(row["n"]) if row else 0

    # -- jobs ---------------------------------------------------------------

    def submit_job(self, kind: str, payload: dict[str, Any] | None = None, priority: int = 100) -> int:
        job_id = self.db("jobs").insert(
            "jobs",
            {
                "kind": kind,
                "payload": json.dumps(payload or {}, sort_keys=True),
                "priority": priority,
                "submitted_at": _utc_now(),
            },
        )
        self.log("jobs", "submit_job", detail=f"{kind} #{job_id}")
        return job_id

    def run_next_job(self) -> dict[str, Any] | None:
        """Claim the highest-priority pending job, run it, record the outcome.

        Supported kinds: `reindex` (re-embed every document) and `noop`.
        Anything else is recorded as failed rather than raising, so one bad job
        never stalls the queue.
        """
        jobs = self.db("jobs")
        row = jobs.query_one(
            "SELECT * FROM jobs WHERE status = 'pending' ORDER BY priority, id LIMIT 1"
        )
        if row is None:
            return None

        job_id = int(row["id"])
        jobs.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job_id,))
        run_id = jobs.insert("job_runs", {"job_id": job_id, "started_at": _utc_now()})

        kind = row["kind"]
        try:
            if kind == "reindex":
                detail = f"reindexed {self.reindex()} documents"
            elif kind == "noop":
                detail = "nothing to do"
            else:
                raise ValueError(f"unsupported job kind {kind!r}")
            outcome = "done"
        except Exception as error:  # recorded, not raised: the queue keeps moving
            outcome, detail = "failed", str(error)

        jobs.execute(
            "UPDATE jobs SET status = ? WHERE id = ?",
            ("done" if outcome == "done" else "failed", job_id),
        )
        jobs.execute(
            "UPDATE job_runs SET finished_at = ?, outcome = ?, detail = ? WHERE id = ?",
            (_utc_now(), outcome, detail, run_id),
        )
        self.log("jobs", "run_job", detail=f"{kind} #{job_id}: {outcome}")
        return {"job_id": job_id, "kind": kind, "outcome": outcome, "detail": detail}

    def has_job(self, kind: str, payload: dict[str, Any] | None = None) -> bool:
        """True if a job with this kind and payload exists in any state."""
        row = self.db("jobs").query_one(
            "SELECT 1 FROM jobs WHERE kind = ? AND payload = ? LIMIT 1",
            (kind, json.dumps(payload or {}, sort_keys=True)),
        )
        return row is not None

    def pending_jobs(self) -> list[dict[str, Any]]:
        rows = self.db("jobs").query(
            "SELECT id, kind, priority, submitted_at FROM jobs WHERE status = 'pending' "
            "ORDER BY priority, id"
        )
        return [dict(row) for row in rows]

    # -- operations ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Health and size report for all seven databases.

        `provisioned` means the database holds the tables its spec calls for,
        not merely that a file of that name is on disk: an empty or damaged
        file is reported as unprovisioned and unhealthy rather than as a
        working database with nothing in it. `exists` tells the two apart.
        """
        report: dict[str, Any] = {
            "name": self.name,
            "version": self.version,
            "root": str(self.root),
            "generated_at": _utc_now(),
            "stale_embeddings": self._stale_embeddings(),
            "databases": [],
        }
        for spec in DATABASES:
            database = self._databases[spec.key]
            exists = database.path.exists()
            tables = database.tables() if exists else []
            provisioned = set(spec.tables).issubset(tables)
            report["databases"].append(
                {
                    "key": spec.key,
                    "title": spec.title,
                    "purpose": spec.purpose,
                    "exists": exists,
                    "provisioned": provisioned,
                    "path": str(database.path),
                    "size_bytes": database.size_bytes(),
                    "tables": tables,
                    "rows": database.row_counts() if provisioned else {},
                    "healthy": provisioned and database.integrity_ok(),
                }
            )
        return report

    def verify(self) -> dict[str, bool]:
        """Check that every database is there, complete and readable.

        A database only passes if its file exists, holds the tables its spec
        calls for and survives SQLite's integrity check. Verifying a root that
        was never initialised answers False for all seven and creates nothing,
        so a mistyped `--root` is reported rather than provisioned by accident.
        """
        results: dict[str, bool] = {}
        for spec in DATABASES:
            database = self._databases[spec.key]
            results[spec.key] = (
                database.path.exists()
                and set(spec.tables).issubset(database.tables())
                and database.integrity_ok()
            )
        if results["audit"]:
            self.log("audit", "verify", detail=f"{sum(results.values())}/{len(results)} healthy")
        return results

    def backup(self, destination: Path | str) -> Path:
        """Back up every provisioned database into a timestamped folder.

        A database whose file is missing is skipped and left out of the
        manifest, rather than being conjured up empty and copied as if it held
        something.
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        target = Path(destination) / stamp
        target.mkdir(parents=True, exist_ok=True)

        copied: list[str] = []
        for spec in DATABASES:
            database = self._databases[spec.key]
            if not database.path.exists():
                continue
            database.backup_to(target / spec.filename)
            copied.append(spec.key)

        manifest = {
            "data_center": self.name,
            "version": self.version,
            "created_at": _utc_now(),
            "databases": copied,
        }
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

        if "audit" in copied:
            self.log("audit", "backup", detail=str(target))
        return target
