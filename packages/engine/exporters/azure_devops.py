"""Azure DevOps — SPEC §14. Work items are patched with a JSON Patch document, on create
as well as on update, which is the only thing unusual about this one."""

from __future__ import annotations

import base64
from typing import Any

from engine.exporters.base import Bundle, ExportResult, Target, exporter
from engine.exporters.common import ExportError, body, labels_for, request, summary, token

API_VERSION = "7.1"
SEVERITY = {
    "blocker": "1 - Critical",
    "critical": "1 - Critical",
    "major": "2 - High",
    "minor": "3 - Medium",
    "trivial": "4 - Low",
}


@exporter
class AzureDevOpsExporter:
    kind = "azure_devops"

    def map(self, bundle: Bundle, target: Target) -> dict[str, Any]:
        issue = bundle.issue
        return {
            "operations": [
                {"op": "add", "path": "/fields/System.Title", "value": summary(issue)[:255]},
                {
                    "op": "add",
                    "path": "/fields/Microsoft.VSTS.TCM.ReproSteps",
                    "value": body(bundle).replace("\n", "<br>"),
                },
                {
                    "op": "add",
                    "path": "/fields/System.Tags",
                    "value": "; ".join(labels_for(issue, target)),
                },
                {
                    "op": "add",
                    "path": "/fields/Microsoft.VSTS.Common.Severity",
                    "value": SEVERITY[issue.severity.value],
                },
            ]
        }

    def push(
        self, payloads: list[tuple[Bundle, dict[str, Any]]], target: Target
    ) -> list[ExportResult]:
        if target.dry_run:
            return [
                ExportResult(bundle.issue.fingerprint, action="skipped") for bundle, _ in payloads
            ]
        base = target.base_url.rstrip("/")
        pair = base64.b64encode(f":{token(target)}".encode()).decode()
        headers = {
            "authorization": f"Basic {pair}",
            "content-type": "application/json-patch+json",
        }
        work_item_type = target.extra.get("workItemType", "Bug")
        results = []
        for bundle, payload in payloads:
            remote = payload.pop("remoteKey", "")
            operations = payload["operations"]
            try:
                if remote:
                    url = f"{base}/{target.project}/_apis/wit/workitems/{remote}"
                    keep = ("/fields/System.Title", "/fields/Microsoft.VSTS.TCM.ReproSteps")
                    operations = [
                        {**op, "op": "replace"} for op in operations if op["path"] in keep
                    ]
                    action = "updated"
                else:
                    url = f"{base}/{target.project}/_apis/wit/workitems/${work_item_type}"
                    action = "created"
                response = request(
                    "POST" if not remote else "PATCH",
                    f"{url}?api-version={API_VERSION}",
                    headers=headers,
                    body=operations,
                )
                identifier = str((response.body or {}).get("id") or remote)
                results.append(
                    ExportResult(
                        fingerprint=bundle.issue.fingerprint,
                        remote_key=identifier,
                        url=f"{base}/{target.project}/_workitems/edit/{identifier}",
                        action=action,
                    )
                )
            except ExportError as exc:
                results.append(
                    ExportResult(bundle.issue.fingerprint, action="failed", error=str(exc))
                )
        return results
