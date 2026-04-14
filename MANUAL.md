# Orbits — Full System Manual

This document covers the entire orbits system: how it works, what runs in the
background, and how to use every part of it.

---

## What orbits is

Orbits is a personal memory + orchestration workspace. It has two main systems
that work together:

1. **Memory layer** — automatically captures, stores, and injects relevant
   knowledge into every Claude Code session. You never have to copy-paste
   context again.

2. **Multi-agent orchestrator** — a separate runtime (`orchestrator/`) where
   you give a task to Agent 1, which plans it, spawns model-specific workers,
   and returns a combined result — all stored back into memory automatically.

The three tools underneath everything:

| Tool | Role |
|---|---|
| **SuperLocalMemory (slm)** | Local SQLite database + embeddings. Stores and retrieves memories by semantic similarity. Everything stays on your machine. |
| **Gemini CLI** | The "brain". Curates what's worth storing, distills raw text, synthesizes recalled chunks, links notes, tags metadata. |
| **oh-my-claudecode (OMC)** | Orchestration layer for Claude Code. Provides agents, skills, hooks, and the session infrastructure. |

---

## Part 1 — Memory layer

### How memory gets stored

Three hooks run automatically inside Claude Code:

| Hook | When | What it does |
|---|---|---|
| **SessionStart** | New Claude Code session opens | Queries slm for recent context ("active projects, open decisions, important facts") and injects the result as a `<memory>` block before your first message |
| **UserPromptSubmit** | Every time you type a message | Queries slm using your message text (top-5 results) and injects a `<memory>` block so Claude has relevant context before answering |
| **PostToolUse** | After every tool call (file read, bash, etc.) | Runs the tool output through **curator** — decides if it's worth storing, then calls `slm remember` if yes |

All three hooks are gated by flags in `orbit.json` and environment variables so
you can disable them individually.

### What the brain modules do (orchestration/brain/)

These run silently in the background. You never call them directly.

| Module | Triggered by | What it does |
|---|---|---|
| **curator.py** | PostToolUse hook | Decides if tool output is worth storing. First pass: Python regex (rejects read-only tools, boilerplate, short text). Second pass: asks Gemini Flash. Returns `(should_store, {topic, slug, tags})`. |
| **tagger.py** | curator, distiller | Extracts structured metadata from text via Gemini Flash: `topic` (phrase), `slug` (snake_case), `tags` (list). Falls back to Python slug generation if Gemini unavailable. |
| **distiller.py** | `/knowledge-sync` command | Takes raw markdown notes from `Knowledge/notes/` and de-fluffs them into dense, factual prose suitable for slm storage. |
| **synthesizer.py** | SessionStart + UserPromptSubmit hooks | Takes top-k slm recall results and condenses them into a single `<memory>…</memory>` XML block. Has a fast path: if ≤2 results, all high-score, short text — skips Gemini and formats directly. |
| **linker.py** | `/knowledge-sync` command | Cross-links your markdown notes by adding `## Related` backlinks based on slm recall. |
| **policy.py** | All brain modules | Defines which Gemini model tier each module uses (all currently Flash for speed; linker uses the full Pro→Flash→Default cascade). |

### Gemini model cascade

Every Gemini call tries models in this order, falling through on rate-limit or error:

```
gemini-2.5-pro → gemini-2.5-flash → default (CLI picks)
```

Set `GEMINI_DISABLED=1` to skip all Gemini calls and run slm-only.

### Manual memory commands

Inside Claude Code:

```
/recall <query>       Query slm and show top-8 results synthesized by Gemini
/remember <text>      Store text into slm with auto-generated metadata
/knowledge-sync       Ingest Knowledge/notes/ → slm, then cross-link with linker
```

### Where data lives

```
Knowledge/                  ← gitignored, stays on your machine
├── notes/                  ← your markdown files (source of truth)
├── progress/               ← session logs, TODOs, implementation plans
├── ingested/               ← JSONL logs of what the PostToolUse hook stored
│   └── hook-errors.log     ← any hook failures
└── slm_data/               ← slm's SQLite databases
    ├── memory.db           ← main memory store
    ├── pending.db          ← write queue
    ├── audit_chain.db      ← audit log
    ├── config.json         ← slm config (embedding model, cross-encoder toggle)
    └── logs/               ← daemon and async worker logs
```

