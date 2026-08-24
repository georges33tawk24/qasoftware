"""OpenAI adapter.

Uses the official `openai` SDK. `pip install 'bureau-engine[openai]'`.

As with Google, the model id and prices belong in `AgentConfig.custom` rather than in the
catalogue — see `google.py` for why.
"""

from __future__ import annotations

import base64
from typing import Any

from engine.agents.config import ModelSpec
from engine.agents.provider import ProviderError, Request, Response, Usage


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str | None = None, **client_kwargs: Any) -> None:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ProviderError(
                "the openai package is not installed: pip install 'bureau-engine[openai]'"
            ) from exc
        self._openai = openai
        self._client = openai.OpenAI(api_key=api_key, **client_kwargs)

    def complete(self, request: Request, spec: ModelSpec) -> Response:
        return self._call(request, spec)

    def complete_vision(self, request: Request, spec: ModelSpec) -> Response:
        if not spec.vision:
            raise ProviderError(f"{spec.model} cannot take images")
        return self._call(request, spec)

    def _call(self, request: Request, spec: ModelSpec) -> Response:
        content: list[dict[str, Any]] = [
            {
                "type": "image_url",
                "image_url": {
                    "url": "data:"
                    + image.media_type
                    + ";base64,"
                    + base64.standard_b64encode(image.data).decode()
                },
            }
            for image in request.images
        ]
        content.append({"type": "text", "text": request.prompt})

        try:
            completion = self._client.chat.completions.create(
                model=spec.model,
                max_tokens=min(request.max_tokens, spec.maxTokens),
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": content},
                ],
            )
        except self._openai.OpenAIError as exc:
            raise ProviderError(f"openai: {exc}") from exc

        usage = completion.usage
        return Response(
            text=completion.choices[0].message.content or "",
            usage=Usage(
                inputTokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                outputTokens=int(getattr(usage, "completion_tokens", 0) or 0),
            ),
            model=completion.model,
            stopped=str(completion.choices[0].finish_reason or ""),
        )
