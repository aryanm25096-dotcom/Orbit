"""
telemetry/collector.py — Run Telemetry Collector (Phase 7 Enhanced)
====================================================================
PRD §4.8, §5.3:
  "Tokens, tool calls, wall-clock time, and recovery-loop counts are logged
   per task and per role from the first prototype, not bolted on for the demo.
   This is the data source for the Section 3.2 hero artifact."

Structured JSON logging across all phases:
  - Token usage: prompt eval (in), eval (out), total
  - Tool calls: count, tool names, success/failure
  - Recovery loops: loop count, candidate scores
  - Wall-clock time: per-phase and total
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("orbit.telemetry")


@dataclass
class RunEvent:
    timestamp: float
    phase: str
    event: str
    metadata: dict[str, Any] = field(default_factory=dict)


class TelemetryCollector:
    """Accumulates token counts, wall-clock time, tool calls, and recovery loops."""

    def __init__(self) -> None:
        self._start: float = time.monotonic()
        self._events: list[RunEvent] = []
        self.total_tokens_in: int = 0
        self.total_tokens_out: int = 0
        self.total_tool_calls: int = 0
        self.total_recovery_loops: int = 0
        self.phase_durations: dict[str, float] = {}

    def log(self, phase: str, event: str, **metadata: Any) -> None:
        """Record an arbitrary telemetry event."""
        self._events.append(
            RunEvent(
                timestamp=round(time.monotonic() - self._start, 3),
                phase=phase,
                event=event,
                metadata=metadata,
            )
        )

    def record_llm_call(
        self,
        phase: str,
        tokens_in: int,
        tokens_out: int,
        model: str,
        provider: str = "ollama",
        endpoint: str = "http://localhost:11434",
    ) -> None:
        """Record LLM prompt evaluation and output tokens."""
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.log(
            phase,
            "llm_call",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            total_tokens=tokens_in + tokens_out,
            model=model,
            provider=provider,
            endpoint=endpoint,
        )

    def record_tool_call(self, phase: str, tool: str, success: bool, **kwargs: Any) -> None:
        """Record a tool invocation through ToolGateway."""
        self.total_tool_calls += 1
        self.log(phase, "tool_call", tool=tool, success=success, **kwargs)

    def record_recovery_loop(self, attempt: int, task_id: str, passing_tests: int = 0) -> None:
        """Record an Integrator targeted recovery pass."""
        self.total_recovery_loops += 1
        self.log(
            "integrator",
            "recovery_loop",
            attempt=attempt,
            task_id=task_id,
            passing_tests=passing_tests,
        )

    def record_phase_duration(self, phase: str, duration_sec: float) -> None:
        """Record wall-clock duration for a given pipeline phase."""
        self.phase_durations[phase] = round(duration_sec, 2)
        self.log(phase, "phase_complete", duration_sec=round(duration_sec, 2))

    def wall_clock_sec(self) -> float:
        """Elapsed monotonic seconds since creation."""
        return round(time.monotonic() - self._start, 2)

    def report(self) -> dict[str, Any]:
        """Generate structured JSON report for this run."""
        tool_counts: dict[str, int] = {}
        for ev in self._events:
            if ev.event == "tool_call":
                tname = ev.metadata.get("tool", "unknown")
                tool_counts[tname] = tool_counts.get(tname, 0) + 1

        return {
            "wall_clock_sec": self.wall_clock_sec(),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_tokens": self.total_tokens_in + self.total_tokens_out,
            "total_tool_calls": self.total_tool_calls,
            "tool_calls_breakdown": tool_counts,
            "total_recovery_loops": self.total_recovery_loops,
            "phase_durations": self.phase_durations,
            "events": [asdict(e) for e in self._events],
        }

    def save(self, path: Path) -> None:
        """Save report to disk as pretty JSON."""
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.report(), indent=2), encoding="utf-8")
        logger.info(f"[telemetry] Saved telemetry report → {path}")
