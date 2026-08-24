"""Google adapter.

Uses the official `google-genai` SDK. `pip install 'bureau-engine[google]'`.

Model id and prices are not in the catalogue: they belong in `AgentConfig.custom` with
the figures the project has checked for itself, because a wrong price here silently
mis-reports every run's spend.
"""

from __future__ import annotations

from typing import Any

from engine.agents.config import ModelSpec
from engine.agents.provider import ProviderError, Request, Response, Usage


class GoogleProvider:
    name = "google"

    def __init__(self, api_key: str | None = None, **client_kwargs: Any) -> None:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ProviderError(
                "the google-genai package is not installed: pip install 'bureau-engine[google]'"
            ) from exc
        self._genai = genai
        self._client = genai.Client(api_key=api_key, **client_kwargs)

    def complete(self, request: Request, spec: ModelSpec) -> Response:
        return self._call(request, spec)

    def complete_vision(self, request: Request, spec: ModelSpec) -> Response:
        if not spec.vision:
            raise ProviderError(f"{spec.model} cannot take images")
        return self._call(request, spec)

    def _call(self, request: Request, spec: ModelSpec) -> Response:
        types = self._genai.types
        parts: list[Any] = [
            types.Part.from_bytes(data=image.data, mime_type=image.media_type)
            for image in request.images
        ]
        parts.append(types.Part.from_text(text=request.prompt))

        try:
            response = self._client.models.generate_content(
                model=spec.model,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(
                    system_instruction=request.system,
                    max_output_tokens=min(request.max_tokens, spec.maxTokens),
                    temperature=request.temperature,
                ),
            )
        except Exception as exc:
            raise ProviderError(f"google: {exc}") from exc

        usage = getattr(response, "usage_metadata", None)
        return Response(
            text=response.text or "",
            usage=Usage(
                inputTokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                outputTokens=int(getattr(usage, "candidates_token_count", 0) or 0),
            ),
            model=spec.model,
        )
