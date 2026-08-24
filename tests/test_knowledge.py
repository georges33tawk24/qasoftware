"""Project knowledge — SPEC §10 — and run diffing — SPEC §11.

Fixture-based like every other checker test: a frozen artifact, no live site, no network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from engine.agents.provider import Usage
from engine.agents.providers.scripted import ScriptedProvider
from engine.artifact.context import RunContext
from engine.checkers import runner
from engine.fixtures import fixture_path
from engine.issues.diff import Change, diff
from engine.issues.models import IssuesFile
from engine.knowledge.apply import apply
from engine.knowledge.models import Entry, EntryKind, Note, Verdict
from engine.knowledge.parse import parse_note


@pytest.fixture(scope="module")
def ctx() -> RunContext:
    return RunContext.open(fixture_path("broken"))


@pytest.fixture(scope="module")
def issues(ctx: RunContext) -> IssuesFile:
    return IssuesFile(
        runId=ctx.run_id, generatedAt=datetime.now(UTC), issues=runner.check(ctx).issues
    )


def note(*entries: Entry, confirmed: bool = True, raw: str = "the client said so") -> Note:
    return Note(id="kn_test", raw=raw, entries=list(entries), confirmed=confirmed)


def override(**over: Any) -> Entry:
    fields: dict[str, Any] = {
        "kind": EntryKind.override,
        "scope": "selector:.lid",
        "property": "backgroundColor",
        "expected": "#eeeeff",
    }
    fields.update(over)
    return Entry(**fields)


# ----------------------------------------------------------------------- parsing


SCRIPT = json.dumps(
    [
        {
            "kind": "override",
            "scope": "selector:.btn-primary",
            "property": "backgroundColor",
            "expected": "#1DB954",
            "note": "client request, not in Figma",
            "assertPresence": True,
        },
        {"kind": "removal", "scope": "figma:Testimonials", "note": "deferred"},
    ]
)


def test_free_text_becomes_entries_but_is_never_confirmed_by_the_parser() -> None:
    provider = ScriptedProvider({"knowledge:parse": SCRIPT}, usage=Usage(100, 40))
    parsed = parse_note("CTA is green now, testimonials are gone", provider)
    assert [e.kind.value for e in parsed.entries] == ["override", "removal"]
    assert parsed.entries[0].expected == "#1DB954"
    assert not parsed.confirmed, "SPEC §10: a human confirms, never the parser"


def test_unusable_entries_are_dropped_rather_than_guessed_at() -> None:
    provider = ScriptedProvider(
        {
            "knowledge:parse": json.dumps(
                [
                    {"kind": "override", "scope": "the big green button"},  # no prefix
                    {"kind": "sideways", "scope": "selector:.a"},  # not a kind
                    {"kind": "override", "scope": "selector:.a"},  # no property/expected
                    {"kind": "removal", "scope": "selector:"},  # empty scope
                    {"kind": "ignore", "scope": "checker:content.spelling"},
                ]
            )
        }
    )
    parsed = parse_note("a mess", provider)
    assert [e.scope for e in parsed.entries] == ["checker:content.spelling"]
    assert parsed.entries[0].assertPresence is False, "an ignore claims nothing to confirm"


def test_a_provider_failure_leaves_an_empty_draft_rather_than_blocking_the_run() -> None:
    provider = ScriptedProvider(strict=True)
    parsed = parse_note("something", provider)
    assert parsed.raw == "something"
    assert parsed.entries == []


# -------------------------------------------------------------------- assertions


def test_a_change_that_was_made_is_confirmed_present(ctx: RunContext, issues: IssuesFile) -> None:
    applied = apply(ctx, [note(override())], issues)
    change = applied.changes[0]
    assert change.verdict is Verdict.applied
    assert change.headline().startswith("Requested change confirmed present")


def test_a_change_that_was_not_made_says_so_loudly(ctx: RunContext, issues: IssuesFile) -> None:
    applied = apply(ctx, [note(override(expected="#1DB954"))], issues)
    change = applied.changes[0]
    assert change.verdict is Verdict.not_applied
    assert "NOT applied" in change.headline()
    assert "still measure" in change.detail


def test_a_scope_that_matches_nothing_is_unverifiable_not_unapplied(
    ctx: RunContext, issues: IssuesFile
) -> None:
    """The difference matters: one is the client's problem, the other is ours."""
    applied = apply(ctx, [note(override(scope="selector:.no-such-thing"))], issues)
    assert applied.changes[0].verdict is Verdict.unverifiable


