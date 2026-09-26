"""
integrator/integrator.py — Workspace Integrator + Evidence-Driven Recovery Loop
=================================================================================
PRD §4.5, §5.1:
  "Snapshot the pre-edit directory tree with a plain recursive copy (per Section 5.1,
   so a diff exists even without git), merge each worker's isolated workspace into
   the canonical directory, then run the repo's own test suite. On failure, build a
   FailureContext object (command, error, stack trace, changed files) and send exactly
   one targeted rework TaskContract back to the responsible worker only — never
   regenerate everything. When MAX_RECOVERY_LOOPS is hit, apply the tie-breaker
   from Section 4.5 in this exact order:
     1st: highest count of passing tests
     2nd (tie): fewest files left in a broken/uncompiling state
     3rd (tie): most recently verified candidate (last known-good checkpoint)."

Implementation notes:
  - Zero git calls required; pure filesystem copies & difflib unified_diff.
  - Generates full git-compatible diffs against the pre-edit snapshot.
  - Strict adherence to the 3-step tie-breaker order.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from models.spec import FailureContext, TaskContract, TaskStatus
from workers.base_worker import run_worker

logger = logging.getLogger("orbit.integrator")


# ---------------------------------------------------------------------------
# Filesystem Snapshot & Diff Utilities (Git-optional per §5.1)
# ---------------------------------------------------------------------------

_SKIP_PARTS = frozenset({
    ".git", ".hg", ".svn", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "node_modules", "venv", ".venv",
})


def snapshot_directory(source_dir: Path, destination_dir: Path) -> Path:
    """
    Recursively snapshot *source_dir* to *destination_dir*.
    Excludes VCS metadata and cache directories.
    """
    destination_dir = Path(destination_dir).resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)

    for src in source_dir.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(source_dir)
        if any(p.startswith(".") or p in _SKIP_PARTS for p in rel.parts):
            continue
        dest = destination_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    return destination_dir


def restore_directory(snapshot_dir: Path, target_dir: Path) -> None:
    """
    Restore *target_dir* to match *snapshot_dir* exactly.
    Removes files added after snapshot, restores modified/deleted files.
    """
    snapshot_dir = Path(snapshot_dir).resolve()
    target_dir = Path(target_dir).resolve()

    # 1. Remove files in target that do not exist in snapshot
    for item in list(target_dir.rglob("*")):
        if item.is_dir():
            continue
        rel = item.relative_to(target_dir)
        if any(p.startswith(".") or p in _SKIP_PARTS for p in rel.parts):
            continue
        if not (snapshot_dir / rel).exists():
            item.unlink()

    # 2. Copy files from snapshot into target
    for src in snapshot_dir.rglob("*"):
        if src.is_dir():
            continue
        rel = src.relative_to(snapshot_dir)
        dest = target_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def generate_filesystem_diff(before_dir: Path, after_dir: Path) -> str:
    """
    Generate unified diff string between two directory trees without git.
    """
    before_dir = Path(before_dir).resolve()
    after_dir = Path(after_dir).resolve()

    all_rel_paths: set[Path] = set()
    if before_dir.exists():
        for p in before_dir.rglob("*"):
            if p.is_file():
                rel = p.relative_to(before_dir)
                if not any(part.startswith(".") or part in _SKIP_PARTS for part in rel.parts):
                    all_rel_paths.add(rel)

    if after_dir.exists():
        for p in after_dir.rglob("*"):
            if p.is_file():
                rel = p.relative_to(after_dir)
                if not any(part.startswith(".") or part in _SKIP_PARTS for part in rel.parts):
                    all_rel_paths.add(rel)

    diff_lines: list[str] = []
    for rel in sorted(all_rel_paths):
        file_before = before_dir / rel
        file_after = after_dir / rel

        lines_before = (
            file_before.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            if file_before.exists() else []
        )
        lines_after = (
            file_after.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            if file_after.exists() else []
        )

        if lines_before != lines_after:
            from_name = f"a/{rel}" if lines_before else "/dev/null"
            to_name = f"b/{rel}" if lines_after else "/dev/null"
            diff = difflib.unified_diff(lines_before, lines_after, fromfile=from_name, tofile=to_name)
            diff_lines.extend(diff)

    return "".join(diff_lines)


# ---------------------------------------------------------------------------
# Test Verification & Parser
# ---------------------------------------------------------------------------

@dataclass
class VerificationResult:
    passed: bool
    output: str
    returncode: int
    command: str
    passing_tests: int = 0
    failing_tests: int = 0
    broken_files: int = 0
    stack_trace: str = ""
    failing_files: list[str] = field(default_factory=list)


def parse_pytest_output(output: str, returncode: int, command: str) -> VerificationResult:
    """Parse pytest stdout/stderr to extract counts and stack traces."""
    # Count passed tests
    m_pass = re.search(r"(\d+)\s+passed", output)
    passing_count = int(m_pass.group(1)) if m_pass else 0

    # Count failed tests
    m_fail = re.search(r"(\d+)\s+failed", output)
    failing_count = int(m_fail.group(1)) if m_fail else 0

    # Identify broken / failing files
    failing_files: list[str] = []
    for line in output.splitlines():
        if line.startswith("FAILED "):
            # Format: FAILED tests/test_foo.py::test_bar - AssertionError...
            parts = line.split()
            if len(parts) >= 2:
                file_target = parts[1].split("::")[0]
                if file_target not in failing_files:
                    failing_files.append(file_target)

    # If syntax error or collection error
    for m in re.finditer(r"(?:ERROR|Error)\s+collecting\s+([\w\./-]+)", output):
        fn = m.group(1)
        if fn not in failing_files:
            failing_files.append(fn)

    broken_files_count = len(failing_files) if failing_files else (1 if returncode != 0 else 0)

    # Extract stack trace block if present
    stack_trace = ""
    if "FAILURES" in output:
        start_idx = output.find("FAILURES")
        end_idx = output.find("short test summary info")
        if end_idx != -1 and end_idx > start_idx:
            stack_trace = output[start_idx:end_idx].strip()
        else:
            stack_trace = output[start_idx:start_idx + 2000].strip()

    return VerificationResult(
        passed=(returncode == 0),
        output=output,
        returncode=returncode,
        command=command,
        passing_tests=passing_count,
        failing_tests=failing_count,
        broken_files=broken_files_count,
        stack_trace=stack_trace,
        failing_files=failing_files,
    )


# ---------------------------------------------------------------------------
# Candidate Checkpoint (for PRD §4.5 Tie-Breaker)
# ---------------------------------------------------------------------------

@dataclass
class CandidateCheckpoint:
    attempt: int
    passing_tests: int
    broken_files: int
    timestamp: float
    snapshot_path: Path
    test_output: str
    responsible_task_id: str


# ---------------------------------------------------------------------------
# Workspace Integrator Class (PRD §4.5, §5.1)
# ---------------------------------------------------------------------------

class Integrator:
    """
    Merges worker workspaces into the canonical directory.
    Verifies with repository test suite.
    On failure: constructs FailureContext and runs targeted rework loops.
    Applies the ordered tie-breaker if MAX_RECOVERY_LOOPS is exhausted.
    """

    def __init__(
        self,
        target_dir: Path,
        max_recovery_loops: int = 3,
        test_command: str = "python3 -m pytest",
        snapshots_root: Path | None = None,
    ) -> None:
        self.target_dir = Path(target_dir).resolve()
        self.max_recovery_loops = max_recovery_loops
        self.test_command = test_command

        root = snapshots_root or (self.target_dir.parent / f".orbit_integrator_{int(time.time())}")
        self.snapshots_root = Path(root).resolve()
        self.snapshots_root.mkdir(parents=True, exist_ok=True)

        self.pre_edit_snapshot: Path | None = None
        self.candidates: list[CandidateCheckpoint] = []

    def snapshot_pre_edit(self) -> Path:
        """Snapshot the pre-edit directory tree before any merges."""
        dest = self.snapshots_root / "pre_edit"
        self.pre_edit_snapshot = snapshot_directory(self.target_dir, dest)
        return self.pre_edit_snapshot

    def merge(self, completed_tasks: list[TaskContract]) -> list[str]:
        """
        Merge files written by completed workers from their isolated workspaces
        into target_dir.
        """
        applied: list[str] = []
        for task in completed_tasks:
            if task.status != TaskStatus.DONE:
                continue
            ws = Path(task.allowed_workspace)
            files = task.evidence.get("files_written", [])
            for rel_str in files:
                src = ws / rel_str
                if src.exists():
                    dest = self.target_dir / rel_str
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    applied.append(rel_str)
        return sorted(list(set(applied)))

    def verify(self) -> VerificationResult:
        """Run the repository test suite against target_dir."""
        has_tests = (
            any(self.target_dir.rglob("test_*.py")) or
            any(self.target_dir.rglob("*_test.py"))
        )
        if not has_tests:
            return VerificationResult(
                passed=True,
                output="No tests found in repository.",
                returncode=0,
                command=self.test_command,
                passing_tests=0,
                failing_tests=0,
                broken_files=0,
            )

        cmd_list = self.test_command.split() if isinstance(self.test_command, str) else list(self.test_command)
        try:
            proc = subprocess.run(
                cmd_list,
                cwd=str(self.target_dir),
                capture_output=True,
                text=True,
                timeout=120,
            )
            output = proc.stdout + proc.stderr
            return parse_pytest_output(output, proc.returncode, self.test_command)
        except subprocess.TimeoutExpired as exc:
            output = (exc.stdout or "") + (exc.stderr or "") + "\nTest run timed out"
            return VerificationResult(
                passed=False,
                output=output,
                returncode=-1,
                command=self.test_command,
                broken_files=1,
            )
        except Exception as exc:
            return VerificationResult(
                passed=False,
                output=str(exc),
                returncode=-1,
                command=self.test_command,
                broken_files=1,
            )

    def identify_responsible_task(
        self,
        tasks: list[TaskContract],
        verification: VerificationResult,
    ) -> TaskContract:
        """
        Identify the single responsible task that produced the failing code.
        Matches failing files against worker written files; falls back to the
        last completed task.
        """
        completed = [t for t in tasks if t.status == TaskStatus.DONE]
        if not completed:
            return tasks[-1]

        # Match against failing files
        for failing_file in verification.failing_files:
            for task in reversed(completed):
                written = task.evidence.get("files_written", [])
                if any(failing_file in w or w in failing_file for w in written):
                    return task

        # Fallback to the latest completed task that wrote code
        return completed[-1]

    def build_failure_context(
        self,
        task: TaskContract,
        verification: VerificationResult,
        attempt: int,
    ) -> FailureContext:
        """
        Build a structured FailureContext matching PRD §4.5:
        command, error, stack trace, changed files.
        """
        changed_files = task.evidence.get("files_written", [])
        return FailureContext(
            task_id=task.task_id,
            command=verification.command,
            error_output=verification.output[-3000:],
            stack_trace=verification.stack_trace,
            changed_files=changed_files,
            recovery_attempt=attempt,
            passing_test_count=verification.passing_tests,
            broken_file_count=verification.broken_files,
        )

    def create_rework_contract(
        self,
        task: TaskContract,
        ctx: FailureContext,
    ) -> TaskContract:
        """
        Create exactly one targeted rework TaskContract for the responsible worker.
        Never regenerates everything (PRD §4.5).
        """
        rework_id = f"{task.task_id}-rework-{ctx.recovery_attempt}"
        headline = ctx.error_output.strip().splitlines()[-1] if ctx.error_output.strip() else "test failure"
        return TaskContract(
            task_id=rework_id,
            role=task.role,
            objective=f"Fix test failure in {task.task_id}: {headline[:80]}",
            inputs=list(set(task.inputs + ctx.changed_files)),
            allowed_workspace=task.allowed_workspace,
            expected_artifact=task.expected_artifact,
            acceptance_criteria=task.acceptance_criteria + ["All test suite tests must pass"],
            style_guide=task.style_guide,
            evidence={"failure_context": ctx.model_dump()},
        )

    def apply_tie_breaker(self, candidates: list[CandidateCheckpoint]) -> CandidateCheckpoint:
        """
        PRD §4.5 Ordered Tie-Breaker:
          1st: highest count of passing tests
          2nd (tie): fewest files left in a broken/uncompiling state
          3rd (tie): most recently verified candidate (last known-good checkpoint)
        """
        if not candidates:
            raise ValueError("Cannot apply tie-breaker with empty candidate list.")

        # Key components:
        # 1. c.passing_tests (descending -> higher is better)
        # 2. -c.broken_files (descending -> fewer broken files is better)
        # 3. c.attempt (descending -> more recent is better)
        sorted_candidates = sorted(
            candidates,
            key=lambda c: (c.passing_tests, -c.broken_files, c.attempt),
            reverse=True,
        )
        return sorted_candidates[0]

    def integrate_and_verify(
        self,
        tasks: list[TaskContract],
        worker_runner: Callable[[TaskContract, FailureContext], TaskContract] | None = None,
    ) -> dict[str, Any]:
        """
        Execute the full Phase 5 integration & recovery loop:
          1. Snapshot pre-edit state.
          2. Merge completed tasks into target_dir.
          3. Verify with test suite.
          4. On failure: targeted rework loop up to max_recovery_loops.
          5. If still failing: apply ordered tie-breaker and restore best checkpoint.
          6. Return complete evidence report including git-optional diff.
        """
        # 1. Snapshot pre-edit state
        self.snapshot_pre_edit()

        # 2. Merge completed tasks
        applied_files = self.merge(tasks)

        # 3. Initial verification
        verification = self.verify()

        if verification.passed:
            diff_text = generate_filesystem_diff(self.pre_edit_snapshot, self.target_dir)
            return {
                "success": True,
                "recovery_loops_run": 0,
                "tie_breaker_applied": False,
                "applied_files": applied_files,
                "verification": verification.__dict__,
                "diff": diff_text,
            }

        # Failure path — enter targeted recovery loop
        logger.warning("Verification failed on initial merge. Entering targeted rework loop...")
        recovery_loops = 0

        # Save initial merge candidate
        initial_snap = self.snapshots_root / "candidate_0"
        snapshot_directory(self.target_dir, initial_snap)
        self.candidates.append(CandidateCheckpoint(
            attempt=0,
            passing_tests=verification.passing_tests,
            broken_files=verification.broken_files,
            timestamp=time.monotonic(),
            snapshot_path=initial_snap,
            test_output=verification.output,
            responsible_task_id="",
        ))

        # Default worker runner if none provided
        def _default_worker_runner(contract: TaskContract, ctx: FailureContext) -> TaskContract:
            return run_worker(contract=contract, target_dir=self.target_dir)

        runner = worker_runner or _default_worker_runner

        while recovery_loops < self.max_recovery_loops:
            recovery_loops += 1

            responsible_task = self.identify_responsible_task(tasks, verification)
            failure_ctx = self.build_failure_context(responsible_task, verification, recovery_loops)

            logger.info(
                f"[integrator] Recovery loop {recovery_loops}/{self.max_recovery_loops}: "
                f"Sending targeted rework contract to worker {responsible_task.task_id}"
            )

            # Exactly one targeted rework task
            rework_contract = self.create_rework_contract(responsible_task, failure_ctx)
            reworked_result = runner(rework_contract, failure_ctx)

            # Merge reworked files into target_dir
            if reworked_result.status == TaskStatus.DONE:
                self.merge([reworked_result])

            # Re-verify
            verification = self.verify()

            # Record candidate checkpoint
            cand_snap = self.snapshots_root / f"candidate_{recovery_loops}"
            snapshot_directory(self.target_dir, cand_snap)
            self.candidates.append(CandidateCheckpoint(
                attempt=recovery_loops,
                passing_tests=verification.passing_tests,
                broken_files=verification.broken_files,
                timestamp=time.monotonic(),
                snapshot_path=cand_snap,
                test_output=verification.output,
                responsible_task_id=responsible_task.task_id,
            ))

            if verification.passed:
                logger.info(f"[integrator] Recovery loop {recovery_loops} SUCCEEDED!")
                diff_text = generate_filesystem_diff(self.pre_edit_snapshot, self.target_dir)
                return {
                    "success": True,
                    "recovery_loops_run": recovery_loops,
                    "tie_breaker_applied": False,
                    "applied_files": applied_files,
                    "verification": verification.__dict__,
                    "diff": diff_text,
                }

        # MAX_RECOVERY_LOOPS exhausted — apply tie-breaker (PRD §4.5)
        logger.warning(
            f"[integrator] MAX_RECOVERY_LOOPS ({self.max_recovery_loops}) exhausted. "
            "Applying PRD §4.5 ordered tie-breaker..."
        )
        best = self.apply_tie_breaker(self.candidates)
        restore_directory(best.snapshot_path, self.target_dir)
        diff_text = generate_filesystem_diff(self.pre_edit_snapshot, self.target_dir)

        return {
            "success": False,
            "recovery_loops_run": recovery_loops,
            "tie_breaker_applied": True,
            "best_candidate": {
                "attempt": best.attempt,
                "passing_tests": best.passing_tests,
                "broken_files": best.broken_files,
                "responsible_task_id": best.responsible_task_id,
            },
            "applied_files": applied_files,
            "verification": verification.__dict__,
            "diff": diff_text,
            "candidates_count": len(self.candidates),
        }
