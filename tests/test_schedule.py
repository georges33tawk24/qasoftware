"""Cron, digests and channels — SPEC §15.

The rule under test throughout: a scheduled run that finds nothing new sends nothing.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import pytest

from engine.issues.diff import Change, DiffEntry, RunDiff
from engine.issues.models import Severity
from engine.notify import digest as digests
from engine.notify.channels import Channel, NotifyError, send
from engine.schedule import CronError, due, next_after, parse

UTC_ = UTC


def when(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC_)


# ------------------------------------------------------------------------- cron


@pytest.mark.parametrize(
    ("expression", "after", "expected"),
    [
        ("0 * * * *", "2026-03-01T10:30", "2026-03-01T11:00"),
        ("*/15 * * * *", "2026-03-01T10:31", "2026-03-01T10:45"),
        ("0 2 * * *", "2026-03-01T03:00", "2026-03-02T02:00"),
        ("0 0 1 * *", "2026-03-15T00:00", "2026-04-01T00:00"),
        ("0 9 * * 1", "2026-03-01T00:00", "2026-03-02T09:00"),
        ("30 8,17 * * *", "2026-03-01T09:00", "2026-03-01T17:30"),
        ("@daily", "2026-03-01T05:00", "2026-03-02T00:00"),
    ],
)
def test_next_firing(expression: str, after: str, expected: str) -> None:
    assert next_after(expression, when(after)) == when(expected)


def test_sunday_is_both_zero_and_seven() -> None:
    assert parse("0 0 * * 7").weekdays == parse("0 0 * * 0").weekdays


def test_day_and_weekday_together_fire_on_either() -> None:
    """Crontab's one real oddity, and getting it wrong means silent missed runs."""
    cron = parse("0 0 13 * 5")
    assert cron.matches(when("2026-03-13T00:00")), "the 13th"
    assert cron.matches(when("2026-03-06T00:00")), "a Friday"
    assert not cron.matches(when("2026-03-07T00:00"))


def test_a_timezone_holds_the_local_hour_across_the_clock_change() -> None:
    """A daily 02:00 in London is 02:00 in London, in January and in July."""
    winter = next_after("0 2 * * *", when("2026-01-15T12:00"), "Europe/London")
    summer = next_after("0 2 * * *", when("2026-07-15T12:00"), "Europe/London")
    assert winter is not None and summer is not None
    assert winter.hour == 2, "GMT"
    assert summer.hour == 1, "BST, so 02:00 local is 01:00 UTC"


@pytest.mark.parametrize(
    "expression", ["", "* * * *", "0 0 * *", "60 * * * *", "0 0 0 * *", "a * * * *", "5-1 * * * *"]
)
def test_a_bad_expression_is_refused_at_configuration_time(expression: str) -> None:
    with pytest.raises(CronError):
        parse(expression)


def test_a_missed_window_fires_once_not_once_per_minute_missed() -> None:
    """Eight hours of downtime must not queue eight hourly runs at the client's site."""
    last = when("2026-03-01T01:00")
    assert due("0 * * * *", last, when("2026-03-01T09:30"))
    assert not due("0 * * * *", when("2026-03-01T09:00"), when("2026-03-01T09:30"))


def test_a_schedule_that_has_never_run_is_due() -> None:
    assert due("0 3 * * *", None, when("2026-03-01T09:30"))


# ----------------------------------------------------------------------- digest


def diff_of(*entries: tuple[Change, str, str]) -> RunDiff:
    return RunDiff(
        baseRunId="run_before",
        entries=[
            DiffEntry(
                fingerprint=f"fp{index}",
                change=change,
                title=title,
                severity=severity,
                checkerId="layout.alignment",
                instanceCount=1,
            )
            for index, (change, severity, title) in enumerate(entries)
        ],
    )


def build(diff: RunDiff) -> digests.Digest:
    return digests.build(
        diff, project="Acme", target="https://acme.test/", run_id="run_1", report_url="r://report"
    )


def test_a_quiet_run_sends_nothing_at_all() -> None:
    """The phase 9 done-when. Everything else in this file is detail next to it."""
    quiet = build(diff_of((Change.still_open, "major", "old"), (Change.fixed, "minor", "gone")))
    assert not quiet.worth_sending
    assert send(quiet, [Channel(kind="webhook", url="http://127.0.0.1:1")]) == []


def test_new_and_regressed_earn_a_digest() -> None:
    noisy = build(diff_of((Change.new, "major", "a"), (Change.regressed, "blocker", "b")))
    assert noisy.worth_sending
    assert noisy.headline() == "Acme: 1 regressed and 1 new"
    assert "blocker · b" in "\n".join(noisy.lines())


def test_the_worst_is_listed_first() -> None:
    noisy = build(
        diff_of(
            (Change.new, "trivial", "spelling"),
            (Change.new, "blocker", "checkout is broken"),
            (Change.new, "minor", "spacing"),
        )
    )
    assert [severity for severity, _ in noisy.new] == ["blocker", "minor", "trivial"]


def test_a_threshold_can_hold_back_the_small_stuff() -> None:
    small = build(diff_of((Change.new, "minor", "spacing")))
    assert small.worth_sending
    assert not digests.above(small, Severity.major)
    assert digests.above(small, Severity.minor)


# --------------------------------------------------------------------- channels


RECEIVED: list[dict[str, Any]] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        RECEIVED.append(json.loads(self.rfile.read(length) or b"{}"))
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()


@pytest.fixture
def endpoint() -> Iterator[str]:
    RECEIVED.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/hook"
    finally:
        server.shutdown()
        server.server_close()


def test_slack_gets_blocks_and_a_fallback_line(endpoint: str) -> None:
    noisy = build(diff_of((Change.regressed, "critical", "login broke again")))
    assert send(noisy, [Channel(kind="slack", url=endpoint)]) == [("slack", "200")]
    payload = RECEIVED[0]
    assert payload["text"] == noisy.headline(), "a fallback for notifications"
    assert payload["blocks"][0]["type"] == "header"
    assert "login broke again" in json.dumps(payload["blocks"])


def test_a_webhook_gets_the_structured_shape(endpoint: str) -> None:
    noisy = build(diff_of((Change.new, "major", "gap is 20px")))
    assert send(noisy, [Channel(kind="webhook", url=endpoint)]) == [("webhook", "200")]
    payload = RECEIVED[0]
    assert payload["new"] == [{"severity": "major", "title": "gap is 20px"}]
    assert payload["runId"] == "run_1"


def test_a_channel_that_fails_does_not_take_the_others_with_it(endpoint: str) -> None:
    noisy = build(diff_of((Change.new, "major", "a")))
    results = send(
        noisy,
        [
            Channel(kind="webhook", url="http://127.0.0.1:1/nope"),
            Channel(kind="slack", url=endpoint),
            Channel(kind="carrier-pigeon", url=endpoint),
        ],
    )
    assert results[0][1].startswith("failed:")
    assert results[1] == ("slack", "200")
    assert results[2] == ("carrier-pigeon", "unknown channel")


def test_a_secret_url_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md: a Slack webhook URL is a credential and does not live in project JSON."""
    channel = Channel(kind="slack", url_env="BUREAU_TEST_SLACK")
    with pytest.raises(NotifyError):
        channel.endpoint()
    monkeypatch.setenv("BUREAU_TEST_SLACK", "https://hooks.slack.test/x")
    assert channel.endpoint() == "https://hooks.slack.test/x"
