"""Credential resolution and redaction.

Credentials come from the environment or an OS keychain. Never from project JSON, and
never written to the artifact, a trace, a HAR or a log (CLAUDE.md).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "«redacted»"

_REF = re.compile(r"^(env|keychain):(.+)$")

COOKIE_HEADERS = frozenset({"cookie", "set-cookie"})

SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth-token",
        "x-csrf-token",
        "x-xsrf-token",
    }
)

SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "token",
        "access_token",
        "id_token",
        "api_key",
        "apikey",
        "key",
        "password",
        "secret",
        "signature",
        "sig",
        "auth",
    }
)


class SecretError(RuntimeError):
    pass


def resolve(ref: str) -> str:
    """Resolve `env:NAME` or `keychain:service/account` to a value.

    A bare literal is refused on purpose: if it were allowed, credentials would end up
    pasted into project JSON, which is the thing this rule exists to prevent.
    """
    match = _REF.match(ref)
    if not match:
        raise SecretError(
            f"{ref!r} is not a secret reference; use 'env:NAME' or 'keychain:service/account'"
        )
    scheme, rest = match.groups()
    if scheme == "env":
        value = os.environ.get(rest)
        if not value:
            raise SecretError(f"environment variable {rest} is unset or empty")
        return value

    service, _, account = rest.partition("/")
    if not account:
        raise SecretError(f"keychain reference {ref!r} needs the form 'service/account'")
    try:
        import keyring  # optional backend, not a hard dependency
    except ImportError as exc:  # pragma: no cover - depends on the host
        raise SecretError(
            "keychain references need the optional 'keyring' package installed"
        ) from exc
    stored: str | None = keyring.get_password(service, account)
    if not stored:
        raise SecretError(f"keychain has no password for {service}/{account}")
    return stored


def redact_cookie(header: str) -> str:
    """Blank the cookie value and keep everything else.

    A session cookie's value is a credential and must never reach the artifact. Its
    flags are not — and Secure/HttpOnly/SameSite are exactly what SPEC §8.4 A asks us to
    check, so redacting the whole header would make that check permanently blind.
    """
    parts = []
    for index, part in enumerate(header.split(";")):
        name, sep, _value = part.strip().partition("=")
        if index == 0 or (sep and name.lower() in ("expires", "max-age")):
            parts.append(f"{name}={REDACTED}" if index == 0 else part.strip())
        else:
            parts.append(part.strip())
    return "; ".join(parts)


class Redactor:
    """Scrubs known secret values, sensitive headers and token-ish query params."""

    def __init__(self) -> None:
        self._values: set[str] = set()

    def add(self, *values: str | None) -> None:
        for value in values:
            # Short strings would scrub half the page; secrets worth hiding are longer.
            if value and len(value) >= 4:
                self._values.add(value)

    def text(self, value: str | None) -> str | None:
        if value is None:
            return None
        for secret in self._values:
            value = value.replace(secret, REDACTED)
        return value

    def headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for name, value in headers.items():
            lowered = name.lower()
            if lowered in COOKIE_HEADERS:
                out[lowered] = redact_cookie(value)
            elif lowered in SENSITIVE_HEADERS:
                out[lowered] = REDACTED
            else:
                out[lowered] = self.text(value) or ""
        return out

    def url(self, url: str) -> str:
        parts = urlsplit(url)
        if not parts.query:
            return self.text(url) or url
        pairs = [
            (k, REDACTED if k.lower() in SENSITIVE_QUERY_PARAMS else v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
        ]
        rebuilt = urlunsplit(parts._replace(query=urlencode(pairs)))
        return self.text(rebuilt) or rebuilt
