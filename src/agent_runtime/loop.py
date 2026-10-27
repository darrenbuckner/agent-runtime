"""One cycle: load state from the checkout, run the tool loop, write the
cycle log, commit and push. Every exit path that is not "nothing to do"
leaves a cycle-log entry and a pushed commit, or a red run.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import cyclelog
from .config import Config, load_config
from .email import INBOUND_DIR, Resend, send_approved
from .errors import AgentError, CycleStopped, GitError, WorkspaceError
from .git import commit_and_push
from .github import GitHub
from .llm import Provider, ToolResult, make_provider
from .prompt import build_system_prompt
from .tools import FINISH, ToolContext, build_tool_specs, execute
from .workspace import Workspace

OPENING_MESSAGE = "Begin your cycle. Review your workspace state and decide what to do."
RESULT_LIMIT = 20_000

ProviderFactory = Callable[[Config], Provider]


def _log(msg: str) -> None:
    print(msg, flush=True)


def _default_provider(cfg: Config) -> Provider:
    return make_provider(cfg.model, cfg.max_tokens)


def _read_inbound(ws: Workspace) -> list[tuple[str, str]]:
    """Snapshot the inbound directory at prompt-build time. Only these
    paths are archived at finish; mail that lands mid-cycle waits for the
    next one instead of being deleted unread (v1 archived everything)."""
    return [(path, ws.read_required(path)) for path in ws.list_dir(INBOUND_DIR)]


def run_cycle(
    root: Path,
    *,
    provider_factory: ProviderFactory | None = None,
    gh: GitHub | None = None,
    resend: Resend | None = None,
    now: datetime | None = None,
    push: bool | None = None,
) -> cyclelog.CycleEntry:
    now = now or datetime.now(timezone.utc)
    cycle_id = now.strftime("%Y-%m-%d-%H%M")
    if push is None:
        push = os.environ.get("AGENT_PUSH", "1") != "0"

    cfg = load_config(root / "config.yaml")
    ws = Workspace(root, cfg.protected_paths)
    _log(f"{cfg.name}: cycle {cycle_id} ({cfg.model})")

    mission = ws.read_optional("MISSION.md")
    if mission is None:
        raise WorkspaceError("MISSION.md not found; the template ships one")

    ctx = ToolContext(cfg=cfg, ws=ws, cycle_id=cycle_id, gh=gh, resend=resend)
    try:
        entry = _run(ctx, mission, now, provider_factory or _default_provider)
    except GitError:
        raise
    except Exception as exc:  # noqa: BLE001 - every failure must leave a log entry
        failed = cyclelog.CycleEntry(
            cycle_id=cycle_id,
            summary=f"FAILED: {type(exc).__name__}: {exc}",
            actions=list(ctx.actions),
            errors=list(ctx.errors),
        )
        cyclelog.append_entry(ws, failed)
        commit_and_push(root, f"Cycle {cycle_id}: FAILED ({type(exc).__name__})", push=push)
        raise

    committed = commit_and_push(root, f"Cycle {cycle_id}: {entry.summary[:60]}", push=push)
    _log(f"cycle {cycle_id} complete: {len(ctx.actions)} actions, {len(ctx.errors)} errors, "
         f"{'pushed' if committed and push else 'committed locally' if committed else 'nothing to commit'}")
    return entry


def _run(ctx: ToolContext, mission: str, now: datetime, provider_factory: ProviderFactory) -> cyclelog.CycleEntry:
    cfg, ws, cycle_id = ctx.cfg, ctx.ws, ctx.cycle_id

    inbound: list[tuple[str, str]] = []
    emails_sent: list[str] = []
    send_failures: list[str] = []
    if cfg.email.enabled:
        if ctx.resend is None:
            ctx.resend = Resend()
        if cfg.email.review_required:
            if ctx.gh is None:
                ctx.gh = GitHub()
            emails_sent, send_failures = send_approved(ctx.gh, ctx.resend, cfg.email)
            ctx.actions.extend(emails_sent)
            ctx.errors.extend(send_failures)
        inbound = _read_inbound(ws)
        _log(f"inbound: {len(inbound)}  approved sent: {len(emails_sent)}  send failures: {len(send_failures)}")

    system = build_system_prompt(
        cfg, mission, ws.manifest(), cyclelog.trimmed_view(ws, cfg.max_log_entries),
        now, inbound, emails_sent, send_failures,
    )
    provider = provider_factory(cfg)
    provider.start(system, build_tool_specs(cfg), OPENING_MESSAGE)

    for round_num in range(1, cfg.max_tool_rounds + 1):
        _log(f"round {round_num}")
        turn = provider.step()
        for line in turn.text:
            _log(f"  model: {line[:200]}")

        if turn.kind == "stopped":
            _log(f"  stopped: {turn.detail}")
            raise CycleStopped(turn.detail)

        if turn.kind == "end":
            _log("  ended without finish_cycle")
            entry = cyclelog.CycleEntry(
                cycle_id, "Cycle ended without finish_cycle call",
                actions=list(ctx.actions), errors=list(ctx.errors),
            )
            cyclelog.append_entry(ws, entry)
            return entry

        results: list[ToolResult] = []
        for call in turn.tool_calls:
            if call.name == FINISH:
                entry = cyclelog.CycleEntry(
                    cycle_id,
                    summary=str(call.input.get("summary") or "(no summary)"),
                    thinking=str(call.input.get("thinking") or ""),
                    actions=list(ctx.actions),
                    errors=list(ctx.errors),
                    next_notes=str(call.input.get("next_cycle_notes") or ""),
                )
                cyclelog.append_entry(ws, entry)
                for path, _ in inbound:
                    ws.delete(path)
                _log(f"  finish_cycle: {entry.summary[:80]}")
                return entry

            label = call.input.get("path") or call.input.get("subject") or ""
            _log(f"  {call.name}({str(label)[:60]})")
            try:
                result = execute(ctx, call.name, call.input)
            except AgentError as exc:
                result = f"Error: {exc}"
                ctx.errors.append(f"{call.name}({str(label)[:40]}): {exc}")
                _log(f"    {result}")
            if len(result) > RESULT_LIMIT:
                result = result[:RESULT_LIMIT] + "\n...(truncated)"
            results.append(ToolResult(call.id, result))
        provider.add_tool_results(results)

    detail = f"max_tool_rounds={cfg.max_tool_rounds} exhausted without finish_cycle, model={cfg.model}"
    raise CycleStopped(detail)
