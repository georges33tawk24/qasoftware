"""CSV — SPEC §14. The lowest common denominator, and the one people actually use."""

from __future__ import annotations

import csv
import io
from typing import Any

from engine.exporters.base import Bundle, ExportResult, Target, exporter
from engine.exporters.common import body, labels_for, summary

COLUMNS = [
    "fingerprint",
    "severity",
    "category",
    "checker",
    "title",
    "expected",
    "actual",
    "instances",
    "pages",
    "labels",
    "description",
]


@exporter
class CsvExporter:
    kind = "csv"

    def map(self, bundle: Bundle, target: Target) -> dict[str, Any]:
        issue = bundle.issue
        return {
            "fingerprint": issue.fingerprint,
            "severity": issue.severity.value,
            "category": issue.category.value,
            "checker": issue.checkerId,
            "title": summary(issue),
            "expected": issue.expected or "",
            "actual": issue.actual or "",
            "instances": issue.instanceCount,
            "pages": " ".join(issue.pagePaths),
            "labels": " ".join(labels_for(issue, target)),
            "description": body(bundle, markdown=False),
        }

    def push(
        self, payloads: list[tuple[Bundle, dict[str, Any]]], target: Target
    ) -> list[ExportResult]:
        return [
            ExportResult(
                fingerprint=bundle.issue.fingerprint,
                remote_key=bundle.issue.fingerprint,
                action="created",
            )
            for bundle, _ in payloads
        ]


def render(payloads: list[dict[str, Any]]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for payload in payloads:
        writer.writerow({column: payload.get(column, "") for column in COLUMNS})
    return out.getvalue()
