"""Tests for Software da Database User 13. Run: python -m unittest discover tests"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datacenter import DATABASE_KEYS, DataCenter  # noqa: E402
from datacenter.cli import main as cli_main  # noqa: E402
from datacenter.seed import seed  # noqa: E402
from datacenter.vectors import cosine_similarity, embed_text  # noqa: E402


class DataCenterTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "storage"
        self.center = DataCenter(root=self.root)
        self.center.provision()

    def tearDown(self) -> None:
        self.center.close()
        self._tmp.cleanup()


class TestProvisioning(DataCenterTestCase):
    def test_seven_databases_exist(self) -> None:
        self.assertEqual(len(DATABASE_KEYS), 7)
        for key in DATABASE_KEYS:
            self.assertTrue(self.center.db(key).path.exists(), f"{key} missing")

    def test_every_database_has_its_tables(self) -> None:
        for key in DATABASE_KEYS:
            spec = self.center.db(key).spec
            self.assertEqual(sorted(spec.tables), self.center.db(key).tables())

    def test_provision_is_idempotent(self) -> None:
        self.center.provision()
        self.center.provision()
        self.assertTrue(all(self.center.verify().values()))

    def test_provisioning_upgrades_a_database_that_holds_duplicate_records(self) -> None:
        """A data center from before UNIQUE(dataset_id, payload) can be reopened.

        The earlier version stored an exact duplicate as a second row, and the
        index cannot be created while those rows are there.
        """
        datasets = self.center.db("datasets")
        datasets.execute("DROP INDEX IF EXISTS idx_records_unique")
        dataset_id = self.center.create_dataset("legacy")
        for _ in range(3):
            datasets.execute(
                "INSERT INTO records (dataset_id, payload) VALUES (?, ?)",
                (dataset_id, '{"a": 1}'),
            )
        datasets.execute(
            "INSERT INTO records (dataset_id, payload) VALUES (?, ?)",
            (dataset_id, '{"a": 2}'),
        )
        self.assertEqual(datasets.row_counts()["records"], 4)

        self.center.provision()  # must not raise

        payloads = [row["payload"] for row in datasets.query("SELECT payload FROM records ORDER BY id")]
        self.assertEqual(payloads, ['{"a": 1}', '{"a": 2}'])
        self.assertTrue(self.center.verify()["datasets"])

    def test_upgrading_a_root_whose_audit_database_does_not_exist_yet(self) -> None:
        """The pre-existing database is migrated before audit is provisioned.

        The audit database is created last, so a migration cannot be logged
        while the loop is still running.
        """
        root = Path(self._tmp.name) / "legacy"
        root.mkdir()
        connection = sqlite3.connect(root / "datasets.db")
        connection.executescript(
            "CREATE TABLE datasets (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "name TEXT NOT NULL UNIQUE, description TEXT, schema_json TEXT, "
            "created_at TEXT NOT NULL DEFAULT (datetime('now')));"
            "CREATE TABLE records (id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE, "
            "payload TEXT NOT NULL, ingested_at TEXT NOT NULL DEFAULT (datetime('now')));"
        )
        connection.execute("INSERT INTO datasets (name) VALUES ('legacy')")
        for _ in range(2):
            connection.execute("INSERT INTO records (dataset_id, payload) VALUES (1, '{}')")
        connection.commit()
        connection.close()

        center = DataCenter(root=root)
        try:
            center.provision()  # must not raise
            self.assertEqual(center.db("datasets").row_counts()["records"], 1)
            actions = [event["action"] for event in center.recent_events()]
            self.assertIn("migrate", actions)
        finally:
            center.close()

    def test_migration_leaves_a_current_database_alone(self) -> None:
        self.center.add_record("d", {"a": 1})
        self.assertEqual(self.center.db("datasets").migrate(), 0)
        self.assertEqual(len(self.center.list_records("d")), 1)

    def test_unknown_database_raises(self) -> None:
        with self.assertRaises(KeyError):
            self.center.db("warehouse")


class TestContacts(DataCenterTestCase):
    def test_contact_joins_its_organization(self) -> None:
        self.center.add_contact(
            "Ada Okoro", email="ada@example.test", organization="Northgate Logistics"
        )
        contacts = self.center.list_contacts()
        self.assertEqual(len(contacts), 1)
        self.assertEqual(contacts[0]["organization"], "Northgate Logistics")

    def test_naming_an_organization_keeps_its_details(self) -> None:
        self.center.add_organization("Acme", industry="Logistics", website="https://acme.example")
        self.center.add_contact("Ada", email="ada@acme.example", organization="Acme")
        row = self.center.db("contacts").query_one(
            "SELECT industry, website FROM organizations WHERE name = 'Acme'"
        )
        self.assertEqual(row["industry"], "Logistics")
        self.assertEqual(row["website"], "https://acme.example")

    def test_organization_is_reused_not_duplicated(self) -> None:
        self.center.add_contact("Ada", organization="Northgate")
        self.center.add_contact("Ben", organization="Northgate")
        rows = self.center.db("contacts").query("SELECT id FROM organizations")
        self.assertEqual(len(rows), 1)


class TestDocumentsAndSearch(DataCenterTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.center.add_document(
            "Network hardening checklist",
            "Segment the network, rotate router credentials, review firewall rules.",
            tags=["security"],
        )
        self.center.add_document(
            "Quarterly invoice summary",
            "Invoices cover support hours and hardware procurement, net thirty terms.",
            tags=["finance"],
        )

    def test_document_is_indexed_on_write(self) -> None:
        rows = self.center.db("embeddings").query("SELECT document_id FROM embeddings")
        self.assertEqual(len(rows), 2)

    def test_tags_are_stored(self) -> None:
        rows = self.center.db("documents").query("SELECT tag FROM document_tags ORDER BY tag")
        self.assertEqual([row["tag"] for row in rows], ["finance", "security"])

    def test_search_ranks_the_relevant_document_first(self) -> None:
        results = self.center.search("firewall rules and router credentials")
        self.assertTrue(results)
        self.assertEqual(results[0]["title"], "Network hardening checklist")

    def test_search_returns_nothing_for_unrelated_terms(self) -> None:
        self.assertEqual(self.center.search("photosynthesis chlorophyll"), [])

    def test_score_floor_filters_weak_matches(self) -> None:
        self.assertEqual(self.center.search("firewall", min_score=0.99), [])
        self.assertTrue(self.center.search("firewall", min_score=0.0))

    def test_reindex_covers_every_document(self) -> None:
        self.assertEqual(self.center.reindex(), 2)


class TestDatasets(DataCenterTestCase):
    def test_reusing_a_dataset_returns_the_existing_id(self) -> None:
        first = self.center.create_dataset("support_tickets", "tickets")
        second = self.center.create_dataset("support_tickets", "tickets, again")
        self.assertEqual(first, second)

    def test_records_attach_to_an_existing_dataset(self) -> None:
        self.center.create_dataset("support_tickets")
        self.center.add_record("support_tickets", {"ticket": "SUP-1"})
        self.assertEqual(len(self.center.list_records("support_tickets")), 1)

    def test_records_round_trip_as_json(self) -> None:
        self.center.add_record("support_tickets", {"ticket": "SUP-1", "hours": 2.5})
        records = self.center.list_records("support_tickets")
        self.assertEqual(records[0]["payload"], {"ticket": "SUP-1", "hours": 2.5})


class TestModels(DataCenterTestCase):
    def test_registering_the_same_model_twice_adds_one_version(self) -> None:
        for _ in range(2):
            self.center.register_model(
                "local-hashing-v1", provider="built-in", task="embedding",
                version="1.0.0", dimensions=256,
            )
        self.assertEqual(len(self.center.list_models()), 1)


class TestJobs(DataCenterTestCase):
    def test_empty_queue_returns_none(self) -> None:
        self.assertIsNone(self.center.run_next_job())

    def test_reindex_job_runs(self) -> None:
        self.center.add_document("Notes", "some text", embed=False)
        self.center.submit_job("reindex")
        result = self.center.run_next_job()
        self.assertEqual(result["outcome"], "done")
        self.assertEqual(self.center.pending_jobs(), [])

    def test_unsupported_job_fails_without_stalling_the_queue(self) -> None:
        self.center.submit_job("teleport")
        self.center.submit_job("noop")
        first = self.center.run_next_job()
        self.assertEqual(first["outcome"], "failed")
        second = self.center.run_next_job()
        self.assertEqual(second["outcome"], "done")

    def test_priority_wins_over_submission_order(self) -> None:
        self.center.submit_job("noop", priority=100)
        urgent = self.center.submit_job("noop", priority=1)
        self.assertEqual(self.center.run_next_job()["job_id"], urgent)


class TestAuditAndOperations(DataCenterTestCase):
    def test_writes_are_logged(self) -> None:
        self.center.add_contact("Ada")
        actions = [event["action"] for event in self.center.recent_events()]
        self.assertIn("add_contact", actions)

    def test_status_reports_all_seven(self) -> None:
        report = self.center.status()
        self.assertEqual(len(report["databases"]), 7)
        self.assertTrue(all(db["provisioned"] and db["healthy"] for db in report["databases"]))

    def test_backup_copies_every_database_with_a_manifest(self) -> None:
        self.center.add_document("Notes", "backed up text")
        target = self.center.backup(self.root.parent / "backups")

        for key in DATABASE_KEYS:
            self.assertTrue((target / f"{key}.db").exists(), f"{key} not backed up")
        manifest = json.loads((target / "manifest.json").read_text())
        self.assertEqual(manifest["databases"], list(DATABASE_KEYS))

        restored = DataCenter(root=target)
        try:
            self.assertEqual(len(restored.list_documents()), 1)
        finally:
            restored.close()


class TestSeed(DataCenterTestCase):
    def test_seed_populates_the_data_center(self) -> None:
        summary = seed(self.center)
        self.assertEqual(summary["contacts"], len(self.center.list_contacts()))
        self.assertEqual(summary["documents"], len(self.center.list_documents()))
        self.assertTrue(self.center.search("incident escalation on-call analyst"))


class TestVectors(unittest.TestCase):
    def test_embedding_is_deterministic_and_normalized(self) -> None:
        vector = embed_text("security audit invoice")
        self.assertEqual(vector, embed_text("security audit invoice"))
        self.assertAlmostEqual(sum(value * value for value in vector), 1.0, places=6)

    def test_related_text_scores_above_unrelated_text(self) -> None:
        anchor = embed_text("invoice for the security audit")
        related = embed_text("security audit invoice")
        unrelated = embed_text("tuesday lunch menu")
        self.assertGreater(
            cosine_similarity(anchor, related), cosine_similarity(anchor, unrelated)
        )

    def test_empty_text_has_zero_similarity(self) -> None:
        self.assertEqual(cosine_similarity(embed_text(""), embed_text("anything")), 0.0)

    def test_length_mismatch_raises(self) -> None:
        with self.assertRaises(ValueError):
            cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])


class TestCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = str(Path(self._tmp.name) / "storage")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_cli(self, *argv: str) -> int:
        """Run the CLI with its output captured, and return the exit code."""
        with contextlib.redirect_stdout(io.StringIO()):
            return cli_main(["--root", self.root, *argv])

    def test_init_then_verify(self) -> None:
        self.assertEqual(self.run_cli("init"), 0)
        self.assertEqual(self.run_cli("verify"), 0)

    def test_piping_into_head_does_not_traceback(self) -> None:
        """`python -m datacenter status | head -2` must exit quietly."""
        self.assertEqual(self.run_cli("init"), 0)
        repo_root = Path(__file__).resolve().parent.parent
        reader = subprocess.Popen(["head", "-2"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        writer = subprocess.run(
            [sys.executable, "-m", "datacenter", "--root", self.root, "status"],
            cwd=repo_root,
            stdout=reader.stdin,
            stderr=subprocess.PIPE,
            text=True,
        )
        reader.stdin.close()
        reader.wait()
        self.assertNotIn("Traceback", writer.stderr)
        self.assertEqual(writer.returncode, 0)

    def test_seed_then_search(self) -> None:
        self.assertEqual(self.run_cli("seed"), 0)
        self.assertEqual(self.run_cli("search", "firewall"), 0)
        self.assertEqual(self.run_cli("status", "--json"), 0)

    def test_documents_contacts_events_and_backup(self) -> None:
        self.assertEqual(self.run_cli("seed"), 0)
        for command in ("documents", "contacts", "events", "jobs"):
            self.assertEqual(self.run_cli(command), 0, command)
        self.assertEqual(self.run_cli("backup", str(Path(self._tmp.name) / "backups")), 0)


if __name__ == "__main__":
    unittest.main()
