"""Adapter translation against fake SDK clients. The point is that both
adapters produce the same Turn shapes for the same situations, so the
loop never sees a vendor."""

from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from agent_runtime.errors import ProviderError
from agent_runtime.llm import ToolResult, ToolSpec, make_provider
from agent_runtime.llm.anthropic_provider import AnthropicProvider
from agent_runtime.llm.openai_provider import OpenAIProvider

TOOLS = [ToolSpec("read_file", "Read.", {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]})]


class Recorder:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def anthropic_client(*responses):
    rec = Recorder(responses)
    return NS(messages=NS(create=rec)), rec


def openai_client(*responses):
    rec = Recorder(responses)
    return NS(responses=NS(create=rec)), rec


class Block(NS):
    pass


def test_anthropic_tool_call_then_end():
    client, rec = anthropic_client(
        NS(stop_reason="tool_use", content=[Block(type="text", text="thinking"), Block(type="tool_use", id="t1", name="read_file", input={"path": "a"})], usage=NS(output_tokens=5)),
        NS(stop_reason="end_turn", content=[Block(type="text", text="bye")], usage=NS(output_tokens=2)),
    )
    p = AnthropicProvider("claude-x", 100, client=client)
    p.start("sys", TOOLS, "go")
    turn = p.step()
    assert turn.kind == "tool_calls" and turn.text == ["thinking"]
    assert turn.tool_calls[0].name == "read_file" and turn.tool_calls[0].input == {"path": "a"}
    p.add_tool_results([ToolResult("t1", "contents")])
    turn = p.step()
    assert turn.kind == "end" and turn.text == ["bye"]

    first = rec.calls[0]
    assert first["model"] == "claude-x" and first["max_tokens"] == 100 and first["system"] == "sys"
    assert first["tools"][0]["input_schema"] == TOOLS[0].input_schema
    assert first["cache_control"] == {"type": "ephemeral"}
    second = rec.calls[1]
    assert second["messages"][-1] == {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "contents"}]}
    assert second["messages"][-2]["role"] == "assistant"


def test_anthropic_refusal_is_stopped_with_details():
    client, _ = anthropic_client(NS(stop_reason="refusal", content=[], usage=NS(output_tokens=0), stop_details={"category": "x"}))
    p = AnthropicProvider("claude-x", 100, client=client)
    p.start("sys", TOOLS, "go")
    turn = p.step()
    assert turn.kind == "stopped"
    assert "refusal" in turn.detail and "category" in turn.detail


def test_anthropic_max_tokens_is_stopped():
    client, _ = anthropic_client(NS(stop_reason="max_tokens", content=[Block(type="text", text="half")], usage=NS(output_tokens=100)))
    p = AnthropicProvider("claude-x", 100, client=client)
    p.start("sys", TOOLS, "go")
    assert p.step().kind == "stopped"


def test_openai_tool_call_then_end():
    fc = NS(type="function_call", call_id="c1", name="read_file", arguments='{"path": "a"}', model_dump=lambda exclude_none: {"type": "function_call", "call_id": "c1", "name": "read_file", "arguments": '{"path": "a"}'})
    msg = NS(type="message", content=[NS(type="output_text", text="bye")], model_dump=lambda exclude_none: {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "bye"}]})
    client, rec = openai_client(
        NS(status="completed", output=[fc], usage=NS(output_tokens=3)),
        NS(status="completed", output=[msg], usage=NS(output_tokens=1)),
    )
    p = OpenAIProvider("gpt-x", 100, client=client)
    p.start("sys", TOOLS, "go")
    turn = p.step()
    assert turn.kind == "tool_calls" and turn.tool_calls[0].input == {"path": "a"} and turn.tool_calls[0].id == "c1"
    p.add_tool_results([ToolResult("c1", "contents")])
    turn = p.step()
    assert turn.kind == "end" and turn.text == ["bye"]

    first = rec.calls[0]
    assert first["model"] == "gpt-x" and first["instructions"] == "sys" and first["max_output_tokens"] == 100
    assert first["tools"][0] == {"type": "function", "name": "read_file", "description": "Read.", "parameters": TOOLS[0].input_schema, "strict": False}
    second = rec.calls[1]
    assert second["input"][-1] == {"type": "function_call_output", "call_id": "c1", "output": "contents"}
    assert second["input"][-2]["type"] == "function_call"


def test_openai_incomplete_is_stopped_and_bad_json_raises():
    client, _ = openai_client(NS(status="incomplete", output=[], incomplete_details=NS(reason="max_output_tokens"), usage=None, error=None))
    p = OpenAIProvider("gpt-x", 100, client=client)
    p.start("sys", TOOLS, "go")
    turn = p.step()
    assert turn.kind == "stopped" and "max_output_tokens" in turn.detail

    bad = NS(type="function_call", call_id="c1", name="read_file", arguments="{not json", model_dump=lambda exclude_none: {})
    client, _ = openai_client(NS(status="completed", output=[bad], usage=None))
    p = OpenAIProvider("gpt-x", 100, client=client)
    p.start("sys", TOOLS, "go")
    with pytest.raises(ProviderError, match="non-JSON"):
        p.step()


def test_make_provider_dispatches_and_rejects_unknown():
    client, _ = anthropic_client()
    assert isinstance(make_provider("anthropic/claude-x", 10, client=client), AnthropicProvider)
    client, _ = openai_client()
    assert isinstance(make_provider("openai/gpt-x", 10, client=client), OpenAIProvider)
    with pytest.raises(ProviderError, match="unknown provider"):
        make_provider("gemini/x", 10, client=object())
    with pytest.raises(ProviderError):
        make_provider("no-slash", 10, client=object())


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider("x", 1)
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        OpenAIProvider("x", 1)


def test_openai_live_sdk_types_still_match():
    """The adapter targets the SDK's own output-item and param shapes;
    fail here if a newer SDK renames them."""
    openai = pytest.importorskip("openai")
    from openai.types.responses import ResponseFunctionToolCall
    from openai.types.responses.function_tool_param import FunctionToolParam

    fields = set(ResponseFunctionToolCall.model_fields)
    assert {"call_id", "name", "arguments"} <= fields
    assert {"name", "parameters", "strict", "type"} <= set(FunctionToolParam.__annotations__)
    assert hasattr(openai.OpenAI, "responses") or "responses" in dir(openai.OpenAI)
