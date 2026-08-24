"""GitHub Issues — SPEC §14. Markdown bodies, labels, no attachments API.

GitHub has no attachment endpoint outside the web uploader, so evidence is linked from
the report rather than uploaded. Saying that here is better than quietly dropping it.
"""

from __future__ import annotations

from typing import Any

from engine.exporters.base import Bundle, ExportResult, Target, exporter
from engine.exporters.common import ExportError, body, labels_for, request, summary, token

API = "https://api.github.com"


@exporter
class GitHubExporter:
    kind = "github"

    def map(self, bundle: Bundle, target: Target) -> dict[str, Any]:
        issue = bundle.issue
        return {
            "title": summary(issue),
            "body": body(bundle),
            "labels": [*labels_for(issue, target), f"severity:{issue.severity.value}"],
        }

    def push(
        self, payloads: list[tuple[Bundle, dict[str, Any]]], target: Target
    ) -> list[ExportResult]:
        if target.dry_run:
            return [
                ExportResult(bundle.issue.fingerprint, action="skipped") for bundle, _ in payloads
            ]
        base = (target.base_url or API).rstrip("/")
        headers = {
            "authorization": f"Bearer {token(target)}",
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
        }
        results = []
        for bundle, payload in payloads:
            remote = payload.pop("remoteKey", "")
            try:
                if remote:
                    response = request(
                        "PATCH",
                        f"{base}/repos/{target.project}/issues/{remote}",
                        headers=headers,
                        body={"title": payload["title"], "body": payload["body"]},
                    )
                    action = "updated"
                else:
                    response = request(
                        "POST",
                        f"{base}/repos/{target.project}/issues",
                        headers=headers,
                        body=payload,
                    )
                    action = "created"
                number = str((response.body or {}).get("number") or remote)
                results.append(
                    ExportResult(
                        fingerprint=bundle.issue.fingerprint,
                        remote_key=number,
                        url=str((response.body or {}).get("html_url") or ""),
                        action=action,
                    )
                )
            except ExportError as exc:
                results.append(
                    ExportResult(bundle.issue.fingerprint, action="failed", error=str(exc))
                )
        return results
