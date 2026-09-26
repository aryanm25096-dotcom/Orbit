"""
tests/test_phase5_integrator.py — Phase 5 Integrator & Recovery Loop Tests
==========================================================================
Tests the Phase 5 Integrator:
  1. Pre-edit recursive snapshot and git-optional unified diff (§5.1).
  2. Merging worker workspaces into canonical target.
  3. FailureContext construction (command, error, stack trace, changed files).
  4. FORCED FAILURE TEST: targeted rework TaskContract sent to responsible worker only,
     verifying the targeted rework loop resolves the bug and passes verification.
  5. PRD §4.5 ordered tie-breaker enforcement when MAX_RECOVERY_LOOPS is exhausted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from integrator.integrator import (
    CandidateCheckpoint,
    Integrator,
    VerificationResult,
    generate_filesystem_diff,
    parse_pytest_output,
    restore_directory,
    snapshot_directory,
)
from models.spec import FailureContext, TaskContract, TaskStatus, WorkerRole
from workers.base_worker import BackendWorker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def target_repo(tmp_path: Path) -> Path:
    """Fixture creating a test repo with src and test files."""
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    src = repo / "src"
    tests = repo / "tests"
    src.mkdir(parents=True, exist_ok=True)
    tests.mkdir(parents=True, exist_ok=True)

    (src / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    (tests / "test_calc.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
        "from calc import add\n\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
    )
    return repo


@pytest.fixture
def worker_ws(tmp_path: Path) -> Path:
    ws = tmp_path / "worker_ws"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


# ---------------------------------------------------------------------------
# 1. Snapshot and Diff Tests
# ---------------------------------------------------------------------------

class TestSnapshotAndDiff:

    def test_snapshot_directory_copies_all_files(self, target_repo: Path, tmp_path: Path):
        snap = tmp_path / "snapshot"
        snapshot_directory(target_repo, snap)

        assert (snap / "src" / "calc.py").exists()
        assert (snap / "tests" / "test_calc.py").exists()
        assert (snap / "src" / "calc.py").read_text() == (target_repo / "src" / "calc.py").read_text()

    def test_restore_directory_restores_state(self, target_repo: Path, tmp_path: Path):
        snap = tmp_path / "snapshot"
        snapshot_directory(target_repo, snap)

        # Mutate target
        (target_repo / "src" / "calc.py").write_text("corrupted")
        (target_repo / "new_file.txt").write_text("added")

        # Restore
        restore_directory(snap, target_repo)

        assert "def add" in (target_repo / "src" / "calc.py").read_text()
        assert not (target_repo / "new_file.txt").exists()

    def test_generate_filesystem_diff_without_git(self, target_repo: Path, tmp_path: Path):
        snap = tmp_path / "snapshot"
        snapshot_directory(target_repo, snap)

        # Modify a file
        (target_repo / "src" / "calc.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n\ndef sub(a, b): return a - b\n"
        )
        diff = generate_filesystem_diff(snap, target_repo)

        assert "--- a/src/calc.py" in diff
        assert "+++ b/src/calc.py" in diff
        assert "+def sub(a, b): return a - b" in diff


# ---------------------------------------------------------------------------
# 2. Pytest Output Parsing Tests
# ---------------------------------------------------------------------------

class TestPytestOutputParsing:

    def test_parse_success_output(self):
        sample = (
            "============================= test session starts ==============================\n"
            "tests/test_calc.py::test_add PASSED [100%]\n"
            "============================== 3 passed in 0.05s ==============================="
        )
        res = parse_pytest_output(sample, returncode=0, command="pytest")
        assert res.passed is True
        assert res.passing_tests == 3
        assert res.failing_tests == 0
        assert res.broken_files == 0

    def test_parse_failure_output(self):
        sample = (
            "=================================== FAILURES ===================================\n"
            "_________________________________ test_multiply ________________________________\n"
            "tests/test_calc.py:12: in test_multiply\n"
            "    assert multiply(2, 3) == 6\n"
            "E   AssertionError: assert 5 == 6\n"
            "=========================== short test summary info ============================\n"
            "FAILED tests/test_calc.py::test_multiply - AssertionError: assert 5 == 6\n"
            "========================= 1 failed, 2 passed in 0.08s =========================="
        )
        res = parse_pytest_output(sample, returncode=1, command="pytest")
        assert res.passed is False
        assert res.passing_tests == 2
        assert res.failing_tests == 1
        assert res.broken_files == 1
        assert "tests/test_calc.py" in res.failing_files
        assert "AssertionError: assert 5 == 6" in res.stack_trace


# ---------------------------------------------------------------------------
# 3. FORCED-FAILURE Targeted Rework Test
# ---------------------------------------------------------------------------

class TestForcedFailureTargetedRework:

    def test_forced_failure_triggers_targeted_rework_and_succeeds(
        self,
        target_repo: Path,
        worker_ws: Path,
    ):
        """
        PRD §4.5 Forced-Failure Test:
          1. Worker introduces a bug (multiply returns a + b instead of a * b).
          2. Integrator merges and runs tests -> FAILS.
          3. Integrator builds FailureContext (command, error, stack trace, changed files).
          4. Integrator sends EXACTLY ONE targeted rework TaskContract to the responsible worker.
          5. Worker fixes the bug during the targeted rework pass.
          6. Integrator merges the rework and re-verifies -> PASSES!
        """
        # Step 1: Add a test that expects multiply(2, 3) == 6
        test_file = target_repo / "tests" / "test_calc.py"
        test_file.write_text(
            "import sys, pathlib\n"
            "sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / 'src'))\n"
            "from calc import add, multiply\n\n"
            "def test_add():\n"
            "    assert add(2, 3) == 5\n\n"
            "def test_multiply():\n"
            "    assert multiply(2, 3) == 6\n"
        )

        # Worker initial workspace with BUGGY implementation (returns a + b)
        buggy_code = (
            "def add(a: int, b: int) -> int:\n"
            "    return a + b\n\n"
            "def multiply(a: int, b: int) -> int:\n"
            "    return a + b  # BUG: should be a * b\n"
        )
        (worker_ws / "src").mkdir(parents=True, exist_ok=True)
        (worker_ws / "src" / "calc.py").write_text(buggy_code)

        task = TaskContract(
            task_id="task-be-001",
            role=WorkerRole.BACKEND,
            objective="Add multiply function to calc.py",
            allowed_workspace=str(worker_ws),
            expected_artifact="src/calc.py",
            status=TaskStatus.DONE,
            evidence={"files_written": ["src/calc.py"]},
        )

        # Rework call counter to prove worker is only called for targeted rework
        rework_call_count = 0

        def mock_worker_runner(contract: TaskContract, ctx: FailureContext) -> TaskContract:
            nonlocal rework_call_count
            rework_call_count += 1

            # Verify that FailureContext was properly constructed and forwarded
            assert ctx.task_id == "task-be-001"
            assert "AssertionError" in ctx.error_output or "assert 5 == 6" in ctx.error_output
            assert "src/calc.py" in ctx.changed_files
            assert ctx.recovery_attempt == 1

            # Worker fixes the bug in its workspace!
            fixed_code = (
                "def add(a: int, b: int) -> int:\n"
                "    return a + b\n\n"
                "def multiply(a: int, b: int) -> int:\n"
                "    return a * b  # FIXED\n"
            )
            (worker_ws / "src" / "calc.py").write_text(fixed_code)

            contract.status = TaskStatus.DONE
            contract.result_summary = "Fixed multiply function"
            contract.evidence["files_written"] = ["src/calc.py"]
            return contract

        # Run Integrator with max_recovery_loops = 2
        integrator = Integrator(target_dir=target_repo, max_recovery_loops=2)
        report = integrator.integrate_and_verify(
            tasks=[task],
            worker_runner=mock_worker_runner,
        )

        # Verification asserts:
        assert report["success"] is True, "Targeted rework must resolve failure"
        assert report["recovery_loops_run"] == 1, "Must succeed on the first rework loop"
        assert report["tie_breaker_applied"] is False
        assert rework_call_count == 1, "Exactly one targeted rework call must be made"

        # Check target repo content
        final_code = (target_repo / "src" / "calc.py").read_text()
        assert "return a * b  # FIXED" in final_code
        assert "diff" in report and "return a * b  # FIXED" in report["diff"]


# ---------------------------------------------------------------------------
# 4. Tie-Breaker Ordering Tests (PRD §4.5)
# ---------------------------------------------------------------------------

class TestTieBreakerOrdering:

    def test_tie_breaker_picks_highest_passing_tests(self, tmp_path: Path):
        integrator = Integrator(target_dir=tmp_path)

        c1 = CandidateCheckpoint(
            attempt=1,
            passing_tests=2,
            broken_files=1,
            timestamp=1.0,
            snapshot_path=tmp_path / "c1",
            test_output="",
            responsible_task_id="t1",
        )
        c2 = CandidateCheckpoint(
            attempt=2,
            passing_tests=5,  # Higher passing tests
            broken_files=2,
            timestamp=2.0,
            snapshot_path=tmp_path / "c2",
            test_output="",
            responsible_task_id="t1",
        )
        best = integrator.apply_tie_breaker([c1, c2])
        assert best == c2, "Rule 1: Highest passing tests wins"

    def test_tie_breaker_tie_on_passing_picks_fewest_broken_files(self, tmp_path: Path):
        integrator = Integrator(target_dir=tmp_path)

        c1 = CandidateCheckpoint(
            attempt=1,
            passing_tests=4,
            broken_files=3,
            timestamp=1.0,
            snapshot_path=tmp_path / "c1",
            test_output="",
            responsible_task_id="t1",
        )
        c2 = CandidateCheckpoint(
            attempt=2,
            passing_tests=4,  # Tied on passing tests
            broken_files=1,  # Fewer broken files
            timestamp=2.0,
            snapshot_path=tmp_path / "c2",
            test_output="",
            responsible_task_id="t1",
        )
        best = integrator.apply_tie_breaker([c1, c2])
        assert best == c2, "Rule 2: Fewest broken files wins in a tie"

    def test_tie_breaker_tie_on_both_picks_most_recent_candidate(self, tmp_path: Path):
        integrator = Integrator(target_dir=tmp_path)

        c1 = CandidateCheckpoint(
            attempt=1,
            passing_tests=4,
            broken_files=1,
            timestamp=1.0,
            snapshot_path=tmp_path / "c1",
            test_output="",
            responsible_task_id="t1",
        )
        c2 = CandidateCheckpoint(
            attempt=2,  # More recent attempt
            passing_tests=4,
            broken_files=1,
            timestamp=2.0,
            snapshot_path=tmp_path / "c2",
            test_output="",
            responsible_task_id="t1",
        )
        best = integrator.apply_tie_breaker([c1, c2])
        assert best == c2, "Rule 3: Most recently verified candidate wins in a tie"

    def test_exhausted_recovery_loops_applies_tie_breaker(
        self,
        target_repo: Path,
        worker_ws: Path,
    ):
        """
        When MAX_RECOVERY_LOOPS is hit without passing tests, the tie-breaker
        is applied and the best partial candidate is restored into target_repo.
        """
        # Add a test that will never pass
        test_file = target_repo / "tests" / "test_calc.py"
        test_file.write_text(
            "def test_impossible():\n    assert False, 'Always fails'\n"
        )

        task = TaskContract(
            task_id="task-be-001",
            role=WorkerRole.BACKEND,
            objective="Doomed task",
            allowed_workspace=str(worker_ws),
            expected_artifact="src/calc.py",
            status=TaskStatus.DONE,
            evidence={"files_written": ["src/calc.py"]},
        )

        loop_count = 0

        def always_failing_runner(contract: TaskContract, ctx: FailureContext) -> TaskContract:
            nonlocal loop_count
            loop_count += 1
            return contract

        integrator = Integrator(target_dir=target_repo, max_recovery_loops=3)
        report = integrator.integrate_and_verify(
            tasks=[task],
            worker_runner=always_failing_runner,
        )

        assert report["success"] is False
        assert report["recovery_loops_run"] == 3
        assert report["tie_breaker_applied"] is True
        assert "best_candidate" in report
        assert loop_count == 3
