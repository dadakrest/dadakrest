"""Sign in with Google: OpenID Connect, authorization-code flow with PKCE.

Configured by three environment variables:

    SDDU13_GOOGLE_CLIENT_ID      the OAuth client's ID (Google Cloud console)
    SDDU13_GOOGLE_CLIENT_SECRET  the OAuth client's secret
    SDDU13_PUBLIC_URL            where people reach the portal, e.g.
                                 https://portal.example.ts.net

The redirect URI registered with Google must be PUBLIC_URL + CALLBACK_PATH.
When neither the client ID nor the secret is set, `config_from_env` returns
None and the portal hides the Google button, whether or not PUBLIC_URL is
set (it is also the portal's own address, needed for HTTPS without Google).
When only some of the three are set, it raises and names the rest.

How a sign-in goes:

1. `begin(flows)` asks the accounts store for a new flow (a random key the
   portal puts in a short-lived pre-auth cookie; the store keeps only its
   hash, for 10 minutes, usable once) and returns that key with the Google
   URL to send the browser to.
2. Google sends the browser back to the callback with `code` and `state`.
3. `complete(flows, flow_key, params)` consumes the flow (a second callback
   with the same key fails), checks `state`, swaps the code for tokens at
   Google's token endpoint with the PKCE verifier, and checks the ID token.

The state, nonce and PKCE verifier are derived from the flow key with an
HMAC under the portal's server secret, so they are reproducible on the
server and nowhere else, and the store never holds any of them in plain
text. The browser sees the state, the nonce and the S256 challenge, which
are made to be public; it never sees the verifier.

The ID token comes straight from Google's token endpoint over TLS with the
certificate verified, so per OpenID Connect Core 3.1.3.7 TLS stands in for
the signature check. The claims are still checked: `iss` is Google, `aud`
(and `azp`, when present) is this client, `exp` is in the future, `nonce`
matches this flow, and `email_verified` is true.

The HTTP call is injectable (`http_post`) so tests can stand in for Google
without a network.

Public API:

    config_from_env(environ=None) -> GoogleConfig | None
        The configuration, None when Google is not set up at all.
    missing_settings(environ=None) -> list[str]
        Which of the three settings are not set.
    GoogleSignIn(config, secret, *, http_post=None, clock=None, timeout=10.0)
        .begin(flows) -> (flow_key, authorization_url)
        .authorization_url(flow_key) -> str
        .complete(flows, flow_key, params) -> GoogleIdentity
        .check_id_token(id_token, nonce) -> GoogleIdentity
    post_form(url, form, timeout) -> dict
        The default HTTP call: an HTTPS form POST that returns parsed JSON.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlencode, urlsplit

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
SCOPE = "openid email profile"
CALLBACK_PATH = "/auth/google/callback"

#: The hosts a connection check should be able to reach for Google sign-in.
HOSTS = ("accounts.google.com", "oauth2.googleapis.com")

SETTINGS = ("SDDU13_GOOGLE_CLIENT_ID", "SDDU13_GOOGLE_CLIENT_SECRET", "SDDU13_PUBLIC_URL")

_LOOPBACK = {"localhost", "127.0.0.1", "::1"}
_FLOW_KEY = re.compile(r"^[A-Za-z0-9_-]{32,128}$")

HttpPost = Callable[[str, dict[str, str], float], dict[str, Any]]


class GoogleSignInError(Exception):
    """Google sign-in did not produce a usable identity. The message is safe
    to show: it never carries a token, a code or the client secret."""


class GoogleConfigError(GoogleSignInError):
    """The Google settings are incomplete or unusable."""


class IdTokenError(GoogleSignInError):
    """The ID token failed one of the checks."""


class FlowStore(Protocol):
    """Where flows live; `accounts.AccountStore` is one."""

    def begin_oauth_flow(self) -> str: ...

    def consume_oauth_flow(self, flow_key: str) -> bool: ...


# -- configuration ------------------------------------------------------------------

@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str = field(repr=False)
    public_url: str

    @property
    def redirect_uri(self) -> str:
        return self.public_url + CALLBACK_PATH


def check_public_url(url: str) -> str:
    """Normalise SDDU13_PUBLIC_URL, refusing anything Google would not accept
    or that would carry a sign-in over plain HTTP to another machine."""
    url = url.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise GoogleConfigError(f"SDDU13_PUBLIC_URL must be a full URL such as "
                                f"https://portal.example.com, not {url!r}")
    if parts.query or parts.fragment or parts.username or parts.password:
        raise GoogleConfigError("SDDU13_PUBLIC_URL must not have a query, fragment or login in it")
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK:
        raise GoogleConfigError("SDDU13_PUBLIC_URL must start with https:// "
                                "(plain http is only allowed for localhost)")
    return url


def missing_settings(environ: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    return [name for name in SETTINGS if not env.get(name, "").strip()]


def config_from_env(environ: Mapping[str, str] | None = None) -> GoogleConfig | None:
    env = os.environ if environ is None else environ
    missing = missing_settings(env)
    if {"SDDU13_GOOGLE_CLIENT_ID", "SDDU13_GOOGLE_CLIENT_SECRET"}.issubset(missing):
        return None
    if missing:
        raise GoogleConfigError("Google sign-in is half set up. Missing: " + ", ".join(missing))
    return GoogleConfig(
        client_id=env["SDDU13_GOOGLE_CLIENT_ID"].strip(),
        client_secret=env["SDDU13_GOOGLE_CLIENT_SECRET"].strip(),
        public_url=check_public_url(env["SDDU13_PUBLIC_URL"]),
    )


# -- HTTP -------------------------------------------------------------------------------

def post_form(url: str, form: dict[str, str], timeout: float) -> dict[str, Any]:
    """POST a form over HTTPS with the certificate verified; return the JSON."""
    if not url.startswith("https://"):
        raise GoogleSignInError("refusing to send sign-in data over plain HTTP")
    request = urllib.request.Request(
        url,
        data=urlencode(form).encode("ascii"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout,
                                    context=ssl.create_default_context()) as response:
            raw = response.read(1_000_000)
    except urllib.error.HTTPError as error:
        raise GoogleSignInError(
            f"Google refused the sign-in (HTTP {error.code}{_error_name(error.read(10_000))})"
        ) from None
    except (urllib.error.URLError, OSError):
        raise GoogleSignInError("could not reach Google") from None
    try:
        answer = json.loads(raw)
    except ValueError:
        raise GoogleSignInError("Google sent an answer that is not JSON") from None
    if not isinstance(answer, dict):
        raise GoogleSignInError("Google sent an unexpected answer")
    return answer


def _error_name(raw: bytes) -> str:
    """The OAuth `error` code from an error body, if it is a plain word."""
    try:
        name = json.loads(raw).get("error", "")
    except (ValueError, AttributeError):
        return ""
    return f": {name}" if isinstance(name, str) and re.fullmatch(r"[a-z_]{1,40}", name) else ""


# -- the flow ---------------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]*", text):
        raise ValueError("not base64url")
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    name: str


@dataclass(frozen=True)
class FlowValues:
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)
    code_challenge: str = field(repr=False)


class GoogleSignIn:
    """One configured Google client."""

    def __init__(self, config: GoogleConfig, secret: bytes, *,
                 http_post: HttpPost | None = None,
                 clock: Callable[[], float] | None = None,
                 timeout: float = 10.0) -> None:
        if len(secret) < 32:
            raise GoogleConfigError("the server secret is too short")
        self.config = config
        self._secret = secret
        self._http_post = http_post or post_form
        self._clock = clock or time.time
        self.timeout = timeout

    def flow_values(self, flow_key: str) -> FlowValues:
        """State, nonce and PKCE pair for one flow, derived from its key."""
        if not isinstance(flow_key, str) or not _FLOW_KEY.match(flow_key):
            raise GoogleSignInError("this sign-in has no valid flow; start again")

        def derive(label: str) -> str:
            message = f"google-{label}:{flow_key}".encode("ascii")
            return _b64url(hmac.new(self._secret, message, hashlib.sha256).digest())

        verifier = derive("pkce")  # 43 characters, within PKCE's 43..128
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        return FlowValues(state=derive("state"), nonce=derive("nonce"),
                          code_verifier=verifier, code_challenge=challenge)

    def begin(self, flows: FlowStore) -> tuple[str, str]:
        """Start a sign-in: the flow key for the pre-auth cookie, and the URL."""
        flow_key = flows.begin_oauth_flow()
        return flow_key, self.authorization_url(flow_key)

    def authorization_url(self, flow_key: str) -> str:
        values = self.flow_values(flow_key)
        query = urlencode({
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": self.config.redirect_uri,
            "scope": SCOPE,
            "state": values.state,
            "nonce": values.nonce,
            "code_challenge": values.code_challenge,
            "code_challenge_method": "S256",
            "prompt": "select_account",
        })
        return f"{AUTH_ENDPOINT}?{query}"

    def complete(self, flows: FlowStore, flow_key: str,
                 params: Mapping[str, str | Sequence[str]]) -> GoogleIdentity:
        """Finish a sign-in from the callback's query parameters.

        The flow is consumed first, whatever happens next, so a callback URL
        cannot be replayed.
        """
        if not isinstance(flow_key, str) or not _FLOW_KEY.match(flow_key) \
                or not flows.consume_oauth_flow(flow_key):
            raise GoogleSignInError("this sign-in expired or was already used; start again")
        values = self.flow_values(flow_key)

        error = _one(params, "error", required=False)
        if error:
            reason = error if re.fullmatch(r"[a-z_]{1,40}", error) else "an error"
            raise GoogleSignInError(f"Google did not sign you in ({reason})")
        state = _one(params, "state")
        if not hmac.compare_digest(state.encode("utf-8"), values.state.encode("ascii")):
            raise GoogleSignInError("the sign-in did not come back to the browser that started it")
        code = _one(params, "code")

        answer = self._http_post(TOKEN_ENDPOINT, {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            "code_verifier": values.code_verifier,
        }, self.timeout)
        id_token = answer.get("id_token") if isinstance(answer, dict) else None
        if not isinstance(id_token, str) or not id_token:
            raise GoogleSignInError("Google did not send an ID token")
        return self.check_id_token(id_token, values.nonce)

    def check_id_token(self, id_token: str, nonce: str) -> GoogleIdentity:
        """Check the claims of an ID token received from the token endpoint."""
        claims = _claims(id_token)

        issuer = claims.get("iss")
        if not isinstance(issuer, str) or issuer not in ISSUERS:
            raise IdTokenError("the ID token was not issued by Google")

        client_id = self.config.client_id
        audience = claims.get("aud")
        if isinstance(audience, str):
            audiences = [audience]
        elif isinstance(audience, list) and all(isinstance(item, str) for item in audience):
            audiences = audience
        else:
            raise IdTokenError("the ID token has no audience")
        if client_id not in audiences:
            raise IdTokenError("the ID token is for another application")
        authorized = claims.get("azp")
        if (len(audiences) > 1 or authorized is not None) and authorized != client_id:
            raise IdTokenError("the ID token was issued to another application")

        expires = claims.get("exp")
        if isinstance(expires, bool) or not isinstance(expires, (int, float)):
            raise IdTokenError("the ID token has no expiry")
        if expires <= self._clock():
            raise IdTokenError("the ID token has expired")

        token_nonce = claims.get("nonce")
        if not isinstance(token_nonce, str) or not hmac.compare_digest(
                token_nonce.encode("utf-8"), nonce.encode("utf-8")):
            raise IdTokenError("the ID token does not belong to this sign-in")

        verified = claims.get("email_verified")
        # `is True`, not `in (True, ...)`: 1 == True in Python.
        if not (verified is True or verified == "true"):
            raise IdTokenError("Google has not verified this account's email address")

        sub, email = claims.get("sub"), claims.get("email")
        if not isinstance(sub, str) or not sub.strip():
            raise IdTokenError("the ID token has no subject")
        if not isinstance(email, str) or "@" not in email:
            raise IdTokenError("the ID token has no email address")
        name = claims.get("name")
        if not isinstance(name, str) or not name.strip():
            name = email.partition("@")[0]
        return GoogleIdentity(sub=sub.strip(), email=email.strip(), name=name.strip()[:100])


def _one(params: Mapping[str, str | Sequence[str]], name: str, *, required: bool = True) -> str:
    """One query parameter. Repeating it is refused rather than guessed at."""
    value = params.get(name)
    if isinstance(value, (list, tuple)):
        if len(value) > 1:
            raise GoogleSignInError(f"the sign-in answer repeats {name}")
        value = value[0] if value else None
    if not value:
        if required:
            raise GoogleSignInError(f"the sign-in answer has no {name}")
        return ""
    return str(value)


def _claims(id_token: str) -> dict[str, Any]:
    parts = id_token.split(".")
    if len(parts) != 3:
        raise IdTokenError("the ID token is not a JWT")
    try:
        claims = json.loads(_b64url_decode(parts[1]))
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise IdTokenError("the ID token cannot be read") from None
    if not isinstance(claims, dict):
        raise IdTokenError("the ID token cannot be read")
    return claims