### Disabling auto-triggers

```bash
# Environment variables (per-session)
ORBITS_NO_SESSION_RECALL=1   # skip SessionStart injection
ORBITS_NO_PROMPT_INJECT=1    # skip per-prompt injection
ORBITS_NO_AUTO_REMEMBER=1    # skip PostToolUse auto-store

# Or toggle in orbit.json (persists across sessions)
{
  "memory": {
    "triggers": {
      "session_recall": false,
      "prompt_inject": false,
      "auto_remember": false
    }
  }
}
```

---

## Part 2 — Multi-agent orchestrator

### Overview

The orchestrator is a separate Python runtime in `orchestrator/`. It is not
always running — you launch it explicitly. Once running, two agents stay alive
permanently and worker agents spawn and exit per task.

```
You type a task
   └─→ Agent 1 (Claude, always-on)
         ├─→ asks Agent 2 for context
         ├─→ Planner: breaks task into steps via Gemini
         ├─→ Prompter: writes per-model prompts via Gemini
         ├─→ spawns Worker agents (one per step)
         │     Workers → TASK_COMPLETE → Agent 1
         └─→ assembles output → prints to you
                           → sends to Agent 2 for storage
```

### Starting the orchestrator

```bash
# Copy and fill in API keys first
cp orchestrator/.env.example orchestrator/.env

# Option A — tmux layout (recommended, shows all agents)
bash orchestrator/tmux/layout.sh

# Option B — single terminal
.venv/bin/python orchestrator/main.py
```

### The message bus

Everything goes through `orchestrator/core/bus.py` — a SQLite file at
`orchestrator/bus.db`. No agent calls another agent's Python functions directly.

Every message is logged to `orchestrator/logs/bus.log`.

**Message types:**

| Type | Direction | Meaning |
|---|---|---|
| `TASK_ASSIGN` | Agent1 → Worker | Here is your prompt + context, go execute |
| `TASK_COMPLETE` | Worker → Agent1 | Here is my output |
| `TASK_FAILED` | Worker → Agent1 | I failed, here is why |
| `CONTEXT_REQUEST` | Any → Agent2 | I need context about X |
| `CONTEXT_DELIVERY` | Agent2 → Any | Here is your scoped context |
| `CONTEXT_WARN` | Agent2 → Any | Your context window is 75% full |
| `CONTEXT_COMPRESS` | Agent2 → Any | Context critical — summarise and reset history |
| `MODEL_RECOMMENDATION` | Agent2 → Agent1 | Use this model for this task type |
| `HEARTBEAT` | Any → bus | I am alive, here are my metrics |
| `PLAN_REQUEST` | Agent1 → Planner | Break this task down |
| `PLAN_READY` | Planner → Prompter | Here is the plan |
| `PROMPTS_READY` | Prompter → Agent1 | Here are the prompts |

### Agent 1 — task orchestrator

**Model:** claude-sonnet-4-6 (via Anthropic SDK when acting as worker, and for planning/prompting)

**What it does in order:**
1. Receives your task (currently from stdin — TODO: middle-man interface)
2. Sends `CONTEXT_REQUEST` to Agent 2, waits up to 10s for `CONTEXT_DELIVERY`
3. Planner subagent calls Claude with task + context → structured `Plan`
4. Asks Agent 2 for `MODEL_RECOMMENDATION` per step type
5. Prompter subagent calls Claude → writes a prompt adapted to each target model's style
6. Spawns one worker per plan step (parallel if `plan.parallelizable`)
7. Waits for all workers → retries failures once with a fallback model
8. Assembles step outputs into final result, prints it
9. Sends `TASK_COMPLETE` to Agent 2 for storage

**Heartbeat:** every 5 seconds → updates its row in `agent_state` table

