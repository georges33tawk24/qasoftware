"""OpenProject — SPEC §14. API v3, basic auth as `apikey:<token>`, attachments per work
package."""

from __future__ import annotations

import base64
from typing import Any

from engine.exporters.base import Bundle, ExportResult, Target, exporter
from engine.exporters.common import ExportError, body, labels_for, request, summary, token, upload

TYPE_BUG = 1


@exporter
class OpenProjectExporter:
    kind = "openproject"

    def map(self, bundle: Bundle, target: Target) -> dict[str, Any]:
        issue = bundle.issue
        return {
            "subject": summary(issue)[:255],
            "description": {"format": "markdown", "raw": body(bundle)},
            "_links": {
                "type": {"href": f"/api/v3/types/{target.extra.get('typeId', TYPE_BUG)}"},
                "project": {"href": f"/api/v3/projects/{target.project}"},
            },
            "_labels": labels_for(issue, target),
        }

    def push(
        self, payloads: list[tuple[Bundle, dict[str, Any]]], target: Target
    ) -> list[ExportResult]:
        if target.dry_run:
            return [
                ExportResult(bundle.issue.fingerprint, action="skipped") for bundle, _ in payloads
            ]
        base = target.base_url.rstrip("/")
        pair = base64.b64encode(f"apikey:{token(target)}".encode()).decode()
        headers = {"authorization": f"Basic {pair}"}
        results = []
        for bundle, payload in payloads:
            remote = payload.pop("remoteKey", "")
            payload.pop("_labels", None)
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
            current = request("GET", f"{base}/api/v3/work_packages/{remote}", headers=headers)
            version = (current.body or {}).get("lockVersion", 0)
            response = request(
                "PATCH",
                f"{base}/api/v3/work_packages/{remote}",
                headers=headers,
                body={
                    "lockVersion": version,
                    "subject": payload["subject"],
                    "description": payload["description"],
                },
            )
            action = "updated"
        else:
            response = request(
                "POST", f"{base}/api/v3/work_packages", headers=headers, body=payload
            )
            action = "created"
        key = str((response.body or {}).get("id") or remote)
        if not key:
            raise ExportError("OpenProject returned no work package id")

        attached = 0
        for path in bundle.attachments():
            upload(
                f"{base}/api/v3/work_packages/{key}/attachments",
                path,
                headers=headers,
                field="file",
            )
            attached += 1
        return ExportResult(
            fingerprint=bundle.issue.fingerprint,
            remote_key=key,
            url=f"{base}/work_packages/{key}",
            action=action,
            attachments=attached,
        )
