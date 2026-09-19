"""Tests for the file-based loader and its validator."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datacenter import DATA_DIR, DataCenter, DataFileError, load_all, validate  # noqa: E402
from datacenter.loader import part_files, read_file  # noqa: E402


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
