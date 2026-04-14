# Orbits Orchestrator — User Manual

This document explains how to run the orchestrator, what happens at each step,
and what every component does behind the scenes.

---

## What is the orchestrator?

The orchestrator is a multi-agent system built on top of the orbits memory stack.
You give it a task. It decomposes the task into steps, assigns each step to the
right model, runs them in parallel (where possible), and returns a combined result —
while automatically storing what it learned for future sessions.

Two permanent agents run the whole time:

| Agent | Model | What it does |
|---|---|---|
| **Agent 1** | claude-sonnet-4-6 | Takes your task, plans it, generates prompts, manages workers, assembles output |
| **Agent 2** | gemini-2.5-pro | Answers context requests, guards context windows, stores results into memory |

Worker agents are spawned per task step and exit when done.

---

## Quick start

```bash
# Copy and fill in API keys
cp orchestrator/.env.example orchestrator/.env
# Edit orchestrator/.env — set ANTHROPIC_API_KEY at minimum

# Launch in tmux (recommended — shows all panes)
bash orchestrator/tmux/layout.sh

# Or run directly (single terminal)
.venv/bin/python orchestrator/main.py
```

Type your task at the `>` prompt and press Enter.

---

## What happens when you submit a task

```
You type: "Write a Python function that fetches the top 10 HN stories"
```

### Step 1 — Context fetch
Agent 1 sends a `CONTEXT_REQUEST` to Agent 2 with your task text.
Agent 2 queries SuperLocalMemory (SLM) for semantically similar past results,
synthesizes them into a `<memory>` block, and sends it back as `CONTEXT_DELIVERY`.
This gives Agent 1 relevant background before it starts planning.

### Step 2 — Planning
Agent 1's **Planner subagent** sends your task + context to Gemini (Pro → Flash cascade).
Gemini returns a structured plan:

```json
{
  "steps": [
    {
      "step_id": "step_1",
      "description": "Write the fetch_top_stories() function",
      "task_type": "coding",
      "recommended_model": "deepseek-coder",
      "depends_on": [],
      "estimated_tokens": 800
    }
  ],
  "parallelizable": true,
  "total_estimated_tokens": 800
}
```

### Step 3 — Model recommendation
Agent 1 asks Agent 2 for model recommendations per step type.
Agent 2's **ModelOracle** consults `orchestrator/knowledge/model_index.json`
and any past experience data to suggest the best model for each task type.

### Step 4 — Prompt generation
The **Prompter subagent** sends each step's description to Gemini,
which writes an optimised prompt adapted to the *target model's* style:

| Target model | Prompt style |
|---|---|
| claude-* | XML tags (`<task>`, `<context>`, `<instructions>`), chain-of-thought |
| gemini-* | Markdown sections, verbose context OK |
| gpt-* | Direct imperative, markdown, concrete examples |
| deepseek-* | Code-first, minimal prose, types and edge cases |

### Step 5 — Worker execution
The **WorkerManager** spawns one worker per plan step.
Workers are stateless — they receive everything in the `TASK_ASSIGN` message:
prompt, context, model, task type.

Worker types:
- `ClaudeWorker` — Anthropic SDK (`ANTHROPIC_API_KEY`)
- `GeminiWorker` — Gemini CLI subprocess (same path as the brain modules)
- `OpenAIWorker` — OpenAI SDK (`OPENAI_API_KEY`), also handles DeepSeek

If a worker fails, Agent 1 retries once with a fallback model (e.g. deepseek-coder → claude-haiku-4-5).

### Step 6 — Result assembly
Outputs from all steps are combined into a single response and printed.

### Step 7 — Storage
Agent 1 sends a `TASK_COMPLETE` message to Agent 2.
Agent 2 runs the output through **curator** (`orchestration.brain.curator.should_remember()`).
If the result is worth keeping, it's stored in SLM and linked in Graphify.
The ModelOracle records a success for the model used.

---

## Background: Agent 2 in detail

Agent 2 runs a continuous loop every 2 seconds, handling these message types:

| Message | Action |
|---|---|
| `CONTEXT_REQUEST` | Query SLM for relevant chunks → synthesize → send `CONTEXT_DELIVERY` |
| `HEARTBEAT` | Update `agent_state` table with token count, context %, RAM |
| `TASK_COMPLETE` | Curate → store in SLM → add graph node → update ModelOracle |
| `TASK_FAILED` | Log failure → update ModelOracle with negative outcome |

Agent 2 also runs the **ContextWindowGuard** as a background task (polls every 10s):

| Context fill | Action |
|---|---|
| < 75% | Nothing |
| ≥ 75% | Send `CONTEXT_WARN` to the agent |
| ≥ 90% | Send `CONTEXT_COMPRESS` — agent should summarise and reset history |

---

## The message bus

All inter-agent communication goes through `orchestrator/core/bus.py`.
No agent calls another agent's functions directly.

The bus is a SQLite file at `orchestrator/bus.db` (gitignored).
Every message is also written to `orchestrator/logs/bus.log`.

**Message flow for a typical task:**

```
User
  → Agent1: handle_task()
    → bus: CONTEXT_REQUEST → Agent2
    ← bus: CONTEXT_DELIVERY ← Agent2
    → bus: [internal] Planner builds Plan
    → bus: [internal] Prompter writes prompts
    → bus: TASK_ASSIGN → Worker(step_1)
    ← bus: TASK_COMPLETE ← Worker(step_1)
    → bus: TASK_COMPLETE → Agent2 (for storage)
  → User: assembled output
```

---

## Configuration

Copy `orchestrator/.env.example` to `orchestrator/.env`:

```bash
cp orchestrator/.env.example orchestrator/.env
```

