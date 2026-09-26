"""
scheduler.py — Orbit Top-Level Entry Point
==========================================
Phase 0: skeleton only.  Every public function raises NotImplementedError
until the corresponding Phase is implemented.

Usage (Phase 4+):
    python scheduler.py --target <path> --task "<requirement>"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


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
        "--dry-run",
        action="store_true",
        help="Print the task graph without executing workers.",
    )
    args = parser.parse_args(argv)

    target: Path = args.target.resolve()
    if not target.exists():
        print(f"[orbit] ERROR: target path does not exist: {target}", file=sys.stderr)
        return 1

    is_git = (target / ".git").exists()
    print(f"[orbit] target  : {target}")
    print(f"[orbit] git repo: {is_git}")
    print(f"[orbit] task    : {args.task!r}")
    print("[orbit] Phase 0 — skeleton only.  No workers run yet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
