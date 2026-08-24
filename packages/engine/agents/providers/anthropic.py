"""Anthropic adapter.

Uses the official SDK. `pip install 'bureau-engine[anthropic]'`.
"""

from __future__ import annotations

import base64
from typing import Any

from engine.agents.config import ModelSpec
from engine.agents.provider import ProviderError, Request, Response, Usage


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, **client_kwargs: Any) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise ProviderError(
                "the anthropic package is not installed: pip install 'bureau-engine[anthropic]'"
            ) from exc
        # A bare client resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN or an
        # `ant auth login` profile, so an unset env var does not mean no credentials.
        self._client = (
            anthropic.Anthropic(api_key=api_key, **client_kwargs)
            if api_key
            else anthropic.Anthropic(**client_kwargs)
        )

    def complete(self, request: Request, spec: ModelSpec) -> Response:
        return self._call(request, spec)

    def complete_vision(self, request: Request, spec: ModelSpec) -> Response:
        if not spec.vision:
            raise ProviderError(f"{spec.model} cannot take images")
        return self._call(request, spec)

    def _call(self, request: Request, spec: ModelSpec) -> Response:
        import anthropic

        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": image.media_type,
                    "data": base64.standard_b64encode(image.data).decode(),
                },
            }
            for image in request.images
        ]
        content.append({"type": "text", "text": request.prompt})

        try:
            message = self._client.messages.create(
                model=spec.model,
                max_tokens=min(request.max_tokens, spec.maxTokens),
                system=request.system,
                messages=[{"role": "user", "content": content}],
            )
        except anthropic.APIStatusError as exc:
            raise ProviderError(f"anthropic {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(f"could not reach the Anthropic API: {exc}") from exc

        text = "".join(block.text for block in message.content if block.type == "text")
        return Response(
            text=text,
            usage=Usage(
                inputTokens=message.usage.input_tokens,
                outputTokens=message.usage.output_tokens,
            ),
            model=message.model,
            stopped=str(message.stop_reason or ""),
        )
