# Changelog

## v2.0.0 (2026-10-27)

Extracted from `ai-agent-template` `agent.py` v1.1 so fixes propagate to forks by version bump instead of by hand.

### Security
- `modify_file` and `delete_file` can no longer touch `MISSION.md`, `config.yaml`, `requirements.txt`, `log/cycle-log.md`, `.github/`, `webhook/`. v1 only protected these from delete; the agent could rewrite its own runtime and config.
- Path guard: rejects `..`, `.`, absolute paths, backslashes, `.git/`, and symlinks that resolve outside the checkout.
- No personal access token. Pushes use the checkout's credentials; issues use `GH_TOKEN` (the workflow's `GITHUB_TOKEN`, scoped by `permissions:`).
- Review-gate recipient comes from `config.yaml`, not from the issue body.
- Inbound mail is delimited and labelled as untrusted data in the prompt.

### Failure direction
- Missing or invalid `config.yaml` is an error, not a silent default.
- Read failures raise; only a genuinely missing file returns `None`.
- Cycle log is append-only. v1 read-then-rewrote it, so one transient read failure could replace the whole log with one entry.
- An approved email's issue closes only on Resend `200`; failures are commented and left open.
- Finish archives only the inbound files that were in the prompt; mail arriving mid-cycle survives.
- Outbound records are keyed by timestamp and cycle id, not by day.
- Tool errors are recorded under **Errors** in the cycle log.
- One email per cycle is enforced in code, not just stated in the prompt.

### Structure
- Reads and writes go through the checkout; one commit per cycle, pushed with rebase-retry over the inbound worker's commits. No per-call contents-API PUTs, no SHA races, no `BRANCH` plumbing.
- Provider seam (`llm/`): Anthropic (unchanged behaviour: automatic prompt caching, `stop_details` on refusal) and OpenAI Responses adapters. `model` is `provider/model-id`.
- Test suite (55 tests) and CI.