### Agent 2 — knowledge brain

**Model:** gemini-2.5-pro (via Gemini CLI, same as rest of orbits)

**What it does (continuous loop every 2s):**

| Message received | Action |
|---|---|
| `CONTEXT_REQUEST` | Query slm for relevant chunks → `synthesize()` → query Graphify for related nodes → send `CONTEXT_DELIVERY` |
| `HEARTBEAT` | Update `agent_state` table with sender's metrics |
| `TASK_COMPLETE` | Run output through `curator.should_remember()` → if yes, store in slm + add Graphify node + update ModelOracle |
| `TASK_FAILED` | Log failure, update ModelOracle with negative outcome |

**Background task — ContextWindowGuard (every 10s):**
- Reads `context_pct` from `agent_state` for each agent
- ≥ 75%: sends `CONTEXT_WARN`
- ≥ 90%: sends `CONTEXT_COMPRESS` (agent should summarise conversation history and reset)
- Resets when agent drops back below 75%

### Workers

Workers are stateless — they receive prompt, context, model, and task type in
the `TASK_ASSIGN` message. They execute, send `TASK_COMPLETE` or `TASK_FAILED`,
then exit.

| Worker | SDK used | Required key |
|---|---|---|
| `ClaudeWorker` | `anthropic` | `ANTHROPIC_API_KEY` |
| `GeminiWorker` | `orchestration.gemini` CLI | none (uses existing Gemini auth) |
| `OpenAIWorker` | `openai` | `OPENAI_API_KEY` |
| `OpenAIWorker` (DeepSeek) | `openai` compatible | `DEEPSEEK_API_KEY` |

### Model routing

`orchestrator/agents/agent2/model_oracle.py` reads
`orchestrator/knowledge/model_index.json` and scores models by task type.

Default routing:

| Task type | Default model |
|---|---|
| planning | claude-sonnet-4-6 |
| coding | deepseek-coder |
| research | gemini-2.5-pro |
| summarization | gemini-2.5-pro |
| formatting / quick | claude-haiku-4-5 |
| review | claude-sonnet-4-6 |
| long_context | gemini-2.5-pro |

Edit `model_index.json` to change costs, strengths, or add new models. The
oracle updates scores automatically based on real task outcomes via
`update_from_experience()`.

### Prompt style adaption

The Prompter writes different prompt styles per model family:

| Family | Style |
|---|---|
| claude-* | XML tags (`<task>`, `<context>`, `<instructions>`), explicit chain-of-thought |
| gemini-* | Markdown sections, verbose context OK, numbered steps |
| gpt-* | Direct imperative, markdown, concrete examples |
| deepseek-* | Code-first, minimal prose, types and edge cases in spec |

### tmux layout

```
┌─────────────────────┬─────────────────────┐
│   Agent 1 stdout    │   Agent 2 stdout    │
├──────────┬──────────┼─────────────────────┤
│  Planner │ Prompter │   Status Monitor    │
│  stdout  │  stdout  │   (rich dashboard)  │
├──────────┴──────────┴─────────────────────┤
│   > your task input here                  │
└───────────────────────────────────────────┘
```

`bash orchestrator/tmux/layout.sh` — creates session
`bash orchestrator/tmux/attach.sh` — reattaches after detach

---

## Part 3 — RAM management

### Why SLM uses a lot of RAM

SLM loads `nomic-ai/nomic-embed-text-v1.5` (a 768-dimensional sentence
transformer) into memory as a persistent daemon process. With the cross-encoder
reranker also enabled, you get 2–3 worker processes each holding model weights.

### Current configuration (after 2026-04-12 session)

- **Cross-encoder disabled** (`Knowledge/slm_data/config.json`: `use_cross_encoder: false`) — eliminates the reranker worker, saves ~250 MB, slight recall quality tradeoff
- **RAM tracker enabled** (`orbit.json`: `tracker.enabled: true`, `ram_limit_mb: 5120`)

### Watchdog script

`scripts/ram_watchdog.sh` monitors SLM worker RSS and kills them when over limit:

