"""Provider registry. Adding a vendor is one adapter file and one line
here; the loop never changes."""

from __future__ import annotations

from typing import Any, Callable

from ..errors import ProviderError
from .base import Provider, ToolCall, ToolResult, ToolSpec, Turn


def _anthropic(model_id: str, max_tokens: int, client: Any | None) -> Provider:
    from .anthropic_provider import AnthropicProvider

    return AnthropicProvider(model_id, max_tokens, client=client)


def _openai(model_id: str, max_tokens: int, client: Any | None) -> Provider:
    from .openai_provider import OpenAIProvider

    return OpenAIProvider(model_id, max_tokens, client=client)


PROVIDERS: dict[str, Callable[[str, int, Any | None], Provider]] = {
    "anthropic": _anthropic,
    "openai": _openai,
}


def make_provider(model: str, max_tokens: int, client: Any | None = None) -> Provider:
    """`model` is `provider/model-id` as resolved by config.resolve_model."""
    if "/" not in model:
        raise ProviderError(f"model must be 'provider/model-id', got {model!r}")
    vendor, model_id = model.split("/", 1)
    factory = PROVIDERS.get(vendor)
    if factory is None:
        raise ProviderError(f"unknown provider {vendor!r}; known: {sorted(PROVIDERS)}")
    return factory(model_id, max_tokens, client)


__all__ = [
    "PROVIDERS",
    "Provider",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "Turn",
    "make_provider",
]
