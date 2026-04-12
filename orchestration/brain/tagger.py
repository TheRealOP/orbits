"""
tagger.py — extract {topic, slug, tags} from raw text via Gemini.

Used by distiller.py and curator.py to generate slm metadata.
Degrades gracefully: returns a minimal dict if Gemini is unavailable.
"""
import re
from orchestration import gemini
from orchestration.brain.policy import TAGGER_CASCADE


_PROMPT_TEMPLATE = """\
Extract structured metadata from the following text for a personal knowledge base.

Return ONLY valid JSON (no markdown fences, no preamble):
{{
  "topic": "<concise 5-10 word topic phrase>",
  "slug":  "<lowercase_snake_case_identifier_max_6_words>",
  "tags":  ["<tag1>", "<tag2>"]
}}

Rules:
- topic: a human-readable phrase describing what this is about
- slug: filesystem-safe, lowercase, underscores, no spaces, max 6 words
- tags: 2-5 short category labels (e.g. "quant/momentum", "tool/claude", "project/orbits")

TEXT:
{text}
"""


def _slugify_fallback(text: str) -> str:
    """Cheap slug from first ~6 words if Gemini is unavailable."""
    words = re.sub(r"[^a-z0-9\s]", "", text.lower()).split()
    return "_".join(words[:6])


def tag(text: str) -> dict:
    """
    Return {"topic": str, "slug": str, "tags": list[str]}.
    Falls back to minimal values if Gemini is unavailable.
    """
    prompt = _PROMPT_TEMPLATE.format(text=text[:2000])  # cap input
    result = gemini.ask_json(prompt, label="Tagger", _cascade=TAGGER_CASCADE)

    if result and isinstance(result, dict):
        return {
            "topic": str(result.get("topic", "")).strip() or text[:60],
            "slug":  str(result.get("slug",  "")).strip() or _slugify_fallback(text),
            "tags":  result.get("tags", []) if isinstance(result.get("tags"), list) else [],
        }

    # graceful degradation
    return {
        "topic": text[:60].strip(),
        "slug":  _slugify_fallback(text),
        "tags":  [],
    }