Key variables:

| Variable | Default | Effect |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for Claude workers |
| `OPENAI_API_KEY` | — | Required for OpenAI/DeepSeek workers |
| `DEFAULT_WORKER_MODEL` | `claude-haiku-4-5` | Fallback model when oracle has no preference |
| `AGENT2_MODEL` | `gemini-2.5-pro` | Logged in registry; Gemini CLI cascade handles actual model selection |
| `MAX_WORKER_TOKENS` | `4000` | Max output tokens per worker call |
| `CONTEXT_WARN_THRESHOLD` | `0.75` | Fraction at which ctx_guard sends CONTEXT_WARN |
| `CONTEXT_HARD_LIMIT` | `0.90` | Fraction at which ctx_guard sends CONTEXT_COMPRESS |
| `LOG_LEVEL` | `INFO` | Python logging level |

---

## Model routing

Edit `orchestrator/knowledge/model_index.json` to adjust which models are available
and what they're good at. The ModelOracle uses the `strengths` and `best_for` fields
to score models for each task type.

Task types the oracle knows about:

| Task type | Default winner |
|---|---|
| `planning` | claude-sonnet-4-6 |
| `coding` | deepseek-coder |
| `research` | gemini-2.5-pro |
| `summarization` | gemini-2.5-pro |
| `formatting` | claude-haiku-4-5 |
| `review` | claude-sonnet-4-6 |
| `quick_task` | claude-haiku-4-5 |
| `long_context` | gemini-2.5-pro |

Over time, `update_from_experience()` adjusts scores based on actual success rates.

---

## tmux layout

`bash orchestrator/tmux/layout.sh` creates a session called `orchestrator`:

```
┌─────────────────────┬─────────────────────┐
│   Agent 1           │   Agent 2           │
│   (logs)            │   (logs)            │
├──────────┬──────────┼─────────────────────┤
│  Planner │ Prompter │   Status Monitor    │
│  (logs)  │  (logs)  │   (rich dashboard)  │
├──────────┴──────────┴─────────────────────┤
│   > (your task input here)                │
└───────────────────────────────────────────┘
```

- Top row: Agent 1 and Agent 2 stdout
- Middle row: Planner/Prompter logs + live metrics dashboard
- Bottom: the interactive prompt where you type tasks

Reattach after detaching: `bash orchestrator/tmux/attach.sh`

---

## File map

```
orchestrator/
├── main.py                    Startup entrypoint
├── .env.example               Config template
├── requirements.txt           Python deps (install into .venv)
├── USER_MANUAL.md             This file
│
├── core/
│   ├── bus.py                 Async SQLite message bus
│   ├── registry.py            Agent state tracker
│   ├── metrics.py             Token counting (tiktoken) + RAM (psutil)
│   ├── monitor.py             Rich live dashboard
│   └── config.py              Typed config from .env
│
├── agents/
│   ├── agent1/
│   │   ├── executor.py        Main loop — handles tasks end-to-end
│   │   ├── planner.py         Decomposes tasks into Plan dataclasses via Gemini
│   │   ├── prompter.py        Writes per-model-family optimised prompts
│   │   └── worker_manager.py  Spawns workers, waits for results, retries failures
│   └── agent2/
│       ├── knowledge.py       KnowledgeStore (SLM + Graphify) + Agent2 main loop
│       ├── distiller.py       Wraps orchestration.brain.distiller/synthesizer
│       ├── ctx_guard.py       Context window overflow monitor (10s poll)
│       ├── model_oracle.py    Model recommendation engine
│       └── researcher.py      Gemini-backed gap-filling research subagent
│
├── workers/
│   ├── base_worker.py         Abstract base — stateless, bus-driven
│   ├── claude_worker.py       Anthropic SDK
│   ├── gemini_worker.py       Gemini CLI (orchestration.gemini)
│   └── openai_worker.py       OpenAI SDK + DeepSeek compatible endpoint
│
├── knowledge/
│   ├── model_index.json       Model capabilities database
│   └── user_profile.json      User understanding map (maintained by Agent 2)
│
└── tmux/
    ├── layout.sh              Create and start the tmux session
    └── attach.sh              Reattach to existing session
```

---

## What's reused from orbits (not reimplemented)

The orchestrator deliberately wraps the existing `orchestration/` package
rather than duplicating it:

| Orchestrator uses | From orbits |
|---|---|
| `agents/agent2/distiller.py` | `orchestration.brain.distiller.distill()` + `synthesizer.synthesize()` |
| `agents/agent2/knowledge.py` (SLM) | `orchestration.memory.remember()` + `.recall()` |
| All Gemini calls in Agent 2 + Planner | `orchestration.gemini.ask()` + `.ask_json()` |
| Task result curation gate | `orchestration.brain.curator.should_remember()` |
| Metadata tagging | `orchestration.brain.tagger.tag()` |
| Model cascade constants | `orchestration.brain.policy.LINKER_CASCADE`, `DISTILLER_CASCADE` |
| Feature flags | `orchestration.config.is_gemini_disabled()` |

---

## Known limitations and TODOs

- **Graphify** calls in `agents/agent2/knowledge.py` are via subprocess with `# TODO: graphify Python API` markers — replace once a Python API is available.
- **Middle-man interface** in `agents/agent1/executor.py` (`# TODO: middle-man interface`) — currently reads from stdin. Wire to an API or bus input for production use.
- **Tests** — `tests/` is empty. The Phase 7 checklist in the implementation plan is the test spec.
- **Status monitor** in `core/monitor.py` renders a layout but does not yet fetch live agent data in the sync render path — extend `_agent_table()` with a cached snapshot updated by `_refresh()`.
