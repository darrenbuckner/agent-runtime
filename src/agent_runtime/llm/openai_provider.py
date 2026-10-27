"""OpenAI Responses API adapter.

Status: exercised in the test suite against a fake client. Not yet run
against a live key. The wire shapes (function tools, function_call
output items, function_call_output inputs, response.status and
incomplete_details) follow the openai>=1.60 SDK types.
"""

from __future__ import annotations

import json
import os
from typing import Any

from ..errors import ProviderError
from .base import ToolCall, ToolResult, ToolSpec, Turn


def _dump(item: Any) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump(exclude_none=True)
    if isinstance(item, dict):
        return item
    raise ProviderError(f"cannot serialise output item of type {type(item).__name__}")


class OpenAIProvider:
    name = "openai"

    def __init__(self, model_id: str, max_tokens: int, client: Any | None = None):
        self.model_id = model_id
        self.max_tokens = max_tokens
        if client is None:
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ProviderError("OPENAI_API_KEY is not set")
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ProviderError(
                    "the openai package is not installed; add `agent-runtime[openai]`"
                ) from exc
            client = OpenAI(api_key=api_key)
        self.client = client
        self.instructions = ""
        self.tools: list[dict[str, Any]] = []
        self.input: list[dict[str, Any]] = []

    def start(self, system: str, tools: list[ToolSpec], user_message: str) -> None:
        self.instructions = system
        self.tools = [
            {
                "type": "function",
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
                "strict": False,
            }
            for t in tools
        ]
        self.input = [{"role": "user", "content": user_message}]

    def step(self) -> Turn:
        response = self.client.responses.create(
            model=self.model_id,
            instructions=self.instructions,
            input=list(self.input),
            tools=self.tools,
            max_output_tokens=self.max_tokens,
        )
        output = list(getattr(response, "output", []) or [])
        self.input.extend(_dump(item) for item in output)

        text: list[str] = []
        calls: list[ToolCall] = []
        for item in output:
            kind = getattr(item, "type", None)
            if kind == "message":
                for part in getattr(item, "content", []) or []:
                    if getattr(part, "type", None) == "output_text" and getattr(part, "text", ""):
                        text.append(part.text)
            elif kind == "function_call":
                try:
                    args = json.loads(item.arguments or "{}")
                except json.JSONDecodeError as exc:
                    raise ProviderError(
                        f"tool call {item.name} carried non-JSON arguments: {exc}"
                    ) from exc
                if not isinstance(args, dict):
                    raise ProviderError(f"tool call {item.name} arguments are not an object")
                calls.append(ToolCall(id=item.call_id, name=item.name, input=args))

        status = getattr(response, "status", None)
        if status == "completed" and calls:
            return Turn(kind="tool_calls", text=text, tool_calls=calls)
        if status == "completed":
            return Turn(kind="end", text=text)

        incomplete = getattr(response, "incomplete_details", None)
        usage = getattr(response, "usage", None)
        detail = (
            f"status={status!r} model={self.model_id} "
            f"output_tokens={getattr(usage, 'output_tokens', '?')} max_output_tokens={self.max_tokens} "
            f"items={[getattr(i, 'type', '?') for i in output]} "
            f"incomplete_details={incomplete!r} error={getattr(response, 'error', None)!r}"
        )
        return Turn(kind="stopped", text=text, detail=detail)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.input.extend(
            {"type": "function_call_output", "call_id": r.call_id, "output": r.content}
            for r in results
        )
