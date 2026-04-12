#!/usr/bin/env bash
# bootstrap.sh — one-shot idempotent setup for the orbits workspace.
#
# What it does:
#   1. Creates .venv with Python 3.10+
#   2. pip install -e vendor/superlocalmemory  (editable slm)
#   3. slm setup && slm mode a && slm doctor
#   4. Backs up ~/.superlocalmemory and symlinks it to Knowledge/slm_data/
#   5. Verifies gemini CLI is reachable
#
# Safe to re-run: every step checks before acting.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
fail() { echo -e "${RED}  ✗${NC} $*"; exit 1; }
step() { echo -e "\n${YELLOW}▶${NC} $*"; }

# ── 1. Python venv ──────────────────────────────────────────────────────────
step "Python virtual environment"
if [[ ! -d .venv ]]; then
    python3 -m venv .venv
    ok "Created .venv"
else
    ok ".venv already exists"
fi
source .venv/bin/activate

# ── 2. Install slm editable ─────────────────────────────────────────────────
step "Install SuperLocalMemory (editable)"
if [[ -d vendor/superlocalmemory ]]; then
    pip install -e vendor/superlocalmemory --quiet
    ok "Installed vendor/superlocalmemory editable"
else
    warn "vendor/superlocalmemory not found — run: git submodule update --init --recursive"
fi

# ── 3. slm setup ────────────────────────────────────────────────────────────
step "SuperLocalMemory first-time setup"
SLM=".venv/bin/slm"
if [[ ! -x "$SLM" ]]; then
    fail "slm not found at $SLM after install — check vendor/superlocalmemory"
fi

# Check if already configured
if "$SLM" status &>/dev/null; then
    ok "slm already configured"
else
    "$SLM" setup
    "$SLM" mode a
    ok "slm configured (Mode A — zero cloud)"
fi

step "slm health check"
"$SLM" doctor || warn "slm doctor reported issues — see output above"

# ── 4. Symlink ~/.superlocalmemory → Knowledge/slm_data/ ────────────────────
step "Wire slm data dir to Knowledge/slm_data/"
mkdir -p "$REPO_ROOT/Knowledge/slm_data"
SLM_HOME="$HOME/.superlocalmemory"
TARGET="$REPO_ROOT/Knowledge/slm_data"

if [[ -L "$SLM_HOME" ]]; then
    CURRENT_TARGET="$(readlink "$SLM_HOME")"
    if [[ "$CURRENT_TARGET" == "$TARGET" ]]; then
        ok "~/.superlocalmemory already symlinked → Knowledge/slm_data/"
    else
        warn "~/.superlocalmemory points to $CURRENT_TARGET (unexpected). Update manually."
    fi
elif [[ -d "$SLM_HOME" ]]; then
    # Stop daemon if running
    if [[ -f "$SLM_HOME/daemon.pid" ]]; then
        PID="$(cat "$SLM_HOME/daemon.pid" 2>/dev/null || echo "")"
        if [[ -n "$PID" ]] && kill -0 "$PID" 2>/dev/null; then
            kill "$PID" && sleep 1
            ok "Stopped slm daemon (PID $PID)"
        fi
    fi

    # Back up existing dir
    BACKUP="$HOME/.superlocalmemory.bak-$(date +%Y%m%dT%H%M%S)"
    mv "$SLM_HOME" "$BACKUP"
    ok "Backed up existing ~/.superlocalmemory → $BACKUP"

    # Move contents to Knowledge/slm_data/ and symlink
    cp -r "$BACKUP/." "$TARGET/"
    ln -s "$TARGET" "$SLM_HOME"
    ok "Symlinked ~/.superlocalmemory → Knowledge/slm_data/"
else
    ln -s "$TARGET" "$SLM_HOME"
    ok "Symlinked ~/.superlocalmemory → Knowledge/slm_data/ (fresh)"
fi

# ── 5. Check gemini CLI ──────────────────────────────────────────────────────
step "Gemini CLI check"
if command -v gemini &>/dev/null; then
    ok "gemini CLI found: $(command -v gemini)"
else
    warn "'gemini' CLI not found on PATH."
    warn "Install it and authenticate before using memory brain features."
    warn "Set GEMINI_DISABLED=1 to run in slm-only mode."
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}Bootstrap complete.${NC}"
echo "  Next: bash scripts/link_omc.sh   # wire forked OMC into .claude/"
echo "  Then: bash scripts/doctor.sh     # full health check"
echo ""
echo "  To activate the venv: source .venv/bin/activate"
