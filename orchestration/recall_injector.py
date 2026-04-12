"""
recall_injector.py — query slm and emit a <memory>…</memory> context block.

Used by hooks:
    session_start.sh   →  --session-start (generic "what do I know?" query)
    user_prompt_submit →  --query <user prompt text>

The block is written to stdout; Claude Code's hook integration surfaces
it as injected context before the model call.

Exit 0 always — hooks must never block the parent tool.
"""
import argparse
import os
import sys
from pathlib import Path

# Allow running as a script from any cwd
sys.path.insert(0, str(Path(__file__).parent.parent))

import orchestration.memory as memory
from orchestration.brain.synthesizer import synthesize


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query",          default="",    help="Explicit recall query")
    parser.add_argument("--session-start",  action="store_true",
                        help="Use a broad session-start query")
    parser.add_argument("--k",              type=int, default=5,
                        help="Number of chunks to retrieve")
    args = parser.parse_args()

    # Kill-switch: ORBITS_NO_SESSION_RECALL for session hooks,
    #             ORBITS_NO_PROMPT_INJECT for per-prompt hooks
    if args.session_start and os.environ.get("ORBITS_NO_SESSION_RECALL") == "1":
        sys.exit(0)
    if not args.session_start and os.environ.get("ORBITS_NO_PROMPT_INJECT") == "1":
        sys.exit(0)

    query = (
        "recent context, active projects, open decisions, important facts"
        if args.session_start
        else args.query.strip()
    )

    if not query:
        sys.exit(0)

    chunks = memory.recall(query, k=args.k)
    if not chunks:
        sys.exit(0)

    block = synthesize(query, chunks)
    if block:
        print(block)

    sys.exit(0)


if __name__ == "__main__":
    main()
