"""A provider that answers from a script instead of a network.

The AI layer has to be testable without keys, and the things worth testing — the
grounding contract, the verifier dropping candidates, the cost ceiling, the calibration
log — are all about what the pipeline does with an answer, not about which model gave it.

Responses are keyed by the request's `label`, so the tests do not break every time a
prompt is edited.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from engine.agents.config import ModelSpec
from engine.agents.provider import ProviderError, Request, Response, Usage


class ScriptedProvider:
    name = "scripted"

    def __init__(
        self,
        script: dict[str, str] | None = None,
        *,
        default: str | None = None,
        usage: Usage | None = None,
        strict: bool = False,
    ) -> None:
        self.script = dict(script or {})
        self.default = default
        self.usage = usage or Usage(inputTokens=1000, outputTokens=200)
        self.strict = strict
        self.calls: list[Request] = []
        self.counts: Counter[str] = Counter()

    @classmethod
    def from_file(cls, path: Path, **kwargs: object) -> ScriptedProvider:
        return cls(json.loads(Path(path).read_text()), **kwargs)  # type: ignore[arg-type]

    def complete(self, request: Request, spec: ModelSpec) -> Response:
        self.calls.append(request)
        self.counts[request.label] += 1
        if request.label in self.script:
            text = self.script[request.label]
        elif self.default is not None:
            text = self.default
        elif self.strict:
            raise ProviderError(f"no scripted response for {request.label!r}")
        else:
            text = "[]"
        return Response(text=text, usage=self.usage, model=spec.model)

    def complete_vision(self, request: Request, spec: ModelSpec) -> Response:
        return self.complete(request, spec)
