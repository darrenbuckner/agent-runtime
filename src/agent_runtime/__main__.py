"""Entry point: `python -m agent_runtime` or the `agent-cycle` script.

Runs one cycle against the current directory (or AGENT_WORKSPACE). Exit
code is non-zero on any failure so the Actions run goes red.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .errors import AgentError
from .loop import run_cycle


def main() -> int:
    root = Path(os.environ.get("AGENT_WORKSPACE") or os.getcwd())
    try:
        run_cycle(root)
    except AgentError as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