```bash
# Single check
bash scripts/ram_watchdog.sh

# Continuous (every 30s, run in a spare terminal or tmux pane)
bash scripts/ram_watchdog.sh --loop
```

To change the limit, edit `orbit.json`:
```json
{ "tracker": { "ram_limit_mb": 6144, "enabled": true } }
```

### Kill SLM workers manually

```bash
pkill -9 -f "superlocalmemory.core" || true
# or specifically:
pkill -9 -f embedding_worker
pkill -9 -f reranker_worker
# or via slm:
.venv/bin/slm reap
```

### Note on macOS RAM measurement

`vm_stat` active+wired+speculative pages ≠ user process RAM. macOS uses
aggressive file cache which inflates those numbers. The watchdog measures
actual process RSS via `ps -eo pid,rss,command` — a much more accurate figure
for "RAM orbits is using".

---

## Part 4 — Configuration reference

### orbit.json

```json
{
  "name": "orbit",
  "memory": {
    "engine": "superlocalmemory",
    "brain": "gemini",
    "brain_disabled": false,       // set true to disable all Gemini brain calls
    "triggers": {
      "session_recall": true,      // SessionStart hook
      "prompt_inject": true,       // UserPromptSubmit hook
      "auto_remember": true        // PostToolUse hook
    }
  },
  "tracker": {
    "ram_limit_mb": 5120,          // watchdog threshold
    "enabled": true
  }
}
```

### orchestrator/.env

```bash
ANTHROPIC_API_KEY=      # required for ClaudeWorker
OPENAI_API_KEY=         # required for OpenAI/DeepSeek workers
DEFAULT_WORKER_MODEL=claude-haiku-4-5
AGENT2_MODEL=gemini-2.5-pro
MAX_WORKER_TOKENS=4000
CONTEXT_WARN_THRESHOLD=0.75
CONTEXT_HARD_LIMIT=0.90
LOG_LEVEL=INFO
```

### Knowledge/slm_data/config.json

```json
{
  "embedding": {
    "model_name": "nomic-ai/nomic-embed-text-v1.5",  // change for lighter model
    "dimension": 768
  },
  "retrieval": {
    "use_cross_encoder": false   // true = better recall, ~250MB extra RAM
  }
}
```

---

## Part 5 — Scripts reference

| Script | What it does |
|---|---|
| `scripts/bootstrap.sh` | One-time setup: venv, slm editable install, slm setup, symlink Knowledge/slm_data |
| `scripts/doctor.sh` | Health check: verifies slm, Gemini, OMC, hooks all working |
| `scripts/link_omc.sh` | Re-links OMC fork agents/skills/hooks into `.claude/` |
| `scripts/knowledge_ingest.py` | Ingests `Knowledge/notes/` into slm (called by `/knowledge-sync`) |
| `scripts/ram_watchdog.sh` | Kills SLM workers when RSS exceeds `ram_limit_mb` |

---

## Part 6 — What to do when things break

| Symptom                       | Fix                                                                                                                             |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| No `<memory>` block appearing | Check `Knowledge/ingested/hook-errors.log`. Run `bash scripts/doctor.sh`. Verify slm daemon is running: `.venv/bin/slm status`. |
| Gemini calls silently failing | Run `gemini auth` in your terminal. Or set `GEMINI_DISABLED=1` to fall back to slm-only.                                        |
| SLM using too much RAM        | `pkill -9 -f superlocalmemory.core` then `bash scripts/ram_watchdog.sh --loop`                                                  |
| Orchestrator bus errors       | Delete `orchestrator/bus.db` — it gets recreated on next start.                                                                 |
| Worker always fails           | Check `orchestrator/logs/bus.log`. Verify API key is set in `orchestrator/.env`.                                                |
| Planner returns None          | Gemini unavailable — check cascade, or set `GEMINI_DISABLED=1` (planner will use single-step fallback plan).                    |
| Context never delivered       | Agent 2 may not be running. Check tmux Agent 2 pane or restart with `python orchestrator/main.py`.                              |
