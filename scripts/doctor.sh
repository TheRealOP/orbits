#!/usr/bin/env bash
# doctor.sh — health check for the orbits workspace.
#
# Checks:
#   1. Python venv + slm reachable
#   2. slm doctor (8-point internal check)
#   3. Knowledge/slm_data symlink integrity
#   4. Gemini CLI reachable + smoke test
#   5. MCP server probe (list-tools)
#   6. Hook scripts parse (shellcheck if available, else bash -n)
#   7. Submodule freshness vs upstream default branch
#   8. Stale .omc hot paths reconcile

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BOLD='\033[1m'; NC='\033[0m'
PASS=0; WARN=0; FAIL=0

pass()  { echo -e "  ${GREEN}✓${NC} $*"; ((PASS++))  || true; }
warn()  { echo -e "  ${YELLOW}⚠${NC} $*"; ((WARN++)) || true; }
fail()  { echo -e "  ${RED}✗${NC} $*"; ((FAIL++))   || true; }
header(){ echo -e "\n${BOLD}$*${NC}"; }

# ── 1. Python venv + slm ────────────────────────────────────────────────────
header "1. Python venv & slm"
VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
VENV_SLM="$REPO_ROOT/.venv/bin/slm"

if [[ -x "$VENV_PYTHON" ]]; then
    pass "venv found: $VENV_PYTHON"
else
    fail "venv not found — run: bash scripts/bootstrap.sh"
fi

if [[ -x "$VENV_SLM" ]]; then
    pass "slm found: $VENV_SLM"
elif command -v slm &>/dev/null; then
    warn "slm found on PATH (not in venv): $(command -v slm)"
else
    fail "slm not found in venv or PATH — run: bash scripts/bootstrap.sh"
fi

# ── 2. slm doctor ────────────────────────────────────────────────────────────
header "2. SuperLocalMemory internal check"
SLM="${VENV_SLM:-slm}"
if [[ -x "$SLM" ]]; then
    if "$SLM" doctor 2>&1 | grep -q "All checks passed\|healthy\|ok"; then
        pass "slm doctor: all checks passed"
    else
        echo ""
        "$SLM" doctor 2>&1 | sed 's/^/    /'
        warn "slm doctor reported issues (see above)"
    fi
else
    fail "slm unavailable — skipping internal check"
fi

# ── 3. Knowledge/slm_data symlink ────────────────────────────────────────────
header "3. Knowledge/slm_data symlink"
SLM_HOME="$HOME/.superlocalmemory"
EXPECTED_TARGET="$REPO_ROOT/Knowledge/slm_data"

if [[ -L "$SLM_HOME" ]]; then
    TARGET="$(readlink "$SLM_HOME")"
    if [[ "$TARGET" == "$EXPECTED_TARGET" ]]; then
        pass "~/.superlocalmemory → Knowledge/slm_data/ ✓"
    else
        warn "~/.superlocalmemory points to $TARGET (expected $EXPECTED_TARGET)"
    fi
elif [[ -d "$SLM_HOME" ]]; then
    warn "~/.superlocalmemory is a real directory (not symlinked to Knowledge/slm_data/)"
    warn "Run: bash scripts/bootstrap.sh to wire it up"
else
    warn "~/.superlocalmemory does not exist — run: bash scripts/bootstrap.sh"
fi

# ── 4. Gemini CLI ────────────────────────────────────────────────────────────
header "4. Gemini CLI"
if command -v gemini &>/dev/null; then
    pass "gemini found: $(command -v gemini)"
    # smoke test with a valid lightweight model for gemini-cli v0.24.0
    if gemini --yolo "Reply with the single word: pong" --model gemini-2.5-flash \
        &>/dev/null 2>&1; then
        pass "gemini smoke test passed (2.5-flash)"
    else
        warn "gemini smoke test failed — check auth, CLI config, or model availability"
    fi
else
    warn "'gemini' CLI not found — brain modules will degrade to slm-only"
    warn "Set GEMINI_DISABLED=1 to silence this warning"
fi

# ── 5. MCP server probe ───────────────────────────────────────────────────────
header "5. MCP server (.mcp.json)"
if [[ -f "$REPO_ROOT/.mcp.json" ]]; then
    pass ".mcp.json present"
    # Basic probe: can slm mcp start at all?
    if [[ -x "$SLM" ]]; then
        if echo '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
            | timeout 5 "$SLM" mcp 2>/dev/null | grep -q '"tools"'; then
            pass "slm mcp server responds to tools/list"
        else
            warn "slm mcp server did not respond — MCP may still work via Claude Code"
        fi
    fi
else
    fail ".mcp.json not found"
fi

# ── 6. Hook scripts syntax ────────────────────────────────────────────────────
header "6. Hook script syntax"
HOOKS_OK=true
for hook in .claude/hooks/*.sh; do
    [[ -f "$hook" ]] || continue
    name="$(basename "$hook")"
    if command -v shellcheck &>/dev/null; then
        if shellcheck "$hook" &>/dev/null; then
            pass "$name (shellcheck)"
        else
            warn "$name (shellcheck found issues — run: shellcheck $hook)"
            HOOKS_OK=false
        fi
    else
        if bash -n "$hook" 2>/dev/null; then
            pass "$name (bash -n)"
        else
            fail "$name syntax error"
            HOOKS_OK=false
        fi
    fi
done
[[ -z "$(ls .claude/hooks/*.sh 2>/dev/null)" ]] && warn "No hook scripts found"

# ── 7. Submodule freshness ────────────────────────────────────────────────────
header "7. Submodule freshness"
for sub in vendor/superlocalmemory vendor/oh-my-claudecode; do
    if [[ -d "$sub/.git" ]] || [[ -f "$sub/.git" ]]; then
        # Check if behind upstream
        BEHIND=$(git -C "$sub" rev-list HEAD..origin/main --count 2>/dev/null \
                 || git -C "$sub" rev-list HEAD..origin/master --count 2>/dev/null \
                 || echo "?")
        if [[ "$BEHIND" == "0" ]]; then
            pass "$sub is up to date"
        elif [[ "$BEHIND" == "?" ]]; then
            warn "$sub — could not compare against upstream (fetch needed?)"
        else
            warn "$sub is $BEHIND commit(s) behind upstream"
        fi
    else
        warn "$sub not initialised — run: git submodule update --init --recursive"
    fi
done

# ── 8. Stale .omc hot paths ───────────────────────────────────────────────────
header "8. .omc/project-memory.json hot paths"
OMC_MEM=".omc/project-memory.json"
if [[ -f "$OMC_MEM" ]]; then
    STALE=$( python3 -c "
import json, os
data = json.load(open('$OMC_MEM'))
paths = data.get('hotPaths', {})
stale = [p for p in paths if not os.path.exists(p)]
print('\n'.join(stale))
" 2>/dev/null || echo "")
    if [[ -z "$STALE" ]]; then
        pass "All hot paths exist on disk"
    else
        warn "Stale hot paths (file deleted but still in .omc memory):"
        echo "$STALE" | sed 's/^/      /'
    fi
else
    warn ".omc/project-memory.json not found"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}─── Doctor summary ─────────────────────────────────────────${NC}"
echo -e "  ${GREEN}Pass: $PASS${NC}  ${YELLOW}Warn: $WARN${NC}  ${RED}Fail: $FAIL${NC}"
echo ""
[[ "$FAIL" -gt 0 ]] && exit 1 || exit 0
