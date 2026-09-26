"""
tests/test_phase6_reviewer.py — Phase 6 Global Reviewer & Contract Adherence Tests
===================================================================================
Tests Phase 6:
  1. Per-requirement PASS/FAIL table generated from one structured Ollama call (§4.6).
  2. Contract-adherence check comparing diffs against TaskContract acceptance criteria (§4.6, §4.9).
  3. Every FAIL generates exactly one targeted rework TaskContract (never full regeneration).
  4. ReviewVerdict PASS logic (all criteria true + contract_adherence true).
  5. Markdown format table generation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from models.spec import MasterSpecification, ReviewVerdict, TaskContract, TaskStatus, WorkerRole
from reviewer.reviewer import GlobalReviewer, review_implementation


# ---------------------------------------------------------------------------
# Fixtures & Canned Mock Callers
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_spec() -> MasterSpecification:
    t1 = TaskContract(
        task_id="task-be-1",
        role=WorkerRole.BACKEND,
        objective="Add multiply function to math.py",
        allowed_workspace="/tmp/ws1",
        expected_artifact="math.py",
        acceptance_criteria=["multiply(a, b) returns product", "type annotations included"],
        style_guide="Use snake_case and type annotations.",
    )
    t2 = TaskContract(
        task_id="task-be-2",
        role=WorkerRole.BACKEND,
        objective="Add test_multiply to test_math.py",
        allowed_workspace="/tmp/ws2",
        expected_artifact="test_math.py",
        acceptance_criteria=["test_multiply asserts 2*3 == 6"],
        depends_on=["task-be-1"],
    )
    return MasterSpecification(
        requirement="Add a multiply function and unit tests.",
        tasks=[t1, t2],
        style_guide="Use snake_case and type annotations.",
    )


def passing_mock_caller(prompt: str, system: str, **kwargs) -> tuple[str, int, int]:
    data = {
        "per_requirement_results": {
            "multiply(a, b) returns product": True,
            "type annotations included": True,
            "test_multiply asserts 2*3 == 6": True,
        },
        "contract_adherence": True,
        "contract_adherence_issues": [],
        "failing_reasons": {},
        "notes": "All requirements and contract acceptance criteria satisfied.",
    }
    return json.dumps(data), 100, 150


def failing_criterion_mock_caller(prompt: str, system: str, **kwargs) -> tuple[str, int, int]:
    data = {
        "per_requirement_results": {
            "multiply(a, b) returns product": True,
            "type annotations included": False,  # Failing criterion!
            "test_multiply asserts 2*3 == 6": True,
        },
        "contract_adherence": True,
        "contract_adherence_issues": [],
        "failing_reasons": {
            "type annotations included": "Function multiply lacks type annotations.",
        },
        "notes": "Missing type annotations on multiply function signature.",
    }
    return json.dumps(data), 100, 150


def failing_adherence_mock_caller(prompt: str, system: str, **kwargs) -> tuple[str, int, int]:
    data = {
        "per_requirement_results": {
            "multiply(a, b) returns product": True,
            "type annotations included": True,
            "test_multiply asserts 2*3 == 6": True,
        },
        "contract_adherence": False,  # Contract adherence failure!
        "contract_adherence_issues": [
            {
                "task_id": "task-be-1",
                "criterion": "Style compliance",
                "reason": "Used camelCase instead of required snake_case in helper.",
            }
        ],
        "failing_reasons": {},
        "notes": "Naming convention violation in helper function.",
    }
    return json.dumps(data), 100, 150


# ---------------------------------------------------------------------------
# Reviewer Tests
# ---------------------------------------------------------------------------

class TestGlobalReviewer:

    def test_passing_review_verdict(self, sample_spec: MasterSpecification):
        reviewer = GlobalReviewer(ollama_caller=passing_mock_caller)
        evidence = {
            "diff": "--- math.py\n+++ math.py\n+def multiply(a: int, b: int) -> int: return a * b\n",
            "test_output": "3 passed in 0.02s",
        }
        verdict = reviewer.review(
            requirement="Add a multiply function and unit tests.",
            spec=sample_spec,
            evidence=evidence,
        )

        assert verdict.passed is True
        assert verdict.contract_adherence is True
        assert len(verdict.rework_tasks) == 0
        assert all(verdict.per_requirement_results.values())

        # Check formatted table output
        table = reviewer.format_table(verdict)
        assert "✅ PASS" in table
        assert "All requirements and contract acceptance criteria satisfied" in table

    def test_failing_criterion_generates_targeted_rework_task(self, sample_spec: MasterSpecification):
        reviewer = GlobalReviewer(ollama_caller=failing_criterion_mock_caller)
        evidence = {
            "diff": "--- math.py\n+++ math.py\n+def multiply(a, b): return a * b\n",
            "test_output": "3 passed in 0.02s",
        }
        verdict = reviewer.review(
            requirement="Add a multiply function and unit tests.",
            spec=sample_spec,
            evidence=evidence,
        )

        assert verdict.passed is False
        assert verdict.per_requirement_results["type annotations included"] is False
        assert len(verdict.rework_tasks) == 1, "Must generate exactly one rework task for the failing criterion"

        rework = verdict.rework_tasks[0]
        assert rework.role == WorkerRole.BACKEND
        assert "task-be-1" in rework.task_id
        assert "type annotations" in rework.objective.lower()
        assert rework.acceptance_criteria == ["type annotations included"]

        table = reviewer.format_table(verdict)
        assert "❌ FAIL" in table
        assert "**Targeted Rework Tasks Generated:** 1" in table

    def test_failing_contract_adherence_generates_targeted_rework_task(self, sample_spec: MasterSpecification):
        reviewer = GlobalReviewer(ollama_caller=failing_adherence_mock_caller)
        evidence = {
            "diff": "--- math.py\n+++ math.py\n+def multiply(a: int, b: int) -> int: return a * b\n",
            "test_output": "3 passed in 0.02s",
        }
        verdict = reviewer.review(
            requirement="Add a multiply function and unit tests.",
            spec=sample_spec,
            evidence=evidence,
        )

        assert verdict.passed is False
        assert verdict.contract_adherence is False
        assert len(verdict.rework_tasks) == 1
        rework = verdict.rework_tasks[0]
        assert "task-be-1" in rework.task_id
        assert "Style compliance" in rework.acceptance_criteria or "compliance" in rework.acceptance_criteria[0].lower()

    def test_review_implementation_functional_interface(self, sample_spec: MasterSpecification):
        evidence = {
            "diff": "--- math.py\n+++ math.py\n+def multiply(a: int, b: int) -> int: return a * b\n",
            "test_output": "3 passed in 0.02s",
        }
        verdict = review_implementation(
            requirement="Add a multiply function and unit tests.",
            spec=sample_spec,
            evidence=evidence,
            ollama_caller=passing_mock_caller,
        )
        assert verdict.passed is True
