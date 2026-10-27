"""commit_and_push against a real bare remote, including the race with a
concurrent writer (the inbound webhook commits to the same branch)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agent_runtime.errors import GitError
from agent_runtime.git import commit_and_push, current_branch

from .conftest import git


def remote_log(remote: Path) -> list[str]:
    return git(remote, "log", "--format=%s", "main").splitlines()


def test_nothing_to_commit_is_a_noop(git_repo):
    remote, work = git_repo
    assert commit_and_push(work, "noop", branch="main") is False
    assert remote_log(remote) == ["seed"]


def test_commit_and_push_lands(git_repo):
    remote, work = git_repo
    (work / "new.md").write_text("x")
    assert commit_and_push(work, "Cycle 1: new", branch="main") is True
    assert remote_log(remote)[0] == "Cycle 1: new"
    author = git(work, "log", "-1", "--format=%an <%ae>")
    assert "github-actions[bot]" in author


def test_push_rebases_over_concurrent_writer(git_repo, tmp_path):
    remote, work = git_repo
    # A second clone (the webhook) pushes first.
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(remote), str(other))
    git(other, "config", "user.name", "worker")
    git(other, "config", "user.email", "w@example.com")
    (other / "inbound" / "mail.md").write_text("incoming")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "Inbound email")
    git(other, "push", "-q", "origin", "main")

    (work / "out.md").write_text("agent work")
    assert commit_and_push(work, "Cycle 2: work", branch="main") is True
    assert remote_log(remote)[:2] == ["Cycle 2: work", "Inbound email"]
    assert (work / "inbound" / "mail.md").exists()


def test_conflicting_rebase_raises_after_abort(git_repo, tmp_path):
    remote, work = git_repo
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(remote), str(other))
    git(other, "config", "user.name", "w")
    git(other, "config", "user.email", "w@example.com")
    (other / "notes.md").write_text("theirs\n")
    git(other, "add", "-A")
    git(other, "commit", "-q", "-m", "theirs")
    git(other, "push", "-q", "origin", "main")

    (work / "notes.md").write_text("ours\n")
    with pytest.raises(GitError, match="conflicted"):
        commit_and_push(work, "ours", branch="main")
    # rebase was aborted: the tree is clean, our commit still exists locally
    assert git(work, "status", "--porcelain") == ""
    assert remote_log(remote)[0] == "theirs"


def test_current_branch_prefers_env(git_repo, monkeypatch):
    _, work = git_repo
    monkeypatch.delenv("GITHUB_REF_NAME", raising=False)
    assert current_branch(work) == "main"
    monkeypatch.setenv("GITHUB_REF_NAME", "feature")
    assert current_branch(work) == "feature"
    monkeypatch.delenv("GITHUB_REF_NAME")
    sha = git(work, "rev-parse", "HEAD").strip()
    subprocess.run(["git", "checkout", "-q", sha], cwd=work, check=True)
    with pytest.raises(GitError, match="detached"):
        current_branch(work)
