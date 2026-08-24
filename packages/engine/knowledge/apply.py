"""Applying confirmed knowledge to a finished run — SPEC §10.

Two jobs, both arithmetic and neither needing a model:

1. **Suppress.** A finding that an entry explains is dropped, and the entry records how
   many it dropped. Nothing disappears silently.
2. **Assert.** The same entry is checked against what was captured, so the report can say
   "confirmed present" or — far more usefully — "NOT applied".

A pure function over a run artifact, like every checker: no browser, no network.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from engine.artifact.context import RunContext
from engine.artifact.models import ElementRecord
from engine.artifact.selectors import matches
from engine.checkers import colour
from engine.checkers.support import Surface, surfaces
from engine.issues.models import Issue, IssuesFile
from engine.knowledge.models import Entry, EntryKind, Note, RequestedChange, Verdict

LENGTH_TOLERANCE_PX = 1.0
"""A length is "as requested" within a pixel. Anything tighter reports rounding."""

EXPLAINS: dict[str, tuple[str, ...]] = {
    "colour": ("figma.colour", "typography.palette"),
    "spacing": ("figma.spacing", "layout.spacing-scale", "layout.group-gaps"),
    "type": ("figma.typography", "typography.scale", "typography.line-height"),
    "geometry": ("figma.geometry", "layout.alignment", "figma.decoration"),
    "content": ("figma.content", "content.casing", "content.terminology"),
}
"""Which checkers an override of a given property can honestly explain.

Scope alone is too blunt: "the CTA is green now" must not also silence "the CTA is too
small to tap". The table is deliberately explicit so that what an entry can and cannot
hide is readable rather than inferred.
"""

FAMILIES: dict[str, str] = {
    "color": "colour",
    "backgroundColor": "colour",
    "borderColor": "colour",
    "boxShadow": "colour",
    "fontFamily": "type",
    "fontSize": "type",
    "fontWeight": "type",
    "lineHeight": "type",
    "letterSpacing": "type",
    "textTransform": "type",
    "gap": "spacing",
    "borderRadius": "geometry",
    "borderWidth": "geometry",
    "opacity": "geometry",
    "display": "geometry",
    "textAlign": "geometry",
    "text": "content",
}


@dataclass
class Applied:
    issues: IssuesFile
    changes: list[RequestedChange] = field(default_factory=list)
    suppressed: int = 0


def _family(prop: str | None) -> str | None:
    if not prop:
        return None
    if prop in FAMILIES:
        return FAMILIES[prop]
    if prop.startswith(("margin", "padding")):
        return "spacing"
    if prop in ("width", "height", "x", "y", "top", "left"):
        return "geometry"
    return None


def matches_element(entry: Entry, element: ElementRecord) -> bool:
    """Does this element fall inside the entry's scope?"""
    kind, value = entry.scope_kind(), entry.scope_value()
    if kind == "text":
        return value.casefold() in (element.textFull or element.text or "").casefold()
    return matches(value, element) if kind == "selector" else False


def _scoped_elements(entry: Entry, surface: Surface) -> list[ElementRecord]:
    return [e for e in surface.laid_out if matches_element(entry, e)]


def _live_value(element: ElementRecord, prop: str) -> str | None:
    if prop == "text":
        return element.textFull or element.text or None
    value = getattr(element.styles, prop, None)
    if value is None:
        return None
    if isinstance(value, list):
        return str(value[0])
    return str(value)


def _agrees(prop: str, expected: str, actual: str) -> bool:
    if _family(prop) == "colour":
        distance = colour.distance(expected, actual)
        return distance is not None and distance <= colour.DEFAULT_DELTA_E
    try:
        return abs(float(expected.rstrip("px")) - float(actual.rstrip("px"))) <= LENGTH_TOLERANCE_PX
    except ValueError:
        return expected.strip().casefold() == actual.strip().casefold()


def _explains(entry: Entry, issue: Issue) -> bool:
    """Can this entry account for this issue, ignoring where it happened?"""
    if entry.kind is EntryKind.ignore:
        return True
    if entry.kind is EntryKind.removal:
        return issue.checkerId in ("figma.presence", "figma.no-match", "content.empty-card")
    if entry.kind is EntryKind.addition:
        return issue.checkerId in ("figma.no-match", "figma.presence")
    family = _family(entry.property)
    if family is None:
        # An override with no readable property still explains the design diff it was
        # written about, and nothing else.
        return issue.checkerId.startswith("figma.")
    return issue.checkerId in EXPLAINS[family]


