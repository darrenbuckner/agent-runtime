from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.config import DEFAULT_MODEL, HARD_PROTECTED, load_config, resolve_model
from agent_runtime.errors import ConfigError


def test_model_precedence():
    assert resolve_model("openai/gpt-x", {}) == "openai/gpt-x"
    assert resolve_model("openai/gpt-x", {"CLAUDE_MODEL": "claude-a"}) == "anthropic/claude-a"
    assert resolve_model("openai/gpt-x", {"CLAUDE_MODEL": "claude-a", "AGENT_MODEL": "openai/g"}) == "openai/g"
    assert resolve_model(None, {}) == DEFAULT_MODEL
    assert resolve_model("claude-b", {}) == "anthropic/claude-b"
    assert resolve_model("", {}) == DEFAULT_MODEL  # empty means unset


@pytest.mark.parametrize("bad", ["   ", "/x", "anthropic/"])
def test_model_rejects_malformed(bad: str):
    with pytest.raises(ConfigError):
        resolve_model(bad, {})


def test_missing_config_is_an_error_not_a_default(tmp_path: Path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "config.yaml", {})


def test_load_config_validates(repo: Path):
    cfg = load_config(repo / "config.yaml", {})
    assert cfg.name == "Test Agent"
    assert cfg.provider == "anthropic"
    assert cfg.model_id == "claude-sonnet-5-5"
    assert cfg.protected_paths == HARD_PROTECTED

    (repo / "config.yaml").write_text("name: ''\n")
    with pytest.raises(ConfigError):
        load_config(repo / "config.yaml", {})

    (repo / "config.yaml").write_text("name: a\nmax_tool_rounds: 0\n")
    with pytest.raises(ConfigError):
        load_config(repo / "config.yaml", {})

    (repo / "config.yaml").write_text("name: a\ncapabilities:\n  email:\n    enabled: true\n")
    with pytest.raises(ConfigError):
        load_config(repo / "config.yaml", {})

    (repo / "config.yaml").write_text("name: a\nprotected_paths: [data/]\n")
    cfg = load_config(repo / "config.yaml", {})
    assert cfg.protected_paths == HARD_PROTECTED + ("data/",)
