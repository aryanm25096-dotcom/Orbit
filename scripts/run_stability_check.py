#!/usr/bin/env python3
"""
scripts/run_stability_check.py — Run 3 iterations of Hero Artifact benchmark
=============================================================================
Runs hero artifact 3 times, saving:
  - orbit_runs/hero_artifact_run1.json
  - orbit_runs/hero_artifact_run2.json
  - orbit_runs/hero_artifact_run3.json
Then prints side-by-side comparison across all 3 runs.
"""

import json
import subprocess
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def run_iteration(run_idx: int) -> dict:
    out_file = f"orbit_runs/hero_artifact_run{run_idx}.json"
    print(f"\n{'='*70}\n  STARTING STABILITY RUN {run_idx}/3 → {out_file}\n{'='*70}\n")
    proc = subprocess.run(
        [sys.executable, "scripts/run_hero_artifact.py", "--out", out_file],
        cwd=str(ROOT),
        capture_output=False,
    )
    if proc.returncode != 0:
        print(f"[ERROR] Run {run_idx} failed with exit code {proc.returncode}")
    out_path = ROOT / out_file
    if out_path.exists():
        return json.loads(out_path.read_text(encoding="utf-8"))
    return {}

def main() -> int:
    results = []
    for i in range(1, 4):
        res = run_iteration(i)
        results.append(res)

    # Copy run 1 (or best run) to hero_artifact.json
    p1 = ROOT / "orbit_runs/hero_artifact_run1.json"
    if p1.exists():
        shutil.copy2(p1, ROOT / "orbit_runs/hero_artifact.json")

    # Print 3-run summary table
    print("\n" + "=" * 80)
    print("  ORBIT STABILITY CHECK: 3 RUNS SIDE-BY-SIDE")
    print("=" * 80)
    header = f"{'Metric':<25} | {'Run 1':<16} | {'Run 2':<16} | {'Run 3':<16}"
    print(header)
    print("-" * len(header))

    metrics = [
        ("Reviewer Verdict", lambda r: r.get("orbit", {}).get("reviewer_verdict", "N/A")),
        ("Test Suite Passed", lambda r: str(r.get("orbit", {}).get("test_suite_passed", "N/A"))),
        ("Parallel Workers", lambda r: str(r.get("orbit", {}).get("parallel_workers", "N/A"))),
        ("Recovery Loops Run", lambda r: str(r.get("orbit", {}).get("recovery_loops_run", "N/A"))),
        ("Tool Calls Total", lambda r: str(r.get("orbit", {}).get("tool_calls", "N/A"))),
        ("Total Tokens (In+Out)", lambda r: str(r.get("orbit", {}).get("total_tokens", "N/A"))),
        ("Tokens In", lambda r: str(r.get("orbit", {}).get("tokens_in", "N/A"))),
        ("Tokens Out", lambda r: str(r.get("orbit", {}).get("tokens_out", "N/A"))),
        ("Wall-Clock Sec", lambda r: str(r.get("orbit", {}).get("wall_clock_sec", "N/A"))),
        ("Baseline Passed?", lambda r: str(r.get("baseline", {}).get("test_suite_passed", "N/A"))),
        ("Baseline Tokens", lambda r: str(r.get("baseline", {}).get("total_tokens", "N/A"))),
    ]

    for label, extractor in metrics:
        vals = [extractor(r) for r in results]
        print(f"{label:<25} | {vals[0]:<16} | {vals[1]:<16} | {vals[2]:<16}")

    print("=" * 80 + "\n")
    return 0

if __name__ == "__main__":
    sys.exit(main())
