"""Run progress — SPEC §16.

Runs take minutes. The UI streams page-by-page progress rather than showing a spinner,
and that is much harder to retrofit than to build in, so the engine emits events from the
start and the transport decides what to do with them.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class Stage(StrEnum):
    """SPEC §3's worker stages, in order."""

    queued = "queued"
    capture = "capture"
    ingest = "ingest"
    match = "match"
    check = "check"
    exercise = "exercise"
    reason = "reason"
    resolve = "resolve"
    render = "render"
    done = "done"
    failed = "failed"


@dataclass
class Event:
    kind: str
    """`stage`, `page`, `flow`, `issue`, `note`, `error`."""

    stage: Stage
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    payload: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "stage": self.stage.value,
            "at": self.at.isoformat(),
            **self.payload,
        }


Listener = Callable[[Event], None]


class Progress:
    """A fan-out with a memory.

    The memory matters: a browser that connects halfway through a run needs the events it
    missed, or the page it lands on is emptier than the run it is watching.
    """

    def __init__(self, *listeners: Listener, history: int = 500) -> None:
        self.listeners = list(listeners)
        self.events: list[Event] = []
        self.limit = history
        self.stage = Stage.queued

    def listen(self, listener: Listener) -> None:
        self.listeners.append(listener)

    def emit(self, kind: str, stage: Stage | None = None, **payload: Any) -> Event:
        event = Event(kind=kind, stage=stage or self.stage, payload=payload)
        if kind == "stage":
            self.stage = event.stage
        self.events.append(event)
        if len(self.events) > self.limit:
            del self.events[: len(self.events) - self.limit]
        for listener in self.listeners:
            listener(event)
        return event

    def enter(self, stage: Stage, **payload: Any) -> None:
        self.emit("stage", stage, **payload)

    def note(self, text: str, **payload: Any) -> None:
        self.emit("note", text=text, **payload)

    def error(self, text: str, **payload: Any) -> None:
        self.emit("error", Stage.failed, text=text, **payload)


NULL = Progress()
"""A progress object nobody is listening to, for callers that do not care."""
