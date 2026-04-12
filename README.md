# orbits

**All-in-one memory + orchestration workspace.**

Three layers, clean separation:

| Layer | Tool | Role |
|---|---|---|
| Orchestration | Claude via OMC | Dispatches work, runs tool calls, talks to the user |
| Memory brain | Gemini CLI (cascade) | Curates, distills, synthesizes, links, tags |
| Retrieval substrate | SuperLocalMemory (slm) | Fast local semantic search — SQLite + embeddings |

## Public vs private

```
orbits/
├── orchestration/   ← committed — Gemini brain modules, slm wrapper, addons
├── vendor/          ← committed — editable submodules for slm and OMC
├── scripts/         ← committed — bootstrap, doctor, ingest
├── .claude/         ← committed — repo-local OMC agents, commands, hooks
└── Knowledge/       ← GITIGNORED — your notes and slm's SQLite db live here
```

`Knowledge/` is gitignored so your memories never leave your machine.
The orchestration half is what this repo publishes.

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

Prerequisites: Python 3.10+, `gemini` CLI installed and authenticated,
`git` 2.28+.

## Daily use

- Drop markdown files into `Knowledge/notes/` — they become searchable memory.
- Run `/knowledge-sync` in Claude Code to ingest new notes into slm.
- Memory is injected automatically on session start and with each prompt
  (see `.claude/hooks/`).
- Use `/recall <query>` and `/remember <text>` as escape hatches.

## Customising OMC

`vendor/oh-my-claudecode/` is an editable git submodule. Make changes there,
run `bash scripts/link_omc.sh` to re-link, and commit your fork branch.

## Customising slm / writing addons

`vendor/superlocalmemory/` is an editable git submodule installed with
`pip install -e`. Put your own extensions under `orchestration/addons/`.

## Model cascade (Gemini)

Brain modules call Gemini via the CLI in this order:
`gemini-2.5-pro` → `gemini-2.5-flash` → `gemini-2.0-flash-lite`

Rate-limit or error on one tier falls through to the next. Set
`GEMINI_DISABLED=1` to run in slm-only mode (no Gemini calls, no synthesis).

## Disabling auto-triggers

Each hook checks an env flag before running:

| Env var | Disables |
|---|---|
| `ORBITS_NO_SESSION_RECALL=1` | SessionStart memory injection |
| `ORBITS_NO_PROMPT_INJECT=1` | per-prompt memory injection |
| `ORBITS_NO_AUTO_REMEMBER=1` | PostToolUse auto-store |

## Super Important Todos

- [ ] **Add a proper UI**: Build a frontend/GUI for the orchestration and memory system.
- [ ] **Link with opencode**: Integrate orbits with the opencode framework.
- [ ] **Hook up to Obsidian**: Seamlessly sync the `Knowledge/notes/` directory and backlinks with an Obsidian vault.
- [ ] **RAM tracker with limits**: Implement memory profiling and set strict limits on resource/RAM consumption for the orchestration agents and slm.
- [ ] **Authenticate gemini CLI** — `gemini auth` (interactive, must be done by user). Without it, brain modules degrade to slm-only mode.
- [ ] **End-to-end smoke test** — drop a note in `Knowledge/notes/`, run `/knowledge-sync`, start fresh Claude Code session, verify `<memory>` injection.
- [ ] **OMC fork personalisation** — `git -C vendor/oh-my-claudecode checkout -b my-orbits`, customize agents/skills.
- [ ] **slm fork personalisation** — `git -C vendor/superlocalmemory checkout -b my-orbits`.
- [ ] **(Optional)** Fix lightgbm/libomp warning: `brew install libomp`.
- [ ] **(Optional v2)** `/forget <query>` command, weekly digest, scheduled linker cron.
