#!/usr/bin/env python3
"""
scripts/run_phase2_live.py — Live Phase 2 Demo
===============================================
Runs MasterArchitect.plan() against real Ollama on the plain_dir test
target and prints the full MasterSpecification as formatted JSON.

Usage:
    python3 scripts/run_phase2_live.py [--target git_repo|plain_dir]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from architect.architect import ArchitectError, MasterArchitect
from indexer.indexer import RepoIndex
from telemetry.collector import TelemetryCollector


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--target",
        choices=["git_repo", "plain_dir"],
        default="plain_dir",
        help="Which test target to run against (default: plain_dir).",
    )
    parser.add_argument(
        "--task",
        default="Add a multiply function that takes two numbers and returns their product. Add it to the source file and add a corresponding unit test.",
        help="Task description to plan.",
    )
    parser.add_argument(
        "--model",
        default="deepseek-coder-v2:latest",
    )
    args = parser.parse_args()

    target_dir = ROOT / "test_targets" / args.target
    workspace_root = str(ROOT / "orbit_runs" / "phase2_demo" / args.target)

    print(f"\n{'='*65}")
    print(f"  Orbit Phase 2 — Live MasterArchitect Demo")
    print(f"  Target  : {target_dir}")
    print(f"  Model   : {args.model}")
    print(f"{'='*65}\n")

    # 1. Index the target directory
    print("[1/3] Indexing target directory …")
    t0 = time.monotonic()
    index = RepoIndex(root=target_dir).scan()
    summary = index.summary()
    print(f"      {summary['total_files']} files | is_git={summary['is_git']} | "
          f"languages={summary['languages']} | rg={summary['rg_available']}")
    print(f"      Done in {time.monotonic()-t0:.2f}s\n")

    # 2. Run the Architect
    print("[2/3] Calling MasterArchitect.plan() …")
    print(f"      Task: {args.task!r}\n")
    telemetry = TelemetryCollector()
    arch = MasterArchitect(model=args.model, telemetry=telemetry)

    t1 = time.monotonic()
    try:
        spec = arch.plan(
            requirement=args.task,
            repo_summary=summary,
            workspace_root=workspace_root,
            style_guide="Use snake_case. Type-annotate all function signatures. Keep functions small.",
        )
    except ArchitectError as exc:
        print(f"\n[ERROR] ArchitectError: {exc}")
        print(f"  raw_response  : {exc.raw_response[:500]}")
        print(f"  validation_err: {exc.validation_error[:500]}")
        return 1
    elapsed = time.monotonic() - t1

    # 3. Display the result
    print(f"[3/3] MasterSpecification received in {elapsed:.1f}s\n")
    spec_dict = spec.model_dump()
    print(json.dumps(spec_dict, indent=2))

    # Telemetry summary
    report = telemetry.report()
    print(f"\n{'─'*65}")
    print(f"  Telemetry")
    print(f"  wall_clock_sec  : {report['wall_clock_sec']}")
    print(f"  tokens_in       : {report['total_tokens_in']}")
    print(f"  tokens_out      : {report['total_tokens_out']}")
    print(f"{'─'*65}\n")

    # Quick sanity checks
    assert len(spec.tasks) >= 1, "Expected at least one task"
    assert spec.requirement == args.task
    for task in spec.tasks:
        assert task.allowed_workspace.startswith(workspace_root), \
            f"Workspace path mismatch: {task.allowed_workspace}"
    print("  ✅  All sanity checks passed.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
