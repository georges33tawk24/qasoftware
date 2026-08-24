"""Credentials never reach the artifact — CLAUDE.md, SPEC §5."""

from __future__ import annotations

import pytest

from engine.capture.auth import totp
from engine.capture.secrets import REDACTED, Redactor, SecretError, resolve


def test_bare_literals_are_refused() -> None:
    """If a literal were accepted it would end up pasted into project JSON."""
    with pytest.raises(SecretError, match="not a secret reference"):
        resolve("hunter2")


def test_env_references_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BUREAU_TEST_PW", "s3cret-value")
    assert resolve("env:BUREAU_TEST_PW") == "s3cret-value"


def test_unset_env_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BUREAU_TEST_PW", raising=False)
    with pytest.raises(SecretError, match="unset or empty"):
        resolve("env:BUREAU_TEST_PW")


def test_known_values_are_scrubbed_from_text() -> None:
    redactor = Redactor()
    redactor.add("s3cret-value")
    assert redactor.text("login failed for s3cret-value") == f"login failed for {REDACTED}"


def test_short_values_are_not_scrubbed() -> None:
    """Scrubbing a three-character secret would black out half the page."""
    redactor = Redactor()
    redactor.add("abc")
    assert redactor.text("abcdef") == "abcdef"


def test_sensitive_headers_go_regardless_of_what_we_know() -> None:
    redactor = Redactor()
    scrubbed = redactor.headers({"Authorization": "Bearer x", "Accept": "*/*"})
    assert scrubbed == {"authorization": REDACTED, "accept": "*/*"}


def test_cookies_keep_their_flags_and_lose_their_value() -> None:
    """The value is a credential; Secure/HttpOnly/SameSite are what SPEC §8.4 A checks.
    Redacting the whole header would make that check permanently blind."""
    redactor = Redactor()
    scrubbed = redactor.headers({"Set-Cookie": "sid=abc123; Path=/; Secure; HttpOnly"})
    assert scrubbed["set-cookie"] == f"sid={REDACTED}; Path=/; Secure; HttpOnly"
    assert "abc123" not in scrubbed["set-cookie"]


def test_token_query_params_are_scrubbed() -> None:
    redactor = Redactor()
    out = redactor.url("https://x.test/cb?code=abc&access_token=xyz&page=2")
    assert "access_token=%C2%AB" in out or f"access_token={REDACTED}" in out
    assert "page=2" in out


def test_totp_matches_the_rfc_6238_vector() -> None:
    """RFC 6238 appendix B, SHA-1, T=59 → 94287082."""
    seed = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert totp(seed, at=59, digits=8) == "94287082"
    assert totp(seed, at=59) == "287082"