def _in_scope(entry: Entry, issue: Issue, elements: dict[str, set[str]]) -> bool:
    """Does the entry's scope cover any of this issue's instances?"""
    kind, value = entry.scope_kind(), entry.scope_value()
    if kind == "checker":
        return issue.checkerId == value or issue.checkerId.startswith(f"{value}.")
    if kind == "page":
        return any(i.pagePath == value for i in issue.instances)
    if kind == "figma":
        layer = str(issue.data.get("layer") or "")
        return layer.casefold() == value.casefold()
    return any(
        i.elementId in elements.get(f"{i.pageId}|{i.viewport}", set()) for i in issue.instances
    )


def _assert(entry: Entry, ctx: RunContext) -> RequestedChange:
    """Check the entry against what was captured, which is the half people remember."""
    matched: list[tuple[Surface, ElementRecord]] = []
    for surface in surfaces(ctx):
        matched.extend((surface, e) for e in _scoped_elements(entry, surface))

    change = RequestedChange(
        entry=entry,
        verdict=Verdict.unverifiable,
        matched=len(matched),
        pagePaths=sorted({s.page.path for s, _ in matched}),
    )
    if not entry.assertPresence or entry.kind is EntryKind.ignore:
        change.detail = "recorded as a suppression only, so there is nothing to confirm"
        return change

    if entry.kind is EntryKind.removal:
        change.verdict = Verdict.applied if not matched else Verdict.not_applied
        change.detail = (
            "nothing on the site matches it any more"
            if not matched
            else f"still present on {len(change.pagePaths)} page(s)"
        )
        return change

    if entry.kind is EntryKind.addition:
        change.verdict = Verdict.applied if matched else Verdict.not_applied
        change.detail = (
            f"found on {len(change.pagePaths)} page(s)" if matched else "nothing matches it"
        )
        return change

    if not matched:
        change.detail = "nothing on the site matches this scope, so it cannot be checked"
        return change
    if not entry.property or entry.expected is None:
        change.detail = "no property and expected value to compare"
        return change

    values = [v for v in (_live_value(e, entry.property) for _, e in matched) if v is not None]
    if not values:
        change.detail = f"{entry.property} was not captured for these elements"
        return change
    agreeing = [v for v in values if _agrees(entry.property, entry.expected, v)]
    if len(agreeing) == len(values):
        change.verdict = Verdict.applied
        change.detail = f"{len(values)} element(s) measure {values[0]}"
    else:
        change.verdict = Verdict.not_applied
        wrong = next(v for v in values if v not in agreeing)
        change.detail = (
            f"{len(values) - len(agreeing)} of {len(values)} element(s) still measure {wrong}"
        )
    return change


def apply(ctx: RunContext, notes: Iterable[Note], payload: IssuesFile) -> Applied:
    """Suppress what the notes explain, and report whether each one was acted on."""
    confirmed = [n for n in notes if n.confirmed]
    pairs = [(note, entry) for note in confirmed for entry in note.entries]
    if not pairs:
        return Applied(issues=payload)

    scoped: dict[int, dict[str, set[str]]] = {}
    for index, (_, entry) in enumerate(pairs):
        per_surface: dict[str, set[str]] = {}
        if entry.scope_kind() in ("selector", "text"):
            for surface in surfaces(ctx):
                ids = {e.id for e in _scoped_elements(entry, surface)}
                if ids:
                    per_surface[f"{surface.page.id}|{surface.viewport.name}"] = ids
        scoped[index] = per_surface

    kept: list[Issue] = []
    counts: dict[int, int] = dict.fromkeys(range(len(pairs)), 0)
    for issue in payload.issues:
        hit = next(
            (
                index
                for index, (_, entry) in enumerate(pairs)
                if _explains(entry, issue) and _in_scope(entry, issue, scoped[index])
            ),
            None,
        )
        if hit is None:
            kept.append(issue)
        else:
            counts[hit] += issue.instanceCount or 1

    changes = []
    for index, (note, entry) in enumerate(pairs):
        change = _assert(entry, ctx)
        change.noteId = note.id
        change.suppressed = counts[index]
        changes.append(change)

    remaining = payload.model_copy(update={"issues": kept})
    return Applied(
        issues=remaining,
        changes=changes,
        suppressed=len(payload.issues) - len(kept),
    )
