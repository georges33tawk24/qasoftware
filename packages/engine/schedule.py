"""Crontab expressions with a timezone — SPEC §15.

Hourly through monthly, plus manual. Stored as a crontab expression and an IANA zone,
because "every night" means the client's night and a server in another region is not a
reason to run at four in the afternoon.

Five fields, the ones people actually write: `*`, `n`, `a-b`, `*/n`, `a,b,c`, and the
`@hourly`-style aliases. No seconds, no `L`, no `W` — a QA sweep does not need them, and
the parser stays short enough to read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}

RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))
FIELD = re.compile(r"^(\*|\d+)(?:-(\d+))?(?:/(\d+))?$")
LOOKAHEAD_DAYS = 400
"""Far enough for `0 0 29 2 *` to find the next leap year and stop."""


class CronError(ValueError):
    """The expression cannot be read. Raised at configuration time, never at fire time."""


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    expression: str
    day_restricted: bool
    weekday_restricted: bool
    """Crontab's one genuine oddity: with both day-of-month and day-of-week set, a match
    on *either* fires. With only one set, that one must match."""

    def matches(self, moment: datetime) -> bool:
        if moment.minute not in self.minutes or moment.hour not in self.hours:
            return False
        if moment.month not in self.months:
            return False
        day = moment.day in self.days
        weekday = (moment.weekday() + 1) % 7 in self.weekdays
        if self.day_restricted and self.weekday_restricted:
            return day or weekday
        return day and weekday


def parse(expression: str) -> Cron:
    text = ALIASES.get(expression.strip().lower(), expression).strip()
    fields = text.split()
    if len(fields) != 5:
        raise CronError(f"{expression!r} is not five crontab fields")
    sets = [_field(field, *bounds) for field, bounds in zip(fields, RANGES, strict=True)]
    return Cron(
        minutes=sets[0],
        hours=sets[1],
        days=sets[2],
        months=sets[3],
        weekdays=sets[4],
        expression=text,
        day_restricted=fields[2] != "*",
        weekday_restricted=fields[4] != "*",
    )


def _field(field: str, low: int, high: int) -> frozenset[int]:
    # Sunday is 0 in some crontabs and 7 in others, and every implementation accepts
    # both. Widen the weekday bound rather than rejecting half the expressions people
    # paste in, then fold 7 back onto 0 below.
    ceiling = 7 if (low, high) == (0, 6) else high
    values: set[int] = set()
    for part in field.split(","):
        match = FIELD.match(part.strip())
        if match is None:
            raise CronError(f"{part!r} is not a crontab field")
        start, end, step = match.group(1), match.group(2), match.group(3)
        if start == "*":
            first, last = low, high
        else:
            first = int(start)
            last = int(end) if end else first
        if end and start == "*":
            raise CronError(f"{part!r} mixes a wildcard with a range")
        if not (low <= first <= ceiling and low <= last <= ceiling) or last < first:
            raise CronError(f"{part!r} is outside {low}-{high}")
        values.update(range(first, last + 1, int(step) if step else 1))
    if 7 in values and high == 6:
        values.discard(7)
        values.add(0)  # Sunday is both 0 and 7 in every crontab anyone has used.
    return frozenset(values)


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronError(f"unknown timezone {name!r}") from exc


def next_after(expression: str, after: datetime, timezone: str = "UTC") -> datetime | None:
    """The first firing strictly after `after`, in UTC.

    Walks minute by minute in the project's own zone, so a daily 02:00 stays at 02:00
    across a daylight-saving change instead of drifting by an hour twice a year.
    """
    cron = parse(expression)
    tz = zone(timezone)
    local = after.astimezone(tz).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = local + timedelta(days=LOOKAHEAD_DAYS)
    while local < limit:
        if cron.matches(local):
            return local.astimezone(UTC)
        # Skipping a whole day at a time when the date cannot match keeps a yearly
        # expression from walking half a million minutes.
        if local.month not in cron.months or not _day_ok(cron, local):
            local = (local + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        local += timedelta(minutes=1)
    return None


def _day_ok(cron: Cron, moment: datetime) -> bool:
    day = moment.day in cron.days
    weekday = (moment.weekday() + 1) % 7 in cron.weekdays
    if cron.day_restricted and cron.weekday_restricted:
        return day or weekday
    return day and weekday


def due(expression: str, last: datetime | None, now: datetime, timezone: str = "UTC") -> bool:
    """Should this have fired by now?

    A missed window fires once when the worker comes back, not once per minute missed:
    catching up on eight hours of hourly runs would hammer the client's site for nothing.
    """
    if last is None:
        return True
    upcoming = next_after(expression, last, timezone)
    return upcoming is not None and upcoming <= now