def test_a_removal_is_checked_both_ways(ctx: RunContext, issues: IssuesFile) -> None:
    gone = Entry(kind=EntryKind.removal, scope="selector:.testimonials")
    still_there = Entry(kind=EntryKind.removal, scope="selector:.tile")
    applied = apply(ctx, [note(gone, still_there)], issues)
    assert applied.changes[0].verdict is Verdict.applied
    assert applied.changes[1].verdict is Verdict.not_applied
    assert applied.changes[1].pagePaths


def test_an_ignore_never_claims_to_have_checked_anything(
    ctx: RunContext, issues: IssuesFile
) -> None:
    entry = Entry(kind=EntryKind.ignore, scope="checker:layout.spacing-scale")
    applied = apply(ctx, [note(entry)], issues)
    assert applied.changes[0].verdict is Verdict.unverifiable
    assert "suppression only" in applied.changes[0].detail


# ------------------------------------------------------------------- suppression


def test_the_entry_suppresses_what_it_explains(ctx: RunContext, issues: IssuesFile) -> None:
    before = [i for i in issues.issues if i.checkerId == "typography.palette"]
    assert before, "fixture must have a palette finding for this to mean anything"

    applied = apply(ctx, [note(override())], issues)
    after = [i for i in applied.issues.issues if i.checkerId == "typography.palette"]
    assert len(after) == len(before) - 1
    assert applied.changes[0].suppressed >= 1


def test_an_override_does_not_silence_unrelated_findings(
    ctx: RunContext, issues: IssuesFile
) -> None:
    """ "The CTA is green now" must not also hide "the CTA is too small to tap"."""
    entry = override(scope="selector:.tiny-button")
    applied = apply(ctx, [note(entry)], issues)
    kinds = {i.checkerId for i in applied.issues.issues}
    assert "a11y.tap-target" in kinds


def test_a_checker_scope_suppresses_that_checker_only(ctx: RunContext, issues: IssuesFile) -> None:
    entry = Entry(kind=EntryKind.ignore, scope="checker:layout.spacing-scale")
    applied = apply(ctx, [note(entry)], issues)
    remaining = {i.checkerId for i in applied.issues.issues}
    assert "layout.spacing-scale" not in remaining
    assert "layout.group-gaps" in remaining


def test_an_unconfirmed_note_changes_nothing(ctx: RunContext, issues: IssuesFile) -> None:
    applied = apply(ctx, [note(override(), confirmed=False)], issues)
    assert applied.suppressed == 0
    assert applied.changes == []
    assert len(applied.issues.issues) == len(issues.issues)


# ------------------------------------------------------------------- run diffing


def only(payload: IssuesFile, keep: int) -> IssuesFile:
    return payload.model_copy(update={"issues": payload.issues[:keep]})


def test_a_first_run_has_no_diff(issues: IssuesFile) -> None:
    assert diff(issues, None).entries == []


def test_new_still_open_and_fixed(issues: IssuesFile) -> None:
    base = only(issues, 5)
    current = issues.model_copy(update={"issues": issues.issues[3:8]})
    result = diff(current, base)
    counts = result.counts()
    assert counts["still-open"] == 2
    assert counts["new"] == 3
    assert counts["fixed"] == 3


def test_a_returning_issue_is_regressed_and_sorted_first(issues: IssuesFile) -> None:
    base = only(issues, 3)
    returning = issues.issues[7]
    current = base.model_copy(update={"issues": [*base.issues, returning]})
    result = diff(current, base, previously_fixed={returning.fingerprint})
    assert result.entries[0].change is Change.regressed
    assert result.entries[0].fingerprint == returning.fingerprint
    assert result.counts()["regressed"] == 1


def test_instance_counts_carry_their_delta(issues: IssuesFile) -> None:
    base = only(issues, 2)
    grown = base.issues[0].model_copy(update={"instances": base.issues[0].instances * 2})
    current = base.model_copy(update={"issues": [grown, base.issues[1]]})
    entry = next(e for e in diff(current, base).entries if e.fingerprint == grown.fingerprint)
    assert entry.delta == base.issues[0].instanceCount
