"""Export adapters — SPEC §14.

One neutral issue format, thin adapters on top. An adapter maps an `Issue` to whatever
shape a tracker wants and pushes it; everything else — which issues, what happens to the
returned keys, where the credentials come from — lives here and is the same for all of
them.

Adding a tracker is one new file under `exporters/` and no change to anything in this
one. If that ever stops being true, the abstraction is wrong and the fix is here rather
than a special case there.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from engine.issues.models import Issue, Severity

DEFAULT_PRIORITY: dict[str, str] = {
    "blocker": "Highest",
    "critical": "High",
    "major": "Medium",
    "minor": "Low",
    "trivial": "Lowest",
}
"""Severity to the word most trackers use. Overridable per project (SPEC §14)."""


@dataclass
class Target:
    """Where to send issues, and how this project wants them shaped.

    Never holds a credential: `token_env` names an environment variable, because a token
    written into project JSON is a token in a backup (CLAUDE.md).
    """

    kind: str
    base_url: str = ""
    project: str = ""
    """The tracker's own project key, repository, or team id."""

    token_env: str = ""
    user: str = ""
    """Some APIs want a user alongside the token; Jira's basic auth does."""

    priorities: dict[str, str] = field(default_factory=dict)
    labels: list[str] = field(default_factory=list)
    """Applied to every issue on top of the ones derived from the finding."""

    extra: dict[str, Any] = field(default_factory=dict)
    dry_run: bool = False
    """Map and return payloads without touching the network. What the tests use, and
    what a person should run before pointing this at a live project."""

    def priority(self, severity: Severity) -> str:
        return self.priorities.get(severity.value) or DEFAULT_PRIORITY[severity.value]


@dataclass
class ExportResult:
    """What happened to one issue."""

    fingerprint: str
    remote_key: str = ""
    url: str = ""
    action: str = "created"
    """`created`, `updated`, `skipped`, or `failed`."""

    error: str = ""
    attachments: int = 0

    @property
    def ok(self) -> bool:
        return self.action != "failed"


@dataclass
class Bundle:
    """One issue plus the run it came from, which is all an adapter ever needs."""

    issue: Issue
    run_dir: Path | None = None
    report_url: str = ""
    work: Path | None = None
    """Somewhere to render a crop. Supplied by `export`, and only used when the issue has
    no evidence file of its own."""

    def attachments(self, limit: int = 6) -> list[Path]:
        """What to hang on the ticket.

        Flows carry a trace and a video; a measured finding carries nothing on disk, so
        the annotated crop from the report is rendered instead. A ticket with a picture
        of the defect gets fixed; a ticket with a selector gets argued about.
        """
        if self.run_dir is None:
            return []
        out: list[Path] = []
        for evidence in self.issue.evidence:
            candidate = self.run_dir / evidence.path
            if candidate.is_file() and candidate not in out:
                out.append(candidate)
            if len(out) >= limit:
                break
        if not out and self.work is not None:
            crop = self._crop()
            if crop is not None:
                out.append(crop)
        return out

    def _crop(self) -> Path | None:
        """The same annotated picture the report shows, written into `work`."""
        from engine.artifact.context import RunContext
        from engine.report.compose import build_evidence

        assert self.run_dir is not None and self.work is not None
        try:
            ctx = RunContext.open(self.run_dir)
            media = build_evidence(ctx, self.issue, 1, self.work)
        except (OSError, ValueError):
            return None
        if media is None:
            return None
        target = self.work / f"{self.issue.fingerprint[:12]}.png"
        target.write_bytes(media.data)
        return target


@runtime_checkable
class Exporter(Protocol):
    kind: str
    """Matches `Target.kind`. Stable: it is stored on the issue with the remote key."""

    def map(self, bundle: Bundle, target: Target) -> dict[str, Any]: ...

    def push(
        self, payloads: list[tuple[Bundle, dict[str, Any]]], target: Target
    ) -> list[ExportResult]: ...


_REGISTRY: dict[str, Exporter] = {}


def exporter[E: type[Exporter]](cls: E) -> E:
    instance = cls()
    if instance.kind in _REGISTRY:
        raise ValueError(f"duplicate exporter {instance.kind!r}")
    _REGISTRY[instance.kind] = instance
    return cls


def registry() -> dict[str, Exporter]:
    return dict(_REGISTRY)


def discover(package: str = "engine.exporters") -> dict[str, Exporter]:
    """Import every adapter so its decorator runs."""
    pkg = importlib.import_module(package)
    for module in pkgutil.walk_packages(pkg.__path__, prefix=f"{package}."):
        importlib.import_module(module.name)
    return registry()


def get(kind: str) -> Exporter:
    discover()
    if kind not in _REGISTRY:
        raise KeyError(f"unknown exporter {kind!r}; have {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[kind]


def export(
    issues: Iterable[Issue],
    target: Target,
    *,
    run_dir: Path | None = None,
    report_url: str = "",
    known: dict[str, str] | None = None,
    work: Path | None = None,
) -> list[ExportResult]:
    """Map, then push. `known` is fingerprint to remote key, so a second export updates
    rather than duplicates (SPEC §14) — the adapter is told, it does not have to search.
    """
    adapter = get(target.kind)
    bundles = [Bundle(issue=i, run_dir=run_dir, report_url=report_url, work=work) for i in issues]
    payloads = []
    for bundle in bundles:
        payload = adapter.map(bundle, target)
        remote = (known or {}).get(bundle.issue.fingerprint, "")
        if remote:
            payload["remoteKey"] = remote
        payloads.append((bundle, payload))
    return adapter.push(payloads, target)
