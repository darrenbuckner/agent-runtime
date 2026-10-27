"""Path guard and protected-file guard. The v1 template let modify_file
rewrite agent.py and config.yaml; these tests pin that closed."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_runtime.errors import ProtectedPathError, WorkspaceError
from agent_runtime.workspace import READ_LIMIT, Workspace


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "/etc/passwd", "../outside.md", "a/../../b", "./MISSION.md", "a/./b", "~/x", "a\\b", ".git/config", ".git"],
)
def test_resolve_rejects_unsafe_paths(ws: Workspace, bad: str):
    with pytest.raises(WorkspaceError):
        ws.resolve(bad)


def test_resolve_rejects_symlink_escape(ws: Workspace, tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("nope")
    os.symlink(outside, ws.root / "link")
    with pytest.raises(WorkspaceError):
        ws.resolve("link/secret.txt")


@pytest.mark.parametrize(
    "path",
    ["MISSION.md", "config.yaml", "requirements.txt", "log/cycle-log.md", ".github/workflows/agent.yml", ".github", "webhook/worker.js"],
)
def test_protected_paths_cannot_be_modified_or_deleted(ws: Workspace, path: str):
    with pytest.raises(ProtectedPathError):
        ws.modify(path, "x")
    with pytest.raises(ProtectedPathError):
        ws.delete(path)
    with pytest.raises(ProtectedPathError):
        ws.create(path, "x")


def test_protected_prefix_does_not_overmatch(ws: Workspace):
    # "log/cycle-log.md" is protected; "log/cycle-log.md.bak" and "logs/x" are not.
    ws.create("log/cycle-log.md.bak", "ok")
    ws.create("logs/x.md", "ok")
    ws.create("MISSION.md.draft", "ok")


def test_extra_protected_paths_from_config(repo: Path):
    ws = Workspace(repo, ("MISSION.md", "data/"))
    with pytest.raises(ProtectedPathError):
        ws.create("data/x.json", "{}")
    ws.create("other/x.json", "{}")


def test_read_optional_distinguishes_absence_from_failure(ws: Workspace):
    assert ws.read_optional("missing.md") is None
    assert ws.read_optional("notes.md") == "hello\n"
    with pytest.raises(WorkspaceError):
        ws.read_optional("inbound")  # a directory
    (ws.root / "bin.dat").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(WorkspaceError):
        ws.read_optional("bin.dat")


def test_read_for_model_truncates_with_marker(ws: Workspace):
    ws.create("big.md", "x" * (READ_LIMIT + 10))
    out = ws.read_for_model("big.md")
    assert out.startswith("x" * READ_LIMIT)
    assert "truncated" in out


def test_create_modify_delete_semantics(ws: Workspace):
    with pytest.raises(WorkspaceError):
        ws.modify("new.md", "x")
    ws.create("new.md", "x")
    with pytest.raises(WorkspaceError):
        ws.create("new.md", "y")
    ws.modify("new.md", "y")
    assert ws.read_required("new.md") == "y"
    ws.delete("new.md")
    with pytest.raises(WorkspaceError):
        ws.delete("new.md")


def test_manifest_excludes_git_and_includes_github(ws: Workspace):
    (ws.root / ".git").mkdir()
    (ws.root / ".git" / "HEAD").write_text("ref")
    paths = [e.path for e in ws.manifest()]
    assert ".git/HEAD" not in paths
    assert ".github/workflows/agent.yml" in paths
    assert "notes.md" in paths


def test_list_dir_absent_is_empty_and_skips_gitkeep(ws: Workspace):
    assert ws.list_dir("nope") == []
    assert ws.list_dir("inbound") == []
    ws.create("inbound/a.md", "x")
    assert ws.list_dir("inbound") == ["inbound/a.md"]
    with pytest.raises(WorkspaceError):
        ws.list_dir("notes.md")
