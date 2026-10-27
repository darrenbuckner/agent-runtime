"""The tool loop against a scripted provider. Pins the failure
directions: every exit leaves a cycle-log entry, inbound archiving is
scoped to what the prompt saw, one email per cycle is enforced."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from agent_runtime.config import Config, EmailConfig
from agent_runtime.cyclelog import LOG_PATH
from agent_runtime.errors import CycleStopped
from agent_runtime.llm import ToolCall, ToolResult, ToolSpec, Turn
from agent_runtime.loop import run_cycle
from agent_runtime.tools import ToolContext, execute
from agent_runtime.workspace import Workspace

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


class ScriptedProvider:
    name = "fake"
    model_id = "fake"

    def __init__(self, turns: list[Turn]):
        self.turns = list(turns)
        self.system = ""
        self.tools: list[ToolSpec] = []
        self.results: list[list[ToolResult]] = []

    def start(self, system: str, tools: list[ToolSpec], user_message: str) -> None:
        self.system, self.tools = system, tools

    def step(self) -> Turn:
        if not self.turns:
            raise AssertionError("provider asked for more turns than scripted")
        return self.turns.pop(0)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        self.results.append(results)


def finish(summary="done", notes="next"):
    return Turn("tool_calls", tool_calls=[ToolCall("f1", "finish_cycle", {"summary": summary, "thinking": "t", "next_cycle_notes": notes})])


def calls(*cs: tuple[str, dict]):
    return Turn("tool_calls", tool_calls=[ToolCall(f"c{i}", n, i_) for i, (n, i_) in enumerate(cs)])


def run(repo: Path, turns: list[Turn], now=NOW, **kw):
    prov = ScriptedProvider(turns)
    entry = run_cycle(repo, provider_factory=lambda cfg: prov, now=now, push=False, **kw)
    return entry, prov


def log_text(repo: Path) -> str:
    return (repo / LOG_PATH).read_text()


def test_happy_path_writes_file_and_log(git_repo):
    _, repo = git_repo
    entry, prov = run(repo, [calls(("create_file", {"path": "out/a.md", "content": "hi", "reason": "make a"})), finish("made a")])
    assert (repo / "out/a.md").read_text() == "hi"
    assert entry.actions == ["Created out/a.md: make a"]
    assert "## 2026-10-27-1500" in log_text(repo)
    assert "made a" in log_text(repo)
    assert "Next cycle**: next" in log_text(repo)
    # one commit for the cycle
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True).stdout
    assert log.splitlines()[0].endswith("Cycle 2026-10-27-1500: made a")
    assert "Protected paths you cannot modify or delete" in prov.system


def test_protected_write_returns_error_and_is_logged(git_repo):
    _, repo = git_repo
    entry, prov = run(repo, [calls(("modify_file", {"path": "config.yaml", "content": "x", "reason": "r"})), finish()])
    assert prov.results[0][0].content.startswith("Error:")
    assert (repo / "config.yaml").read_text().startswith("name:")
    assert entry.actions == []
    assert entry.errors and "protected" in entry.errors[0]
    assert "**Errors**" in log_text(repo)


def test_stopped_turn_logs_failed_and_raises(git_repo):
    _, repo = git_repo
    with pytest.raises(CycleStopped, match="refusal"):
        run(repo, [calls(("create_file", {"path": "x.md", "content": "1", "reason": "r"})), Turn("stopped", detail="stop_reason='refusal'")])
    text = log_text(repo)
    assert "FAILED: CycleStopped" in text
    assert "Created x.md" in text  # actions before the stop are preserved
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True).stdout
    assert "FAILED" in log.splitlines()[0]


def test_exhausted_rounds_logs_failed_and_raises(git_repo):
    _, repo = git_repo
    turns = [calls(("read_file", {"path": "notes.md"})) for _ in range(5)]
    with pytest.raises(CycleStopped, match="exhausted"):
        run(repo, turns)
    assert "FAILED" in log_text(repo)


def test_end_without_finish_logs_and_does_not_raise(git_repo):
    _, repo = git_repo
    entry, _ = run(repo, [Turn("end", text=["bye"])])
    assert "without finish_cycle" in entry.summary
    assert "without finish_cycle" in log_text(repo)


def test_log_trim_keeps_last_n_entries(git_repo):
    _, repo = git_repo
    for i in range(5):
        now = NOW.replace(minute=i)
        prov = ScriptedProvider([finish(f"s{i}")])
        run_cycle(repo, provider_factory=lambda cfg, p=prov: p, now=now, push=False)
    prov = ScriptedProvider([finish("last")])
    run_cycle(repo, provider_factory=lambda cfg: prov, now=NOW.replace(minute=9), push=False)
    assert "oldest 2 entries trimmed" in prov.system
    assert "s0" not in prov.system and "s4" in prov.system
    assert log_text(repo).count("\n## ") == 6  # the file itself keeps everything


EMAIL_CONFIG = """\
name: "Mail Agent"
max_tool_rounds: 5
capabilities:
  email:
    enabled: true
    from_address: "agent@x.io"
    to_address: "person@y.io"
    review_required: true
