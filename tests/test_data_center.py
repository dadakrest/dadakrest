"""Tests for Software da Database User 13. Run: python -m unittest discover tests"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import datacenter.vectors  # noqa: E402
from datacenter import DATABASE_KEYS, DataCenter  # noqa: E402
from datacenter.cli import main as cli_main  # noqa: E402
from datacenter.seed import seed  # noqa: E402
from datacenter.vectors import embed, loads, similarity, tokenize  # noqa: E402


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

    def test_editing_a_channel_replaces_it(self) -> None:
        for handle in ("ada", "ada-okoro"):
            self.center.add_contact(
                "Ada", email="ada@x.example",
                channels=[{"channel": "github", "handle": handle, "is_primary": True}],
            )
        rows = self.center.db("contacts").query("SELECT handle FROM contact_channels")
        self.assertEqual([row["handle"] for row in rows], ["ada-okoro"])

    def test_saving_a_contact_without_channels_keeps_them(self) -> None:
        """What the CLI's `add-contact` does: no channels argument at all."""
        self.center.add_contact(
            "Ada", email="ada@x.example",
            channels=[{"channel": "github", "handle": "ada"}],
        )
        self.center.add_contact("Ada", email="ada@x.example", phone="555")
        self.assertEqual(self.center.db("contacts").row_counts()["contact_channels"], 1)

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

    def test_a_document_in_another_script_is_indexed_and_findable(self) -> None:
        """Text outside ASCII must not embed to an empty, unreachable vector."""
        document_id = self.center.add_document(
            "東京 データセンター 運用",
            "東京 の データセンター は 冷却 と 電力 を 監視 します",
        )
        row = self.center.db("embeddings").query_one(
            "SELECT vector FROM embeddings WHERE document_id = ?", (document_id,)
        )
        self.assertTrue(any(loads(row["vector"])), "the vector is all zeros")

        results = self.center.search("東京 データセンター")
        self.assertEqual(results[0]["document_id"], document_id)

    def test_a_shared_apostrophe_is_not_a_match(self) -> None:
        """Two texts must not match on the fragments their possessives leave."""
        self.center.add_document(
            "Asset register",
            "Ben's laptop, Ana's monitor, Kim's dock, Lee's headset, Sam's keyboard.",
        )
        plan_id = self.center.add_document(
            "Quarterly plan",
            "The plan sets targets for each quarter and names an owner for every target.",
        )
        results = self.center.search("what's the plan")
        self.assertEqual([hit["document_id"] for hit in results], [plan_id])

    def test_a_question_matches_on_its_topic_not_its_function_words(self) -> None:
        """"about" must not pull in a document that merely says it a lot."""
        self.center.add_document(
            "Weekly team sync",
            "Tell me about your week, about anything that blocked you, "
            "and about what you plan next.",
        )
        self.center.add_document(
            "Penetration testing scope",
            "The scope covers the external network and the model inference endpoints.",
        )
        results = self.center.search("what about penetration testing")
        self.assertEqual([hit["title"] for hit in results], ["Penetration testing scope"])

    def test_search_returns_nothing_for_unrelated_terms(self) -> None:
        self.assertEqual(self.center.search("photosynthesis chlorophyll"), [])

    def test_score_floor_filters_weak_matches(self) -> None:
        self.assertEqual(self.center.search("firewall", min_score=0.99), [])
        self.assertTrue(self.center.search("firewall", min_score=0.0))

    def test_a_document_with_nothing_in_common_is_never_a_hit(self) -> None:
        """Not even with the floor turned off: zero similarity is not a match."""
        self.assertEqual(self.center.search("photosynthesis chlorophyll", min_score=0.0), [])

    def test_scores_are_the_similarity_of_the_stored_vectors(self) -> None:
        hit = self.center.search("firewall rules")[0]
        row = self.center.db("embeddings").query_one(
            "SELECT vector FROM embeddings WHERE document_id = ?", (hit["document_id"],)
        )
        expected = similarity(embed("firewall rules"), loads(row["vector"]))
        self.assertAlmostEqual(hit["score"], expected, places=4)

    def test_a_one_word_query_finds_a_long_document(self) -> None:
        """The defect the score floor used to cause.

        A one-word query scores 1/sqrt(N) against a document of N distinct
        words, so an absolute floor of 0.15 made anything over ~44 words
        unreachable by a single term.
        """
        body = " ".join(f"clause{index}" for index in range(200))
        self.center.add_document("Long policy", f"{body} firewall {body}")
        titles = [hit["title"] for hit in self.center.search("firewall", limit=10)]
        self.assertIn("Long policy", titles)

    def test_limit_must_be_at_least_one(self) -> None:
        for limit in (0, -1, None):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.center.search("firewall", limit=limit)

    def test_reindex_covers_every_document(self) -> None:
        self.assertEqual(self.center.reindex(), 2)


