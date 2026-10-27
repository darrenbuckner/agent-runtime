"""One commit per cycle, pushed to the branch the run was dispatched on.

Push is retried with a rebase: the inbound webhook worker commits to
the same branch at any time, so a plain push can lose the race. Any
failure after the retries raises; a cycle whose work did not land is a
red run, not a green one.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .errors import GitError

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
PUSH_ATTEMPTS = 4


def _run(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def current_branch(root: Path) -> str:
    """GITHUB_REF_NAME under Actions (set for schedule and dispatch); the
    checked-out branch otherwise. A detached HEAD with no ref name is a
    failure, because there is nowhere to push."""
    env_branch = os.environ.get("GITHUB_REF_NAME")
    if env_branch:
        return env_branch
    name = _run(root, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if not name or name == "HEAD":
        raise GitError("detached HEAD and GITHUB_REF_NAME unset: no branch to push to")
    return name


def has_changes(root: Path) -> bool:
    return bool(_run(root, "status", "--porcelain").strip())


def commit_and_push(
    root: Path, message: str, branch: str | None = None, remote: str = "origin", push: bool = True
) -> bool:
    """Stage everything, commit if there is anything to commit, push with
    rebase-retry. Returns True if a commit was made, False if there was
    nothing to commit (a genuine no-op, not a failure). push=False
    commits locally only (local experiments)."""
    _run(root, "add", "-A")
    if not has_changes(root):
        return False
    _run(root, "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}", "commit", "-q", "-m", message)
    if not push:
        return True
    branch = branch or current_branch(root)

    last_error = ""
    for attempt in range(1, PUSH_ATTEMPTS + 1):
        try:
            _run(root, "push", remote, f"HEAD:refs/heads/{branch}")
            return True
        except GitError as exc:
            last_error = str(exc)
        if attempt == PUSH_ATTEMPTS:
            break
        _run(root, "fetch", remote, branch)
        try:
            _run(
                root,
                "-c", f"user.name={BOT_NAME}", "-c", f"user.email={BOT_EMAIL}",
                "rebase", f"{remote}/{branch}",
            )
        except GitError as exc:
            _run(root, "rebase", "--abort")
            raise GitError(f"rebase onto {remote}/{branch} conflicted; aborted: {exc}") from exc
    raise GitError(f"push to {branch} failed after {PUSH_ATTEMPTS} attempts: {last_error}")
