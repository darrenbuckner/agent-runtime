"""Tool definitions and execution. Every tool either returns a result
string for the model or raises; the loop turns raises into "Error: ..."
results and records them in the cycle log's Errors section.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import Config
from .email import OUTBOUND_DIR, Resend, create_review_issue
from .errors import AgentError
from .github import GitHub
from .llm import ToolSpec
from .workspace import Workspace

FINISH = "finish_cycle"


def _path_schema(extra: dict[str, Any] | None = None, required: list[str] | None = None) -> dict[str, Any]:
    props: dict[str, Any] = {"path": {"type": "string", "description": "File path relative to repo root"}}
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": required or ["path"]}


def build_tool_specs(cfg: Config) -> list[ToolSpec]:
    reason = {"reason": {"type": "string", "description": "Brief reason (used in the commit message)"}}
    content = {"content": {"type": "string", "description": "Full file content"}}
    tools = [
        ToolSpec("read_file", "Read the full contents of a file in the workspace.", _path_schema()),
        ToolSpec(
            "create_file",
            "Create a new file. Fails if it already exists; use modify_file for that.",
            _path_schema({**content, **reason}, ["path", "content", "reason"]),
        ),
        ToolSpec(
            "modify_file",
            "Replace the entire contents of an existing file. Read it first.",
            _path_schema({**content, **reason}, ["path", "content", "reason"]),
        ),
        ToolSpec("delete_file", "Delete a file from the workspace.", _path_schema(reason, ["path", "reason"])),
        ToolSpec(
            FINISH,
            "Call when done. Logs your work and saves notes for your future self. Required every cycle.",
            {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "One sentence: what you did and why"},
                    "thinking": {"type": "string", "description": "2-5 sentences: your reasoning"},
                    "next_cycle_notes": {"type": "string", "description": "Notes for your future self"},
                },
                "required": ["summary", "thinking", "next_cycle_notes"],
            },
        ),
    ]
    if cfg.email.enabled:
        note = (
            " The draft is filed as a GitHub Issue for human review and sends on a later cycle once approved."
            if cfg.email.review_required
            else " The email sends immediately."
        )
        tools.append(
            ToolSpec(
                "send_email",
                f"Send one email to the person you work with.{note} One email per cycle.",
                {
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string", "description": "Email subject line"},
                        "body": {"type": "string", "description": "Email body (plain text)"},
                    },
                    "required": ["subject", "body"],
                },
            )
        )
    return tools


@dataclass
class ToolContext:
    cfg: Config
    ws: Workspace
    cycle_id: str
    gh: GitHub | None = None
    resend: Resend | None = None
    emails_sent_this_cycle: int = 0
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _require_str(tool_input: dict[str, Any], key: str) -> str:
    value = tool_input.get(key)
    if not isinstance(value, str):
        raise AgentError(f"{key} must be a string")
    return value


def execute(ctx: ToolContext, name: str, tool_input: dict[str, Any]) -> str:
    """Run one tool. Raises AgentError (or a subclass) on any failure."""
    ws = ctx.ws
    if name == "read_file":
        return ws.read_for_model(_require_str(tool_input, "path"))

    if name == "create_file":
        path = _require_str(tool_input, "path")
        ws.create(path, _require_str(tool_input, "content"))
        ctx.actions.append(f"Created {path}: {_require_str(tool_input, 'reason')[:72]}")
        return f"Created {path}"

    if name == "modify_file":
        path = _require_str(tool_input, "path")
        ws.modify(path, _require_str(tool_input, "content"))
        ctx.actions.append(f"Modified {path}: {_require_str(tool_input, 'reason')[:72]}")
        return f"Modified {path}"

    if name == "delete_file":
        path = _require_str(tool_input, "path")
        ws.delete(path)
        ctx.actions.append(f"Deleted {path}: {_require_str(tool_input, 'reason')[:72]}")
        return f"Deleted {path}"

    if name == "send_email":
        return _send_email(ctx, _require_str(tool_input, "subject"), _require_str(tool_input, "body"))

    raise AgentError(f"unknown tool {name}")


def _send_email(ctx: ToolContext, subject: str, body: str) -> str:
    cfg = ctx.cfg.email
    if not cfg.enabled:
        raise AgentError("email capability is not enabled")
    if ctx.emails_sent_this_cycle >= 1:
        raise AgentError("one email per cycle; this cycle's email has already been sent or drafted")
    if not subject.strip() or not body.strip():
        raise AgentError("subject and body must both be non-empty")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    record_path = f"{OUTBOUND_DIR}/{stamp}-{ctx.cycle_id}.md"

    if cfg.review_required:
        if ctx.gh is None:
            raise AgentError("review gate needs GitHub; GH_TOKEN is not configured")
        number = create_review_issue(ctx.gh, cfg, subject, body)
        ctx.ws.write(record_path, f"# {subject}\n\nStatus: draft, issue #{number}\n\n{body}\n")
        ctx.emails_sent_this_cycle += 1
        ctx.actions.append(f"Drafted email as issue #{number}: {subject[:50]}")
        return f"Email draft filed as issue #{number}; it sends once a human adds the `approved` label."

    if ctx.resend is None:
        raise AgentError("direct send needs Resend; RESEND_API_KEY is not configured")
    outcome = ctx.resend.send(cfg, subject, body)
    if not outcome.ok:
        raise AgentError(outcome.detail)
    ctx.ws.write(record_path, f"# {subject}\n\nStatus: sent\n\n{body}\n")
    ctx.emails_sent_this_cycle += 1
    ctx.actions.append(f"Sent email: {subject[:50]}")
    return outcome.detail