class TestEmbeddingScheme(DataCenterTestCase):
    """What the index does when the embedding scheme behind it changes.

    `dimensions` records the bucket space a vector was built in, so vectors
    from an older scheme are excluded from a search rather than compared
    against numbers that mean something else.
    """

    def setUp(self) -> None:
        super().setUp()
        self.center.add_document(
            "Network hardening checklist",
            "Segment the network, rotate router credentials, review firewall rules.",
        )

    @contextlib.contextmanager
    def scheme(self, space: int):
        """Run the block as though the embedder hashed into another space."""
        original_embed = datacenter.vectors.embed
        original_space = datacenter.core.BUCKET_SPACE
        datacenter.vectors.embed = lambda text, _space=space: original_embed(text, _space)
        datacenter.core.BUCKET_SPACE = space
        try:
            yield
        finally:
            datacenter.vectors.embed = original_embed
            datacenter.core.BUCKET_SPACE = original_space

    def stored_spaces(self) -> list[int]:
        rows = self.center.db("embeddings").query("SELECT dimensions FROM embeddings")
        return [row["dimensions"] for row in rows]

    def test_the_stored_space_is_the_one_the_vectors_were_built_in(self) -> None:
        with self.scheme(1536):
            self.center.reindex()
            self.assertTrue(self.center.search("firewall rules"))
        self.assertEqual(self.stored_spaces(), [1536])

    def test_a_stale_index_asks_for_a_reindex_instead_of_answering_nothing(self) -> None:
        with self.scheme(1536), self.assertRaises(RuntimeError) as raised:
            self.center.search("firewall rules")
        self.assertIn("reindex", str(raised.exception))

    def test_status_counts_vectors_from_another_scheme(self) -> None:
        self.assertEqual(self.center.status()["stale_embeddings"], 0)
        with self.scheme(1536):
            self.center.reindex()
        self.assertEqual(self.center.status()["stale_embeddings"], 1)

    def test_an_unreadable_vector_is_skipped_not_fatal(self) -> None:
        """One corrupt blob must not take the rest of the index down with it."""
        self.center.add_document("Meeting notes", "unrelated filler text")
        self.center.db("embeddings").execute(
            "UPDATE embeddings SET vector = ? WHERE document_id = 2", ("{not json",),
        )
        results = self.center.search("firewall rules")
        self.assertEqual([hit["document_id"] for hit in results], [1])
        self.assertTrue(
            any(event["action"] == "search_skip" for event in self.center.recent_events())
        )


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

    def test_status_survives_a_table_created_outside_the_schema(self) -> None:
        """A scratch table whose name needs quoting must not sink the report."""
        self.center.db("audit").execute('CREATE TABLE "scratch notes" (x)')
        report = self.center.status()
        audit = next(db for db in report["databases"] if db["key"] == "audit")
        self.assertEqual(audit["rows"]["scratch notes"], 0)

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


class TestDamagedAndMissingDatabases(DataCenterTestCase):
    """What the health report says about files that are not working databases."""

    def detach(self, key: str):
        """Close a database and drop its WAL sidecars, so its file can be edited."""
        database = self.center.db(key)
        database.close()
        for suffix in ("-wal", "-shm"):
            database.path.with_name(database.path.name + suffix).unlink(missing_ok=True)
        return database

    def report(self) -> dict[str, dict]:
        return {database["key"]: database for database in self.center.status()["databases"]}

    def test_a_file_that_is_not_a_database_is_reported_not_raised(self) -> None:
        self.detach("models").path.write_bytes(b"this is not a sqlite database at all" * 100)
        report = self.report()
        self.assertTrue(report["models"]["exists"])
        self.assertFalse(report["models"]["provisioned"])
        self.assertFalse(report["models"]["healthy"])
        self.assertTrue(report["contacts"]["healthy"], "the other six must still be reported")
        results = self.center.verify()
        self.assertFalse(results["models"])
        self.assertTrue(results["contacts"])

    def test_an_emptied_database_is_not_reported_healthy(self) -> None:
        self.detach("models").path.write_bytes(b"")
        self.assertFalse(self.report()["models"]["healthy"])
        self.assertFalse(self.center.verify()["models"])

    def test_verify_on_an_unprovisioned_root_creates_nothing(self) -> None:
        root = Path(self._tmp.name) / "nowhere"
        with DataCenter(root=root) as center:
            self.assertEqual(set(center.verify().values()), {False})
        self.assertFalse(root.exists(), "verify provisioned the root it was checking")

    def test_reading_an_unprovisioned_database_refuses_to_create_it(self) -> None:
        root = Path(self._tmp.name) / "elsewhere"
        with DataCenter(root=root) as center:
            with self.assertRaises(FileNotFoundError):
                center.recent_events()
        self.assertFalse(root.exists())

    def test_backup_leaves_out_a_database_that_is_not_there(self) -> None:
        models = self.detach("models")
        models.path.unlink()
        target = self.center.backup(self.root.parent / "backups")
        manifest = json.loads((target / "manifest.json").read_text())
        self.assertNotIn("models", manifest["databases"])
        self.assertFalse((target / "models.db").exists())
        self.assertFalse(models.path.exists(), "the backup recreated the source file")

    def test_size_counts_rows_that_are_still_in_the_wal(self) -> None:
        self.center.add_document("Notes", "text that has to live somewhere")
        documents = self.center.db("documents")
        self.assertGreater(documents.size_bytes(), documents.path.stat().st_size)


