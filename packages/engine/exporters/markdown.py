"""Markdown — SPEC §14. A file someone pastes into a wiki or a pull request."""

from __future__ import annotations

from typing import Any

from engine.exporters.base import Bundle, ExportResult, Target, exporter
from engine.exporters.common import body, labels_for, summary


@exporter
class MarkdownExporter:
    kind = "markdown"

    def map(self, bundle: Bundle, target: Target) -> dict[str, Any]:
        issue = bundle.issue
        return {
            "heading": f"### {summary(issue)}",
            "severity": issue.severity.value,
            "labels": labels_for(issue, target),
            "body": body(bundle),
            "fingerprint": issue.fingerprint,
        }

    def push(
        self, payloads: list[tuple[Bundle, dict[str, Any]]], target: Target
    ) -> list[ExportResult]:
        """Nowhere to push to: the document *is* the export, and `render` builds it."""
        return [
            ExportResult(
                fingerprint=bundle.issue.fingerprint,
                remote_key=bundle.issue.fingerprint,
                action="created",
            )
            for bundle, _ in payloads
        ]


def render(payloads: list[dict[str, Any]], *, title: str = "Issues") -> str:
    parts = [f"# {title}", ""]
    for payload in payloads:
        labels = " ".join(f"`{label}`" for label in payload["labels"])
        parts += [payload["heading"], "", f"{labels}", "", payload["body"], "", "---", ""]
    return "\n".join(parts).rstrip() + "\n"
