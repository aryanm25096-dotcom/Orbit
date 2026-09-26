"""
reviewer/reviewer.py — Global Reviewer
========================================
Phase 6 implementation target.  Phase 0 stub only.

Input : requirement + MasterSpecification + evidence (diffs, test results)
Output: ReviewVerdict with per-requirement PASS/FAIL table.
On FAIL: exactly one targeted rework TaskContract, never full regeneration.
"""

from __future__ import annotations

from models.spec import MasterSpecification, ReviewVerdict, TaskContract


class GlobalReviewer:
    def __init__(self, model: str, base_url: str = "http://localhost:11434") -> None:
        self.model = model
        self.base_url = base_url

    def review(
        self,
        spec: MasterSpecification,
        evidence: dict,  # {task_id: {"diff": str, "test_output": str}}
    ) -> ReviewVerdict:
        """
        Returns a ReviewVerdict.
        If verdict.passed is False, verdict.rework_tasks contains the
        smallest set of targeted rework tasks (≥1, each mapped to a worker).
        """
        raise NotImplementedError("Phase 6")