class TestSeed(DataCenterTestCase):
    def test_seed_populates_the_data_center(self) -> None:
        summary = seed(self.center)
        self.assertEqual(summary["contacts"], len(self.center.list_contacts()))
        self.assertEqual(summary["documents"], len(self.center.list_documents()))
        self.assertTrue(self.center.search("incident escalation on-call analyst"))


class TestVectors(unittest.TestCase):
    def test_embedding_is_deterministic_and_normalized(self) -> None:
        vector = embed("security audit invoice")
        self.assertEqual(vector, embed("security audit invoice"))
        self.assertAlmostEqual(sum(w * w for w in vector.values()), 1.0, places=6)

    def test_only_the_buckets_a_text_uses_are_stored(self) -> None:
        """Sparse, not dense: the bucket space is far too large to materialize."""
        self.assertEqual(len(embed("security audit invoice")), 3)

    def test_related_text_scores_above_unrelated_text(self) -> None:
        anchor = embed("invoice for the security audit")
        related = embed("security audit invoice")
        unrelated = embed("tuesday lunch menu")
        self.assertGreater(similarity(anchor, related), similarity(anchor, unrelated))

    def test_unrelated_text_shares_no_bucket(self) -> None:
        """The wide bucket space makes a collision a ~2**-63 event, so
        unrelated text scores exactly zero rather than a small positive."""
        self.assertEqual(similarity(embed("photosynthesis chlorophyll"),
                                    embed("invoice payment terms")), 0.0)

    def test_accented_words_stay_whole(self) -> None:
        """'Zürich' is one token, not the fragments 'z' and 'rich'."""
        self.assertEqual(tokenize("Zürich café naïve"), ["zürich", "café", "naïve"])

    def test_contraction_fragments_are_dropped(self) -> None:
        """The word survives the apostrophe; the leftover stub does not."""
        self.assertEqual(
            tokenize("The client's invoice isn't paid"), ["client", "invoice", "paid"]
        )

    def test_equivalent_spellings_normalize_to_one_token(self) -> None:
        composed = "Zürich"  # ü as a single code point
        decomposed = "Zürich"  # u followed by a combining diaeresis
        self.assertEqual(tokenize(composed), tokenize(decomposed))

    def test_empty_text_has_zero_similarity(self) -> None:
        self.assertEqual(similarity(embed(""), embed("anything")), 0.0)

    def test_similarity_is_symmetric(self) -> None:
        left, right = embed("invoice for the security audit"), embed("security audit")
        self.assertEqual(similarity(left, right), similarity(right, left))


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

    def run_module(self, *argv: str, stdout, env: dict[str, str] | None = None):
        """Run the CLI as a subprocess, the way a shell pipeline would."""
        return subprocess.run(
            [sys.executable, "-m", "datacenter", "--root", self.root, *argv],
            cwd=Path(__file__).resolve().parent.parent,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def test_piping_into_head_does_not_traceback(self) -> None:
        """`python -m datacenter documents | head -1` must exit quietly.

        The listing has to be longer than the 64 KiB pipe buffer, or the
        writer never blocks, `head` exits before the buffer is full and no
        broken pipe is ever produced - which would leave the handler in
        `cli.main` untested.
        """
        with DataCenter(root=self.root) as center:
            center.provision()
            for index in range(2000):
                center.add_document(
                    f"Document {index} with a long enough title to fill the pipe",
                    "x",
                    embed=False,
                )
        reader = subprocess.Popen(["head", "-1"], stdin=subprocess.PIPE, stdout=subprocess.DEVNULL)
        writer = self.run_module("documents", stdout=reader.stdin)
        reader.stdin.close()
        reader.wait()
        self.assertNotIn("Traceback", writer.stderr)
        self.assertEqual(writer.returncode, 0)

    def test_writing_to_an_already_closed_pipe_exits_quietly(self) -> None:
        """The reader is gone before the first write, e.g. `| grep -q` that matched.

        Nothing reaches the pipe until stdout is flushed, so this is the case
        the interpreter's flush at exit would report as 'Exception ignored'.
        PYTHONUNBUFFERED is dropped because it would flush each print instead.
        """
        self.assertEqual(self.run_cli("init"), 0)
        reader = subprocess.Popen(["true"], stdin=subprocess.PIPE)
        reader.wait()
        buffered = {key: value for key, value in os.environ.items() if key != "PYTHONUNBUFFERED"}
        writer = self.run_module("status", stdout=reader.stdin, env=buffered)
        reader.stdin.close()
        self.assertNotIn("BrokenPipeError", writer.stderr)
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
