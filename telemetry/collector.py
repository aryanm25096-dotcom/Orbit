"""
telemetry/collector.py — Run Telemetry Collector
==================================================
Phase 7 surfaces this as a report; it is wired in from Phase 0 so every
phase accumulates evidence automatically.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class RunEvent:
    timestamp: float
    phase: str
    event: str
    metadata: dict = field(default_factory=dict)


class TelemetryCollector:
    """Accumulates token counts, wall-clock time, and tool-call log for one run."""

    def __init__(self) -> None:
        self._start: float = time.monotonic()
        self._events: list[RunEvent] = []
        self.total_tokens_in: int = 0
        self.total_tokens_out: int = 0
        self.total_tool_calls: int = 0

    def log(self, phase: str, event: str, **metadata) -> None:
        self._events.append(
            RunEvent(
                timestamp=time.monotonic() - self._start,
                phase=phase,
                event=event,
                metadata=metadata,
            )
        )

    def record_llm_call(
        self, phase: str, tokens_in: int, tokens_out: int, model: str
    ) -> None:
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.log(
            phase,
            "llm_call",
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            model=model,
        )

    def record_tool_call(self, phase: str, tool: str, success: bool) -> None:
        self.total_tool_calls += 1
        self.log(phase, "tool_call", tool=tool, success=success)

    def wall_clock_sec(self) -> float:
        return time.monotonic() - self._start

    def report(self) -> dict:
        return {
            "wall_clock_sec": round(self.wall_clock_sec(), 2),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_tool_calls": self.total_tool_calls,
            "events": [asdict(e) for e in self._events],
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.report(), indent=2))
        print(f"[telemetry] saved → {path}")
