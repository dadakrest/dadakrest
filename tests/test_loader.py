"""Tests for the file-based loader and its validator."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import datacenter.core  # noqa: E402
from datacenter import DATA_DIR, DataCenter, DataFileError, load_all, validate  # noqa: E402
from datacenter.loader import part_files, read_file, read_sources  # noqa: E402


def write(directory: Path, name: str, payload: object) -> None:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class LoaderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.center = DataCenter(root=self.tmp / "storage")
        self.center.provision()

    def tearDown(self) -> None:
        self.center.close()
        self._tmp.cleanup()


class TestBundledData(LoaderTestCase):
    def test_bundled_data_files_are_valid(self) -> None:
        self.assertEqual(validate(DATA_DIR), [])

    def test_loading_twice_does_not_duplicate(self) -> None:
        first = load_all(self.center, DATA_DIR)
        before = {key: db.row_counts() for key, db in self.center.databases.items() if key != "audit"}
        second = load_all(self.center, DATA_DIR)
        after = {key: db.row_counts() for key, db in self.center.databases.items() if key != "audit"}
        self.assertEqual(before, after)
        self.assertEqual(first["documents"], second["documents"])
        self.assertEqual(second["jobs"], 0, "jobs must not be queued a second time")


class TestPartFiles(LoaderTestCase):
    def test_single_file_then_parts_in_name_order(self) -> None:
        write(self.data, "documents.json", [{"external_id": "a"}])
        write(self.data, "documents/20-second.json", [{"external_id": "c"}])
        write(self.data, "documents/10-first.json", [{"external_id": "b"}])
        self.assertEqual(
            [path.name for path in part_files(self.data, "documents.json")],
            ["documents.json", "10-first.json", "20-second.json"],
        )
        ids = [item["external_id"] for item in read_file(self.data, "documents.json")]
        self.assertEqual(ids, ["a", "b", "c"])

    def test_missing_files_are_empty(self) -> None:
        self.assertEqual(read_file(self.data, "documents.json"), [])
        self.assertEqual(validate(self.data), [])

    def test_malformed_json_names_the_file(self) -> None:
        (self.data / "models.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(DataFileError) as raised:
            read_file(self.data, "models.json")
        self.assertIn("models.json", str(raised.exception))


class TestValidation(LoaderTestCase):
    def test_real_looking_email_is_rejected(self) -> None:
        write(self.data, "contacts.json", [{"full_name": "Ada", "email": "ada@gmail.com"}])
        problems = validate(self.data)
        self.assertEqual(len(problems), 1)
        self.assertIn("reserved domain", problems[0])

    def test_reserved_domains_are_accepted(self) -> None:
        for email in ("a@x.example", "b@lab.test", "c@example.com", "d@sub.example.org"):
            write(self.data, "contacts.json", [{"full_name": "Ada", "email": email}])
            self.assertEqual(validate(self.data), [], email)

    def test_unknown_organization_is_reported(self) -> None:
        write(self.data, "contacts.json", [{"full_name": "Ada", "email": "a@x.example", "organization": "Ghost"}])
        self.assertTrue(any("Ghost" in problem for problem in validate(self.data)))

    def test_duplicate_external_ids_are_reported_across_parts(self) -> None:
        write(self.data, "documents/a.json", [{"external_id": "kb-1", "title": "t", "body": "b"}])
        write(self.data, "documents/b.json", [{"external_id": "kb-1", "title": "t", "body": "b"}])
        self.assertTrue(any("duplicate external_id" in problem for problem in validate(self.data)))

    def test_record_fields_must_match_schema(self) -> None:
        write(self.data, "datasets.json", [{
            "name": "d", "schema": {"a": "number"}, "records": [{"a": 1, "zzz": 2}],
        }])
        self.assertTrue(any("zzz" in problem for problem in validate(self.data)))

    def test_invalid_data_loads_nothing(self) -> None:
        write(self.data, "organizations.json", [{"name": "Good Org"}])
        write(self.data, "contacts.json", [{"full_name": "Ada", "email": "not-an-email"}])
        with self.assertRaises(DataFileError):
            load_all(self.center, self.data)
        self.assertEqual(self.center.db("contacts").row_counts()["organizations"], 0)


class TestLoading(LoaderTestCase):
    def test_everything_lands_in_the_right_database(self) -> None:
        write(self.data, "organizations.json", [{"name": "Lab", "industry": "Research"}])
        write(self.data, "contacts.json", [{
            "full_name": "Ada", "email": "ada@lab.test", "organization": "Lab",
            "channels": [{"channel": "github", "handle": "ada", "is_primary": True}],
        }])
        write(self.data, "documents.json", [{
            "external_id": "kb-1", "title": "Embeddings", "body": "vectors for search", "tags": ["ai"],
        }])
        write(self.data, "datasets.json", [{"name": "runs", "schema": {"id": "string"}, "records": [{"id": "r1"}]}])
        write(self.data, "models.json", [{
            "name": "m", "provider": "p", "task": "embedding",
            "versions": [{"version": "1", "dimensions": 8, "is_active": True}],
        }])
        write(self.data, "jobs.json", [{"kind": "noop", "priority": 5}])

        counts = load_all(self.center, self.data)
        self.assertEqual(counts, {
            "organizations": 1, "contacts": 1, "documents": 1, "records": 1, "models": 1, "jobs": 1,
        })
        self.assertEqual(self.center.db("contacts").row_counts()["contact_channels"], 1)
        self.assertEqual(self.center.document_tags(1), ["ai"])
        self.assertEqual(self.center.list_datasets()[0]["schema"], {"id": "string"})
        self.assertEqual(len(self.center.pending_jobs()), 1)
        self.assertTrue(self.center.search("vectors for search"))

    def test_refill_updates_a_changed_document_in_place(self) -> None:
        write(self.data, "documents.json", [{"external_id": "kb-1", "title": "Old", "body": "old text", "tags": ["x"]}])
        load_all(self.center, self.data)
        write(self.data, "documents.json", [{"external_id": "kb-1", "title": "New", "body": "new text", "tags": ["y"]}])
        load_all(self.center, self.data)
        documents = self.center.list_documents()
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]["title"], "New")
        self.assertEqual(self.center.document_tags(documents[0]["id"]), ["y"])


if __name__ == "__main__":
    unittest.main()


class TestShapeValidation(LoaderTestCase):
    """A value of the wrong type is reported, never raised and never coerced."""

    def problems_for(self, name: str, payload: object) -> list[str]:
        write(self.data, name, payload)
        return validate(self.data)

    def test_wrong_types_are_reported_not_raised(self) -> None:
        cases = [
            ("organizations.json", [{"name": ["a"]}], "'name' must be a string"),
            ("contacts.json", [{"full_name": "A", "email": "a@x.example", "channels": 5}],
             "'channels' must be a list"),
            ("documents.json", [{"external_id": "1", "title": "T", "body": 7}],
             "'body' must be a string"),
            ("datasets.json", [{"name": "d", "schema": "nope"}],
             "'schema' must be an object"),
            ("jobs.json", [{"kind": "noop", "priority": "soon"}],
             "'priority' must be a whole number"),
        ]
        for name, payload, expected in cases:
            with self.subTest(name=name):
                problems = self.problems_for(name, payload)
                self.assertTrue(any(expected in p for p in problems), problems)
            (self.data / name).unlink()

    def test_a_numeric_key_cannot_overwrite_a_string_one(self) -> None:
        problems = self.problems_for("documents.json", [
            {"external_id": 1, "title": "first", "body": "a"},
            {"external_id": "1", "title": "second", "body": "b"},
        ])
        self.assertTrue(any("'external_id' must be a string" in p for p in problems), problems)

    def test_string_booleans_are_rejected(self) -> None:
        problems = self.problems_for("models.json", [{
            "name": "m", "provider": "p", "task": "embedding",
            "versions": [{"version": "1", "is_active": "false"}],
        }])
        self.assertTrue(any("'is_active' must be true or false" in p for p in problems), problems)
        (self.data / "models.json").unlink()

        problems = self.problems_for("contacts.json", [{
            "full_name": "A", "email": "a@x.example",
            "channels": [{"channel": "gh", "handle": "h", "is_primary": "false"}],
        }])
        self.assertTrue(any("'is_primary' must be true or false" in p for p in problems), problems)

    def test_email_with_a_trailing_newline_is_rejected(self) -> None:
        problems = self.problems_for("contacts.json", [{"full_name": "A", "email": "a@x.example\n"}])
        self.assertTrue(any("is not an email address" in p for p in problems), problems)


class TestProblemLabels(LoaderTestCase):
    def test_labels_name_the_file_and_the_index_inside_it(self) -> None:
        write(self.data, "documents.json", [{"external_id": "a", "title": "t", "body": "b"}])
        write(self.data, "documents/10-kb.json", [
            {"external_id": "c", "title": "t", "body": "b"},
            {"external_id": "d", "title": "t", "body": ""},
        ])
        write(self.data, "documents/20-kb.json", [{"external_id": "a", "title": "t", "body": "b"}])

        problems = validate(self.data)
        self.assertIn("documents/10-kb.json[1]: missing or empty 'body'", problems)
        self.assertTrue(
            any(p.startswith("documents/20-kb.json[0]: duplicate external_id 'a'") for p in problems),
            problems,
        )

    def test_read_sources_labels_every_entry(self) -> None:
        write(self.data, "documents.json", [{"external_id": "a"}])
        write(self.data, "documents/10-kb.json", [{"external_id": "b"}])
        self.assertEqual(
            [label for label, _ in read_sources(self.data, "documents.json")],
            ["documents.json[0]", "documents/10-kb.json[0]"],
        )


class TestDataDirectory(LoaderTestCase):
    def test_a_missing_directory_is_a_problem_not_a_clean_pass(self) -> None:
        problems = validate(self.tmp / "nowhere")
        self.assertEqual(len(problems), 1)
        self.assertIn("no such data directory", problems[0])

    def test_a_file_where_a_directory_belongs_is_reported(self) -> None:
        path = self.tmp / "notadir.json"
        path.write_text("[]")
        self.assertTrue(any("is not a directory" in p for p in validate(path)))

    def test_a_non_utf8_file_names_itself(self) -> None:
        (self.data / "documents.json").write_bytes(b'[{"external_id": "\xff"}]')
        with self.assertRaises(DataFileError) as raised:
            validate(self.data)
        self.assertIn("documents.json", str(raised.exception))
        self.assertIn("not valid UTF-8", str(raised.exception))


class TestRecordRefill(LoaderTestCase):
    def dataset(self, records: list[dict]) -> None:
        write(self.data, "datasets.json", [{
            "name": "t", "schema": {"ticket": "string", "hours": "number"}, "records": records,
        }])

    def test_editing_a_record_replaces_it(self) -> None:
        self.dataset([{"ticket": "SUP-1", "hours": 2.5}])
        load_all(self.center, self.data)
        self.dataset([{"ticket": "SUP-1", "hours": 3.0}])
        load_all(self.center, self.data)
        records = self.center.list_records("t")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["payload"], {"ticket": "SUP-1", "hours": 3.0})

    def test_the_same_number_written_differently_does_not_duplicate(self) -> None:
        self.dataset([{"ticket": "SUP-1", "hours": 3.0}])
        load_all(self.center, self.data)
        self.dataset([{"ticket": "SUP-1", "hours": 3}])
        load_all(self.center, self.data)
        self.assertEqual(len(self.center.list_records("t")), 1)

    def test_a_record_dropped_from_the_file_is_dropped_from_the_database(self) -> None:
        self.dataset([{"ticket": "SUP-1", "hours": 1}, {"ticket": "SUP-2", "hours": 2}])
        load_all(self.center, self.data)
        self.dataset([{"ticket": "SUP-2", "hours": 2}])
        load_all(self.center, self.data)
        self.assertEqual(
            [r["payload"]["ticket"] for r in self.center.list_records("t")], ["SUP-2"]
        )


class TestAtomicLoad(LoaderTestCase):
    def test_a_failure_part_way_through_keeps_nothing(self) -> None:
        write(self.data, "organizations.json", [{"name": "Acme", "industry": "Logistics"}])
        load_all(self.center, self.data)
        before = {key: db.row_counts() for key, db in self.center.databases.items() if key != "audit"}

        write(self.data, "organizations.json", [
            {"name": "Acme", "industry": "CHANGED"}, {"name": "Second Org"},
        ])
        write(self.data, "jobs.json", [{"kind": "reindex"}])

        def boom(*_args, **_kwargs):
            raise RuntimeError("failure part way through the load")

        original = datacenter.core.DataCenter.submit_job
        datacenter.core.DataCenter.submit_job = boom
        try:
            with self.assertRaises(RuntimeError):
                load_all(self.center, self.data)
        finally:
            datacenter.core.DataCenter.submit_job = original

        after = {key: db.row_counts() for key, db in self.center.databases.items() if key != "audit"}
        self.assertEqual(before, after, "a failed load left rows behind")
        row = self.center.db("contacts").query_one(
            "SELECT industry FROM organizations WHERE name = 'Acme'"
        )
        self.assertEqual(row["industry"], "Logistics", "a failed load changed an existing row")
