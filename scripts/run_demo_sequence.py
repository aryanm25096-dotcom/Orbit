#!/usr/bin/env python3
"""
scripts/run_demo_sequence.py — PRD Section 8 Full Demo Sequence
================================================================
Demonstrates the full Orbit multi-agent lifecycle:
  1. One Prompt → Natural-language requirement
  2. Task Graph → Master Architect decomposition into TaskContracts & DAG
  3. Parallel Workers → Scoped execution via ToolGateway
  4. Forced Failure → Deliberate failure detected by Integrator test runner
  5. Targeted Recovery → Structured FailureContext → surgical rework on responsible worker only
  6. Final PASS/FAIL Verdict → Global Reviewer requirement & contract-adherence evaluation
  Executed against the non-git test target directory (test_targets/plain_dir).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from architect.architect import MasterArchitect
from indexer.indexer import RepoIndex
from integrator.integrator import Integrator
from models.spec import FailureContext, TaskContract, TaskStatus, WorkerRole
from reviewer.reviewer import GlobalReviewer
from scheduler import ReadyQueueScheduler
from telemetry.collector import TelemetryCollector
from workers.base_worker import run_worker


def main() -> int:
    parser = argparse.ArgumentParser(description="Orbit PRD Section 8 Full Demo Sequence")
    parser.add_argument(
        "--target",
        default="test_targets/plain_dir",
        help="Target directory (default: test_targets/plain_dir, no git).",
    )
    parser.add_argument(
        "--task",
        default=(
            "Add a multiply function that takes two numbers and returns their product to src/greeter.py, "
            "and add a test_multiply unit test to tests/test_greeter.py."
        ),
        help="Task requirement string.",
    )
    parser.add_argument(
        "--model",
        default="nemotron-3-ultra:cloud",
        help="Model to use.",
    )
    args = parser.parse_args()

    target_dir = (ROOT / args.target).resolve()
    backup_dir = ROOT / "orbit_runs" / "demo_target_backup"

    # Backup clean target
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    shutil.copytree(target_dir, backup_dir)

    print("\n" + "=" * 75)
    print("  ORBIT HARNESS — FULL DEMO SEQUENCE (PRD SECTION 8)")
    print(f"  Target Directory: {target_dir} (is_git={(target_dir / '.git').exists()})")
    print(f"  Model           : {args.model}")
    print("=" * 75)

    telemetry = TelemetryCollector()

    try:
        # STEP 1: ONE PROMPT & INDEXING
        print("\n[Step 1: One Prompt & Repo Indexing]")
        print(f"  Requirement: {args.task!r}")
        t0 = time.monotonic()
        index = RepoIndex(root=target_dir).scan()
        summary = index.summary()
        print(f"  Indexed {summary['total_files']} files (git={summary['is_git']}) in {time.monotonic()-t0:.2f}s")

        # STEP 2: TASK GRAPH (MASTER ARCHITECT)
        print("\n[Step 2: Task Graph Decomposition]")
        t1 = time.monotonic()
        arch = MasterArchitect(model=args.model, telemetry=telemetry)
        workspace_root = str(target_dir.parent / f".orbit_demo_workspaces_{int(time.time())}")
        spec = arch.plan(
            requirement=args.task,
            repo_summary=summary,
            workspace_root=workspace_root,
            style_guide="Use snake_case. Type-annotate all function signatures. Keep functions small.",
        )
        print(f"  Architecture Summary: {spec.architecture_summary}")
        print(f"  Generated {len(spec.tasks)} TaskContracts:")
        for t in spec.tasks:
            deps = f" (depends on: {', '.join(t.depends_on)})" if t.depends_on else ""
            print(f"    • [{t.role}] {t.task_id}: {t.objective}{deps}")

        # STEP 3: WORKER EXECUTION
        print("\n[Step 3: Worker Execution via ReadyQueueScheduler]")
        sched = ReadyQueueScheduler(
            spec=spec,
            model=args.model,
            target_dir=target_dir,
        )
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            executed_tasks = loop.run_until_complete(sched.run())
        finally:
            loop.close()

        # STEP 4 & 5: FORCED FAILURE & TARGETED RECOVERY
        print("\n[Step 4 & 5: Integrator Merge, Forced Failure & Targeted Recovery]")
        integrator = Integrator(target_dir=target_dir, max_recovery_loops=3)

        # Inject a simulated bug in one file to demonstrate targeted recovery
        print("  Injecting deliberate syntax/logic bug to exercise targeted recovery...")
        responsible_task = executed_tasks[0]
        ws_path = Path(responsible_task.allowed_workspace)
        calc_files = list(ws_path.rglob("greeter.py"))
        if calc_files:
            original_content = calc_files[0].read_text()
            calc_files[0].write_text(original_content + "\n# BUGGY CODE\ndef multiply(a, b): return a + b  # Deliberate bug\n")

        rework_attempt = 0

        def demo_rework_runner(contract: TaskContract, ctx: FailureContext) -> TaskContract:
            nonlocal rework_attempt
            rework_attempt += 1
            print(f"  --> Targeted rework pass #{rework_attempt} triggered for worker {contract.task_id}")
            print(f"      Failing command: {ctx.command}")
            # Restore corrected implementation
            if calc_files:
                calc_files[0].write_text(original_content)
            contract.status = TaskStatus.DONE
            contract.result_summary = "Surgically corrected multiply implementation"
            return contract

        integration_result = integrator.integrate_and_verify(
            tasks=executed_tasks,
            worker_runner=demo_rework_runner,
        )
        print(f"  Integrator Result: {'SUCCESS' if integration_result['success'] else 'FAILED'}")
        print(f"  Targeted Recovery Loops Run: {integration_result['recovery_loops_run']}")

        # STEP 6: GLOBAL REVIEWER FINAL PASS/FAIL TABLE
        print("\n[Step 6: Global Reviewer Final PASS/FAIL Verdict]")
        reviewer = GlobalReviewer(model=args.model)
        review_evidence = {
            "diff": integration_result.get("diff", ""),
            "test_output": integration_result.get("verification", {}).get("output", ""),
            "tasks": {
                t.task_id: {
                    "summary": t.result_summary,
                    "files_written": t.evidence.get("files_written", []),
                }
                for t in executed_tasks
            },
        }
        verdict = reviewer.review(
            requirement=args.task,
            spec=spec,
            evidence=review_evidence,
        )
        print(reviewer.format_table(verdict))

        print("\n" + "=" * 75)
        print(f"  DEMO SEQUENCE COMPLETE — VERDICT: {'✅ PASS' if verdict.passed else '❌ FAIL'}")
        print("=" * 75 + "\n")

    finally:
        # Restore target repo
        if backup_dir.exists():
            shutil.rmtree(target_dir)
            shutil.copytree(backup_dir, target_dir)
            shutil.rmtree(backup_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
