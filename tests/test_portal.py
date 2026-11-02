"""Tests for the web portal's data pages, and its server with nobody signed in.

Sign-in itself is covered over HTTP in test_portal_auth.
"""

from __future__ import annotations

import http.client
import sys
import tempfile
import threading
import unittest
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
        self.assertIn("--allow-remote", str(raised.exception))

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
    """One pass through the real server with nobody signed in."""

    def setUp(self) -> None:
        self._outbox = tempfile.TemporaryDirectory()
        self.server = build_server(self.root, host="127.0.0.1", port=0, quiet=True,
                                   mail_outbox=self._outbox.name, environ={})
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self._outbox.cleanup()

    def fetch(self, path: str, method: str = "GET", body: bytes | None = None,
              headers: dict | None = None) -> tuple[int, str, http.client.HTTPMessage]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8"), response.headers
        finally:
            connection.close()

    def test_the_overview_needs_sign_in(self) -> None:
        # Changed with sign-in: the data pages used to be public.
        status, _, headers = self.fetch("/")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/sign-in")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_health_check(self) -> None:
        status, body, _ = self.fetch("/healthz")
        self.assertEqual(status, 200)
        self.assertIn('"ok"', body)

    def test_a_missing_page_is_404_over_http(self) -> None:
        self.assertEqual(self.fetch("/nope")[0], 404)

    def test_a_post_without_a_form_token_is_refused(self) -> None:
        # Changed with sign-in: POST used to be 501. Account forms post now,
        # but only with a CSRF token from this portal's own pages.
        status, _, _ = self.fetch("/documents", "POST", b"x=1", {
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 403)

    def test_other_write_methods_are_not_implemented(self) -> None:
        for method in ("PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                self.assertEqual(self.fetch("/documents", method, b"x=1")[0], 501)


if __name__ == "__main__":
    unittest.main()
