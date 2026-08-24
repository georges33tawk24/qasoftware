"""The provider-agnostic wrapper — SPEC §9, §17.

`complete`, `complete_vision` and `estimate_cost` over one request shape. Adding a
provider is one new file under `providers/` and zero changes here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from engine.agents.config import ModelSpec

IMAGE_TOKENS_PER_PIXEL = 1 / 750
"""Rough, and honest about it: used only to estimate a call's cost *before* making it."""

CHARS_PER_TOKEN = 4


@dataclass(frozen=True)
class Image:
    data: bytes
    media_type: str = "image/png"
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class Request:
    system: str
    prompt: str
    images: tuple[Image, ...] = ()
    max_tokens: int = 2000
    temperature: float | None = None
    label: str = ""
    """Identifies the call in the cost log and to the scripted provider."""


@dataclass
class Usage:
    inputTokens: int = 0
    outputTokens: int = 0

    def cost(self, spec: ModelSpec) -> float:
        return spec.cost(self.inputTokens, self.outputTokens)


@dataclass
class Response:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    stopped: str = ""


class LLMProvider(Protocol):
    name: str

    def complete(self, request: Request, spec: ModelSpec) -> Response: ...

    def complete_vision(self, request: Request, spec: ModelSpec) -> Response: ...


def estimate_tokens(request: Request) -> int:
    """Enough to decide whether a call fits inside the remaining budget."""
    text = len(request.system) + len(request.prompt)
    pixels = sum(image.width * image.height for image in request.images)
    return int(text / CHARS_PER_TOKEN + pixels * IMAGE_TOKENS_PER_PIXEL)


def estimate_cost(request: Request, spec: ModelSpec) -> float:
    """SPEC §9's third wrapper method: what this call will cost, before making it."""
    return spec.cost(estimate_tokens(request), request.max_tokens)


class ProviderError(RuntimeError):
    pass
