"""SQL schema for each of the seven databases.

Each entry in SCHEMAS is a list of statements applied in order when the
database is provisioned. Every statement is `IF NOT EXISTS`, so provisioning is
idempotent and safe to re-run against an existing data center.

MIGRATIONS holds the statements that run first, to bring a database written by
an earlier version up to what the current schema can accept.
"""

from __future__ import annotations

SCHEMAS: dict[str, tuple[str, ...]] = {
    "contacts": (
        """
        CREATE TABLE IF NOT EXISTS organizations (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            industry    TEXT,
            website     TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS contacts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            organization_id INTEGER REFERENCES organizations(id) ON DELETE SET NULL,
            full_name       TEXT NOT NULL,
            role            TEXT,
            email           TEXT UNIQUE,
            phone           TEXT,
            notes           TEXT,
            created_at      TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS contact_channels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id  INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
            channel     TEXT NOT NULL,
            handle      TEXT NOT NULL,
            is_primary  INTEGER NOT NULL DEFAULT 0,
            UNIQUE (contact_id, channel, handle)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_contacts_org ON contacts(organization_id)",
        "CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(full_name)",
    ),
    "documents": (
        """
        CREATE TABLE IF NOT EXISTS documents (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            external_id  TEXT UNIQUE,
            title        TEXT NOT NULL,
            body         TEXT NOT NULL DEFAULT '',
            source       TEXT,
            mime_type    TEXT NOT NULL DEFAULT 'text/plain',
            checksum     TEXT,
            owner_email  TEXT,
            created_at   TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS document_tags (
            document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            tag         TEXT NOT NULL,
            PRIMARY KEY (document_id, tag)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_documents_title ON documents(title)",
        "CREATE INDEX IF NOT EXISTS idx_document_tags_tag ON document_tags(tag)",
    ),
    "datasets": (
        """
        CREATE TABLE IF NOT EXISTS datasets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            description TEXT,
            schema_json TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS records (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id  INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
            payload     TEXT NOT NULL,
            ingested_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_records_dataset ON records(dataset_id)",
        # Payloads are stored as canonical JSON (sorted keys), so the same
        # record loaded twice is byte-identical and the index rejects it.
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_records_unique ON records(dataset_id, payload)",
    ),
    "models": (
        """
        CREATE TABLE IF NOT EXISTS models (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            provider    TEXT NOT NULL,
            task        TEXT NOT NULL,
            description TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS model_versions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id   INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
            version    TEXT NOT NULL,
            dimensions INTEGER,
            is_active  INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (model_id, version)
        )
        """,
    ),
    "embeddings": (
        """
        CREATE TABLE IF NOT EXISTS embeddings (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL,
            model       TEXT NOT NULL,
            dimensions  INTEGER NOT NULL,
            vector      TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (document_id, model)
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_embeddings_doc ON embeddings(document_id)",
    ),
    "jobs": (
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            kind         TEXT NOT NULL,
            payload      TEXT NOT NULL DEFAULT '{}',
            status       TEXT NOT NULL DEFAULT 'pending',
            priority     INTEGER NOT NULL DEFAULT 100,
            submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
            CHECK (status IN ('pending', 'running', 'done', 'failed'))
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS job_runs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            started_at  TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT,
            outcome     TEXT,
            detail      TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, priority)",
    ),
    "audit": (
        """
        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL DEFAULT (datetime('now')),
            database   TEXT NOT NULL,
            action     TEXT NOT NULL,
            actor      TEXT NOT NULL DEFAULT 'system',
            detail     TEXT
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_events_db ON events(database, occurred_at)",
    ),
}

#: Statements run before a database's schema, so that a database created by an
#: older version can still be provisioned. Each is `(table, statement)`: the
#: statement runs only when that table already exists, and must be safe to run
#: again on a database that has already been migrated.
MIGRATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "datasets": (
        (
            "records",
            # UNIQUE(dataset_id, payload) arrived after the first release. The
            # version before it stored an exact duplicate as a second row, and
            # the index cannot be created while those rows are there, so drop
            # the later copies and keep the earliest of each.
            "DELETE FROM records WHERE id NOT IN "
            "(SELECT MIN(id) FROM records GROUP BY dataset_id, payload)",
        ),
    ),
}
