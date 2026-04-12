"""
synthesizer.py — condense top-k slm recall chunks into a <memory> block.

Called by recall_injector.py before injecting context into a Claude prompt.

Fast path: if chunks are few, small, and high-score, skip Gemini and emit
raw chunks directly (mirrors the zero-Gemini optimisation in linker.py).
"""
from orchestration import gemini
from orchestration.brain.policy import SYNTHESIZER_CASCADE

# Fast-path thresholds: skip Gemini if ALL of these hold
_FAST_PATH_MAX_CHUNKS = 2
_FAST_PATH_MAX_CHARS  = 600   # total text across all chunks
_FAST_PATH_MIN_SCORE  = 0.75  # all chunks must be high-confidence

_PROMPT_TEMPLATE = """\
Synthesize the following memory chunks retrieved for the query below into a
single concise context block that will help an AI assistant answer a question.

QUERY: {query}

RETRIEVED CHUNKS:
{chunks}

Rules:
1. Quote exact facts/numbers/code verbatim where precision matters.
2. Summarise background info in 1-2 sentences.
3. Drop anything clearly irrelevant to the query.
4. Output format: plain prose, under 300 words.
5. No preamble. Start directly with the synthesised context.
"""


def synthesize(query: str, chunks: list[dict]) -> str:
    """
    chunks: list of {"text": str, "score": float}
    Returns a formatted <memory>…</memory> string, or "" if no chunks.
    """
    if not chunks:
        return ""

    # Fast path: small, high-quality result — no need for Gemini
    total_chars = sum(len(c.get("text", "")) for c in chunks)
    min_score   = min(c.get("score", 0.0) for c in chunks)
    if (
        len(chunks) <= _FAST_PATH_MAX_CHUNKS
        and total_chars <= _FAST_PATH_MAX_CHARS
        and min_score >= _FAST_PATH_MIN_SCORE
    ):
        raw = "\n---\n".join(c["text"] for c in chunks)
        return f"<memory>\n{raw}\n</memory>"

    # Gemini synthesis
    chunk_text = "\n\n".join(
        f"[score={c.get('score', 0):.2f}]\n{c.get('text', '')}"
        for c in chunks
    )
    prompt = _PROMPT_TEMPLATE.format(query=query, chunks=chunk_text)
    result = gemini.ask(prompt, label="Synthesizer", _cascade=SYNTHESIZER_CASCADE)

    if result:
        return f"<memory>\n{result}\n</memory>"

    # Fallback: raw chunks
    raw = "\n---\n".join(c["text"] for c in chunks)
    return f"<memory>\n{raw}\n</memory>"
