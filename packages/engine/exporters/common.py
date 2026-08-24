"""What every adapter needs: a readable body, a credential, and an HTTP call.

Kept here so an adapter is a field mapping and nothing else. Uses `urllib` rather than a
client library: this makes a handful of requests per export and the stdlib does it.
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from engine.exporters.base import Bundle, Target
from engine.issues.models import Issue

TIMEOUT_SECONDS = 30
MAX_INSTANCES_IN_BODY = 10


class ExportError(RuntimeError):
    """A tracker said no. Carries enough to put in an `ExportResult.error`."""


@dataclass
class Response:
    status: int
    body: Any


def token(target: Target) -> str:
    """From the environment, never from project config (CLAUDE.md)."""
    if not target.token_env:
        raise ExportError("no token_env configured for this target")
    value = os.environ.get(target.token_env, "")
    if not value:
        raise ExportError(f"{target.token_env} is not set in this environment")
    return value


def request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: Any = None,
) -> Response:
    data = None if body is None else json.dumps(body).encode()
    head = {"accept": "application/json", **(headers or {})}
    if data is not None:
        head.setdefault("content-type", "application/json")
    req = urllib.request.Request(url, data=data, headers=head, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
            return Response(response.status, json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:400]
        raise ExportError(f"{method} {url} → {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise ExportError(f"{method} {url} → {exc.reason}") from exc


def upload(url: str, path: Path, *, headers: dict[str, str], field: str = "file") -> Response:
    """multipart/form-data by hand. One field, one file, no dependency."""
    boundary = uuid.uuid4().hex
    kind = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'.encode(),
            f"Content-Type: {kind}\r\n\r\n".encode(),
            path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={**headers, "content-type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
            return Response(response.status, json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raise ExportError(f"upload {path.name} → {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ExportError(f"upload {path.name} → {exc.reason}") from exc


def labels_for(issue: Issue, target: Target) -> list[str]:
    """Category and checker, plus whatever the project always wants. SPEC §14."""
    derived = [issue.category.value, issue.checkerId.replace(".", "-")]
    return sorted({*derived, *target.labels})


def summary(issue: Issue) -> str:
    where = issue.pagePaths
    tail = f" ({where[0]})" if len(where) == 1 else f" ({len(where)} pages)" if where else ""
    return f"{issue.title}{tail}"


def body(bundle: Bundle, *, markdown: bool = True) -> str:
    """The issue as a task someone can act on: what, where, and how to see it.

    Plain text with markdown accents — the two trackers that want something else convert
    from this rather than building their own.
    """
    issue = bundle.issue
    lines: list[str] = [issue.description or issue.title, ""]
    if issue.expected is not None or issue.actual is not None:
        lines += [f"- **Expected:** {issue.expected}", f"- **Actual:** {issue.actual}"]
    lines += [
        f"- **Severity:** {issue.severity.value}",
        f"- **Checker:** `{issue.checkerId}`",
        f"- **Instances:** {issue.instanceCount}",
    ]
    if bundle.report_url:
        lines.append(f"- **Report:** {bundle.report_url}")
    lines.append("")

    shown = issue.instances[:MAX_INSTANCES_IN_BODY]
    if shown:
        lines.append("**Where**")
        for instance in shown:
            selector = f" `{instance.selector}`" if instance.selector else ""
            measured = f" — {instance.actual}" if instance.actual else ""
            lines.append(f"- {instance.pagePath} @ {instance.viewport}{selector}{measured}")
        if len(issue.instances) > len(shown):
            lines.append(f"- …and {len(issue.instances) - len(shown)} more")
        lines.append("")

    steps = issue.data.get("steps")
    if isinstance(steps, list) and steps:
        lines.append("**Steps to reproduce**")
        lines += [f"{n}. {step}" for n, step in enumerate(steps, start=1)]
        lines.append("")

    lines.append(f"_Fingerprint `{issue.fingerprint}` — re-exporting updates this issue._")
    text = "\n".join(lines).strip()
    return text if markdown else text.replace("**", "").replace("`", "")
