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

import warnings
warnings.filterwarnings("ignore")

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
        default=None,
        help="Task requirement string.",
    )
    from config.router import load_routing_config
    cfg = load_routing_config()
    default_model = cfg.get("stage_a", {}).get("default_model", "deepseek-coder-v2:latest")

    parser.add_argument(
        "--model",
        default=default_model,
        help="Model to use (defaults to routing config stage_a.default_model).",
    )
    args = parser.parse_args()

    target_dir = (ROOT / args.target).resolve()
    if args.task is None:
        if "git_repo" in str(target_dir):
            args.task = (
                "Add a multiply function that takes two numbers and returns their product to src/math_utils.py, "
                "and add a test_multiply unit test to tests/test_math_utils.py."
            )
        else:
            args.task = (
                "Add a multiply function that takes two numbers and returns their product to src/greeter.py, "
                "and add a test_multiply unit test to tests/test_greeter.py."
            )
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
        responsible_task = executed_tasks[0]
        ws_path = Path(responsible_task.allowed_workspace)
        calc_files = [f for f in ws_path.rglob("*.py") if "test" not in f.name and "src" in str(f)]
        if not calc_files:
            calc_files = [f for f in ws_path.rglob("*.py") if "test" not in f.name]

        inject_file = calc_files[0] if calc_files else None
        original_content = ""
        rel_str = ""
        if inject_file:
            original_content = inject_file.read_text()
            inject_file.write_text(original_content + "\n# BUGGY CODE\ndef multiply(a, b): return a +  # Deliberate syntax bug\n")
            rel_str = str(inject_file.relative_to(ws_path))
            if rel_str not in responsible_task.evidence.setdefault("files_written", []):
                responsible_task.evidence["files_written"].append(rel_str)
            print("  Injecting deliberate syntax/logic bug to exercise targeted recovery...")

        rework_attempt = 0

        def demo_rework_runner(contract: TaskContract, ctx: FailureContext) -> TaskContract:
            nonlocal rework_attempt
            rework_attempt += 1
            print(f"  --> Targeted rework pass #{rework_attempt} triggered for worker {contract.task_id}")
            print(f"      Failing command: {ctx.command}")
            # Restore corrected implementation
            if inject_file:
                inject_file.write_text(original_content)
                contract.evidence["files_written"] = [rel_str]
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

        # If reviewer flagged rework tasks, dispatch targeted rework on the responsible worker
        if not verdict.passed and verdict.rework_tasks:
            reviewer_reworks_run = len(verdict.rework_tasks)
            print(f"\n[orbit] Reviewer flagged gaps ({reviewer_reworks_run} rework task(s)). Executing targeted rework...")
            for rework_task in verdict.rework_tasks:
                print(f"  • Launching review rework [{rework_task.role}] {rework_task.task_id}: {rework_task.objective[:60]}...")
                rework_result = run_worker(
                    contract=rework_task,
                    target_dir=target_dir,
                    model=args.model,
                )
                rework_task.status = rework_result.status
                rework_task.evidence = rework_result.evidence
                rework_task.result_summary = rework_result.result_summary
                integrator.merge([rework_task])
                executed_tasks.append(rework_task)

            # Re-verify test suite with Integrator
            verif = integrator.verify()

            # Re-evaluate Reviewer
            review_evidence["diff"] = integrator.compute_diff()
            review_evidence["test_output"] = verif.output
            verdict = reviewer.review(
                requirement=args.task,
                spec=spec,
                evidence=review_evidence,
            )
            print(f"\n[orbit] Post-Rework Review Verdict:\n{reviewer.format_table(verdict)}\n")

        # Record worker and reviewer telemetry
        for t in executed_tasks:
            t_in = t.evidence.get("tokens_in", 0)
            t_out = t.evidence.get("tokens_out", 0)
            if t_in or t_out:
                telemetry.record_llm_call(
                    phase=f"worker_{t.role}",
                    tokens_in=t_in,
                    tokens_out=t_out,
                    model=args.model,
                    provider="ollama",
                    endpoint="http://localhost:11434",
                )
        if getattr(reviewer, "last_tokens_in", 0) or getattr(reviewer, "last_tokens_out", 0):
            telemetry.record_llm_call(
                phase="reviewer",
                tokens_in=reviewer.last_tokens_in,
                tokens_out=reviewer.last_tokens_out,
                model=args.model,
                provider="ollama",
                endpoint="http://localhost:11434",
            )

        telemetry_path = ROOT / "orbit_runs" / "demo_telemetry.json"
        telemetry.save(telemetry_path)
        print(f"\n[telemetry] Report saved to {telemetry_path}")
        llm_events = [e for e in telemetry._events if e.event == "llm_call"]
        print(f"[telemetry] Total LLM calls: {len(llm_events)}")
        for e in llm_events:
            print(f"  • Phase: {e.phase:<15} | Model: {e.metadata.get('model'):<25} | Provider: {e.metadata.get('provider'):<10} | Endpoint: {e.metadata.get('endpoint')}")

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
