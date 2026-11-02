"""Tests for Google sign-in: the flow's state, nonce and PKCE, and every ID-token check.

Google is never contacted: the token endpoint is a function the test supplies.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datacenter.accounts import AccountStore  # noqa: E402
from datacenter.google_signin import (  # noqa: E402
    AUTH_ENDPOINT,
    CALLBACK_PATH,
    SCOPE,
    TOKEN_ENDPOINT,
    GoogleConfig,
    GoogleConfigError,
    GoogleSignIn,
    GoogleSignInError,
    IdTokenError,
    config_from_env,
    missing_settings,
    post_form,
)

CLIENT_ID = "1234-test.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-very-secret-value"
SECRET = b"s" * 32
NOW = 1_800_000_000.0


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_token(claims: dict) -> str:
    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    return f"{header}.{b64url(json.dumps(claims).encode())}.signature"


class MemoryFlows:
    """Stands in for the accounts store's flow table."""

    def __init__(self) -> None:
        self.live: set[str] = set()
        self.counter = 0

    def begin_oauth_flow(self) -> str:
        self.counter += 1
        key = f"flow-key-{self.counter:04d}-" + "x" * 30
        self.live.add(key)
        return key

    def consume_oauth_flow(self, flow_key: str) -> bool:
        if flow_key in self.live:
            self.live.remove(flow_key)
            return True
        return False


class FakeGoogle:
    """Records the token request and answers with an ID token built from it."""

    def __init__(self, signin: "GoogleSignIn | None" = None, **overrides: object) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.overrides = overrides
        self.nonce = ""

    def __call__(self, url: str, form: dict, timeout: float) -> dict:
        self.requests.append((url, dict(form)))
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "azp": CLIENT_ID,
            "sub": "109876543210",
            "email": "person@example.com",
            "email_verified": True,
            "name": "Pat Person",
            "exp": NOW + 3600,
            "iat": NOW,
            "nonce": self.nonce,
        }
        claims.update(self.overrides)
        claims = {key: value for key, value in claims.items() if value is not None}
        return {"access_token": "ya29.access", "id_token": make_token(claims), "token_type": "Bearer"}


class GoogleTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.config = GoogleConfig(client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                                   public_url="https://portal.example.com")
        self.google = FakeGoogle()
        self.signin = GoogleSignIn(self.config, SECRET, http_post=self.google, clock=lambda: NOW)
        self.flows = MemoryFlows()

    def start(self) -> tuple[str, dict[str, str]]:
        flow_key, url = self.signin.begin(self.flows)
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        self.google.nonce = query["nonce"]
        return flow_key, query


class TestAuthorizationRequest(GoogleTestCase):
    def test_the_url_asks_google_for_an_openid_code_with_pkce(self) -> None:
        flow_key, url = self.signin.begin(self.flows)
        self.assertTrue(url.startswith(AUTH_ENDPOINT + "?"))
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        self.assertEqual(query["response_type"], "code")
        self.assertEqual(query["client_id"], CLIENT_ID)
        self.assertEqual(query["redirect_uri"], "https://portal.example.com" + CALLBACK_PATH)
        self.assertEqual(query["scope"], SCOPE)
        self.assertEqual(SCOPE, "openid email profile")
        self.assertEqual(query["code_challenge_method"], "S256")
        for name in ("state", "nonce", "code_challenge"):
            self.assertGreaterEqual(len(query[name]), 43, name)
        self.assertNotIn("code_verifier", query)
        self.assertNotIn(CLIENT_SECRET, url)
        self.assertIn(flow_key, self.flows.live)

    def test_the_challenge_is_the_s256_of_the_verifier_sent_later(self) -> None:
        flow_key, query = self.start()
        self.signin.complete(self.flows, flow_key, {"state": query["state"], "code": "4/abc"})
        verifier = self.google.requests[0][1]["code_verifier"]
        self.assertRegex(verifier, r"^[A-Za-z0-9_-]{43,128}$")
        self.assertEqual(b64url(hashlib.sha256(verifier.encode()).digest()), query["code_challenge"])
        self.assertNotIn(verifier, query.values())

    def test_every_flow_gets_its_own_values(self) -> None:
        values = [self.signin.flow_values(self.flows.begin_oauth_flow()) for _ in range(3)]
        for attribute in ("state", "nonce", "code_verifier"):
            self.assertEqual(len({getattr(v, attribute) for v in values}), 3, attribute)

    def test_values_depend_on_the_server_secret(self) -> None:
        key = self.flows.begin_oauth_flow()
        other = GoogleSignIn(self.config, b"t" * 32, http_post=self.google)
        self.assertNotEqual(self.signin.flow_values(key).state, other.flow_values(key).state)
        self.assertNotEqual(self.signin.flow_values(key).code_verifier, other.flow_values(key).code_verifier)

    def test_flow_values_do_not_show_in_a_repr(self) -> None:
        values = self.signin.flow_values(self.flows.begin_oauth_flow())
        for secret in (values.state, values.nonce, values.code_verifier):
            self.assertNotIn(secret, repr(values))

    def test_a_short_server_secret_is_refused(self) -> None:
        with self.assertRaises(GoogleConfigError):
            GoogleSignIn(self.config, b"short")


