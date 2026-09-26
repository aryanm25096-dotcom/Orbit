"""
integrator/integrator.py — Workspace Integrator + Recovery Loop
================================================================
Phase 5 implementation target.  Phase 0 stub only.
"""

from __future__ import annotations

from pathlib import Path

from models.spec import FailureContext, MasterSpecification, TaskContract


class Integrator:
    """
    Merges worker workspaces into a single output tree.
    On test failure: produces FailureContext → targeted rework → re-verify.
    Respects MAX_RECOVERY_LOOPS from the gateway config.
    """

    def __init__(self, output_dir: Path, max_recovery_loops: int = 3) -> None:
        self.output_dir = output_dir
        self.max_recovery_loops = max_recovery_loops

    def merge(self, completed_tasks: list[TaskContract]) -> Path:
        """Merge per-task workspaces into self.output_dir. Returns merged path."""
        raise NotImplementedError("Phase 5")

    def verify(self) -> tuple[bool, str]:
        """Run the repository test suite against the merged output."""
        raise NotImplementedError("Phase 5")

    def build_failure_context(
        self, task: TaskContract, error_output: str, attempt: int
    ) -> FailureContext:
        raise NotImplementedError("Phase 5")
