"""Anthropic Messages API adapter.

Keeps the v1 behaviours that were paid for: top-level automatic prompt
caching, and `stop_details` surfaced on a refusal so the cycle log says
why the turn stopped (2026-10-01 incident).
"""

from __future__ import annotations

import os
from typing import Any

from ..errors import ProviderError
from .base import ToolCall, ToolResult, ToolSpec, Turn


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model_id: str, max_tokens: int, client: Any | None = None):
        self.model_id = model_id
        self.max_tokens = max_tokens
        if client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ProviderError("ANTHROPIC_API_KEY is not set")
            from anthropic import Anthropic

            client = Anthropic(api_key=api_key)
        self.client = client
        self.system = ""
        self.tools: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    def start(self, system: str, tools: list[ToolSpec], user_message: str) -> None:
        self.system = system
        self.tools = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        self.messages = [{"role": "user", "content": user_message}]

    def step(self) -> Turn:
        response = self.client.messages.create(
            model=self.model_id,
            max_tokens=self.max_tokens,
            system=self.system,
            tools=self.tools,
            messages=list(self.messages),
            cache_control={"type": "ephemeral"},
        )
        self.messages.append({"role": "assistant", "content": response.content})

        text = [b.text for b in response.content if getattr(b, "type", "") == "text" and b.text]
        calls = [
            ToolCall(id=b.id, name=b.name, input=dict(b.input))
            for b in response.content
            if getattr(b, "type", "") == "tool_use"
        ]
        stop = getattr(response, "stop_reason", None)

        if stop == "tool_use" and calls:
            return Turn(kind="tool_calls", text=text, tool_calls=calls)
        if stop == "end_turn":
            return Turn(kind="end", text=text)

        usage = getattr(response, "usage", None)
        detail = (
            f"stop_reason={stop!r} model={self.model_id} "
            f"output_tokens={getattr(usage, 'output_tokens', '?')} max_tokens={self.max_tokens} "
            f"blocks={[getattr(b, 'type', '?') for b in response.content]} "
            f"stop_details={getattr(response, 'stop_details', None)!r}"
        )
        return Turn(kind="stopped", text=text, detail=detail)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r.call_id, "content": r.content}
                    for r in results
                ],
            }
        )
