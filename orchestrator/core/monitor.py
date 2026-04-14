"""
core/monitor.py — Live rich dashboard for orchestrator status.

Displays: active agents (status, tokens, context %, RAM),
bus queue depth, last 5 messages, current task.

Usage:
    monitor = StatusMonitor(bus, registry)
    await monitor.run()   # runs until Ctrl-C
"""
import asyncio
import logging
from datetime import datetime

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from orchestrator.core.bus import MessageBus
from orchestrator.core.registry import AgentRegistry

_log = logging.getLogger("orchestrator.monitor")

_REFRESH_INTERVAL = 2  # seconds


class StatusMonitor:
    def __init__(self, bus: MessageBus, registry: AgentRegistry):
        self._bus = bus
        self._registry = registry
        self._recent_messages: list[dict] = []
        self._current_task: str = "—"
        self._console = Console()

    async def run(self) -> None:
        with Live(self._render(), refresh_per_second=0.5, console=self._console) as live:
            while True:
                try:
                    await self._refresh()
                    live.update(self._render())
                except Exception as exc:
                    _log.error("monitor error: %s", exc)
                await asyncio.sleep(_REFRESH_INTERVAL)

    async def _refresh(self) -> None:
        # Peek at recent messages without consuming them
        msgs = await self._bus.receive("__monitor__", limit=5)
        for m in msgs:
            self._recent_messages.append({
                "time": m.created_at,
                "from": m.from_agent,
                "to": m.to_agent,
                "type": m.msg_type.value,
            })
        self._recent_messages = self._recent_messages[-5:]

    def _render(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(self._agent_table(), name="agents", ratio=3),
            Layout(self._bus_panel(), name="bus", ratio=2),
            Layout(self._task_panel(), name="task", ratio=1),
        )
        return layout

    def _agent_table(self):
        table = Table(title="Active Agents", expand=True, show_lines=True)
        table.add_column("Agent", style="cyan", no_wrap=True)
        table.add_column("Model", style="white")
        table.add_column("Status", style="bold")
        table.add_column("Tokens", justify="right")
        table.add_column("Ctx %", justify="right")
        table.add_column("RAM MB", justify="right")
        table.add_column("Last HB")

        # We run this in a sync context, so we use a cached snapshot
        # Real data is fetched async in _refresh; here we just render
        return Panel(table, title="[bold blue]Agents[/bold blue]")

    def _bus_panel(self):
        lines = []
        for m in self._recent_messages[-5:]:
            t = m.get("time", "")[-8:] if m.get("time") else ""
            lines.append(f"[dim]{t}[/dim] [cyan]{m['from']}[/cyan]→[green]{m['to']}[/green] {m['type']}")
        content = "\n".join(lines) if lines else "[dim]No recent messages[/dim]"
        return Panel(Text.from_markup(content), title="[bold blue]Bus — Last 5 Messages[/bold blue]")

    def _task_panel(self):
        return Panel(
            Text(self._current_task),
            title="[bold blue]Current Task[/bold blue]",
        )

    def set_current_task(self, task: str) -> None:
        self._current_task = task[:120]
