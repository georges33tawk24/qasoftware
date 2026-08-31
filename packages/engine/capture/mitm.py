"""Network capture proxy for mobile native apps — SPEC §19 (Phase 11).

Intercepts HTTP/HTTPS traffic from mobile simulators/devices via mitmproxy and converts
intercepted flows into standard `NetworkEntry` records for `network.json`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from engine.artifact.models import NetworkEntry, NetworkSize, NetworkTiming


@dataclass
class InterceptedFlow:
    url: str
    method: str
    status: int
    req_headers: dict[str, str] = field(default_factory=dict)
    res_headers: dict[str, str] = field(default_factory=dict)
    req_body: str | None = None
    res_body: bytes | None = None
    start_time_ms: float = 0.0
    duration_ms: float = 0.0
    ttfb_ms: float | None = None
    transfer_bytes: int = 0
    resource_bytes: int | None = None
    initiator: str | None = None
    error: str | None = None


def _deduce_resource_type(content_type: str, url: str) -> str:
    ct = content_type.lower()
    if "json" in ct or "xml" in ct or "grpc" in ct:
        return "fetch"
    if "image/" in ct or any(
        url.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".svg", ".gif")
    ):
        return "image"
    if "javascript" in ct or url.endswith(".js"):
        return "script"
    if "css" in ct or url.endswith(".css"):
        return "stylesheet"
    if "font" in ct or any(url.endswith(ext) for ext in (".woff", ".woff2", ".ttf", ".otf")):
        return "font"
    if "html" in ct:
        return "document"
    return "other"


def flow_to_network_entry(flow: InterceptedFlow) -> NetworkEntry:
    """Convert an intercepted proxy flow into a standard NetworkEntry."""
    content_type = flow.res_headers.get("content-type", flow.res_headers.get("Content-Type", ""))
    resource_type = _deduce_resource_type(content_type, flow.url)

    res_body_hash = None
    res_body_sample = None
    if flow.res_body is not None:
        res_body_hash = hashlib.sha256(flow.res_body).hexdigest()
        try:
            res_body_sample = flow.res_body[:400].decode("utf-8", errors="replace")
        except Exception:
            res_body_sample = None

    return NetworkEntry(
        url=flow.url,
        method=flow.method.upper(),
        status=flow.status,
        type=resource_type,
        reqHeaders=flow.req_headers,
        resHeaders=flow.res_headers,
        reqBody=flow.req_body,
        resBodyHash=res_body_hash,
        resBodySample=res_body_sample,
        timing=NetworkTiming(
            startMs=flow.start_time_ms,
            ttfbMs=flow.ttfb_ms,
            durationMs=flow.duration_ms,
        ),
        size=NetworkSize(
            transferBytes=flow.transfer_bytes,
            resourceBytes=flow.resource_bytes,
        ),
        initiator=flow.initiator,
        failure=flow.error,
    )


class MitmCapture:
    """In-memory collector for intercepted mobile app network traffic."""

    def __init__(self) -> None:
        self._flows: list[InterceptedFlow] = []

    def record_flow(self, flow: InterceptedFlow) -> None:
        self._flows.append(flow)

    def record_raw_dict(self, data: dict[str, Any]) -> None:
        flow = InterceptedFlow(
            url=data.get("url", ""),
            method=data.get("method", "GET"),
            status=int(data.get("status", 200)),
            req_headers=data.get("req_headers", {}),
            res_headers=data.get("res_headers", {}),
            req_body=data.get("req_body"),
            res_body=data.get("res_body"),
            start_time_ms=float(data.get("start_time_ms", 0.0)),
            duration_ms=float(data.get("duration_ms", 0.0)),
            ttfb_ms=data.get("ttfb_ms"),
            transfer_bytes=int(data.get("transfer_bytes", 0)),
            resource_bytes=data.get("resource_bytes"),
            initiator=data.get("initiator"),
            error=data.get("error"),
        )
        self._flows.append(flow)

    def export_entries(self) -> list[NetworkEntry]:
        return [flow_to_network_entry(f) for f in self._flows]

    def clear(self) -> None:
        self._flows.clear()
