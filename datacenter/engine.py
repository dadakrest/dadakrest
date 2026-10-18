"""Thin SQLite wrapper used by every database in the data center."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import DatabaseSpec
from .schema import MIGRATIONS, SCHEMAS

#: `INSERT ... RETURNING` landed in SQLite 3.35.
_SUPPORTS_RETURNING = sqlite3.sqlite_version_info >= (3, 35, 0)


def _quoted(identifier: str) -> str:
    """Quote a table or column name for use in a statement."""
    return '"' + identifier.replace('"', '""') + '"'


class Database:
    """One provisioned SQLite database, opened lazily on first use."""

    def __init__(self, spec: DatabaseSpec, root: Path) -> None:
        self.spec = spec
        self.path = Path(root) / spec.filename
        self._connection: sqlite3.Connection | None = None
        self._autocommit = True

    # -- connection handling ------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        """The open connection. The database has to exist already: reading one
        that was never provisioned is an error, not a reason to create it.
        """
        return self._open(create=False)

    def _open(self, *, create: bool) -> sqlite3.Connection:
        """Return the open connection, connecting on first use.

        `create` decides what happens when the file is not there: provisioning
        creates it, everything else refuses.
        """
        if self._connection is not None:
            return self._connection
        if not create and not self.path.exists():
            raise FileNotFoundError(
                f"{self.spec.key} is not provisioned at {self.path}; run init"
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
        except BaseException:
            # A file that is not a database fails here; without this the
            # half-open connection would leak once per attempt.
            connection.close()
            raise
        self._connection = connection
        return connection

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
        """Run a single statement, committing unless a transaction is open."""
        cursor = self.connection.execute(sql, params)
        self._commit()
        return cursor

    def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> sqlite3.Cursor:
        cursor = self.connection.executemany(sql, rows)
        self._commit()
        return cursor

    def _commit(self) -> None:
        if self._autocommit:
            self.connection.commit()

    @contextmanager
    def transaction(self) -> "Iterator[Database]":
        """Hold every write in the block until it ends, then commit once.

        If the block raises, nothing it wrote is kept. Nesting is a no-op, so
        the outermost block decides when the work lands.
        """
        if not self._autocommit:
            yield self
            return

        self._autocommit = False
        try:
            yield self
            self.connection.commit()
        except BaseException:
            self.connection.rollback()
            raise
        finally:
            self._autocommit = True

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
            self._commit()
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
        self._open(create=True)  # the one place a database file is created
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
        """The tables the file holds, or none if it is not a readable database.

        A file that is not SQLite at all has no tables to report, and saying so
        lets a health report cover the other databases instead of stopping at
        the damaged one.
        """
        try:
            rows = self.query(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        except sqlite3.DatabaseError:
            return []
        return [row["name"] for row in rows]

    def row_counts(self) -> dict[str, int]:
        """Count the rows in every table the file holds.

        The names come from `sqlite_master`, so they can be anything SQLite
        accepted — including a scratch table someone created by hand — and are
        quoted rather than spliced in raw.
        """
        return {
            table: int(self.query_one(f"SELECT COUNT(*) AS n FROM {_quoted(table)}")["n"])
            for table in self.tables()
        }

    def size_bytes(self) -> int:
        """How much database there is, in bytes.

        Read through the connection rather than off the file, because every
        connection runs in WAL mode: rows written in this session sit in the
        `-wal` file until a checkpoint, and `stat()` would report a database
        holding hundreds of rows as one empty page. Falls back to the file size
        for a database SQLite cannot open.
        """
        if not self.path.exists():
            return 0
        try:
            page_count = int(self.query_one("PRAGMA page_count")[0])
            page_size = int(self.query_one("PRAGMA page_size")[0])
        except sqlite3.DatabaseError:
            return self.path.stat().st_size
        return page_count * page_size

    def integrity_ok(self) -> bool:
        """True if SQLite can read the file and finds nothing wrong with it.

        A file SQLite cannot open at all is the strongest failure there is, so
        it answers False rather than raising.
        """
        try:
            row = self.query_one("PRAGMA integrity_check")
        except sqlite3.DatabaseError:
            return False
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
