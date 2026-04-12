#!/usr/bin/env bash
# link_omc.sh — wire the forked OMC submodule into .claude/
#
# The forked OMC lives at vendor/oh-my-claudecode/. This script creates
# symlinks from .claude/{agents,commands,hooks,skills} into the fork so:
#   - Claude Code picks up the fork's agents/skills via repo-local .claude/
#   - Edits to the fork are immediately reflected without re-linking
#   - Net-new orbits-specific files (.claude/agents/memory-curator.md, etc.)
#     are real files (not symlinks) and take precedence
#
# Safe to re-run: removes stale symlinks before recreating.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }

OMC_DIR="$REPO_ROOT/vendor/oh-my-claudecode"

if [[ ! -d "$OMC_DIR" ]]; then
    echo "vendor/oh-my-claudecode not found."
    echo "Run: git submodule update --init --recursive"
    exit 1
fi

# Directories in the fork to symlink into .claude/
# (only link sub-items individually so orbits-specific real files aren't clobbered)
link_items_from() {
    local src_dir="$1"
    local dst_dir="$2"
    local label="$3"

    if [[ ! -d "$src_dir" ]]; then
        warn "$label not found in fork ($src_dir) — skipping"
        return
    fi

    mkdir -p "$dst_dir"
    local count=0
    for item in "$src_dir"/*; do
        [[ -e "$item" ]] || continue
        local name
        name="$(basename "$item")"
        local dst="$dst_dir/$name"

        # Don't clobber orbits-specific real files
        if [[ -f "$dst" && ! -L "$dst" ]]; then
            warn "Keeping orbits override: $dst_dir/$name"
            continue
        fi

        # Remove stale symlink
        [[ -L "$dst" ]] && rm "$dst"

        ln -s "$item" "$dst"
        ((count++)) || true
    done
    ok "Linked $count item(s) from fork → .claude/$label"
}

echo ""
echo "Wiring vendor/oh-my-claudecode into .claude/ ..."

# Check what top-level dirs exist in the fork
for dir_name in agents commands hooks skills; do
    link_items_from "$OMC_DIR/$dir_name"    ".claude/$dir_name"   "$dir_name"
done

# Also link the fork's AGENTS.md if present and no local one exists
if [[ -f "$OMC_DIR/AGENTS.md" && ! -f "AGENTS.md" ]]; then
    ln -sf "$OMC_DIR/AGENTS.md" "AGENTS.md"
    ok "Linked AGENTS.md from fork"
fi

echo ""
echo -e "${GREEN}Done.${NC} Fork items are now discoverable by Claude Code."
echo "  To customise: edit files in vendor/oh-my-claudecode/ (on your fork branch)"
echo "  Orbits-specific overrides live as real files in .claude/"
