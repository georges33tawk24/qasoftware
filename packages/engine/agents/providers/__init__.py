"""Provider adapters. Adding one is a new file here and a line in `build`."""

from __future__ import annotations

from typing import Any

from engine.agents.provider import LLMProvider, ProviderError

NAMES = ("anthropic", "google", "openai", "scripted")


def build(name: str, **kwargs: Any) -> LLMProvider:
    if name == "anthropic":
        from engine.agents.providers.anthropic import AnthropicProvider

        return AnthropicProvider(**kwargs)
    if name == "google":
        from engine.agents.providers.google import GoogleProvider

        return GoogleProvider(**kwargs)
    if name == "openai":
        from engine.agents.providers.openai import OpenAIProvider

        return OpenAIProvider(**kwargs)
    if name == "scripted":
        from engine.agents.providers.scripted import ScriptedProvider

        return ScriptedProvider(**kwargs)
    raise ProviderError(f"unknown provider {name!r}; have {', '.join(NAMES)}")
