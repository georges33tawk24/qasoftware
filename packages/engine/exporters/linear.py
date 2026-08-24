"""Linear — SPEC §14. One GraphQL endpoint, so the mapping is the whole adapter."""

from __future__ import annotations

from typing import Any

from engine.exporters.base import Bundle, ExportResult, Target, exporter
from engine.exporters.common import ExportError, body, labels_for, request, summary, token

API = "https://api.linear.app/graphql"

CREATE = """
mutation Create($input: IssueCreateInput!) {
  issueCreate(input: $input) { success issue { id identifier url } }
}
"""

UPDATE = """
mutation Update($id: String!, $input: IssueUpdateInput!) {
  issueUpdate(id: $id, input: $input) { success issue { id identifier url } }
}
"""

PRIORITY = {"blocker": 1, "critical": 1, "major": 2, "minor": 3, "trivial": 4}
"""Linear's priority is 0–4 with 1 urgent, so the shared word map does not apply."""


@exporter
class LinearExporter:
    kind = "linear"

    def map(self, bundle: Bundle, target: Target) -> dict[str, Any]:
        issue = bundle.issue
        return {
            "teamId": target.project,
            "title": summary(issue),
            "description": body(bundle),
            "priority": PRIORITY[issue.severity.value],
            "labelIds": target.extra.get("labelIds") or [],
            "_labels": labels_for(issue, target),
        }

    def push(
        self, payloads: list[tuple[Bundle, dict[str, Any]]], target: Target
    ) -> list[ExportResult]:
        if target.dry_run:
            return [
                ExportResult(bundle.issue.fingerprint, action="skipped") for bundle, _ in payloads
            ]
        url = target.base_url or API
        headers = {"authorization": token(target)}
        results = []
        for bundle, payload in payloads:
            remote = payload.pop("remoteKey", "")
            payload.pop("_labels", None)
            try:
                if remote:
                    variables = {
                        "id": remote,
                        "input": {
                            "title": payload["title"],
                            "description": payload["description"],
                        },
                    }
                    response = request(
                        "POST", url, headers=headers, body={"query": UPDATE, "variables": variables}
                    )
                    node = _node(response.body, "issueUpdate")
                    action = "updated"
                else:
                    response = request(
                        "POST",
                        url,
                        headers=headers,
                        body={"query": CREATE, "variables": {"input": payload}},
                    )
                    node = _node(response.body, "issueCreate")
                    action = "created"
                results.append(
                    ExportResult(
                        fingerprint=bundle.issue.fingerprint,
                        remote_key=str(node.get("id") or ""),
                        url=str(node.get("url") or ""),
                        action=action,
                    )
                )
            except ExportError as exc:
                results.append(
                    ExportResult(bundle.issue.fingerprint, action="failed", error=str(exc))
                )
        return results


def _node(payload: Any, field: str) -> dict[str, Any]:
    """GraphQL answers 200 with the error inside, so this is where failures surface."""
    if not isinstance(payload, dict):
        raise ExportError("Linear returned something that is not JSON")
    if payload.get("errors"):
        raise ExportError(str(payload["errors"])[:300])
    result = (payload.get("data") or {}).get(field) or {}
    if not result.get("success"):
        raise ExportError(f"Linear refused the {field}")
    node: dict[str, Any] = result.get("issue") or {}
    return node