"""


class FakeGitHub:
    def __init__(self, approved=None):
        self.issues = []
        self.comments = []
        self.closed = []
        self.approved = approved or []

    def ensure_label(self, *a):
        pass

    def create_issue(self, title, body, labels):
        self.issues.append((title, body, labels))
        return 40 + len(self.issues)

    def list_open_issues(self, labels):
        return self.approved

    def comment(self, number, body):
        self.comments.append((number, body))

    def close(self, number):
        self.closed.append(number)


class FakeResend:
    def __init__(self, ok=True):
        self.ok = ok
        self.sent = []

    def send(self, cfg, subject, body):
        from agent_runtime.email import SendOutcome

        self.sent.append((cfg.to_address, subject, body))
        return SendOutcome(self.ok, "Email sent" if self.ok else "Resend returned 500: boom")


def test_inbound_archives_only_what_the_prompt_saw(git_repo):
    _, repo = git_repo
    (repo / "config.yaml").write_text(EMAIL_CONFIG)
    (repo / "inbound" / "a.md").write_text("first")
    late = repo / "inbound" / "b.md"

    class LateMailProvider(ScriptedProvider):
        def step(self):
            late.write_text("arrived mid-cycle")  # lands after the prompt was built
            return super().step()

    prov = LateMailProvider([finish()])
    run_cycle(repo, provider_factory=lambda cfg: prov, now=NOW, push=False, gh=FakeGitHub(), resend=FakeResend())
    assert "first" in prov.system
    assert not (repo / "inbound" / "a.md").exists()
    assert late.exists(), "mail that arrived mid-cycle must survive to the next cycle"


def test_review_gate_files_issue_once_per_cycle_and_records_outbound(git_repo):
    _, repo = git_repo
    (repo / "config.yaml").write_text(EMAIL_CONFIG)
    gh = FakeGitHub()
    entry, prov = run(
        repo,
        [calls(("send_email", {"subject": "Hi", "body": "Body"}), ("send_email", {"subject": "Again", "body": "B2"})), finish()],
        gh=gh, resend=FakeResend(),
    )
    assert len(gh.issues) == 1
    assert "person@y.io" in gh.issues[0][1]
    assert prov.results[0][1].content.startswith("Error: one email per cycle")
    outbound = list((repo / "outbound").glob("*.md"))
    assert len(outbound) == 1 and "issue #41" in outbound[0].read_text()
    assert entry.actions == ["Drafted email as issue #41: Hi"]


def test_approved_send_closes_only_on_success(git_repo):
    _, repo = git_repo
    (repo / "config.yaml").write_text(EMAIL_CONFIG)
    from agent_runtime.email import draft_issue_body

    approved = [
        {"number": 7, "body": draft_issue_body("S1", "B1", "evil@z.io")},  # body says evil@, config wins
        {"number": 8, "body": "hand-edited garbage"},
    ]
    gh = FakeGitHub(approved=approved)
    resend = FakeResend(ok=True)
    entry, _ = run(repo, [finish()], gh=gh, resend=resend)
    assert resend.sent == [("person@y.io", "S1", "B1")]
    assert gh.closed == [7]
    assert any(n == 8 and "Could not send" in b for n, b in gh.comments)
    assert any(a.startswith("Sent approved email #7") for a in entry.actions)
    assert any("#8" in e for e in entry.errors)

    gh = FakeGitHub(approved=approved[:1])
    resend = FakeResend(ok=False)
    entry, _ = run(repo, [finish()], gh=gh, resend=resend, now=NOW.replace(minute=1))
    assert gh.closed == []
    assert any(n == 7 and "leaving open" in b for n, b in gh.comments)
    assert any("500" in e for e in entry.errors)


def test_direct_send_failure_is_an_error_result_not_an_action(repo: Path):
    cfg = Config(name="a", model="anthropic/x", email=EmailConfig(enabled=True, from_address="a@x", to_address="b@y", review_required=False))
    ctx = ToolContext(cfg=cfg, ws=Workspace(repo, cfg.protected_paths), cycle_id="c1", resend=FakeResend(ok=False))
    with pytest.raises(Exception, match="500"):
        execute(ctx, "send_email", {"subject": "s", "body": "b"})
    assert ctx.actions == [] and ctx.emails_sent_this_cycle == 0
    assert list((repo / "outbound").glob("*.md")) == []
