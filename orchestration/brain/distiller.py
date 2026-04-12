"""
distiller.py — transform raw markdown notes into slm-ready payloads.

Called by scripts/knowledge_ingest.py when a new/changed file is found
in Knowledge/notes/. Uses Gemini to de-fluff and extract structure, then
returns the text + metadata to pass to memory.remember().
"""
from orchestration import gemini
from orchestration.brain.policy import DISTILLER_CASCADE
from orchestration.brain.tagger import tag as _tag

_PROMPT_TEMPLATE = """\
Rewrite the following note for storage in a dense semantic memory index.

Goal: make it as information-rich and self-contained as possible so that
a keyword or semantic search later can surface it accurately.

Rules:
1. Remove filler, headings, bullet symbols, markdown formatting.
2. Expand any abbreviations or jargon on first use.
3. Keep all concrete facts, numbers, decisions, commands, and code snippets.
4. Result should be 1-3 tight paragraphs of prose. No markdown.
5. Do NOT add information not present in the original.

ORIGINAL NOTE:
{text}

Return ONLY the rewritten prose. No preamble. No explanation.
"""


def distill(filepath: str, raw_text: str) -> tuple[str, dict]:
    """
    Given a raw note (filepath for context, raw_text for content), return:
        (distilled_text: str, metadata: dict)
    where metadata = {topic, slug, tags}.

    Falls back to raw_text if Gemini is unavailable.
    """
    # Get metadata (tagger also degrades gracefully)
    metadata = _tag(raw_text)

    # Distill prose
    prompt = _PROMPT_TEMPLATE.format(text=raw_text[:6000])
    distilled = gemini.ask(prompt, label="Distiller", _cascade=DISTILLER_CASCADE)

    if distilled and len(distilled) >= 40:
        return distilled, metadata

    # Graceful fallback: store the raw text as-is
    return raw_text, metadata