class TestCallback(GoogleTestCase):
    def test_a_good_callback_gives_the_identity(self) -> None:
        flow_key, query = self.start()
        identity = self.signin.complete(self.flows, flow_key, {"state": [query["state"]], "code": ["4/abc"]})
        self.assertEqual((identity.sub, identity.email, identity.name),
                         ("109876543210", "person@example.com", "Pat Person"))
        url, form = self.google.requests[0]
        self.assertEqual(url, TOKEN_ENDPOINT)
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["code"], "4/abc")
        self.assertEqual(form["client_id"], CLIENT_ID)
        self.assertEqual(form["client_secret"], CLIENT_SECRET)
        self.assertEqual(form["redirect_uri"], "https://portal.example.com" + CALLBACK_PATH)

    def test_a_flow_works_once(self) -> None:
        flow_key, query = self.start()
        params = {"state": query["state"], "code": "4/abc"}
        self.signin.complete(self.flows, flow_key, params)
        with self.assertRaises(GoogleSignInError):
            self.signin.complete(self.flows, flow_key, params)
        self.assertEqual(len(self.google.requests), 1)

    def test_a_wrong_state_is_refused_before_google_is_called(self) -> None:
        flow_key, query = self.start()
        for state in ("", "x" * 43, query["state"][:-1] + ("A" if query["state"][-1] != "A" else "B")):
            with self.subTest(state=state):
                key = self.flows.begin_oauth_flow()
                with self.assertRaises(GoogleSignInError):
                    self.signin.complete(self.flows, key, {"state": state, "code": "4/abc"})
        self.assertEqual(self.google.requests, [])

    def test_another_flows_state_is_refused(self) -> None:
        _, first = self.start()
        second_key, _ = self.start()
        with self.assertRaises(GoogleSignInError):
            self.signin.complete(self.flows, second_key, {"state": first["state"], "code": "4/abc"})

    def test_an_unknown_or_missing_flow_key_is_refused(self) -> None:
        _, query = self.start()
        for key in ("", "not-a-flow-key-but-long-enough-to-look-like-one", None):
            with self.subTest(key=key):
                with self.assertRaises(GoogleSignInError):
                    self.signin.complete(self.flows, key, {"state": query["state"], "code": "4/abc"})

    def test_google_reporting_an_error_is_refused(self) -> None:
        flow_key, query = self.start()
        with self.assertRaises(GoogleSignInError) as raised:
            self.signin.complete(self.flows, flow_key, {"error": "access_denied", "state": query["state"]})
        self.assertIn("access_denied", str(raised.exception))

    def test_a_repeated_parameter_is_refused(self) -> None:
        flow_key, query = self.start()
        with self.assertRaises(GoogleSignInError):
            self.signin.complete(self.flows, flow_key, {"state": [query["state"]], "code": ["a", "b"]})

    def test_a_missing_code_is_refused(self) -> None:
        flow_key, query = self.start()
        with self.assertRaises(GoogleSignInError):
            self.signin.complete(self.flows, flow_key, {"state": query["state"]})

    def test_an_answer_without_an_id_token_is_refused(self) -> None:
        signin = GoogleSignIn(self.config, SECRET, http_post=lambda url, form, timeout: {"access_token": "x"},
                              clock=lambda: NOW)
        flow_key, url = signin.begin(self.flows)
        state = parse_qs(urlsplit(url).query)["state"][0]
        with self.assertRaises(GoogleSignInError):
            signin.complete(self.flows, flow_key, {"state": state, "code": "4/abc"})

    def test_the_client_secret_never_appears_in_an_error(self) -> None:
        flow_key, query = self.start()
        self.google.overrides["aud"] = "someone-else"
        with self.assertRaises(IdTokenError) as raised:
            self.signin.complete(self.flows, flow_key, {"state": query["state"], "code": "4/abc"})
        self.assertNotIn(CLIENT_SECRET, str(raised.exception))
        self.assertNotIn(CLIENT_SECRET, repr(self.config))


