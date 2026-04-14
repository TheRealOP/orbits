"""
main.py — Orchestrator startup sequence.

1. Load .env
2. Init SQLite bus (create tables if not exist)
3. Start Agent 2 as background asyncio task
4. Wait 3 seconds for Agent 2 to initialize
5. Start Agent 1 as foreground task + status monitor as background task

Usage:
    python -m orchestrator.main
    # or
    cd /path/to/orbits && .venv/bin/python orchestrator/main.py
"""
import asyncio
import logging
import sys
from pathlib import Path

# Ensure repo root is on sys.path so 'orchestration' and 'orchestrator' are importable
_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.core.bus import MessageBus
from orchestrator.core.config import BUS_DB_PATH, LOG_LEVEL
from orchestrator.core.monitor import StatusMonitor
from orchestrator.core.registry import AgentRegistry
from orchestrator.agents.agent1.executor import Agent1
from orchestrator.agents.agent2.knowledge import Agent2

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
_log = logging.getLogger("orchestrator.main")


async def main() -> None:
    _log.info("Orchestrator starting — db=%s", BUS_DB_PATH)

    # 1. Init bus
    bus = MessageBus(BUS_DB_PATH)
    await bus.init()

    # 2. Init registry (shares bus connection)
    registry = AgentRegistry(bus)

    # 3. Start Agent 2 as background task
    agent2 = Agent2(bus, registry)
    asyncio.create_task(agent2.run(), name="agent2")
    _log.info("Agent 2 starting...")

    # 4. Wait for Agent 2 to initialize
    await asyncio.sleep(3)

    # 5. Start status monitor as background task
    monitor = StatusMonitor(bus, registry)
    asyncio.create_task(monitor.run(), name="monitor")

    # 6. Start Agent 1 as foreground task (blocks until user quits)
    agent1 = Agent1(bus, registry)
    await agent1.run()

    # Cleanup
    await bus.close()
    _log.info("Orchestrator stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutdown requested — goodbye.")
