from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_runtime.config import HARD_PROTECTED, Config, EmailConfig
from agent_runtime.workspace import Workspace

CONFIG_YAML = """\
name: "Test Agent"
model: "anthropic/claude-sonnet-5-5"
max_tokens: 4000
max_tool_rounds: 5
max_log_entries: 3
capabilities:
  email:
    enabled: false
"""


def seed_repo(root: Path, config: str = CONFIG_YAML) -> None:
    (root / "MISSION.md").write_text("# Mission\n\nDo the thing.\n")
    (root / "config.yaml").write_text(config)
    (root / "requirements.txt").write_text("agent-runtime\n")
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / ".github" / "workflows" / "agent.yml").write_text("name: x\n")
    for d in ("inbound", "outbound", "log"):
        (root / d).mkdir()
        (root / d / ".gitkeep").write_text("")
    (root / "notes.md").write_text("hello\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    seed_repo(tmp_path)
    return tmp_path


@pytest.fixture
def ws(repo: Path) -> Workspace:
    return Workspace(repo, HARD_PROTECTED)


@pytest.fixture
def cfg() -> Config:
    return Config(name="Test Agent", model="anthropic/claude-sonnet-5-5", max_tool_rounds=5, max_log_entries=3)


@pytest.fixture
def email_cfg() -> Config:
    return Config(
        name="Test Agent",
        model="anthropic/claude-sonnet-5-5",
        max_tool_rounds=5,
        email=EmailConfig(enabled=True, from_address="a@x.io", to_address="b@y.io", review_required=True),
    )


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, Path]:
    """A bare remote and a working clone with one commit on main."""
    remote = tmp_path / "remote.git"
    work = tmp_path / "work"
    git(tmp_path, "init", "--bare", "-b", "main", str(remote))
    git(tmp_path, "clone", "-q", str(remote), str(work))
    git(work, "config", "user.name", "seed")
    git(work, "config", "user.email", "seed@example.com")
    seed_repo(work)
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "seed")
    git(work, "push", "-q", "-u", "origin", "main")
    return remote, work
