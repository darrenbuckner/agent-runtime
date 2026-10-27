"""Provider-neutral types. The loop in loop.py speaks only these; each
adapter translates to and from its vendor's wire format and keeps its
own transcript.

A Turn's `kind` is one of:
  "tool_calls"  the model wants tools run; `tool_calls` is non-empty
  "end"         the model finished its turn with no tool calls
  "stopped"     anything else (refusal, max_tokens, incomplete, error);
                `detail` says what, and the loop fails loudly with it
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

TurnKind = Literal["tool_calls", "end", "stopped"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    content: str


@dataclass
class Turn:
    kind: TurnKind
    text: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    detail: str = ""


class Provider(Protocol):
    """One conversation with one model. Adapters are stateful: `start`
    sets the prefix, `step` runs one model call against the transcript
    so far, `add_tool_results` extends it."""

    name: str
    model_id: str

    def start(self, system: str, tools: list[ToolSpec], user_message: str) -> None: ...

    def step(self) -> Turn: ...

    def add_tool_results(self, results: list[ToolResult]) -> None: ...
