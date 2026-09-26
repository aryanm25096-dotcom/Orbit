"""
scheduler.py — Orbit Top-Level Entry Point & Ready-Queue Scheduler (Phase 4)
=============================================================================
PRD §3.2, §4.2:
  "Build the scheduler as a plain ready-queue per Section 4.2 — a task's worker
   starts the moment its dependency artifacts exist, using asyncio.gather over
   ready tasks. Do not add topological-sort logic; that's an explicit stretch
   item, not part of this phase."

Usage:
    python3 scheduler.py --target test_targets/plain_dir --task "Add a multiply function..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from architect.architect import MasterArchitect
from indexer.indexer import RepoIndex
from models.spec import MasterSpecification, TaskContract, TaskStatus
from telemetry.collector import TelemetryCollector
from workers.base_worker import run_worker_async

logger = logging.getLogger("orbit.scheduler")


# ---------------------------------------------------------------------------
# ReadyQueueScheduler (PRD §4.2)
# ---------------------------------------------------------------------------

class ReadyQueueScheduler:
    """
    Plain readiness-based scheduler for Orbit TaskContracts.

    Policy:
      A task starts the moment its declared dependencies are marked DONE AND
      their expected artifacts exist on disk.
      Ready tasks run concurrently in waves using asyncio.gather.
      No topological sort logic is used (kept deliberately simple per §4.2).
    """

    def __init__(
        self,
        spec: MasterSpecification,
        model: str = "nemotron-3-ultra:cloud",
        base_url: str = "http://localhost:11434",
        target_dir: Path | None = None,
        ollama_caller: Callable[..., tuple[str, int, int]] | None = None,
    ) -> None:
        self.spec = spec
        self.model = model
        self.base_url = base_url
        self.target_dir = Path(target_dir).resolve() if target_dir else None
        self.ollama_caller = ollama_caller

    def _is_task_ready(self, task: TaskContract, done_ids: set[str]) -> bool:
        """
        Check if *task* is ready to execute:
          1. Must not already be completed (not in done_ids).
          2. Must have status PENDING or READY.
          3. Every upstream task in depends_on must be in done_ids.
          4. Every upstream task's expected_artifact must exist on disk.
        """
        if task.task_id in done_ids:
            return False
        if task.status not in (TaskStatus.PENDING, TaskStatus.READY):
            return False

        for dep_id in task.depends_on:
            if dep_id not in done_ids:
                return False

            dep_task = self.spec.task_by_id(dep_id)
            if dep_task and dep_task.expected_artifact:
                # Check whether the artifact exists in the upstream allowed_workspace
                # or as an absolute path on disk
                art_name = dep_task.expected_artifact
                ws_path = Path(dep_task.allowed_workspace)
                candidate1 = ws_path / art_name
                candidate2 = Path(art_name)
                if not (candidate1.exists() or candidate2.exists()):
                    return False

        return True

    async def run(self) -> list[TaskContract]:
        """
        Run all tasks in the MasterSpecification until all are DONE or blocked.
        Returns the updated list of TaskContracts.
        """
        done_ids: set[str] = set()
        failed_ids: set[str] = set()
        wave = 1

        print(f"[scheduler] Starting ready-queue execution for {len(self.spec.tasks)} tasks...")

        while len(done_ids) + len(failed_ids) < len(self.spec.tasks):
            ready_tasks = [t for t in self.spec.tasks if self._is_task_ready(t, done_ids)]

            if not ready_tasks:
                unresolved = [
                    t.task_id for t in self.spec.tasks
                    if t.task_id not in done_ids and t.task_id not in failed_ids
                ]
                print(f"[scheduler] No more ready tasks. Unresolved tasks: {unresolved}")
                break

            print(f"\n[scheduler] === Wave {wave} ({len(ready_tasks)} ready tasks) ===")
            for t in ready_tasks:
                print(f"  • Launching {t.task_id} [{t.role}]: {t.objective[:60]}...")
                t.status = TaskStatus.RUNNING

            # Prepare upstream workspace paths for each task
            coroutines = []
            for t in ready_tasks:
                upstream_workspaces = [
                    Path(self.spec.task_by_id(dep_id).allowed_workspace)
                    for dep_id in t.depends_on
                    if self.spec.task_by_id(dep_id)
                ]
                coroutines.append(
                    run_worker_async(
                        contract=t,
                        model=self.model,
                        base_url=self.base_url,
                        target_dir=self.target_dir,
                        upstream_workspaces=upstream_workspaces,
                        ollama_caller=self.ollama_caller,
                    )
                )

            # Run wave concurrently using asyncio.gather
            wave_results = await asyncio.gather(*coroutines, return_exceptions=False)

            for res in wave_results:
                if res.status == TaskStatus.DONE:
                    done_ids.add(res.task_id)
                    print(f"  ✔ {res.task_id} [{res.role}] DONE — {res.result_summary[:60]}")
                else:
                    failed_ids.add(res.task_id)
                    print(f"  ✘ {res.task_id} [{res.role}] FAILED — {res.result_summary[:60]}")

            wave += 1

        print(f"\n[scheduler] Finished: {len(done_ids)} done, {len(failed_ids)} failed.\n")
        return self.spec.tasks


# ---------------------------------------------------------------------------
# Artifact Integration Helper (Phase 4 MVP integration)
# ---------------------------------------------------------------------------

def integrate_artifacts(spec: MasterSpecification, target_dir: Path) -> list[str]:
    """
    Apply files written by completed workers from their isolated workspaces
    directly into the target directory.
    """
    applied: list[str] = []
    for task in spec.tasks:
        if task.status != TaskStatus.DONE:
            continue
        ws = Path(task.allowed_workspace)
        files_written = task.evidence.get("files_written", [])
        for rel_str in files_written:
            src = ws / rel_str
            if src.exists():
                dest = target_dir / rel_str
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)
                applied.append(rel_str)
    return sorted(list(set(applied)))


# ---------------------------------------------------------------------------
# High-Level Orchestrator
# ---------------------------------------------------------------------------

def run_orbit(
    target: Path,
    task: str,
    config_path: Path = Path("config/routing.yaml"),
    dry_run: bool = False,
    model: str | None = None,
    base_url: str = "http://localhost:11434",
    ollama_caller: Callable[..., tuple[str, int, int]] | None = None,
) -> dict[str, Any]:
    """
    High-level entry point:
      1. Index target directory.
      2. Plan with MasterArchitect.
      3. Execute tasks with ReadyQueueScheduler.
      4. Integrate artifacts to target.
      5. Run target test suite.
    """
    target = Path(target).resolve()
    if not target.exists():
        raise FileNotFoundError(f"Target path does not exist: {target}")

    # Load configuration
    cfg: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    stage_a = cfg.get("stage_a", {})
    resolved_model = model or stage_a.get("default_model", "nemotron-3-ultra:cloud")
    resolved_base_url = base_url or stage_a.get("base_url", "http://localhost:11434")

    # 1. Index the target directory
    print(f"[orbit] Indexing target: {target}")
    t0 = time.monotonic()
    index = RepoIndex(root=target).scan()
    summary = index.summary()
    print(f"[orbit] Indexed {summary['total_files']} files (git={summary['is_git']}) in {time.monotonic()-t0:.2f}s")

    # 2. Plan with MasterArchitect
    print(f"[orbit] Planning requirement: {task!r}")
    telemetry = TelemetryCollector()
    arch = MasterArchitect(model=resolved_model, base_url=resolved_base_url, telemetry=telemetry)
    workspace_root = str(target.parent / f".orbit_workspaces_{int(time.time())}")

    spec = arch.plan(
        requirement=task,
        repo_summary=summary,
        workspace_root=workspace_root,
        style_guide="Follow PEP 8 naming conventions. Add type annotations and docstrings. Ensure unit tests pass.",
    )
    print(f"[orbit] Architecture summary: {spec.architecture_summary}")
    print(f"[orbit] Generated {len(spec.tasks)} tasks:")
    for t in spec.tasks:
        deps = f" (depends on: {', '.join(t.depends_on)})" if t.depends_on else ""
        print(f"  - [{t.role}] {t.task_id}: {t.objective}{deps}")

    if dry_run:
        print("[orbit] Dry-run enabled — skipping task execution.")
        return {
            "spec": spec.model_dump(),
            "dry_run": True,
            "telemetry": telemetry.report(),
        }

    # 3. Schedule and run tasks via ReadyQueueScheduler
    scheduler = ReadyQueueScheduler(
        spec=spec,
        model=resolved_model,
        base_url=resolved_base_url,
        target_dir=target,
        ollama_caller=ollama_caller,
    )
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        updated_tasks = loop.run_until_complete(scheduler.run())
    finally:
        loop.close()

    # 4. Integrate artifacts into target
    applied_files = integrate_artifacts(spec, target)
    print(f"[orbit] Integrated {len(applied_files)} files into target: {applied_files}")

    # 5. Run target tests to verify end-to-end correctness
    test_result_output = ""
    test_suite_passed = False
    has_target_tests = any(target.rglob("test_*.py")) or any(target.rglob("*_test.py"))
    if has_target_tests:
        print("[orbit] Running test suite against integrated target...")
        try:
            res = subprocess.run(
                ["python3", "-m", "pytest"],
                cwd=str(target),
                capture_output=True,
                text=True,
                timeout=60,
            )
            test_suite_passed = (res.returncode == 0)
            test_result_output = res.stdout + res.stderr
            print(f"[orbit] Target test suite: {'PASSED' if test_suite_passed else 'FAILED'}")
        except Exception as exc:
            test_result_output = str(exc)
            print(f"[orbit] Target test suite execution error: {exc}")

    return {
        "spec": spec.model_dump(),
        "tasks": [t.model_dump() for t in updated_tasks],
        "applied_files": applied_files,
        "test_suite_passed": test_suite_passed,
        "test_result_output": test_result_output,
        "telemetry": telemetry.report(),
    }


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orbit",
        description="Orbit — Autonomous Multi-Agent Software Engineering Harness",
    )
    parser.add_argument(
        "--target",
        type=Path,
        required=True,
        help="Path to the local directory (git repo or plain directory).",
    )
    parser.add_argument(
        "--task",
        type=str,
        required=True,
        help="Natural-language task / requirement string.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/routing.yaml"),
        help="Path to routing config (default: config/routing.yaml).",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Override model to use for all roles.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the task graph without executing workers.",
    )
    args = parser.parse_args(argv)

    try:
        result = run_orbit(
            target=args.target,
            task=args.task,
            config_path=args.config,
            dry_run=args.dry_run,
            model=args.model,
        )
        print(f"\n[orbit] Run complete! Success: {result.get('test_suite_passed', False)}")
        return 0 if (args.dry_run or result.get("test_suite_passed", False)) else 1
    except Exception as exc:
        print(f"\n[orbit] Execution failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