class TestIdTokenChecks(GoogleTestCase):
    """Every check on the ID token, one bad claim at a time."""

    def complete_with(self, **overrides: object):
        flow_key, query = self.start()
        self.google.overrides.update(overrides)
        return self.signin.complete(self.flows, flow_key, {"state": query["state"], "code": "4/abc"})

    def assertRefused(self, **overrides: object) -> None:  # noqa: N802 - unittest style
        with self.assertRaises(IdTokenError):
            self.complete_with(**overrides)

    def test_both_google_issuer_spellings_pass(self) -> None:
        for issuer in ("https://accounts.google.com", "accounts.google.com"):
            with self.subTest(issuer=issuer):
                self.assertEqual(self.complete_with(iss=issuer).email, "person@example.com")

    def test_a_bad_issuer_is_refused(self) -> None:
        for issuer in ("https://evil.example.com", "https://accounts.google.com.evil.com",
                       "http://accounts.google.com", "", None, ["https://accounts.google.com"]):
            with self.subTest(issuer=issuer):
                self.google.overrides.clear()
                self.assertRefused(iss=issuer)

    def test_a_bad_audience_is_refused(self) -> None:
        for audience in ("other-client.apps.googleusercontent.com", ["other"], [], 42, None):
            with self.subTest(audience=audience):
                self.google.overrides.clear()
                self.assertRefused(aud=audience)

    def test_several_audiences_need_azp_to_be_this_client(self) -> None:
        self.assertRefused(aud=[CLIENT_ID, "other"], azp="other")
        self.google.overrides.clear()
        self.assertRefused(aud=[CLIENT_ID, "other"], azp=None)
        self.google.overrides.clear()
        self.assertEqual(self.complete_with(aud=[CLIENT_ID, "other"], azp=CLIENT_ID).sub, "109876543210")

    def test_a_present_azp_must_be_this_client(self) -> None:
        self.assertRefused(azp="other-client")
        self.google.overrides.clear()
        self.assertEqual(self.complete_with(azp=None).sub, "109876543210")

    def test_an_expired_token_is_refused(self) -> None:
        for expires in (NOW - 1, NOW, "never", True, None):
            with self.subTest(expires=expires):
                self.google.overrides.clear()
                self.assertRefused(exp=expires)

    def test_a_nonce_from_elsewhere_is_refused(self) -> None:
        flow_key, query = self.start()
        self.google.nonce = "a-nonce-from-another-sign-in"
        with self.assertRaises(IdTokenError):
            self.signin.complete(self.flows, flow_key, {"state": query["state"], "code": "4/abc"})

    def test_a_missing_nonce_is_refused(self) -> None:
        flow_key, query = self.start()
        self.google.nonce = None
        with self.assertRaises(IdTokenError):
            self.signin.complete(self.flows, flow_key, {"state": query["state"], "code": "4/abc"})

    def test_an_unverified_email_is_refused(self) -> None:
        for verified in (False, "false", None, 1, "yes"):
            with self.subTest(verified=verified):
                self.google.overrides.clear()
                self.assertRefused(email_verified=verified)

    def test_the_string_true_counts_as_verified(self) -> None:
        self.assertEqual(self.complete_with(email_verified="true").email, "person@example.com")

    def test_missing_subject_or_email_is_refused(self) -> None:
        for claim, value in (("sub", None), ("sub", ""), ("sub", 7), ("email", None), ("email", "nobody")):
            with self.subTest(claim=claim, value=value):
                self.google.overrides.clear()
                self.assertRefused(**{claim: value})

    def test_a_missing_name_falls_back_to_the_address(self) -> None:
        self.assertEqual(self.complete_with(name=None).name, "person")

    def test_malformed_tokens_are_refused(self) -> None:
        nonce = "n" * 43
        for token in ("", "a.b", "a.b.c.d", "a.!!!.c", "a." + b64url(b"not json") + ".c",
                      "a." + b64url(b"[1, 2]") + ".c"):
            with self.subTest(token=token):
                with self.assertRaises(IdTokenError):
                    self.signin.check_id_token(token, nonce)


