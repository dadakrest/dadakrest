"""Tests for the read-only web portal."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datacenter import DATA_DIR, DataCenter, load_all  # noqa: E402
from datacenter.portal import (  # noqa: E402
    NAV,
    _markup,
    build_server,
    esc,
    money,
    render,
)


class PortalTestCase(unittest.TestCase):
    """A seeded data center, shared across the tests in a class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name) / "storage"
        center = DataCenter(root=cls.root)
        center.provision()
        load_all(center, DATA_DIR)
        center.close()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def get(self, path: str, query: dict | None = None) -> tuple[int, str]:
        center = DataCenter(root=self.root)
        try:
            status, body = render(center, path, query or {})
        finally:
            center.close()
        return status, body.decode("utf-8")


class TestRoutes(PortalTestCase):
    def test_every_navigation_link_resolves(self) -> None:
        for path, label in NAV:
            with self.subTest(path=path):
                status, body = self.get(path)
                self.assertEqual(status, HTTPStatus.OK)
                self.assertIn(label, body)

    def test_a_document_page_shows_its_body(self) -> None:
        center = DataCenter(root=self.root)
        document_id = center.list_documents()[0]["id"]
        title = center.get_document(document_id)["title"]
        center.close()
        status, body = self.get(f"/documents/{document_id}")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIn(esc(title), body)

    def test_a_dataset_page_shows_its_rows(self) -> None:
        status, body = self.get("/datasets/government_fees")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIn("GOV-001", body)

    def test_a_plan_page_totals_its_lines(self) -> None:
        status, body = self.get("/plans/year-one-operating")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIn("TOTAL", body)
        self.assertIn("EMP-001", body)

    def test_the_fees_page_shows_all_three_tables(self) -> None:
        status, body = self.get("/fees")
        self.assertEqual(status, HTTPStatus.OK)
        for table in ("government_fees", "service_fees", "employment_fees"):
            self.assertIn(table, body)

    def test_search_finds_a_document(self) -> None:
        status, body = self.get("/search", {"q": ["parental leave"]})
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIn("Parental and family leave", body)

    def test_search_without_a_term_just_shows_the_form(self) -> None:
        status, body = self.get("/search")
        self.assertEqual(status, HTTPStatus.OK)
        self.assertIn('name="q"', body)

    def test_a_trailing_slash_is_the_same_page(self) -> None:
        self.assertEqual(self.get("/documents")[0], self.get("/documents/")[0])

    def test_unknown_paths_are_404(self) -> None:
        for path in ("/nope", "/documents/999999", "/datasets/no-such", "/plans/no-such",
                     "/documents/not-a-number", "/documents/1/extra"):
            with self.subTest(path=path):
                self.assertEqual(self.get(path)[0], HTTPStatus.NOT_FOUND)


class TestEscaping(PortalTestCase):
    """Everything on a page comes out of a database, so everything is escaped."""

    def test_a_document_body_cannot_inject_markup(self) -> None:
        center = DataCenter(root=self.root)
        document_id = center.add_document(
            "Probe",
            "<script>alert(1)</script> and <img src=x onerror=alert(2)>",
            external_id="probe-escaping",
            tags=['"><script>bad()</script>'],
        )
        center.close()
        try:
            _, body = self.get(f"/documents/{document_id}")
            self.assertNotIn("<script>alert(1)</script>", body)
            self.assertNotIn("<img src=x", body)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", body)
            self.assertIn("&lt;script&gt;bad()&lt;/script&gt;", body)
        finally:
            center = DataCenter(root=self.root)
            center.db("documents").execute("DELETE FROM documents WHERE id = ?", (document_id,))
            center.db("embeddings").execute("DELETE FROM embeddings WHERE document_id = ?", (document_id,))
            center.close()

    def test_a_search_term_is_reflected_safely(self) -> None:
        _, body = self.get("/search", {"q": ['"><script>alert(1)</script>']})
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_markup_escapes_before_it_adds_tags(self) -> None:
        rendered = _markup("<b>not bold</b> but **this is**")
        self.assertIn("&lt;b&gt;", rendered)
        self.assertIn("<strong>this is</strong>", rendered)

    def test_markup_renders_lists(self) -> None:
        self.assertIn("<li>one</li>", _markup("- one\n- two"))
        self.assertIn("<li>first</li>", _markup("1. first\n2. second"))

    def test_markup_keeps_paragraphs_apart(self) -> None:
        self.assertEqual(_markup("one\n\ntwo").count("<p>"), 2)

    def test_money_formats_and_survives_nonsense(self) -> None:
        self.assertEqual(money(1234.5), "$1,234.50")
        self.assertEqual(money("not a number"), "not a number")


class TestBinding(unittest.TestCase):
    def test_a_reachable_host_is_refused_without_consent(self) -> None:
        with self.assertRaises(ValueError) as raised:
            build_server("storage", host="0.0.0.0", port=0)
        self.assertIn("no authentication", str(raised.exception))

    def test_localhost_binds_without_argument(self) -> None:
        server = build_server("storage", host="127.0.0.1", port=0, quiet=True)
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()

    def test_a_reachable_host_binds_when_explicitly_allowed(self) -> None:
        server = build_server("storage", host="0.0.0.0", port=0, allow_remote=True, quiet=True)
        try:
            self.assertEqual(server.server_address[0], "0.0.0.0")
        finally:
            server.server_close()


class TestOverHttp(PortalTestCase):
    """One pass through the real server, so the handler itself is covered."""

    def setUp(self) -> None:
        self.server = build_server(self.root, host="127.0.0.1", port=0, quiet=True)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def fetch(self, path: str) -> tuple[int, str, dict]:
        try:
            with urllib.request.urlopen(f"{self.base}{path}", timeout=10) as response:
                return response.status, response.read().decode("utf-8"), dict(response.headers)
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode("utf-8"), dict(error.headers)

    def test_the_overview_is_served(self) -> None:
        status, body, headers = self.fetch("/")
        self.assertEqual(status, 200)
        self.assertIn("Overview", body)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_health_check(self) -> None:
        status, body, _ = self.fetch("/healthz")
        self.assertEqual(status, 200)
        self.assertIn('"ok"', body)

    def test_a_missing_page_is_404_over_http(self) -> None:
        self.assertEqual(self.fetch("/nope")[0], 404)

    def test_the_portal_does_not_accept_writes(self) -> None:
        request = urllib.request.Request(f"{self.base}/documents", data=b"x=1", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
        self.assertEqual(status, 501, "the portal must not implement any write method")


if __name__ == "__main__":
    unittest.main()
