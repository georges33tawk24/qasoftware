"""Run progress, from the worker to the browser — SPEC §16.

Redis pub/sub when there is a Redis, an in-process channel when there is not. Both keep a
history: a browser that connects halfway through a run needs the events it missed, or the
page it lands on is emptier than the run it is watching.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator
from typing import Any, Protocol

HISTORY = 500
POLL_SECONDS = 0.25


class Events(Protocol):
    def publish(self, run_id: str, event: dict[str, Any]) -> None: ...

    def history(self, run_id: str) -> list[dict[str, Any]]: ...

    def stream(self, run_id: str, *, after: int = 0) -> Iterator[dict[str, Any]]: ...


class MemoryEvents:
    """One process, no server. What the tests and a single-container dev run use."""

    def __init__(self) -> None:
        self._log: dict[str, list[dict[str, Any]]] = {}
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        with self._changed:
            entries = self._log.setdefault(run_id, [])
            entries.append(event)
            del entries[:-HISTORY]
            self._changed.notify_all()

    def history(self, run_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._log.get(run_id, []))

    def stream(self, run_id: str, *, after: int = 0) -> Iterator[dict[str, Any]]:
        """Never yields while holding the lock.

        A generator that suspends inside `with self._changed` keeps the lock for as long
        as the consumer takes to come back, and every publisher blocks behind it — which
        on a live run means the worker stalls the moment a browser starts watching.
        """
        sent = after
        while True:
            with self._changed:
                entries = self._log.get(run_id, [])
                if len(entries) <= sent:
                    self._changed.wait(timeout=POLL_SECONDS)
                    entries = self._log.get(run_id, [])
                batch = entries[sent:]
                sent = len(entries)

            if not batch:
                yield {"kind": "heartbeat"}
                continue
            for event in batch:
                yield event
                if event.get("kind") == "stage" and event.get("stage") in ("done", "failed"):
                    return


class RedisEvents:
    """A list for history plus a channel for the live tail, which is all this needs."""

    def __init__(self, url: str) -> None:
        import redis

        self.client = redis.Redis.from_url(url, decode_responses=True)

    def _key(self, run_id: str) -> str:
        return f"bureau:events:{run_id}"

    def publish(self, run_id: str, event: dict[str, Any]) -> None:
        payload = json.dumps(event)
        pipe = self.client.pipeline()
        pipe.rpush(self._key(run_id), payload)
        pipe.ltrim(self._key(run_id), -HISTORY, -1)
        pipe.expire(self._key(run_id), 60 * 60 * 24)
        pipe.publish(self._key(run_id), payload)
        pipe.execute()

    def history(self, run_id: str) -> list[dict[str, Any]]:
        return [json.loads(item) for item in self.client.lrange(self._key(run_id), 0, -1)]

    def stream(self, run_id: str, *, after: int = 0) -> Iterator[dict[str, Any]]:
        subscription = self.client.pubsub(ignore_subscribe_messages=True)
        subscription.subscribe(self._key(run_id))
        try:
            for event in self.history(run_id)[after:]:
                yield event
            while True:
                message = subscription.get_message(timeout=POLL_SECONDS)
                if message is None:
                    yield {"kind": "heartbeat"}
                    continue
                event = json.loads(message["data"])
                yield event
                if event.get("kind") == "stage" and event.get("stage") in ("done", "failed"):
                    return
        finally:
            subscription.close()


_events: Events | None = None


def events() -> Events:
    global _events
    if _events is None:
        _events = _build()
    return _events


def _build() -> Events:
    url = os.environ.get("REDIS_URL")
    if not url:
        return MemoryEvents()
    try:
        channel = RedisEvents(url)
        channel.client.ping()
    except Exception:
        return MemoryEvents()
    return channel


def use(channel: Events) -> None:
    global _events
    _events = channel
