"""
gateway/gateway.py — Tool Gateway
===================================
Phase 3 implementation target.  Phase 0 stub only.

Wraps file ops, exec, test-runner, code search.
Enforces per-worker guardrails from config/routing.yaml:
  MAX_TOOL_CALLS, MAX_RETRIES, TEST_TIMEOUT_SEC.
"""

from __future__ import annotations

from pathlib import Path


class ToolGateway:
    """Central dispatcher for all tool calls issued by workers."""

    def __init__(
        self,
        workspace: Path,
        max_tool_calls: int = 30,
        max_retries: int = 3,
        test_timeout_sec: int = 120,
    ) -> None:
        self.workspace = workspace
        self.max_tool_calls = max_tool_calls
        self.max_retries = max_retries
        self.test_timeout_sec = test_timeout_sec
        self._call_count = 0

    def read_file(self, path: str) -> str:
        raise NotImplementedError("Phase 3")

    def write_file(self, path: str, content: str) -> None:
        raise NotImplementedError("Phase 3")

    def exec(self, command: str) -> tuple[int, str, str]:
        """Returns (returncode, stdout, stderr)."""
        raise NotImplementedError("Phase 3")

    def run_tests(self) -> tuple[bool, str]:
        """Returns (passed, output)."""
        raise NotImplementedError("Phase 3")

    def search_code(self, pattern: str) -> list[dict]:
        raise NotImplementedError("Phase 3")
