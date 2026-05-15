"""Main orchestration loop — Symphony §16 + Orbit §3.

Implements the Orchestrator class that owns the full dispatch lifecycle:
startup → poll tick → dispatch → worker → reconcile → retry.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import watchfiles

from .config import (
    ServiceConfig,
    build_service_config,
    load_workflow,
    validate_dispatch_config,
)
from .hooks import run_hook, run_hook_best_effort
from .knowledge import KnowledgeClient
from .models import (
    AgentTotals,
    Issue,
    LiveSession,
    OrchestratorState,
    RetryEntry,
    RunningEntry,
)
from .prompt import render_prompt
from .routing import select_agent_with_router
from .runner.base import RunnerEvent, RunnerEventType
from .runner.cli import CLIRunner
from .tracker import make_tracker
from .tracker.base import TrackerAdapter
from .workspace import WorkspaceManager

log = logging.getLogger(__name__)


class Orchestrator:
    """Drives the full Orbit dispatch lifecycle.

    All state mutations happen on the asyncio event loop (single-threaded).
    Background tasks post results through an asyncio.Queue.
    """

    def __init__(self, workflow_path: Path, port: int | None = None) -> None:
        self._workflow_path = workflow_path.resolve()
        self._port_override = port

        # Populated during startup
        self._cfg: ServiceConfig | None = None
        self._prompt_template: str = ""
        self._state: OrchestratorState | None = None
        self._workspace_manager: WorkspaceManager | None = None
        self._tracker: TrackerAdapter | None = None
        self._knowledge: KnowledgeClient | None = None
        self._cli_runner: CLIRunner | None = None

        # Worker completion signals: (issue_id, exit_code, error_msg | None)
        self._worker_done_queue: asyncio.Queue[tuple[str, int, str | None]] = asyncio.Queue()

        # Map issue_id → asyncio.Task for running workers
        self._worker_tasks: dict[str, asyncio.Task[None]] = {}

        # Map issue_id → asyncio.TimerHandle for scheduled retries
        self._retry_handles: dict[str, asyncio.TimerHandle] = {}

        # Background tasks managed by the orchestrator
        self._background_tasks: set[asyncio.Task[Any]] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Main async loop.  Runs until the task is cancelled."""
        await self._startup()
        assert self._state is not None

        # Schedule the first poll immediately
        self._schedule_poll(delay_ms=0)

        try:
            while True:
                # Drain the worker-done queue; this is the only place we block
                try:
                    issue_id, exit_code, error_msg = await asyncio.wait_for(
                        self._worker_done_queue.get(),
                        timeout=self._state.poll_interval_ms / 1000.0,
                    )
                    await self._handle_worker_done(issue_id, exit_code, error_msg)
                except asyncio.TimeoutError:
                    # Timeout is fine — just the poll interval passing
                    pass
        except asyncio.CancelledError:
            log.info("orchestrator_cancelled: shutting down")
            await self._shutdown()
            raise

    def get_snapshot(self) -> dict[str, Any]:
        """Return current runtime state for API/monitoring."""
        if self._state is None:
            return {"generated_at": _now_iso(), "counts": {"running": 0, "retrying": 0}}

        state = self._state

        running_entries = []
        for issue_id, entry in state.running.items():
            running_entries.append({
                "issue_id": issue_id,
                "identifier": entry.issue.identifier,
                "title": entry.issue.title,
                "agent": entry.agent_name,
                "attempt": entry.attempt,
                "workspace_path": entry.workspace_path,
                "started_at": entry.started_at.isoformat(),
                "session": _session_dict(entry.session),
            })

        retrying_entries = []
        for issue_id, rentry in state.retry_attempts.items():
            retrying_entries.append({
                "issue_id": issue_id,
                "identifier": rentry.identifier,
                "attempt": rentry.attempt,
                "due_at": rentry.due_at.isoformat(),
                "error": rentry.error,
            })

        totals = state.agent_totals
        return {
            "generated_at": _now_iso(),
            "counts": {
                "running": len(state.running),
                "retrying": len(state.retry_attempts),
            },
            "running": running_entries,
            "retrying": retrying_entries,
            "codex_totals": {
                "input_tokens": totals.input_tokens,
                "output_tokens": totals.output_tokens,
                "total_tokens": totals.total_tokens,
                "seconds_running": totals.seconds_running,
            },
            "rate_limits": state.rate_limits,
            "knowledge_available": state.knowledge_available,
        }

    # ------------------------------------------------------------------
    # Startup — Symphony §16.1
    # ------------------------------------------------------------------

    async def _startup(self) -> None:
        log.info("orchestrator_startup workflow=%s", self._workflow_path)

        # 1. Load workflow
        workflow = load_workflow(self._workflow_path)
        self._prompt_template = workflow.prompt_template

        # 2. Build service config
        cfg = build_service_config(workflow)

        # Apply CLI port override
        if self._port_override is not None:
            cfg.server.port = self._port_override

        # 3. Validate dispatch config — hard failure on invalid
        errors = validate_dispatch_config(cfg)
        if errors:
            msg = "; ".join(errors)
            log.error("startup_validation_failed: %s", msg)
            raise RuntimeError(f"invalid_dispatch_config: {msg}")

        self._cfg = cfg

        # 4. Create workspace manager
        self._workspace_manager = WorkspaceManager(cfg.workspace.root)

        # 5. Create tracker
        self._tracker = make_tracker(cfg)

        # 6. Create knowledge client and check availability
        self._knowledge = KnowledgeClient(cfg.knowledge)
        knowledge_available = await self._knowledge.check_availability()

        # 7. Create CLI runner
        self._cli_runner = CLIRunner(cfg.runner)

        # 8. Build initial orchestrator state
        self._state = OrchestratorState(
            poll_interval_ms=cfg.polling.interval_ms,
            max_concurrent_agents=cfg.agent.max_concurrent_agents,
            knowledge_available=knowledge_available,
        )

        # 9. Startup terminal workspace cleanup
        await self._cleanup_terminal_workspaces()

        # 10. Start watchfiles watcher for WORKFLOW.md changes
        watcher_task = asyncio.create_task(
            self._watch_workflow(), name="workflow_watcher"
        )
        self._background_tasks.add(watcher_task)
        watcher_task.add_done_callback(self._background_tasks.discard)

        log.info(
            "orchestrator_ready poll_interval_ms=%d max_concurrent=%d knowledge=%s",
            cfg.polling.interval_ms,
            cfg.agent.max_concurrent_agents,
            knowledge_available,
        )

    async def _cleanup_terminal_workspaces(self) -> None:
        """Remove workspaces for issues already in terminal states (§16.1 step 9)."""
        assert self._tracker is not None
        assert self._workspace_manager is not None
        assert self._cfg is not None

        terminal_states = self._cfg.tracker.terminal_states
        try:
            terminal_issues = await self._tracker.fetch_issues_by_states(terminal_states)
        except Exception as exc:
            log.warning("startup_terminal_fetch_failed: %s", exc)
            return

        for issue in terminal_issues:
            if not issue.identifier:
                continue
            try:
                await self._workspace_manager.remove_for_issue(
                    issue.identifier,
                    before_remove=self._cfg.hooks.before_remove,
                    hook_timeout_ms=self._cfg.hooks.timeout_ms,
                )
            except Exception as exc:
                log.warning(
                    "startup_cleanup_failed identifier=%s error=%s",
                    issue.identifier, exc,
                )

    async def _shutdown(self) -> None:
        """Cancel all workers and background tasks gracefully."""
        log.info("orchestrator_shutdown: cancelling %d workers", len(self._worker_tasks))
        for task in list(self._worker_tasks.values()):
            task.cancel()
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks.values(), return_exceptions=True)

        for handle in self._retry_handles.values():
            handle.cancel()
        self._retry_handles.clear()

        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)

    # ------------------------------------------------------------------
    # Poll scheduling
    # ------------------------------------------------------------------

    def _schedule_poll(self, delay_ms: int = 0) -> None:
        """Schedule a poll tick after delay_ms milliseconds."""
        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            delay_ms / 1000.0,
            lambda: asyncio.ensure_future(self._poll_tick()),
        )
        # We don't track this handle since a new poll is always scheduled from within _poll_tick

    # ------------------------------------------------------------------
    # Poll tick — Symphony §16.2
    # ------------------------------------------------------------------

    async def _poll_tick(self) -> None:
        if self._state is None or self._cfg is None or self._tracker is None:
            return

        try:
            # 1. Reconcile running issues (stall detection + tracker state refresh)
            await self._reconcile_running_issues()

            # 2. Re-validate dispatch config
            errors = validate_dispatch_config(self._cfg)
            if errors:
                log.warning("poll_dispatch_skipped validation_errors=%s", errors)
            else:
                # 3. Fetch candidate issues
                candidates = await self._fetch_candidates()

                # 4. Sort: priority ASC (null last), created_at oldest first, identifier lex
                candidates.sort(key=_issue_sort_key)

                # 5. Dispatch eligible issues while slots remain
                for issue in candidates:
                    if not self._has_global_slot():
                        break
                    if self._is_eligible(issue):
                        await self._dispatch_issue(issue, attempt=None)

        except Exception as exc:
            log.exception("poll_tick_error: %s", exc)
        finally:
            # Re-schedule next poll
            self._schedule_poll(delay_ms=self._state.poll_interval_ms)

    async def _fetch_candidates(self) -> list[Issue]:
        assert self._tracker is not None
        try:
            return await self._tracker.fetch_candidate_issues()
        except Exception as exc:
            log.warning("fetch_candidates_failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Dispatch eligibility — Symphony §8.2 + Orbit §3.6
    # ------------------------------------------------------------------

    def _is_eligible(self, issue: Issue) -> bool:
        """Return True if this issue may be dispatched right now."""
        assert self._cfg is not None
        assert self._state is not None

        # Must have required fields
        if not (issue.id and issue.identifier and issue.title and issue.state):
            return False

        cfg = self._cfg
        state = self._state

        # State check
        active = cfg.tracker.active_states
        terminal = cfg.tracker.terminal_states
        if issue.state not in active or issue.state in terminal:
            return False

        # Not already running or claimed
        if issue.id in state.running or issue.id in state.claimed:
            return False

        # Global slot check
        if not self._has_global_slot():
            return False

        # Per-state concurrency check
        state_key = issue.state.lower()
        max_by_state = cfg.agent.max_concurrent_agents_by_state.get(state_key)
        if max_by_state is not None:
            current_in_state = sum(
                1 for e in state.running.values()
                if e.issue.state.lower() == state_key
            )
            if current_in_state >= max_by_state:
                return False

        # Per-agent concurrency check (Orbit §3.6) — check the currently-selected agent if possible
        # We do a quick pre-check using the default agent; full check happens post agent-selection
        # Checking is best-effort here; definitive check is done in dispatch after agent selection.

        # Blocker check: if state == "Todo", no non-terminal blockers allowed
        if issue.state == "Todo":
            for blocker in issue.blocked_by:
                if blocker.state and blocker.state not in terminal:
                    return False

        return True

    def _has_global_slot(self) -> bool:
        assert self._state is not None
        return (
            self._state.max_concurrent_agents - len(self._state.running) > 0
        )

    def _agent_has_slot(self, agent_name: str) -> bool:
        assert self._cfg is not None
        assert self._state is not None
        max_by_agent = self._cfg.agent.max_concurrent_by_agent.get(agent_name)
        if max_by_agent is None:
            return True
        current = self._state.agent_running_counts.get(agent_name, 0)
        return current < max_by_agent

    # ------------------------------------------------------------------
    # Dispatch one issue — Symphony §16.4
    # ------------------------------------------------------------------

    async def _dispatch_issue(self, issue: Issue, attempt: int | None) -> None:
        assert self._cfg is not None
        assert self._state is not None
        assert self._workspace_manager is not None
        assert self._cli_runner is not None

        cfg = self._cfg
        state = self._state

        # 1. Select agent
        try:
            agent_name = await select_agent_with_router(issue, cfg.agents, cfg.routing)
        except Exception as exc:
            log.warning(
                "agent_selection_failed issue=%s error=%s", issue.identifier, exc
            )
            return

        # Per-agent slot check (definitive)
        if not self._agent_has_slot(agent_name):
            log.debug(
                "dispatch_skipped_agent_slot issue=%s agent=%s", issue.identifier, agent_name
            )
            return

        # 2. Knowledge context injection
        context = ""
        graph_nodes: list[str] = []
        if self._knowledge and self._knowledge.available and cfg.knowledge.inject_context:
            try:
                context, graph_nodes = await self._knowledge.get_context(issue)
                issue.knowledge_context = context
                issue.graph_nodes = graph_nodes
            except Exception as exc:
                log.warning(
                    "knowledge_context_failed issue=%s error=%s", issue.identifier, exc
                )

        # 3. Render prompt
        try:
            prompt = render_prompt(
                self._prompt_template,
                issue,
                attempt,
                agent=agent_name,
                context=context,
                graph_nodes=graph_nodes,
                workspace_path=str(self._workspace_manager.path_for(issue.identifier)),
            )
        except Exception as exc:
            log.error(
                "prompt_render_failed issue=%s error=%s", issue.identifier, exc
            )
            return

        # 4. Run before_run hook (workspace path may not exist yet — use root)
        ws_path = self._workspace_manager.path_for(issue.identifier)
        if ws_path.exists():
            try:
                await run_hook(
                    "before_run",
                    cfg.hooks.before_run,
                    ws_path,
                    cfg.hooks.timeout_ms,
                )
            except Exception as exc:
                log.warning(
                    "before_run_hook_failed issue=%s error=%s", issue.identifier, exc
                )
                return

        # 5. Create asyncio Task for the worker
        task = asyncio.create_task(
            self._run_worker(
                issue=issue,
                attempt=attempt,
                agent_name=agent_name,
                prompt=prompt,
            ),
            name=f"worker-{issue.identifier}",
        )
        self._worker_tasks[issue.id] = task
        task.add_done_callback(
            lambda t, iid=issue.id: self._worker_tasks.pop(iid, None)
        )

        # 6. Update state
        state.running[issue.id] = RunningEntry(
            issue=issue,
            attempt=attempt,
            workspace_path=str(self._workspace_manager.path_for(issue.identifier)),
            started_at=datetime.now(timezone.utc),
            agent_name=agent_name,
        )
        state.claimed.add(issue.id)
        state.agent_running_counts[agent_name] = (
            state.agent_running_counts.get(agent_name, 0) + 1
        )

        # 7. Remove from retry queue
        state.retry_attempts.pop(issue.id, None)

        log.info(
            "issue_dispatched issue=%s agent=%s attempt=%s",
            issue.identifier, agent_name, attempt,
        )

    # ------------------------------------------------------------------
    # Worker task — Symphony §16.4 / §16.5
    # ------------------------------------------------------------------

    async def _run_worker(
        self,
        issue: Issue,
        attempt: int | None,
        agent_name: str,
        prompt: str,
    ) -> None:
        """Full lifecycle for a single issue run.  Posts to worker_done_queue on exit."""
        assert self._cfg is not None
        assert self._workspace_manager is not None
        assert self._cli_runner is not None

        cfg = self._cfg
        start_time = datetime.now(timezone.utc)
        exit_code: int = -1
        error_msg: str | None = None

        try:
            # 1. Create workspace
            ws = await self._workspace_manager.create_for_issue(
                issue.identifier,
                after_create=cfg.hooks.after_create,
                hook_timeout_ms=cfg.hooks.timeout_ms,
            )

            # 2. before_run hook (workspace now exists)
            try:
                await run_hook(
                    "before_run",
                    cfg.hooks.before_run,
                    ws.path,
                    cfg.hooks.timeout_ms,
                )
            except Exception as exc:
                log.warning(
                    "before_run_hook_failed issue=%s error=%s", issue.identifier, exc
                )

            # 3. Run the agent
            def _on_event(event: RunnerEvent) -> None:
                self._handle_runner_event(issue.id, event)

            exit_code = await self._cli_runner.run(
                agent_name,
                prompt,
                ws.path,
                on_event=_on_event,
            )
            log.info("DEBUG_runner_returned exit_code=%d", exit_code)

            # 4. after_run hook (best effort)
            await run_hook_best_effort(
                "after_run",
                cfg.hooks.after_run,
                ws.path,
                cfg.hooks.timeout_ms,
            )

            # 5. Knowledge ingest
            if (
                self._knowledge
                and self._knowledge.available
                and cfg.knowledge.ingest_results
            ):
                # We need to know the current state of the issue to decide whether to ingest
                # We'll re-fetch after run — if tracker call fails, skip silently
                try:
                    refreshed = await self._tracker.fetch_issue_states_by_ids([issue.id])  # type: ignore[union-attr]
                    refreshed_issue = refreshed[0] if refreshed else None
                    if refreshed_issue and refreshed_issue.state in cfg.knowledge.ingest_on_states:
                        session = None
                        if self._state:
                            entry = self._state.running.get(issue.id)
                            session = entry.session if entry else None

                        await self._knowledge.ingest_result(
                            issue=issue,
                            agent_name=agent_name,
                            turn_count=session.turn_count if session else 0,
                            total_tokens=session.total_tokens if session else 0,
                            workspace_path=str(ws.path),
                        )
                except Exception as exc:
                    log.warning(
                        "knowledge_ingest_failed issue=%s error=%s", issue.identifier, exc
                    )

        except asyncio.CancelledError:
            log.info("worker_cancelled issue=%s", issue.identifier)
            error_msg = "worker_cancelled"
            exit_code = -99
            raise
        except Exception as exc:
            log.exception("worker_error issue=%s error=%s", issue.identifier, exc)
            error_msg = str(exc)
            exit_code = -1
        finally:
            # Account for time
            elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
            if self._state:
                self._state.agent_totals.seconds_running += elapsed

            # Signal orchestrator
            log.info("DEBUG_worker_queue_put issue=%s exit_code=%d error=%s", issue.identifier, exit_code, error_msg)
            try:
                self._worker_done_queue.put_nowait((issue.id, exit_code, error_msg))
            except Exception:
                pass

    def _handle_runner_event(self, issue_id: str, event: RunnerEvent) -> None:
        """Update live session data from runner events (called from worker task)."""
        if self._state is None:
            return

        entry = self._state.running.get(issue_id)
        if entry is None:
            return

        if entry.session is None:
            entry.session = LiveSession(
                session_id="",
                thread_id="",
                turn_id="",
            )

        sess = entry.session
        sess.last_event = event.event.value
        sess.last_event_at = event.timestamp
        if event.pid is not None:
            sess.pid = event.pid
        if event.message:
            sess.last_message = event.message

        if event.event == RunnerEventType.TURN_COMPLETED:
            sess.turn_count += 1

    # ------------------------------------------------------------------
    # Worker done handling — Symphony §16.6
    # ------------------------------------------------------------------

    async def _handle_worker_done(
        self,
        issue_id: str,
        exit_code: int,
        error_msg: str | None,
    ) -> None:
        assert self._state is not None
        assert self._cfg is not None

        entry = self._state.running.pop(issue_id, None)
        if entry is None:
            # Already cleaned up (e.g., by reconciliation)
            return

        agent_name = entry.agent_name
        attempt = entry.attempt or 0

        # Decrement agent slot counter
        current = self._state.agent_running_counts.get(agent_name, 0)
        self._state.agent_running_counts[agent_name] = max(0, current - 1)

        log.info(
            "worker_done issue=%s exit_code=%d attempt=%d error=%s",
            entry.issue.identifier, exit_code, attempt, error_msg,
        )

        if exit_code == 0:
            # Normal exit: schedule continuation retry with 1000ms delay (attempt=1)
            log.info(
                "issue_completed_scheduling_continuation issue=%s", entry.issue.identifier
            )
            self._schedule_retry(
                issue=entry.issue,
                attempt=1,
                delay_ms=1000,
                error=None,
            )
        else:
            # Abnormal exit: exponential backoff retry
            max_backoff = self._cfg.agent.max_retry_backoff_ms
            backoff_ms = min(10000 * (2 ** max(0, attempt - 1)), max_backoff)
            log.warning(
                "issue_failed scheduling_retry issue=%s attempt=%d backoff_ms=%d",
                entry.issue.identifier, attempt + 1, backoff_ms,
            )
            self._schedule_retry(
                issue=entry.issue,
                attempt=attempt + 1,
                delay_ms=backoff_ms,
                error=error_msg or f"exit_code={exit_code}",
            )

    def _schedule_retry(
        self,
        issue: Issue,
        attempt: int,
        delay_ms: int,
        error: str | None,
    ) -> None:
        assert self._state is not None

        due_at = datetime.now(timezone.utc)
        retry_entry = RetryEntry(
            issue_id=issue.id,
            identifier=issue.identifier,
            attempt=attempt,
            due_at=due_at,
            error=error,
        )
        self._state.retry_attempts[issue.id] = retry_entry

        # Cancel any existing retry handle
        old_handle = self._retry_handles.pop(issue.id, None)
        if old_handle:
            old_handle.cancel()

        loop = asyncio.get_event_loop()
        handle = loop.call_later(
            delay_ms / 1000.0,
            lambda iid=issue.id, att=attempt: asyncio.ensure_future(
                self._fire_retry(iid, att)
            ),
        )
        self._retry_handles[issue.id] = handle

    async def _fire_retry(self, issue_id: str, attempt: int) -> None:
        """Execute a retry — Symphony §8.4, §16.6."""
        assert self._state is not None
        assert self._cfg is not None
        assert self._tracker is not None

        # Remove from retry queue and release claim so _is_eligible can evaluate freely
        self._state.retry_attempts.pop(issue_id, None)
        self._retry_handles.pop(issue_id, None)
        self._state.claimed.discard(issue_id)

        # 1. Fetch current candidate issues
        candidates = await self._fetch_candidates()
        issue = next((c for c in candidates if c.id == issue_id), None)

        # 2. Issue not found → claim already released above
        if issue is None:
            log.info("retry_issue_not_found issue_id=%s releasing_claim", issue_id)
            return

        terminal = self._cfg.tracker.terminal_states
        active = self._cfg.tracker.active_states

        # 3a. Not active → claim already released above
        if issue.state in terminal or issue.state not in active:
            log.info(
                "retry_issue_not_active issue=%s state=%s releasing_claim",
                issue.identifier, issue.state,
            )
            return

        # 3b. Global slots available → re-dispatch (dispatch re-adds to claimed)
        if self._has_global_slot() and self._is_eligible(issue):
            log.info(
                "retry_dispatching issue=%s attempt=%d", issue.identifier, attempt
            )
            await self._dispatch_issue(issue, attempt=attempt)
        else:
            # 3c. Slots full → requeue (re-add to claimed while waiting)
            log.info(
                "retry_requeued_no_slots issue=%s attempt=%d", issue.identifier, attempt
            )
            self._state.claimed.add(issue_id)
            self._schedule_retry(
                issue=issue,
                attempt=attempt,
                delay_ms=self._state.poll_interval_ms,
                error="no available orchestrator slots",
            )

    # ------------------------------------------------------------------
    # Reconciliation — Symphony §16.3
    # ------------------------------------------------------------------

    async def _reconcile_running_issues(self) -> None:
        assert self._state is not None
        assert self._cfg is not None
        assert self._tracker is not None

        now = datetime.now(timezone.utc)
        stall_timeout_ms = self._cfg.runner.stall_timeout_ms
        terminal = self._cfg.tracker.terminal_states
        active = self._cfg.tracker.active_states

        # Part A — Stall detection
        stalled_ids: list[str] = []
        for issue_id, entry in list(self._state.running.items()):
            sess = entry.session
            if sess and sess.last_event_at:
                elapsed_ms = (now - sess.last_event_at).total_seconds() * 1000
                if elapsed_ms > stall_timeout_ms:
                    log.warning(
                        "stall_detected issue=%s elapsed_ms=%.0f",
                        entry.issue.identifier, elapsed_ms,
                    )
                    stalled_ids.append(issue_id)

        for issue_id in stalled_ids:
            entry = self._state.running.get(issue_id)
            if entry is None:
                continue
            task = self._worker_tasks.get(issue_id)
            if task and not task.done():
                task.cancel()
            # Worker cancellation will post to done queue and handle retry scheduling
            # But we update state optimistically to avoid double dispatch
            running_entry = self._state.running.pop(issue_id, None)
            if running_entry:
                agent = running_entry.agent_name
                current = self._state.agent_running_counts.get(agent, 0)
                self._state.agent_running_counts[agent] = max(0, current - 1)
                # Schedule retry for stalled issue
                self._schedule_retry(
                    issue=running_entry.issue,
                    attempt=(running_entry.attempt or 0) + 1,
                    delay_ms=1000,
                    error="stall_detected",
                )

        # Part B — Tracker state refresh
        running_ids = list(self._state.running.keys())
        if not running_ids:
            return

        try:
            refreshed_issues = await self._tracker.fetch_issue_states_by_ids(running_ids)
        except Exception as exc:
            log.warning("reconcile_fetch_failed: %s", exc)
            return

        refreshed_map: dict[str, Issue] = {iss.id: iss for iss in refreshed_issues}

        for issue_id in list(self._state.running.keys()):
            entry = self._state.running.get(issue_id)
            if entry is None:
                continue

            refreshed = refreshed_map.get(issue_id)

            if refreshed is None:
                # Issue gone from tracker — cancel without cleanup
                log.warning(
                    "reconcile_issue_vanished issue=%s cancelling_task", entry.issue.identifier
                )
                task = self._worker_tasks.get(issue_id)
                if task and not task.done():
                    task.cancel()
                self._state.running.pop(issue_id, None)
                self._state.claimed.discard(issue_id)
                agent = entry.agent_name
                current = self._state.agent_running_counts.get(agent, 0)
                self._state.agent_running_counts[agent] = max(0, current - 1)

            elif refreshed.state in terminal:
                # Terminal state → cancel task + cleanup workspace
                log.info(
                    "reconcile_terminal issue=%s state=%s cleaning_up",
                    entry.issue.identifier, refreshed.state,
                )
                task = self._worker_tasks.get(issue_id)
                if task and not task.done():
                    task.cancel()
                self._state.running.pop(issue_id, None)
                self._state.claimed.discard(issue_id)
                agent = entry.agent_name
                current = self._state.agent_running_counts.get(agent, 0)
                self._state.agent_running_counts[agent] = max(0, current - 1)
                # Remove workspace in background
                asyncio.ensure_future(self._remove_workspace(entry.issue.identifier))

            elif refreshed.state in active:
                # Active state → update running entry's snapshot
                entry.issue = refreshed

            else:
                # Unknown state — cancel without cleanup
                log.warning(
                    "reconcile_unknown_state issue=%s state=%s cancelling",
                    entry.issue.identifier, refreshed.state,
                )
                task = self._worker_tasks.get(issue_id)
                if task and not task.done():
                    task.cancel()
                self._state.running.pop(issue_id, None)
                self._state.claimed.discard(issue_id)
                agent = entry.agent_name
                current = self._state.agent_running_counts.get(agent, 0)
                self._state.agent_running_counts[agent] = max(0, current - 1)

    async def _remove_workspace(self, identifier: str) -> None:
        """Best-effort workspace removal."""
        assert self._workspace_manager is not None
        assert self._cfg is not None
        try:
            await self._workspace_manager.remove_for_issue(
                identifier,
                before_remove=self._cfg.hooks.before_remove,
                hook_timeout_ms=self._cfg.hooks.timeout_ms,
            )
        except Exception as exc:
            log.warning("workspace_remove_failed identifier=%s error=%s", identifier, exc)

    # ------------------------------------------------------------------
    # Dynamic reload — Symphony §6.2
    # ------------------------------------------------------------------

    async def _watch_workflow(self) -> None:
        """Watch WORKFLOW.md for changes and reload config — Symphony §6.2."""
        log.info("workflow_watcher_started path=%s", self._workflow_path)
        try:
            async for changes in watchfiles.awatch(str(self._workflow_path)):
                for _change_type, _changed_path in changes:
                    log.info("workflow_changed: reloading config")
                    await self._reload_workflow()
        except asyncio.CancelledError:
            log.info("workflow_watcher_stopped")
            raise
        except Exception as exc:
            log.warning("workflow_watcher_error: %s", exc)

    async def _reload_workflow(self) -> None:
        """Reload workflow and rebuild config; keep running on errors — Symphony §6.2."""
        try:
            workflow = load_workflow(self._workflow_path)
            cfg = build_service_config(workflow)

            errors = validate_dispatch_config(cfg)
            if errors:
                log.warning("workflow_reload_invalid errors=%s keeping_old_config", errors)
                return

            self._cfg = cfg
            self._prompt_template = workflow.prompt_template

            if self._state:
                self._state.poll_interval_ms = cfg.polling.interval_ms
                self._state.max_concurrent_agents = cfg.agent.max_concurrent_agents

            # Recreate tracker and runner with new config
            self._tracker = make_tracker(cfg)
            self._cli_runner = CLIRunner(cfg.runner)
            # Note: workspace manager root change would be a more complex migration;
            # we only update the config but keep the existing manager pointing to the same root.
            # Knowledge client availability is preserved unless enabled changes.
            if self._knowledge is not None:
                self._knowledge = KnowledgeClient(cfg.knowledge)
                await self._knowledge.check_availability()
                if self._state:
                    self._state.knowledge_available = self._knowledge.available

            log.info(
                "workflow_reloaded poll_interval_ms=%d max_concurrent=%d",
                cfg.polling.interval_ms, cfg.agent.max_concurrent_agents,
            )
        except Exception as exc:
            log.warning("workflow_reload_error: %s keeping_old_config", exc)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _issue_sort_key(issue: Issue):
    """Sort: priority ASC (null last), created_at oldest first, identifier lex."""
    priority = issue.priority if issue.priority is not None else 999_999
    created = issue.created_at.isoformat() if issue.created_at else "9999-99-99"
    return (priority, created, issue.identifier)


def _session_dict(session: LiveSession | None) -> dict[str, Any] | None:
    if session is None:
        return None
    return {
        "session_id": session.session_id,
        "thread_id": session.thread_id,
        "pid": session.pid,
        "last_event": session.last_event,
        "last_event_at": session.last_event_at.isoformat() if session.last_event_at else None,
        "last_message": session.last_message,
        "input_tokens": session.input_tokens,
        "output_tokens": session.output_tokens,
        "total_tokens": session.total_tokens,
        "turn_count": session.turn_count,
    }
