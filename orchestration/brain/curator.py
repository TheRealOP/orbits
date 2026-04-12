"""
curator.py — decide whether a piece of text is worth storing in slm.

Called by the PostToolUse hook on every tool output. A cheap plain-Python
pre-filter runs BEFORE any Gemini call so flash-lite isn't invoked for
obvious non-candidates (empty, tiny, boilerplate diffs, etc.).

Usage:
    from orchestration.brain.curator import should_remember
    ok, metadata = should_remember(tool_name, tool_output)
    if ok:
        memory.remember(tool_output, metadata=metadata)
"""
import os
import re
from orchestration import gemini
from orchestration.brain.policy import CURATOR_CASCADE

# Plain-Python pre-filter constants
_MIN_CHARS   = 80    # outputs shorter than this are almost never worth storing
_MAX_CHARS   = 8000  # cap Gemini input; truncate longer outputs
_BOILERPLATE = re.compile(
    r"^(File (created|updated|written)|"
    r"Edit (succeeded|applied)|"
    r"Command (completed|failed)|"
    r"No changes|"
    r"(True|False|None|0|1)\s*$)",
    re.IGNORECASE,
)

_SKIP_TOOLS = frozenset({
    "Read", "Glob", "Grep",  # read-only lookups — not worth storing
})

_PROMPT_TEMPLATE = """\
You are a memory curator for a personal AI assistant workspace.
Decide if the following tool output should be saved to long-term memory.

TOOL: {tool}
OUTPUT (truncated to {length} chars):
{output}

Return ONLY valid JSON (no markdown fences):
{{
  "remember": true | false,
  "reason": "<one sentence why>",
  "topic": "<concise topic if remember=true, else empty>",
  "slug":  "<snake_case_slug if remember=true, else empty>",
  "tags":  ["<tag>"]
}}

Remember if: the output contains a new insight, decision, design choice,
error + fix, important file path, or factual finding that would be useful
to recall later. Do NOT remember: file listings, boilerplate success messages,
intermediate scratch work, or routine read results.
"""


def should_remember(
    tool_name: str,
    tool_output: str,
) -> tuple[bool, dict]:
    """
    Returns (should_store: bool, metadata: dict).
    metadata has keys: topic, slug, tags (may be empty if should_store=False).
    """
    # 1 — skip read-only tools entirely (no Gemini call)
    if tool_name in _SKIP_TOOLS:
        return False, {}

    text = tool_output.strip()

    # 2 — plain-Python pre-filter (no Gemini call)
    if len(text) < _MIN_CHARS:
        return False, {}
    if _BOILERPLATE.match(text):
        return False, {}

    # 3 — check configs (orbit.json or env vars)
    from orchestration.config import should_disable_auto_remember
    if should_disable_auto_remember():
        return False, {}

    # 4 — ask Gemini
    truncated = text[:_MAX_CHARS]
    prompt = _PROMPT_TEMPLATE.format(
        tool=tool_name,
        length=len(truncated),
        output=truncated,
    )
    result = gemini.ask_json(prompt, label="Curator", _cascade=CURATOR_CASCADE)

    if not result or not isinstance(result, dict):
        # Gemini unavailable — conservative: don't store
        return False, {}

    if not result.get("remember"):
        return False, {}

    metadata = {
        "topic": str(result.get("topic", "")).strip(),
        "slug":  str(result.get("slug",  "")).strip(),
        "tags":  result.get("tags", []) if isinstance(result.get("tags"), list) else [],
    }
    return True, metadata
