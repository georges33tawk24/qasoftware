"""Figma REST client — SPEC §6.

The API is slow and rate-limited, so responses are cached by file version and never
re-fetched for a version already on disk. Large files are handled by fetching a subtree
rather than the whole document.

Transport is injectable: the tests run against a frozen file, not the live API.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

API = "https://api.figma.com/v1"
TIMEOUT = 60
"""A big file legitimately takes half a minute."""

Fetcher = Callable[[str, dict[str, str]], bytes]


class FigmaError(RuntimeError):
    pass


def http_fetch(url: str, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body: bytes = response.read()
            return body
    except urllib.error.HTTPError as exc:
        raise FigmaError(f"{exc.code} from {url.split('?')[0]}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise FigmaError(f"could not reach the Figma API: {exc.reason}") from exc


class FigmaClient:
    def __init__(
        self,
        token: str,
        *,
        cache_dir: Path | None = None,
        fetch: Fetcher = http_fetch,
    ) -> None:
        self.token = token
        self.cache_dir = cache_dir
        self._fetch = fetch

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-Figma-Token": self.token, "Accept": "application/json"}

    def _get(self, path: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        payload = json.loads(self._fetch(url, self._headers))
        if not isinstance(payload, dict):
            raise FigmaError(f"unexpected response shape from {path}")
        if payload.get("err") or payload.get("error"):
            raise FigmaError(str(payload.get("err") or payload.get("message") or payload))
        return payload

    # ------------------------------------------------------------------- files

    def file_version(self, key: str) -> str:
        """One cheap call, so a cached file can be trusted without downloading it again."""
        meta = self._get(f"/files/{key}", {"depth": "1"})
        return str(meta.get("version", ""))

    def file(self, key: str, *, depth: int | None = None) -> dict[str, Any]:
        params = {"geometry": "paths"} if depth is None else {"depth": str(depth)}
        params.pop("geometry", None)
        return self._get(f"/files/{key}", params)

    def nodes(self, key: str, ids: list[str]) -> dict[str, Any]:
        """Fetch a subtree. This is how a file too large to pull whole gets handled."""
        return self._get(f"/files/{key}/nodes", {"ids": ",".join(ids)})

    def images(self, key: str, ids: list[str], *, scale: int = 2) -> dict[str, str]:
        payload = self._get(
            f"/images/{key}", {"ids": ",".join(ids), "scale": str(scale), "format": "png"}
        )
        images = payload.get("images") or {}
        return {str(k): str(v) for k, v in images.items() if v}

    def download(self, url: str) -> bytes:
        return self._fetch(url, {})

    # ------------------------------------------------------------------- cache

    def cached_file(self, key: str) -> dict[str, Any]:
        """The whole point of the cache: a file whose version has not moved is the same
        file, and pulling it again costs a minute for nothing."""
        if self.cache_dir is None:
            return self.file(key)

        version = self.file_version(key)
        path = self.cache_dir / key / f"{version or 'unversioned'}.json"
        if version and path.is_file():
            loaded: dict[str, Any] = json.loads(path.read_text())
            return loaded

        payload = self.file(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
        return payload
