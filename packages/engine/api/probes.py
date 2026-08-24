"""The probe battery — SPEC §8.4 I.

Every probe here replays a request the site already made, changing one thing about it,
and records what came back. Nothing is fuzzed, nothing is extracted, and nothing runs
against a host the project has not authorised.

The probes record *what happened*; group I checkers decide what it means. That keeps the
checkers pure and the probes free of severity judgements.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.async_api import APIRequestContext, APIResponse
from playwright.async_api import Error as PlaywrightError

from engine.api.authorisation import Authorisation
from engine.artifact.models import Endpoint, ProbeResult

TIMEOUT_MS = 15_000
CONCURRENCY = 4
SLOW_MS = 1_000.0
BODY_SAMPLE = 400
RATE_LIMIT_BURST = 12
"""Enough to see whether a limiter exists. Not a load test, and not a denial of service."""

STACK_MARKERS = (
    "traceback (most recent call last)",
    "at java.",
    "at System.",
    "stack trace",
    '.py", line ',
    "org.springframework",
)
SQL_MARKERS = (
    "sqlstate",
    "syntax error at or near",
    "you have an error in your sql syntax",
    "ora-0",
    "psycopg",
    "sqlalchemy",
)
PII_PATTERNS = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
    "ukPhone": re.compile(r"\+44\s?7\d{3}\s?\d{6}"),
    "iban": re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b"),
    "card": re.compile(r"\b(?:\d[ -]?){13,16}\b"),
}


@dataclass
class ProbeContext:
    request: APIRequestContext
    authorisation: Authorisation
    personas: dict[str, dict[str, str]]
    """Persona name to the headers that identify it. Two are needed for the cross-persona
    check (SPEC §8.4 I), which is why multi-persona auth exists from phase 1."""


async def _send(
    ctx: ProbeContext,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: Any | None = None,
) -> tuple[APIResponse | None, float, str]:
    started = datetime.now(UTC)
    try:
        response = await ctx.request.fetch(
            url,
            method=method,
            headers=headers or {},
            data=data,
            timeout=TIMEOUT_MS,
            max_redirects=3,
            fail_on_status_code=False,
        )
    except PlaywrightError as exc:
        return None, _elapsed(started), str(exc).splitlines()[0][:200]
    return response, _elapsed(started), ""


def _elapsed(started: datetime) -> float:
    return round((datetime.now(UTC) - started).total_seconds() * 1000, 1)


async def _text(response: APIResponse) -> str:
    try:
        return (await response.text())[: BODY_SAMPLE * 8]
    except PlaywrightError:
        return ""


def _result(endpoint: Endpoint, probe: str, url: str, **fields: Any) -> ProbeResult:
    return ProbeResult(
        endpointId=endpoint.id, probe=probe, method=endpoint.method, url=url, **fields
    )


# ------------------------------------------------------------------- the probes


async def unauthenticated(ctx: ProbeContext, endpoint: Endpoint) -> ProbeResult:
    """Replay with no auth. A private endpoint that answers anyway is the finding."""
    response, ms, error = await _send(ctx, endpoint.method, endpoint.sampleUrl)
    if response is None:
        return _result(endpoint, "no-auth", endpoint.sampleUrl, detail=error, durationMs=ms)
    body = await _text(response)
    return _result(
        endpoint,
        "no-auth",
        endpoint.sampleUrl,
        status=response.status,
        durationMs=ms,
        finding=endpoint.hasAuthHeader and 200 <= response.status < 300,
        detail=(
            "answered an anonymous request in full"
            if 200 <= response.status < 300
            else f"refused with {response.status}"
        ),
        evidence={"sample": body[:BODY_SAMPLE]},
    )


async def cross_persona(
    ctx: ProbeContext, endpoint: Endpoint, first: str, second: str
) -> ProbeResult | None:
    """One persona's token asking for another's data — SPEC §8.4 I's IDOR check.

    This asks a question about authorisation using two accounts the project owns. It does
    not attempt to reach anything outside those two accounts.
    """
    theirs, mine = ctx.personas.get(first), ctx.personas.get(second)
    if not theirs or not mine:
        return None
    baseline, _, _ = await _send(ctx, endpoint.method, endpoint.sampleUrl, headers=theirs)
    crossed, ms, error = await _send(ctx, endpoint.method, endpoint.sampleUrl, headers=mine)
    if baseline is None or crossed is None:
        return _result(endpoint, "cross-persona", endpoint.sampleUrl, detail=error, durationMs=ms)

    theirs_body = await _text(baseline)
    mine_body = await _text(crossed)
    identical = bool(theirs_body) and theirs_body == mine_body
    return _result(
        endpoint,
        "cross-persona",
        endpoint.sampleUrl,
        status=crossed.status,
        durationMs=ms,
        finding=identical and 200 <= crossed.status < 300 and baseline.status == crossed.status,
        detail=(
            f"{second} received the same response as {first}"
            if identical
            else f"{second} received a different response ({crossed.status})"
        ),
        evidence={"personas": [first, second], "sample": mine_body[:BODY_SAMPLE]},
    )


async def malformed(ctx: ProbeContext, endpoint: Endpoint) -> ProbeResult:
    """Missing fields and wrong types — SPEC §8.4 I. Handling, not fuzzing: one bad
    request, once.

    A GET is tampered with in its query string and never given a body. A body on a GET is
    left unread by most servers and then reinterpreted as the next request on the same
    keep-alive connection, which produces a 400 that says nothing about the endpoint.
    """
    if endpoint.method in ("GET", "HEAD", "DELETE"):
        url = _tamper_query(endpoint.sampleUrl)
        response, ms, error = await _send(ctx, endpoint.method, url)
    else:
        url = endpoint.sampleUrl
        payload = json.dumps({"id": {"unexpected": "object"}, "quantity": "not-a-number"})
        response, ms, error = await _send(
            ctx, endpoint.method, url, headers={"content-type": "application/json"}, data=payload
        )

    if response is None:
        return _result(endpoint, "malformed-input", url, detail=error, durationMs=ms)
    body = await _text(response)
    # Backslashes stripped first: a stack trace inside a JSON string arrives as
    # `File \"/srv/app.py\", line 42` and would not match otherwise.
    lowered = body.casefold().replace("\\", "")
    leaked = [m for m in (*STACK_MARKERS, *SQL_MARKERS) if m in lowered]
    return _result(
        endpoint,
        "malformed-input",
        url,
        status=response.status,
        durationMs=ms,
        finding=bool(leaked) or response.status >= 500,
        detail=(
            f"the error response contains {leaked[0]!r}"
            if leaked
            else f"responded {response.status}"
        ),
        evidence={"leaked": leaked, "sample": body[:BODY_SAMPLE]},
    )


def _tamper_query(url: str) -> str:
    """A string where a number was, and a missing value where one was required."""
    parts = urlsplit(url)
    if not parts.query:
        return urlunsplit(parts._replace(query="id=not-a-number"))
    pairs = [
        (key, "not-a-number" if value.isdigit() else "")
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


async def method_tampering(ctx: ProbeContext, endpoint: Endpoint) -> ProbeResult | None:
    """A read endpoint that also accepts DELETE is a configuration mistake. The request is
    sent to the endpoint as-is and nothing is deleted that the site would not delete."""
    if endpoint.method != "GET":
        return None
    response, ms, error = await _send(ctx, "DELETE", endpoint.sampleUrl)
    if response is None:
        return _result(
            endpoint, "method-tampering", endpoint.sampleUrl, detail=error, durationMs=ms
        )
    return _result(
        endpoint,
        "method-tampering",
        endpoint.sampleUrl,
        status=response.status,
        durationMs=ms,
        finding=200 <= response.status < 300,
        detail=f"DELETE on a GET endpoint answered {response.status}",
    )


async def cors(ctx: ProbeContext, endpoint: Endpoint) -> ProbeResult:
    """A wildcard origin only matters on a response worth stealing, and a signed-out
    response usually is not one — so this asks as a persona where there is one."""
    identity = next(iter(ctx.personas.values()), {})
    response, ms, error = await _send(
        ctx,
        endpoint.method,
        endpoint.sampleUrl,
        headers={**identity, "Origin": "https://example.invalid"},
    )
    if response is None:
        return _result(endpoint, "cors", endpoint.sampleUrl, detail=error, durationMs=ms)
    headers = {k.casefold(): v for k, v in response.headers.items()}
    origin = headers.get("access-control-allow-origin", "")
    credentials = headers.get("access-control-allow-credentials", "").casefold() == "true"
    return _result(
        endpoint,
        "cors",
        endpoint.sampleUrl,
        status=response.status,
        durationMs=ms,
        finding=origin == "*" and credentials,
        detail=f"Access-Control-Allow-Origin: {origin or 'absent'}"
        + (" with credentials" if credentials else ""),
        evidence={"allowOrigin": origin, "allowCredentials": credentials},
    )


async def rate_limit(ctx: ProbeContext, endpoint: Endpoint) -> ProbeResult:
    """A short burst, to see whether a limiter exists at all."""
    statuses: list[int] = []
    for _ in range(RATE_LIMIT_BURST):
        response, _, _ = await _send(ctx, endpoint.method, endpoint.sampleUrl)
        statuses.append(response.status if response else 0)
    limited = any(status == 429 for status in statuses)
    return _result(
        endpoint,
        "rate-limit",
        endpoint.sampleUrl,
        status=statuses[-1] if statuses else 0,
        finding=not limited,
        detail=(
            f"{RATE_LIMIT_BURST} requests in a row, none refused"
            if not limited
            else "the endpoint rate-limits"
        ),
        evidence={"statuses": statuses},
    )


async def personal_data(ctx: ProbeContext, endpoint: Endpoint) -> ProbeResult:
    """Whether an anonymous request can read something that looks personal.

    Matches are counted and named by category; the values themselves are never recorded,
    because writing them into the artifact would be the leak this is looking for.
    """
    response, ms, error = await _send(ctx, endpoint.method, endpoint.sampleUrl)
    if response is None:
        return _result(endpoint, "personal-data", endpoint.sampleUrl, detail=error, durationMs=ms)
    body = await _text(response)
    hits = {name: len(pattern.findall(body)) for name, pattern in PII_PATTERNS.items()}
    hits = {name: count for name, count in hits.items() if count}
    return _result(
        endpoint,
        "personal-data",
        endpoint.sampleUrl,
        status=response.status,
        durationMs=ms,
        finding=bool(hits) and 200 <= response.status < 300,
        detail=(
            "an anonymous request returned "
            + ", ".join(f"{count} {name}" for name, count in sorted(hits.items()))
            if hits
            else "nothing that looks personal"
        ),
        evidence={"categories": sorted(hits)},
    )


async def latency(ctx: ProbeContext, endpoint: Endpoint) -> ProbeResult:
    response, ms, error = await _send(ctx, endpoint.method, endpoint.sampleUrl)
    return _result(
        endpoint,
        "latency",
        endpoint.sampleUrl,
        status=response.status if response else 0,
        durationMs=ms,
        finding=ms > SLOW_MS,
        detail=error or f"{ms:g}ms",
        evidence={"budgetMs": SLOW_MS},
    )


PROBES = (unauthenticated, malformed, method_tampering, cors, rate_limit, personal_data, latency)


async def run_probes(
    ctx: ProbeContext, endpoints: list[Endpoint], *, personas: tuple[str, str] | None = None
) -> tuple[list[ProbeResult], dict[str, str]]:
    results: list[ProbeResult] = []
    skipped: dict[str, str] = {}
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(endpoint: Endpoint) -> list[ProbeResult]:
        if not ctx.authorisation.allows(endpoint.sampleUrl):
            skipped[endpoint.id] = ctx.authorisation.refuse(endpoint.sampleUrl)
            return []
        async with gate:
            out = [await probe(ctx, endpoint) for probe in PROBES]
            if personas:
                out.append(await cross_persona(ctx, endpoint, personas[0], personas[1]))
            else:
                skipped[f"{endpoint.id}:cross-persona"] = (
                    "the cross-persona check needs two authenticated personas"
                )
        return [result for result in out if result is not None]

    for batch in await asyncio.gather(*(one(endpoint) for endpoint in endpoints)):
        results.extend(batch)
    return results, skipped
