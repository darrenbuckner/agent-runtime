"""The agent's workspace is the checked-out repo on disk. All reads and
writes go through here so the path guard and the protected-file guard
are the only way in.

Failure direction: a missing file is the one case that returns None
(read_optional); any other OSError raises. Writes raise or succeed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import ProtectedPathError, WorkspaceError

READ_LIMIT = 15_000  # characters returned to the model per read_file
MANIFEST_EXCLUDE = (".git/",)


@dataclass(frozen=True)
class ManifestEntry:
    path: str
    size: int


class Workspace:
    def __init__(self, root: Path, protected: tuple[str, ...]):
        self.root = root.resolve()
        if not self.root.is_dir():
            raise WorkspaceError(f"workspace root is not a directory: {root}")
        self.protected = tuple(protected)

    # --- path guard -------------------------------------------------------

    def resolve(self, rel: str) -> Path:
        """Turn a model-supplied path into an absolute path inside the
        workspace, or raise. Rejects absolute paths, parent references,
        backslashes, the .git directory, and anything that resolves
        (through a symlink) outside the root."""
        if not isinstance(rel, str) or not rel.strip():
            raise WorkspaceError("path is empty")
        rel = rel.strip()
        if "\\" in rel or "\x00" in rel:
            raise WorkspaceError(f"path contains an illegal character: {rel!r}")
        pure = PurePosixPath(rel)
        if pure.is_absolute() or rel.startswith("~"):
            raise WorkspaceError(f"path must be relative to the repo root: {rel!r}")
        if any(seg in ("..", ".", "") for seg in rel.split("/")):
            raise WorkspaceError(f"path may not contain '.', '..' or empty segments: {rel!r}")
        if pure.parts and pure.parts[0] == ".git":
            raise WorkspaceError("the .git directory is off limits")
        candidate = (self.root / pure).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError:
            raise WorkspaceError(f"path escapes the workspace: {rel!r}")
        return candidate

    def is_protected(self, rel: str) -> bool:
        normalized = PurePosixPath(rel.strip()).as_posix()
        for rule in self.protected:
            if rule.endswith("/"):
                if normalized == rule.rstrip("/") or normalized.startswith(rule):
                    return True
            elif normalized == rule:
                return True
        return False

    def _guard_write(self, rel: str) -> Path:
        if self.is_protected(rel):
            raise ProtectedPathError(f"{rel} is protected; the agent cannot modify or delete it")
        return self.resolve(rel)

    # --- reads ------------------------------------------------------------

    def exists(self, rel: str) -> bool:
        return self.resolve(rel).is_file()

    def read_optional(self, rel: str) -> str | None:
        """Return the file's text, or None only if it does not exist."""
        path = self.resolve(rel)
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except IsADirectoryError:
            raise WorkspaceError(f"{rel} is a directory, not a file")
        except UnicodeDecodeError:
            raise WorkspaceError(f"{rel} is not UTF-8 text")
        except OSError as exc:
            raise WorkspaceError(f"could not read {rel}: {exc}") from exc

    def read_required(self, rel: str) -> str:
        text = self.read_optional(rel)
        if text is None:
            raise WorkspaceError(f"{rel} does not exist")
        return text

    def read_for_model(self, rel: str) -> str:
        """read_file tool: content, truncated past READ_LIMIT with a
        marker so the model knows it did not see everything."""
        text = self.read_optional(rel)
        if text is None:
            raise WorkspaceError(f"file not found: {rel}")
        if len(text) > READ_LIMIT:
            return text[:READ_LIMIT] + f"\n\n... (truncated: file is {len(text):,} characters)"
        return text

    def list_dir(self, rel: str) -> list[str]:
        """Relative paths of regular files directly inside `rel`, sorted,
        .gitkeep excluded. A missing directory is an empty listing; that
        is genuine absence, not failure."""
        path = self.resolve(rel)
        if not path.exists():
            return []
        if not path.is_dir():
            raise WorkspaceError(f"{rel} is not a directory")
        return sorted(
            f"{rel.rstrip('/')}/{p.name}"
            for p in path.iterdir()
            if p.is_file() and p.name != ".gitkeep"
        )

    def manifest(self) -> list[ManifestEntry]:
        entries: list[ManifestEntry] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            rel_dir = Path(dirpath).relative_to(self.root).as_posix()
            prefix = "" if rel_dir == "." else rel_dir + "/"
            dirnames[:] = sorted(d for d in dirnames if not (prefix + d + "/").startswith(MANIFEST_EXCLUDE))
            for name in sorted(filenames):
                rel = prefix + name
                if rel.startswith(MANIFEST_EXCLUDE):
                    continue
                entries.append(ManifestEntry(rel, (self.root / rel).stat().st_size))
        return sorted(entries, key=lambda e: e.path)

    # --- writes -----------------------------------------------------------

    def write(self, rel: str, content: str) -> None:
        path = self._guard_write(rel)
        if path.is_dir():
            raise WorkspaceError(f"{rel} is a directory")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise WorkspaceError(f"could not write {rel}: {exc}") from exc

    def create(self, rel: str, content: str) -> None:
        path = self._guard_write(rel)
        if path.exists():
            raise WorkspaceError(f"file already exists: {rel} (use modify_file)")
        self.write(rel, content)

    def modify(self, rel: str, content: str) -> None:
        path = self._guard_write(rel)
        if not path.is_file():
            raise WorkspaceError(f"file not found: {rel} (use create_file)")
        self.write(rel, content)

    def delete(self, rel: str) -> None:
        path = self._guard_write(rel)
        if not path.is_file():
            raise WorkspaceError(f"file not found: {rel}")
        try:
            path.unlink()
        except OSError as exc:
            raise WorkspaceError(f"could not delete {rel}: {exc}") from exc

    def append(self, rel: str, content: str) -> None:
        """Append to a file the runtime owns (bypasses the protected guard
        on purpose: this is how the cycle log is written)."""
        path = self.resolve(rel)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(content)
        except OSError as exc:
            raise WorkspaceError(f"could not append to {rel}: {exc}") from exc
