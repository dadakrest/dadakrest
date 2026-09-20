"""Thin SQLite wrapper used by every database in the data center."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import DatabaseSpec
from .schema import MIGRATIONS, SCHEMAS

#: `INSERT ... RETURNING` landed in SQLite 3.35.
_SUPPORTS_RETURNING = sqlite3.sqlite_version_info >= (3, 35, 0)


class Database:
    """One provisioned SQLite database, opened lazily on first use."""

    def __init__(self, spec: DatabaseSpec, root: Path) -> None:
        self.spec = spec
        self.path = Path(root) / spec.filename
        self._connection: sqlite3.Connection | None = None

    # -- connection handling ------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            self._connection = connection
        return self._connection

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- statements ---------------------------------------------------------

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Run a single statement and commit it."""
        cursor = self.connection.execute(sql, params)
        self.connection.commit()
        return cursor

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        cursor = self.connection.executemany(sql, rows)
        self.connection.commit()
        return cursor

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def insert(self, table: str, values: dict[str, Any]) -> int:
        """Insert a row and return its rowid."""
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        cursor = self.execute(
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders})",
            tuple(values.values()),
        )
        return int(cursor.lastrowid)

    def upsert(
        self,
        table: str,
        values: dict[str, Any],
        conflict: str,
        keep: Sequence[str] = (),
    ) -> int:
        """Insert a row, updating the existing one on a conflict target.

        `conflict` is the conflict target: one column name, or several
        comma-separated for a composite unique constraint. Columns named in
        `keep` are written on insert but left alone on update, which is how a
        `created_at` survives a refill. Returns the id of the row that ended
        up in the table — on the update path `lastrowid` is not that id, so
        the statement uses RETURNING, falling back to a lookup on older SQLite
        builds.
        """
        conflict_columns = [column.strip() for column in conflict.split(",")]
        columns = ", ".join(values)
        placeholders = ", ".join("?" for _ in values)
        updates = ", ".join(
            f"{column} = excluded.{column}"
            for column in values
            if column not in conflict_columns and column not in keep
        ) or f"{conflict_columns[0]} = excluded.{conflict_columns[0]}"

        statement = (
            f"INSERT INTO {table} ({columns}) VALUES ({placeholders}) "
            f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
        )
        parameters = tuple(values.values())

        if _SUPPORTS_RETURNING:
            row = self.connection.execute(statement + " RETURNING id", parameters).fetchone()
            self.connection.commit()
            return int(row["id"])

        self.execute(statement, parameters)  # pragma: no cover - legacy SQLite
        where = " AND ".join(f"{column} = ?" for column in conflict_columns)
        row = self.query_one(
            f"SELECT id FROM {table} WHERE {where}",
            tuple(values[column] for column in conflict_columns),
        )
        return int(row["id"])

    # -- provisioning and health -------------------------------------------

    def provision(self) -> int:
        """Migrate, then apply the database's schema. Safe to call repeatedly.

        Returns the number of rows the migration step changed, which is 0 for
        a fresh database and for one already on the current schema.
        """
        migrated = self.migrate()
        for statement in SCHEMAS[self.spec.key]:
            self.connection.execute(statement)
        self.connection.commit()
        return migrated

    def migrate(self) -> int:
        """Bring a database written by an older version up to what the current
        schema accepts, and return the number of rows changed.

        Runs before the schema itself, because a constraint added after the
        first release cannot be applied while the rows that violate it are
        still there.
        """
        statements = MIGRATIONS.get(self.spec.key, ())
        if not statements:
            return 0

        existing = set(self.tables())
        changed = 0
        for table, statement in statements:
            if table in existing:
                changed += max(self.connection.execute(statement).rowcount, 0)
        self.connection.commit()
        return changed

    def tables(self) -> list[str]:
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        return [row["name"] for row in rows]

    def row_counts(self) -> dict[str, int]:
        return {
            table: int(self.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"])
            for table in self.tables()
        }

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0

    def integrity_ok(self) -> bool:
        row = self.query_one("PRAGMA integrity_check")
        return bool(row) and row[0] == "ok"

    def backup_to(self, destination: Path) -> Path:
        """Copy this database to `destination` using SQLite's backup API."""
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self.connection.backup(target)
        finally:
            target.close()
        return destination

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Database {self.spec.key} at {self.path}>"
