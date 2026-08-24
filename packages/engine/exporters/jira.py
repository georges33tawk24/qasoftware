"""Jira — SPEC §14. REST v3, ADF description, attachments.

Jira is the one that has to work properly: the others are variations on "POST some JSON".
The two things that make it awkward are the Atlassian Document Format description and
attachments needing a second, differently-shaped request per file.
"""

from __future__ import annotations

import re
from typing import Any

from engine.exporters.base import Bundle, ExportResult, Target, exporter
from engine.exporters.common import (
    ExportError,
    body,
    labels_for,
    request,
    summary,
    token,
    upload,
)

ISSUE_TYPE = "Bug"
LABEL_SAFE = re.compile(r"[^A-Za-z0-9_.:-]+")
"""Jira rejects labels with spaces, silently on some deployments."""


def adf(text: str) -> dict[str, Any]:
    """Markdown-ish text as an Atlassian Document Format document.

    Deliberately small: paragraphs, bullet lists, and bold runs. Jira renders anything
    else as literal text, which is worse than losing the emphasis.
    """
    content: list[dict[str, Any]] = []
    bullets: list[dict[str, Any]] = []

    def flush() -> None:
        if bullets:
            content.append({"type": "bulletList", "content": list(bullets)})
            bullets.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if stripped.startswith(("- ", "* ")):
            bullets.append(
                {
                    "type": "listItem",
                    "content": [{"type": "paragraph", "content": _inline(stripped[2:])}],
                }
            )
            continue
        flush()
        content.append({"type": "paragraph", "content": _inline(stripped)})
    flush()
    return {"type": "doc", "version": 1, "content": content or [_empty()]}


def _empty() -> dict[str, Any]:
    return {"type": "paragraph", "content": [{"type": "text", "text": " "}]}


def _inline(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for index, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)):
        if not part:
            continue
        node: dict[str, Any] = {"type": "text", "text": part.replace("`", "")}
        if index % 2:
            node["marks"] = [{"type": "strong"}]
        out.append(node)
    return out


@exporter
class JiraExporter:
    kind = "jira"

    def map(self, bundle: Bundle, target: Target) -> dict[str, Any]:
        issue = bundle.issue
        fields: dict[str, Any] = {
            "project": {"key": target.project},
            "summary": summary(issue)[:255],
            "description": adf(body(bundle)),
            "issuetype": {"name": target.extra.get("issueType", ISSUE_TYPE)},
            "labels": [LABEL_SAFE.sub("-", label) for label in labels_for(issue, target)],
        }
        if target.extra.get("usePriority", True):
            fields["priority"] = {"name": target.priority(issue.severity)}
        fields.update(target.extra.get("fields") or {})
        return {"fields": fields}

    def push(
        self, payloads: list[tuple[Bundle, dict[str, Any]]], target: Target
    ) -> list[ExportResult]:
        if target.dry_run:
            return [
                ExportResult(bundle.issue.fingerprint, action="skipped") for bundle, _ in payloads
            ]
        headers = _auth(target)
        base = target.base_url.rstrip("/")
        results = []
        for bundle, payload in payloads:
            remote = payload.pop("remoteKey", "")
            try:
                results.append(self._one(base, headers, bundle, payload, remote))
            except ExportError as exc:
                results.append(
                    ExportResult(bundle.issue.fingerprint, action="failed", error=str(exc))
                )
        return results

    def _one(
        self,
        base: str,
        headers: dict[str, str],
        bundle: Bundle,
        payload: dict[str, Any],
        remote: str,
    ) -> ExportResult:
        if remote:
            # A second export updates rather than duplicates (SPEC §14). Priority and
            # labels are left alone on update: a human may have triaged them since.
            fields = {k: v for k, v in payload["fields"].items() if k in ("summary", "description")}
            request(
                "PUT", f"{base}/rest/api/3/issue/{remote}", headers=headers, body={"fields": fields}
            )
            key = remote
            action = "updated"
        else:
            created = request("POST", f"{base}/rest/api/3/issue", headers=headers, body=payload)
            key = str((created.body or {}).get("key") or "")
            action = "created"
            if not key:
                raise ExportError("Jira accepted the issue but returned no key")

        attached = 0
        for path in bundle.attachments():
            upload(
                f"{base}/rest/api/3/issue/{key}/attachments",
                path,
                headers={**headers, "X-Atlassian-Token": "no-check"},
            )
            attached += 1
        return ExportResult(
            fingerprint=bundle.issue.fingerprint,
            remote_key=key,
            url=f"{base}/browse/{key}",
            action=action,
            attachments=attached,
        )


def _auth(target: Target) -> dict[str, str]:
    """Jira Cloud is basic auth with an API token as the password."""
    import base64

    if not target.user:
        raise ExportError("Jira needs the account email in `user`")
    pair = f"{target.user}:{token(target)}".encode()
    return {"authorization": "Basic " + base64.b64encode(pair).decode()}