class TestConfiguration(unittest.TestCase):
    def test_nothing_set_means_google_is_off(self) -> None:
        self.assertIsNone(config_from_env({}))
        self.assertEqual(len(missing_settings({})), 3)

    def test_half_set_names_what_is_missing(self) -> None:
        with self.assertRaises(GoogleConfigError) as raised:
            config_from_env({"SDDU13_GOOGLE_CLIENT_ID": CLIENT_ID})
        self.assertIn("SDDU13_GOOGLE_CLIENT_SECRET", str(raised.exception))
        self.assertIn("SDDU13_PUBLIC_URL", str(raised.exception))
        self.assertNotIn("SDDU13_GOOGLE_CLIENT_ID", str(raised.exception))

    def test_the_redirect_uri_comes_from_the_public_url(self) -> None:
        config = config_from_env({"SDDU13_GOOGLE_CLIENT_ID": CLIENT_ID,
                                  "SDDU13_GOOGLE_CLIENT_SECRET": CLIENT_SECRET,
                                  "SDDU13_PUBLIC_URL": "https://portal.example.ts.net/"})
        self.assertEqual(config.redirect_uri, "https://portal.example.ts.net/auth/google/callback")
        self.assertNotIn(CLIENT_SECRET, repr(config))

    def test_the_public_url_must_be_https_except_on_this_machine(self) -> None:
        env = {"SDDU13_GOOGLE_CLIENT_ID": CLIENT_ID, "SDDU13_GOOGLE_CLIENT_SECRET": CLIENT_SECRET}
        for url in ("http://portal.example.com", "portal.example.com", "ftp://x.example.com",
                    "https://portal.example.com/?next=x", "https://user:pw@portal.example.com"):
            with self.subTest(url=url):
                with self.assertRaises(GoogleConfigError):
                    config_from_env({**env, "SDDU13_PUBLIC_URL": url})
        for url in ("http://localhost:8013", "http://127.0.0.1:8013"):
            with self.subTest(url=url):
                self.assertEqual(config_from_env({**env, "SDDU13_PUBLIC_URL": url}).public_url, url)

    def test_the_default_http_call_refuses_plain_http(self) -> None:
        with self.assertRaises(GoogleSignInError):
            post_form("http://oauth2.googleapis.com/token", {"client_secret": CLIENT_SECRET}, 1.0)


class TestWithTheAccountsStore(unittest.TestCase):
    """The real flow table: single use, ten minutes, only a hash stored."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
        self.store = AccountStore(Path(self._tmp.name) / "storage", environ={},
                                  clock=lambda: self.now, mirror_audit=False)
        self.store.provision()
        config = GoogleConfig(client_id=CLIENT_ID, client_secret=CLIENT_SECRET,
                              public_url="https://portal.example.com")
        self.google = FakeGoogle()
        self.signin = GoogleSignIn(config, self.store.secret, http_post=self.google,
                                   clock=lambda: self.now.timestamp())

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def begin(self) -> tuple[str, str]:
        flow_key, url = self.signin.begin(self.store)
        query = parse_qs(urlsplit(url).query)
        self.google.nonce = query["nonce"][0]
        self.google.overrides["exp"] = self.now.timestamp() + 3600
        return flow_key, query["state"][0]

    def test_a_stored_flow_completes_once(self) -> None:
        flow_key, state = self.begin()
        identity = self.signin.complete(self.store, flow_key, {"state": state, "code": "4/abc"})
        self.assertEqual(identity.email, "person@example.com")
        with self.assertRaises(GoogleSignInError):
            self.signin.complete(self.store, flow_key, {"state": state, "code": "4/abc"})

    def test_a_flow_lasts_ten_minutes(self) -> None:
        flow_key, state = self.begin()
        self.now += timedelta(minutes=10)
        with self.assertRaises(GoogleSignInError):
            self.signin.complete(self.store, flow_key, {"state": state, "code": "4/abc"})
        flow_key, state = self.begin()
        self.now += timedelta(minutes=9, seconds=59)
        self.assertEqual(self.signin.complete(self.store, flow_key, {"state": state, "code": "x"}).sub,
                         "109876543210")

    def test_only_a_hash_of_the_flow_key_is_stored(self) -> None:
        flow_key, state = self.begin()
        values = self.signin.flow_values(flow_key)
        dump = "\n".join(self.store.connection.iterdump())
        for secret in (flow_key, state, values.nonce, values.code_verifier):
            self.assertNotIn(secret, dump)
        self.assertIn(hashlib.sha256(flow_key.encode()).hexdigest(), dump)

    def test_flow_keys_from_the_store_are_accepted_by_the_flow(self) -> None:
        key = self.store.begin_oauth_flow()
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_-]{43}", key))
        self.signin.flow_values(key)


if __name__ == "__main__":
    unittest.main()
