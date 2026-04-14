# orbits

**All-in-one memory + orchestration workspace.**

Three layers, clean separation:

| Layer | Tool | Role |
|---|---|---|
| Orchestration | Claude via OMC | Dispatches work, runs tool calls, talks to the user |
| Memory brain | Gemini CLI (cascade) | Curates, distills, synthesizes, links, tags |
| Retrieval substrate | SuperLocalMemory (slm) | Fast local semantic search — SQLite + embeddings |

---

## Repo structure

```
orbits/
├── orchestration/       ← Gemini brain modules, slm wrapper, addons
│   └── brain/           ← curator, tagger, distiller, synthesizer, linker, policy
├── orchestrator/        ← Multi-agent orchestrator (Agent 1 + Agent 2 + workers)
│   ├── core/            ← SQLite bus, registry, metrics, monitor
│   ├── agents/
│   │   ├── agent1/      ← Planner, Prompter, WorkerManager, Executor
│   │   └── agent2/      ← Knowledge brain, Distiller, CtxGuard, ModelOracle
│   ├── workers/         ← Claude, Gemini, OpenAI, DeepSeek workers
│   ├── knowledge/       ← model_index.json, user_profile.json
│   ├── tmux/            ← layout.sh, attach.sh
│   └── main.py          ← startup entrypoint
├── vendor/              ← editable submodules: slm and OMC
├── scripts/             ← bootstrap, doctor, ingest
├── .claude/             ← repo-local OMC agents, commands, hooks
└── Knowledge/           ← GITIGNORED — your notes and slm's SQLite db
```

`Knowledge/` is gitignored so your memories never leave your machine.

---

## Setup (one-time)

```bash
# 1. Clone with submodules
git clone --recurse-submodules https://github.com/YOUR_USERNAME/orbits.git
cd orbits

# 2. Bootstrap: venv, install slm editable, run slm setup, wire Knowledge/slm_data
bash scripts/bootstrap.sh

# 3. Health check
bash scripts/doctor.sh

# 4. Wire OMC fork into .claude/
bash scripts/link_omc.sh
```

Prerequisites: Python 3.10+, `gemini` CLI installed and authenticated, `git` 2.28+.

---

## Daily use — memory layer

- Drop markdown files into `Knowledge/notes/` — they become searchable memory.
- Run `/knowledge-sync` in Claude Code to ingest new notes into slm.
- Memory is injected automatically on session start and with each prompt (see `.claude/hooks/`).
- Use `/recall <query>` and `/remember <text>` as escape hatches.

---

## Multi-agent orchestrator

Run the full orchestrator stack:

```bash
# Option A — full tmux layout (recommended)
bash orchestrator/tmux/layout.sh

# Option B — direct
.venv/bin/python orchestrator/main.py
```

The orchestrator starts two agents:
- **Agent 1** — takes your task, plans it, spawns workers, assembles the result
- **Agent 2** — knowledge brain: distills context, guards context windows, stores results

See `orchestrator/USER_MANUAL.md` for a full walkthrough.

---

## Model cascade (Gemini)

Brain modules (Agent 2) call Gemini via the CLI in this order:
`gemini-2.5-pro` → `gemini-2.5-flash` → default

Rate-limit or error on one tier falls through to the next. Set
`GEMINI_DISABLED=1` to run in slm-only mode (no Gemini calls, no synthesis).

---

## Disabling auto-triggers

Each hook checks an env flag before running:

| Env var | Disables |
|---|---|
| `ORBITS_NO_SESSION_RECALL=1` | SessionStart memory injection |
| `ORBITS_NO_PROMPT_INJECT=1` | per-prompt memory injection |
| `ORBITS_NO_AUTO_REMEMBER=1` | PostToolUse auto-store |

---

## Customising

- **OMC fork:** `vendor/oh-my-claudecode/` is an editable git submodule. Modify → `bash scripts/link_omc.sh`.
- **slm fork:** `vendor/superlocalmemory/` is editable via `pip install -e`. Extensions go in `orchestration/addons/`.
- **Orchestrator config:** copy `orchestrator/.env.example` → `orchestrator/.env` and set keys + model defaults.
- **Model routing:** edit `orchestrator/knowledge/model_index.json` to adjust model capabilities and costs.

---

## RAM management

SLM's embedding workers load a full transformer model (~1.5–2 GB per instance). To keep usage in check:

```bash
# Kill all SLM workers immediately
pkill -9 -f "superlocalmemory.core" || true

# Run watchdog (single check)
bash scripts/ram_watchdog.sh

# Run watchdog continuously (every 30s)
bash scripts/ram_watchdog.sh --loop
```

The watchdog reads `ram_limit_mb` from `orbit.json` (currently 5120 MB) and kills workers when exceeded. Cross-encoder reranking is disabled by default (`Knowledge/slm_data/config.json`) to save ~250 MB.

---

## Super Important Todos

- [ ] **Add a proper UI** — Build a frontend/GUI for the orchestration and memory system.
- [ ] **Link with opencode** — Integrate orbits with the opencode framework.
- [ ] **Hook up to Obsidian** — Sync `Knowledge/notes/` and backlinks with an Obsidian vault.
- [x] **RAM tracker with limits** — `scripts/ram_watchdog.sh` + cross-encoder disabled + `orbit.json` tracker enabled.
- [ ] **Authenticate gemini CLI** — `gemini auth` (interactive, must be done by user).
- [ ] **End-to-end smoke test** — drop a note, run `/knowledge-sync`, verify `<memory>` injection.
- [ ] **OMC fork personalisation** — `git -C vendor/oh-my-claudecode checkout -b my-orbits`.
- [ ] **slm fork personalisation** — `git -C vendor/superlocalmemory checkout -b my-orbits`.
- [ ] **(Optional)** Fix lightgbm/libomp warning: `brew install libomp`.
- [ ] **(Optional v2)** `/forget <query>` command, weekly digest, scheduled linker cron.
- [ ] **Wire Graphify Python API** — replace subprocess calls in `orchestrator/agents/agent2/knowledge.py`.
- [ ] **Middle-man interface** — replace stdin input in `orchestrator/agents/agent1/executor.py` with a proper API or bus input.
- [ ] **Tests** — populate `tests/` with the Phase 7 checklist from the implementation plan.
