"""
linker.py — cross-link notes in Knowledge/notes/ via slm recall + Gemini.

Ported from: Financial assistant/linker.py
Adapted for orbits: generic knowledge base (not finance-specific prompts).

For each note, rewrites the ## Related and ## Next Steps sections so that
[[backlinks]] point to real files. Uses slm recall as the fast path; falls
back to Gemini when slm returns no neighbours.

Usage:
    python -m orchestration.brain.linker              # link all notes
    python -m orchestration.brain.linker my_slug      # link one note
"""
import glob
import os
import re
import sys
import time
from pathlib import Path

from orchestration import gemini
from orchestration.brain.policy import LINKER_CASCADE
import orchestration.memory as slm_memory

_REPO_ROOT     = Path(__file__).parent.parent.parent
KNOWLEDGE_DIR  = _REPO_ROOT / "Knowledge" / "notes"


def get_knowledge_files() -> list[str]:
    files = sorted(glob.glob(str(KNOWLEDGE_DIR / "*.md")))
    return [f for f in files if not os.path.basename(f).startswith("_")]


def _extract_topic(content: str) -> str:
    m = re.search(r'^topic:\s*"?([^"\n]+)"?', content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    m = re.search(r'^#\s+(.+)', content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


def _build_gemini_prompt(target_file: str, target_content: str, all_files: list[str]) -> str:
    other_slugs = [
        os.path.basename(f).replace(".md", "")
        for f in all_files
        if f != target_file and not os.path.basename(f).startswith("_")
    ]
    slug_list    = "\n".join(f"- [[{s}]]" for s in other_slugs)
    target_slug  = os.path.basename(target_file).replace(".md", "")

    return (
        f"You are maintaining an Obsidian-style knowledge base.\n\n"
        f"TARGET FILE: [[{target_slug}]]\n\n"
        f"CURRENT CONTENT:\n{target_content}\n\n"
        f"ALL OTHER FILES IN THE KNOWLEDGE BASE:\n{slug_list}\n\n"
        "Your task: Rewrite this file so that its ## Related and ## Next Steps "
        "sections reflect TRUE conceptual relationships with the listed files.\n\n"
        "Rules:\n"
        "1. Read the content carefully. Understand what this topic is ACTUALLY about.\n"
        "2. Scan the other slugs. Identify which are genuinely connected.\n"
        "3. In ## Related: list only real connections with one sentence explaining HOW.\n"
        "4. In ## Next Steps: reference actual existing files where possible.\n"
        "5. DO NOT change any other sections.\n"
        "6. Keep existing [[backlinks]] in the body; add more where genuinely relevant.\n\n"
        "Return the COMPLETE updated file content. No preamble. No explanation."
    )


def _link_via_slm(filepath: str) -> bool:
    """Zero-Gemini fast path. Returns True if successful."""
    with open(filepath) as f:
        content = f.read()

    slug  = os.path.basename(filepath).replace(".md", "")
    topic = _extract_topic(content) or slug

    recalled = slm_memory.recall(topic, k=6)
    recalled = [r for r in recalled if slug not in r.get("text", "")[:100]]
    if not recalled:
        return False

    related_lines = []
    seen_slugs    = {slug}
    for item in recalled:
        text    = item.get("text", "")
        slug_m  = re.search(r'\[slug:\s*([^\]]+)\]', text)
        topic_m = re.search(r'\[topic:\s*([^\]]+)\]', text)
        item_slug  = slug_m.group(1).strip()  if slug_m  else None
        item_topic = topic_m.group(1).strip() if topic_m else None

        if not item_slug or item_slug in seen_slugs:
            continue
        seen_slugs.add(item_slug)

        if item_topic:
            desc = item_topic
        else:
            body_lines = [l.strip() for l in text.splitlines()
                          if l.strip() and not l.startswith('[')]
            desc = body_lines[0][:100] if body_lines else item_slug

        related_lines.append(f"- [[{item_slug}]] — {desc}")

    related_section = "## Related\n" + "\n".join(related_lines) + "\n"

    new_content = re.sub(
        r'## Related\n.*?(?=\n## |\Z)',
        related_section,
        content,
        flags=re.DOTALL,
    )
    if new_content == content:
        new_content = content.rstrip() + "\n\n" + related_section

    with open(filepath, "w") as f:
        f.write(new_content)

    print(f"  ✓ Linked via slm ({len(recalled)} neighbours)")
    return True


def link_file(filepath: str, all_files: list[str]) -> bool:
    slug = os.path.basename(filepath).replace(".md", "")
    print(f"\nLinking: {slug}")

    # slm fast path
    if _link_via_slm(filepath):
        return True
    print("  ⚠ slm recall empty — falling back to Gemini")

    with open(filepath) as f:
        content = f.read()

    prompt = _build_gemini_prompt(filepath, content, all_files)
    output = gemini.ask(prompt, label="Linker", _cascade=LINKER_CASCADE)

    if not output:
        print(f"  ✗ All models failed for {slug}")
        return False

    with open(filepath, "w") as f:
        f.write(output)

    print("  ✓ Updated via Gemini")
    return True


def main() -> None:
    all_files = get_knowledge_files()
    if not all_files:
        print("No files found in Knowledge/notes/. Drop some .md files and run /knowledge-sync first.")
        sys.exit(0)

    print(f"Found {len(all_files)} file(s) to link.\n")

    if len(sys.argv) > 1:
        target_slug = sys.argv[1]
        targets = [f for f in all_files if target_slug in os.path.basename(f)]
        if not targets:
            print(f"No file matching '{target_slug}' found.")
            sys.exit(1)
    else:
        targets = all_files

    success = failed = 0
    for filepath in targets:
        if link_file(filepath, all_files):
            success += 1
        else:
            failed += 1
        time.sleep(2)  # rate-limit buffer between files

    print(f"\n{'─'*40}")
    print(f"  Linked:  {success}")
    print(f"  Failed:  {failed}")
    print(f"{'─'*40}")


if __name__ == "__main__":
    main()
