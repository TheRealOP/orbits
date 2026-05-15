"""Optional AKMS knowledge integration — Orbit §4."""
from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

from .config import KnowledgeConfig
from .models import Issue

log = logging.getLogger(__name__)


class KnowledgeClient:
    """Wraps akms CLI calls — all methods degrade gracefully on failure."""

    def __init__(self, cfg: KnowledgeConfig):
        self._cfg = cfg
        self._available = False

    async def check_availability(self) -> bool:
        """§4.5: run akms status, set self._available, return result."""
        if not self._cfg.enabled:
            return False
        if not shutil.which("akms"):
            log.warning("knowledge_unavailable: akms binary not found in PATH")
            return False

        config_path = self._cfg.config_path
        if not config_path or not Path(config_path).exists():
            log.warning("knowledge_unavailable: akms config not found at %s", config_path)
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                "akms", "--config", config_path, "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10.0)
            self._available = proc.returncode == 0
        except Exception as exc:
            log.warning("knowledge_status_failed: %s", exc)
            self._available = False

        if not self._available:
            log.warning("knowledge_unavailable: akms status returned non-zero; disabling for session")
        else:
            log.info("knowledge_available: akms status ok")
        return self._available

    @property
    def available(self) -> bool:
        return self._available and self._cfg.enabled

    async def get_context(self, issue: Issue) -> tuple[str, list[str]]:
        """§4.2: search then ask; return (context_text, graph_node_ids).

        Returns ('', []) on any failure — must not block dispatch.
        """
        if not self.available or not self._cfg.inject_context:
            return "", []

        config_path = self._cfg.config_path
        query = f"{issue.title} {issue.description or ''}".strip()

        # Step 1: search
        search_section: str | None = None
        search_nodes: list[str] = []
        try:
            proc = await asyncio.create_subprocess_exec(
                "akms", "--config", config_path, "search", query,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            output = stdout.decode(errors="replace").strip()
            if output:
                lines = output.splitlines()
                search_nodes = [l.strip() for l in lines if l.strip()]
                # First line is typically the best section
                if self._cfg.context_sections:
                    search_section = self._cfg.context_sections[0]
                elif search_nodes:
                    search_section = search_nodes[0]
        except Exception as exc:
            log.warning("knowledge_search_failed issue=%s error=%s", issue.identifier, exc)
            return "", []

        if not search_section:
            return "", search_nodes

        # Step 2: ask for synthesized context
        context_text = ""
        try:
            ask_query = f"{issue.title}: {issue.description or ''}"
            proc = await asyncio.create_subprocess_exec(
                "akms", "--config", config_path, "ask", search_section, ask_query,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            context_text = stdout.decode(errors="replace").strip()
        except Exception as exc:
            log.warning("knowledge_ask_failed issue=%s section=%s error=%s",
                        issue.identifier, search_section, exc)
            return "", search_nodes

        # Truncate to context_max_tokens (rough: 4 chars/token)
        max_chars = self._cfg.context_max_tokens * 4
        if len(context_text) > max_chars:
            context_text = context_text[:max_chars]

        return context_text, search_nodes

    async def ingest_result(
        self,
        issue: Issue,
        agent_name: str,
        turn_count: int,
        total_tokens: int,
        workspace_path: str,
    ) -> str | None:
        """§4.3: ingest run summary; returns node ID or None on failure.

        Failure MUST NOT fail the run.
        """
        if not self.available or not self._cfg.ingest_results:
            return None

        summary = (
            f"# Run Summary: {issue.identifier}\n\n"
            f"**Issue:** {issue.identifier} — {issue.title}\n"
            f"**Agent:** {agent_name}\n"
            f"**Turn count:** {turn_count}\n"
            f"**Total tokens:** {total_tokens}\n"
            f"**Status:** completed\n"
            f"**Workspace:** {workspace_path}\n"
            f"**URL:** {issue.url or 'n/a'}\n"
            f"**Graph nodes:** {', '.join(issue.graph_nodes) or 'none'}\n"
        )

        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".md", delete=False, encoding="utf-8"
            ) as f:
                f.write(summary)
                tmp_path = f.name

            proc = await asyncio.create_subprocess_exec(
                "akms", "--config", self._cfg.config_path, "ingest", tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            node_id = stdout.decode(errors="replace").strip()
            log.info("knowledge_ingested issue=%s node_id=%s", issue.identifier, node_id)
            return node_id or None
        except Exception as exc:
            log.warning("knowledge_ingest_failed issue=%s error=%s", issue.identifier, exc)
            return None
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass
