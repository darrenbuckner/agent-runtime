# agent-runtime

The runtime behind GitHub-Actions-hosted autonomous agents: a workspace (the repo), a heartbeat (a cron workflow), a mission (`MISSION.md`), and a provider-neutral tool loop.

A fork of [ai-agent-template](https://github.com/darrenbuckner/ai-agent-template) holds only `MISSION.md`, `config.yaml`, a workflow and a `requirements.txt` that pins this package by tag. Fixes land here once and reach every fork on its next bump.

## What a cycle does

1. Loads `config.yaml` and `MISSION.md` from the checkout (missing or invalid is a failure, not a default).
2. Builds the file manifest and the last N cycle-log entries.
3. If email is on: sends any approved drafts, reads `inbound/`.
4. Runs the model in a tool loop: `read_file`, `create_file`, `modify_file`, `delete_file`, optionally `send_email`, and `finish_cycle`.
5. Appends the cycle-log entry, archives the inbound mail it saw, and lands everything as one commit pushed to the branch the run was dispatched on.

Every exit path either pushes a cycle-log entry or fails the run. A refusal, `max_tokens`, or an exhausted round budget writes a `FAILED:` entry and exits non-zero.

## Install

```
pip install "agent-runtime @ git+https://github.com/darrenbuckner/agent-runtime@v2.0.0"
pip install "agent-runtime[openai] @ git+https://github.com/darrenbuckner/agent-runtime@v2.0.0"   # with the OpenAI adapter
```

Run one cycle from the agent's checkout: `python -m agent_runtime` (or `agent-cycle`).

## Environment

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | for `anthropic/*` models | |
| `OPENAI_API_KEY` | for `openai/*` models | |
| `GH_TOKEN` | if email review gate is on | the workflow's `GITHUB_TOKEN`; needs `issues: write` |
| `GITHUB_REPOSITORY` | if email review gate is on | `owner/name`, set by Actions |
| `RESEND_API_KEY` | if email is on | |
| `AGENT_MODEL` | no | overrides `config.yaml` `model` |
| `CLAUDE_MODEL` | no | the v1 fleet-wide Actions variable, still honoured (below `AGENT_MODEL`) |
| `AGENT_PUSH` | no | `0` commits locally without pushing (local experiments) |
| `AGENT_WORKSPACE` | no | path to the checkout; default is the current directory |
| `GITHUB_REF_NAME` | set by Actions | the branch to push to |

The runtime never needs a personal access token. Pushes use the credentials `actions/checkout` leaves in the clone; issues use `GH_TOKEN`.

## Models and providers

`model` is `provider/model-id`. No slash means `anthropic`. Precedence: `AGENT_MODEL` > `CLAUDE_MODEL` > `config.yaml` > `anthropic/claude-sonnet-5-5`.

| Provider | Adapter | Status |
|---|---|---|
| `anthropic` | Messages API, automatic prompt caching, `stop_details` surfaced on refusal | in production |
| `openai` | Responses API with function tools | exercised against a fake client in the test suite; not yet run against a live key |

Adding a vendor is one file in `src/agent_runtime/llm/` implementing `start`, `step`, `add_tool_results` (see `llm/base.py`) and one line in `llm/__init__.py`. The loop never sees a vendor.

## Protected paths

The agent cannot modify or delete `MISSION.md`, `config.yaml`, `requirements.txt`, `log/cycle-log.md`, `.github/`, `.git/`, or `webhook/`. These are what decide what the agent is and how it runs; an agent that could rewrite them could rewrite its own guard rails. `config.yaml` can add more under `protected_paths:`; it cannot remove any. Paths are also checked against traversal (`..`, absolute paths, symlinks out of the repo).

## Email

`capabilities.email` in `config.yaml`. The recipient is always `to_address`; the model does not choose it. One email per cycle is enforced in code.

With `review_required: true` the draft is filed as a GitHub Issue labelled `email-draft`. Add the `approved` label and it sends on the next cycle. The issue closes only on a `200` from Resend; otherwise the error is posted as a comment and the issue stays open for retry. Inbound mail arrives in `inbound/` from the webhook worker that ships with the template; the loop archives only the messages it put in the prompt, so mail that lands mid-cycle waits for the next one.

## Failure direction

- Return real data or raise. The one `None` in the codebase is `Workspace.read_optional`, and it means "does not exist"; any other read failure raises.
- A cycle that cannot write its log or push its commit is red.
- A green run on a wrong workspace is worse than a crash.

## Development

```
pip install -e ".[dev]"
pytest
```

Tests cover the path and protected-file guards, every loop exit path, the review gate's close-on-success rule, the inbound archive scope, both adapters' translation, and commit-and-push against a real bare remote including the rebase over a concurrent writer.

## Changes from v1

See `CHANGELOG.md`.
