"""
architect/architect.py — Master Architect
==========================================
Phase 2 implementation target.  Phase 0 stub only.

Responsibility: requirement + repo index → MasterSpecification (spec + task graph).
"""

from __future__ import annotations

from models.spec import MasterSpecification, TaskContract


class MasterArchitect:
    """Converts a raw requirement into a MasterSpecification with a task graph."""

    def __init__(self, model: str, base_url: str = "http://localhost:11434") -> None:
        self.model = model
        self.base_url = base_url

    def plan(self, requirement: str, repo_summary: dict) -> MasterSpecification:
        """
        Call the LLM (one or two-step), parse its JSON, return MasterSpecification.
        Phase 2 implements this.
        """
        raise NotImplementedError("Phase 2")
