"""The cycle log: the agent's only memory between runs.

Written by append only. v1 read the whole file and rewrote it, so a
transient read failure replaced the entire log with one entry; here a
read failure raises and nothing is written.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .workspace import Workspace

LOG_PATH = "log/cycle-log.md"
HEADER = "# Cycle Log\n"


@dataclass
class CycleEntry:
    cycle_id: str
    summary: str
    thinking: str = ""
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    next_notes: str = ""

    def render(self) -> str:
        actions = "\n".join(f"- {a}" for a in self.actions) if self.actions else "- (no actions)"
        errors = ""
        if self.errors:
            errors = "**Errors**:\n" + "\n".join(f"- {e}" for e in self.errors) + "\n\n"
        return (
            f"\n\n## {self.cycle_id}\n"
            f"**Summary**: {self.summary}\n\n"
            f"**Thinking**: {self.thinking}\n\n"
            f"**Actions**:\n{actions}\n\n"
            f"{errors}"
            f"**Next cycle**: {self.next_notes}\n"
        )


def append_entry(ws: Workspace, entry: CycleEntry) -> None:
    existing = ws.read_optional(LOG_PATH)
    if existing is None:
        ws.append(LOG_PATH, HEADER)
    ws.append(LOG_PATH, entry.render())


def trimmed_view(ws: Workspace, max_entries: int) -> str:
    """The last `max_entries` entries, for the prompt. Absence of a log is
    a real state (first cycle) and says so."""
    raw = ws.read_optional(LOG_PATH)
    if raw is None:
        return HEADER + "\n(No previous cycles)\n"
    parts = raw.split("\n## ")
    header, entries = parts[0], parts[1:]
    if len(entries) <= max_entries:
        return raw
    kept = entries[-max_entries:]
    trimmed = len(entries) - max_entries
    return header.rstrip() + f"\n\n*(oldest {trimmed} entries trimmed)*\n\n## " + "\n## ".join(kept)
