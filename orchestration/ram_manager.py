"""Lightweight RAM management helpers for the current session."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import psutil

SOFT_LIMIT_BYTES = 8 * 1024**3
HARD_LIMIT_BYTES = 10 * 1024**3
DEFAULT_TOP_N = 8
STATE_FILE = Path(".omc/state/ram_manager_status.json")
NON_ESSENTIAL_PATTERNS = (
    "embedding_worker",
    "reranker_worker",
    "superlocalmemory.cli.daemon",
    "superlocalmemory.core",
    "graphify",
)


@dataclass(slots=True)
class ProcessInfo:
    pid: int
    name: str
    rss_mb: float
    cmdline: str


@dataclass(slots=True)
class RamSnapshot:
    total_used_bytes: int
    total_used_gb: float
    available_bytes: int
    total_bytes: int
    state: str
    top_processes: list[ProcessInfo]
    soft_limit_gb: float = 8.0
    hard_limit_gb: float = 10.0
    captured_at: str = ""


@dataclass(slots=True)
class GateDecision:
    allowed: bool
    state: str
    reason: str
    total_used_gb: float
    essential: bool


@dataclass(slots=True)
class EnforcementResult:
    attempted: bool
    actions: list[str]
    recovered: bool
    final_state: str
    final_used_gb: float
    failure: str = ""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def bytes_to_gb(value: int) -> float:
    return round(value / (1024**3), 2)


def classify_total_used_bytes(total_used_bytes: int) -> str:
    if total_used_bytes >= HARD_LIMIT_BYTES:
        return "critical"
    if total_used_bytes > SOFT_LIMIT_BYTES:
        return "warning"
    return "safe"


def _iter_processes() -> Iterable[ProcessInfo]:
    for proc in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            memory_info = proc.info.get("memory_info")
            if memory_info is None:
                continue
            rss_mb = memory_info.rss / (1024 * 1024)
            if rss_mb <= 0:
                continue
            cmdline = " ".join(proc.info.get("cmdline") or [])
            yield ProcessInfo(
                pid=proc.info["pid"],
                name=proc.info.get("name") or "",
                rss_mb=round(rss_mb, 2),
                cmdline=cmdline,
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue


def collect_snapshot(top_n: int = DEFAULT_TOP_N) -> RamSnapshot:
    vm = psutil.virtual_memory()
    total_used_bytes = int(vm.used)
    top_processes = sorted(_iter_processes(), key=lambda proc: proc.rss_mb, reverse=True)[:top_n]
    return RamSnapshot(
        total_used_bytes=total_used_bytes,
        total_used_gb=bytes_to_gb(total_used_bytes),
        available_bytes=int(vm.available),
        total_bytes=int(vm.total),
        state=classify_total_used_bytes(total_used_bytes),
        top_processes=top_processes,
        captured_at=_now_iso(),
    )


def snapshot_to_dict(snapshot: RamSnapshot) -> dict:
    data = asdict(snapshot)
    data["available_gb"] = bytes_to_gb(snapshot.available_bytes)
    data["total_gb"] = bytes_to_gb(snapshot.total_bytes)
    return data


def write_state(snapshot: RamSnapshot, path: Path = STATE_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = snapshot_to_dict(snapshot)
    tmp_path = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp_path.replace(path)
    return path


def gate_launch(*, essential: bool = False, snapshot: RamSnapshot | None = None) -> GateDecision:
    snapshot = snapshot or collect_snapshot()
    if snapshot.state == "critical":
        return GateDecision(False, snapshot.state, "RAM is at or above the hard cap; halt new launches.", snapshot.total_used_gb, essential)
    if snapshot.state == "warning" and not essential:
        return GateDecision(False, snapshot.state, "RAM is above the soft limit; only essential launches are allowed.", snapshot.total_used_gb, essential)
    reason = "RAM is within safe range." if snapshot.state == "safe" else "RAM warning state, but launch marked essential."
    return GateDecision(True, snapshot.state, reason, snapshot.total_used_gb, essential)


def _matches_non_essential_process(process: ProcessInfo) -> bool:
    haystack = f"{process.name} {process.cmdline}".lower()
    return any(pattern in haystack for pattern in NON_ESSENTIAL_PATTERNS)


def _termination_candidates(snapshot: RamSnapshot) -> list[ProcessInfo]:
    return [proc for proc in snapshot.top_processes if _matches_non_essential_process(proc)]


def enforce_limits(*, dry_run: bool = False) -> EnforcementResult:
    snapshot = collect_snapshot()
    if snapshot.state != "critical":
        return EnforcementResult(True, [], True, snapshot.state, snapshot.total_used_gb)

    actions: list[str] = []
    candidates = _termination_candidates(snapshot)
    if not candidates:
        return EnforcementResult(True, actions, False, snapshot.state, snapshot.total_used_gb, "No known non-essential heavy processes matched enforcement policy.")

    if dry_run:
        actions.extend(f"would terminate pid={proc.pid} name={proc.name}" for proc in candidates)
        return EnforcementResult(True, actions, False, snapshot.state, snapshot.total_used_gb, "Dry run only.")

    live_processes: list[psutil.Process] = []
    for candidate in candidates:
        try:
            proc = psutil.Process(candidate.pid)
            proc.terminate()
            live_processes.append(proc)
            actions.append(f"terminated pid={candidate.pid} name={candidate.name}")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            actions.append(f"failed pid={candidate.pid} name={candidate.name}: {exc}")

    if live_processes:
        gone, alive = psutil.wait_procs(live_processes, timeout=2)
        del gone
        for proc in alive:
            try:
                proc.kill()
                actions.append(f"killed pid={proc.pid}")
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                actions.append(f"kill-failed pid={proc.pid}: {exc}")

    post_snapshot = collect_snapshot()
    recovered = post_snapshot.state == "safe"
    failure = "" if recovered else "RAM remains above the safe threshold after enforcement."
    return EnforcementResult(True, actions, recovered, post_snapshot.state, post_snapshot.total_used_gb, failure)
