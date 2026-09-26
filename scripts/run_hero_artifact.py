#!/usr/bin/env python3
"""
scripts/run_hero_artifact.py — PRD §3.2 Hero Artifact: Baseline vs. Orbit
==========================================================================
PRD §3.2, §4.8:
  "One before/after run — naive single-agent baseline vs. Orbit — on the same
   task and the same underlying model, reporting the token and wall-clock delta.
   This is the one exhibit we guarantee we can hand a judge."

Measures:
  - Total tokens in / out
  - Wall-clock seconds
  - Tool calls & guardrails
  - Test suite outcome (PASS/FAIL)
  - Contract adherence & Reviewer verdict

Saves:
  orbit_runs/hero_artifact.json
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from architect.architect import call_ollama, extract_json
from config.router import get_role_config
from indexer.indexer import RepoIndex
from scheduler import run_orbit
from telemetry.collector import TelemetryCollector


def run_naive_baseline(
    target_dir: Path,
    task: str,
    model: str = "nemotron-3-ultra:cloud",
    base_url: str = "http://localhost:11434",
) -> dict:
    """
    Naive single-agent baseline: dumps all repo files into one unsegmented prompt,
    asks the LLM to write everything in one turn with zero guardrails, applies it,
    and runs the test suite.
    """
    print(f"\n[baseline] Running Naive Single-Agent Baseline with model: {model}...")
    t0 = time.monotonic()

    # Read all files in target_dir
    repo_files = {}
    for p in target_dir.rglob("*.py"):
        rel = str(p.relative_to(target_dir))
        repo_files[rel] = p.read_text(encoding="utf-8", errors="replace")

    prompt = (
        f"You are a coding assistant. Complete this task directly:\n{task}\n\n"
        f"EXISTING REPOSITORY FILES:\n"
    )
    for fn, content in repo_files.items():
        prompt += f"--- {fn} ---\n{content}\n\n"

    prompt += (
        "Return a JSON object with 'files': [{'path': '...', 'content': '...'}] "
        "containing all updated files."
    )

    resp_text, tok_in, tok_out = call_ollama(
        prompt=prompt,
        system="You are a single-agent coding assistant.",
        model=model,
        base_url=base_url,
        temperature=0.0,
    )

    # Apply changes
    json_str = extract_json(resp_text)
    try:
        data = json.loads(json_str)
        files = data.get("files", [])
        for f in files:
            p = target_dir / f["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f["content"], encoding="utf-8")
    except Exception as exc:
        print(f"[baseline] Failed to parse/apply response: {exc}")

    # Run tests
    test_passed = False
    test_proc = subprocess.run(
        ["python3", "-m", "pytest"],
        cwd=str(target_dir),
        capture_output=True,
        text=True,
    )
    test_passed = (test_proc.returncode == 0)
    duration = time.monotonic() - t0

    return {
        "architecture": "Naive Single-Agent",
        "model": model,
        "wall_clock_sec": round(duration, 2),
        "tokens_in": tok_in,
        "tokens_out": tok_out,
        "total_tokens": tok_in + tok_out,
        "tool_calls": 0,
        "guardrails_enforced": False,
        "parallel_workers": 1,
        "recovery_loop": False,
        "reviewer_verdict": "N/A",
        "test_suite_passed": test_passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Orbit Hero Artifact Benchmark")
    parser.add_argument(
        "--target",
        default="test_targets/plain_dir",
        help="Target directory to run benchmark on.",
    )
    parser.add_argument(
        "--task",
        default=(
            "Add a multiply function that takes two numbers and returns their product to src/greeter.py, "
            "and add a test_multiply unit test to tests/test_greeter.py."
        ),
        help="Task requirement for both baseline and Orbit.",
    )
    parser.add_argument(
        "--model",
        default="nemotron-3-ultra:cloud",
        help="Model tag to benchmark on both systems.",
    )
    args = parser.parse_args()

    target_dir = ROOT / args.target
    backup_dir = ROOT / "orbit_runs" / "target_backup"

    # Make clean backup of target
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    shutil.copytree(target_dir, backup_dir)

    print("\n" + "=" * 70)
    print("  ORBIT HERO ARTIFACT BENCHMARK (PRD §3.2)")
    print(f"  Task  : {args.task}")
    print(f"  Target: {target_dir}")
    print(f"  Model : {args.model}")
    print("=" * 70)

    try:
        # 1. Run Baseline
        baseline_result = run_naive_baseline(
            target_dir=target_dir,
            task=args.task,
            model=args.model,
        )

        # Restore target before running Orbit
        shutil.rmtree(target_dir)
        shutil.copytree(backup_dir, target_dir)

        # 2. Run Orbit
        print("\n[orbit] Running Full Orbit Harness...")
        t0 = time.monotonic()
        orbit_res = run_orbit(
            target=target_dir,
            task=args.task,
            model=args.model,
        )
        orbit_duration = time.monotonic() - t0
        telemetry = orbit_res.get("telemetry", {})

        orbit_result = {
            "architecture": "Orbit Multi-Agent Harness",
            "model": args.model,
            "wall_clock_sec": round(orbit_duration, 2),
            "tokens_in": telemetry.get("total_tokens_in", 0),
            "tokens_out": telemetry.get("total_tokens_out", 0),
            "total_tokens": telemetry.get("total_tokens", 0),
            "tool_calls": telemetry.get("total_tool_calls", 0),
            "guardrails_enforced": True,
            "parallel_workers": len(orbit_res.get("tasks", [])),
            "recovery_loop": True,
            "reviewer_verdict": "PASS" if orbit_res.get("verdict", {}).get("passed", False) else "FAIL",
            "test_suite_passed": orbit_res.get("test_suite_passed", False),
        }

        # 3. Calculate Deltas & Hero Artifact
        hero_data = {
            "task": args.task,
            "model": args.model,
            "baseline": baseline_result,
            "orbit": orbit_result,
            "comparison": {
                "token_overhead": orbit_result["total_tokens"] - baseline_result["total_tokens"],
                "wall_clock_delta_sec": round(orbit_result["wall_clock_sec"] - baseline_result["wall_clock_sec"], 2),
                "guardrails_guarantee": "Orbit enforces PathEscapeError, MAX_TOOL_CALLS, and TEST_TIMEOUT_SEC",
                "evidence_guarantee": "Orbit generates pre-edit unified diffs, FailureContext, and Global Review Verdict",
            },
        }

        # Save artifact
        out_path = ROOT / "orbit_runs" / "hero_artifact.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(hero_data, indent=2), encoding="utf-8")

        # 4. Print Comparison Table
        print("\n" + "=" * 70)
        print("  HERO ARTIFACT RESULT TABLE (PRD §3.2)")
        print("=" * 70)
        header = f"{'Metric':<28} | {'Naive Baseline':<18} | {'Orbit Harness':<18}"
        print(header)
        print("-" * len(header))
        print(f"{'Architecture':<28} | {baseline_result['architecture']:<18} | {orbit_result['architecture']:<18}")
        print(f"{'Model':<28} | {baseline_result['model']:<18} | {orbit_result['model']:<18}")
        print(f"{'Test Suite Passed':<28} | {str(baseline_result['test_suite_passed']):<18} | {str(orbit_result['test_suite_passed']):<18}")
        print(f"{'Reviewer Verdict':<28} | {baseline_result['reviewer_verdict']:<18} | {orbit_result['reviewer_verdict']:<18}")
        print(f"{'Wall-Clock Time (s)':<28} | {baseline_result['wall_clock_sec']:<18} | {orbit_result['wall_clock_sec']:<18}")
        print(f"{'Tokens In':<28} | {baseline_result['tokens_in']:<18} | {orbit_result['tokens_in']:<18}")
        print(f"{'Tokens Out':<28} | {baseline_result['tokens_out']:<18} | {orbit_result['tokens_out']:<18}")
        print(f"{'Total Tokens':<28} | {baseline_result['total_tokens']:<18} | {orbit_result['total_tokens']:<18}")
        print(f"{'Tool Calls (Isolated)':<28} | {baseline_result['tool_calls']:<18} | {orbit_result['tool_calls']:<18}")
        print(f"{'Guardrails Enforced':<28} | {str(baseline_result['guardrails_enforced']):<18} | {str(orbit_result['guardrails_enforced']):<18}")
        print(f"{'Parallel Workers':<28} | {baseline_result['parallel_workers']:<18} | {orbit_result['parallel_workers']:<18}")
        print(f"{'Targeted Recovery Loop':<28} | {str(baseline_result['recovery_loop']):<18} | {str(orbit_result['recovery_loop']):<18}")
        print("-" * len(header))
        print(f"Artifact saved to: {out_path}\n")

    finally:
        # Restore target directory
        if backup_dir.exists():
            shutil.rmtree(target_dir)
            shutil.copytree(backup_dir, target_dir)
            shutil.rmtree(backup_dir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
