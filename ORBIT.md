---
tracker:
  kind: linear
  api_key: $LINEAR_API_KEY
  project_slug: orbit-b35efc1720ee
  active_states: ["Todo", "In Progress"]
  terminal_states: ["Done", "Cancelled", "Duplicate", "Canceled"]
  handoff_state: "Human Review"
  handoff_on_success: true

polling:
  interval_ms: 30000

workspace:
  root: /tmp/orbit_workspaces

hooks:
  timeout_ms: 60000
  before_run: |
    if [ ! -f pyproject.toml ]; then
      tar \
        --exclude=.git \
        --exclude=.venv \
        --exclude=.pytest_cache \
        --exclude='__pycache__' \
        -C /home/ojaspolakhare/GitHub/orbits \
        -cf - . | tar -xf -
    fi

agents:
  default: claude
  registry:
    claude:
      capabilities: ["reasoning", "planning", "code", "writing"]
      weight: 10
    codex:
      capabilities: ["code", "implementation", "testing"]
      weight: 8

routing:
  command: null
  timeout_ms: 5000
  fallback: claude

runner:
  kind: cli
  commands:
    claude: "/usr/bin/claude -p --verbose --output-format stream-json"
    codex: "codex exec --full-auto -"
  timeout_ms: 3600000
  stall_timeout_ms: 300000

agent:
  max_concurrent_agents: 3
  max_concurrent_by_agent:
    claude: 2
    codex: 2
  max_turns: 10
  max_retry_backoff_ms: 60000

knowledge:
  enabled: false

---

{% if context %}
## Knowledge Context

{{ context }}

---
{% endif %}

You are working on task {{ issue.identifier }}: **{{ issue.title }}**

Assigned agent: {{ agent }}
{% if attempt %}Attempt: {{ attempt }}{% endif %}

## Task Description

{{ issue.description or "No description provided." }}

## Instructions

1. Understand the task from the description above.
2. Complete the work.
3. When done, move the Linear issue to **Human Review** state using the `linear_graphql` tool or by running the appropriate Linear CLI command.
