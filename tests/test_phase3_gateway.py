"""
tests/test_phase3_gateway.py — Phase 3 Tool Gateway Tests
==========================================================
PRD requirement: "Write unit tests that confirm the path restriction
actually blocks an out-of-scope write."

Test groups
-----------
A. TestPathIsolation      — core security: every write/patch/exec-cwd
                            outside workspace_root raises PathEscapeError.
B. TestReadFile           — read in/outside workspace, missing file.
C. TestWriteFile          — write inside workspace, mkdir parents, content.
D. TestCallBudget         — ToolBudgetExhausted after max_tool_calls.
E. TestExec               — basic exec, retry-on-nonzero, retry exhaustion.
F. TestRunTests           — passing suite, failing suite, timeout kills runner.
G. TestSearchCode         — delegate to RepoIndex, budget charged.
H. TestAuditLog           — every call appended; PathEscapeError logged.
I. TestSummary            — summary() keys and budget_remaining arithmetic.
"""

from __future__ import annotations

import os
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gateway.gateway import (
    ExecResult,
    GatewayEvent,
    PathEscapeError,
    TestResult,
    TestTimeoutError,
    ToolBudgetExhausted,
    ToolGateway,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """Return a fresh workspace directory for each test."""
    return tmp_path / "workspace"


@pytest.fixture
def gw(ws: Path) -> ToolGateway:
    return ToolGateway(
        workspace_root=ws,
        max_tool_calls=20,
        max_retries=2,
        test_timeout_sec=10,
    )


# ===========================================================================
# A. Path Isolation — the headline security guarantee
# ===========================================================================

class TestPathIsolation:

    def test_write_absolute_outside_workspace_raises(self, gw: ToolGateway, tmp_path: Path):
        """Absolute path that resolves outside workspace_root → PathEscapeError."""
        evil_path = tmp_path / "evil.txt"   # sibling of workspace, not inside it
        with pytest.raises(PathEscapeError) as exc_info:
            gw.write_file(evil_path, "I should not appear")
        assert not evil_path.exists(), "File must not be created on escape attempt"
        assert "workspace" in str(exc_info.value).lower() or \
               str(tmp_path) in str(exc_info.value)

    def test_write_dotdot_traversal_raises(self, gw: ToolGateway, tmp_path: Path):
        """../../../etc/passwd style traversal → PathEscapeError."""
        with pytest.raises(PathEscapeError):
            gw.write_file("../../etc/passwd", "root:x:0:0")

    def test_write_symlink_pointing_outside_raises(self, gw: ToolGateway, ws: Path, tmp_path: Path):
        """Symlink inside workspace pointing outside → PathEscapeError."""
        ws.mkdir(parents=True, exist_ok=True)
        outside = tmp_path / "outside_file.txt"
        outside.write_text("secret")
        link = ws / "evil_link.txt"
        link.symlink_to(outside)
        with pytest.raises(PathEscapeError):
            gw.write_file(link, "overwrite secret")
        # Original file should be untouched
        assert outside.read_text() == "secret"

    def test_write_inside_workspace_succeeds(self, gw: ToolGateway, ws: Path):
        """A path clearly inside workspace_root must succeed."""
        gw.write_file("safe.txt", "hello")
        assert (ws / "safe.txt").read_text() == "hello"

    def test_write_subdirectory_inside_workspace_succeeds(self, gw: ToolGateway, ws: Path):
        """Nested relative path inside workspace is fine."""
        gw.write_file("a/b/c.txt", "nested")
        assert (ws / "a" / "b" / "c.txt").read_text() == "nested"

    def test_exec_cwd_outside_workspace_raises(self, gw: ToolGateway, tmp_path: Path):
        """exec() with a cwd outside workspace → PathEscapeError (not execution)."""
        with pytest.raises(PathEscapeError):
            gw.exec("echo hi", cwd=tmp_path)

    def test_patch_outside_workspace_raises(self, gw: ToolGateway, tmp_path: Path):
        """patch_file() targeting a path outside workspace → PathEscapeError."""
        evil = tmp_path / "target.txt"
        evil.write_text("original")
        with pytest.raises(PathEscapeError):
            gw.patch_file(evil, "--- target.txt\n+++ target.txt\n")
        assert evil.read_text() == "original"

    def test_escape_path_logged(self, gw: ToolGateway, tmp_path: Path):
        """PathEscapeError attempts appear in the audit log as failed events."""
        evil = tmp_path / "evil.txt"
        try:
            gw.write_file(evil, "x")
        except PathEscapeError:
            pass
        assert any(not e.success for e in gw.log), \
            "Escape attempt must be logged as a failed event"


# ===========================================================================
# B. Read File
# ===========================================================================

class TestReadFile:

    def test_read_inside_workspace(self, gw: ToolGateway, ws: Path):
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "hello.py").write_text("print('hi')")
        assert gw.read_file("hello.py") == "print('hi')"

    def test_read_absolute_path_outside_workspace(self, gw: ToolGateway, tmp_path: Path):
        """Reads are NOT workspace-restricted — workers must read the target repo."""
        outside = tmp_path / "repo_file.py"
        outside.write_text("# target repo file")
        content = gw.read_file(outside)
        assert content == "# target repo file"

    def test_read_nonexistent_raises(self, gw: ToolGateway):
        with pytest.raises(FileNotFoundError):
            gw.read_file("does_not_exist.txt")

    def test_read_charges_budget(self, gw: ToolGateway, ws: Path):
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "f.txt").write_text("x")
        before = gw._call_count
        gw.read_file("f.txt")
        assert gw._call_count == before + 1


# ===========================================================================
# C. Write File
# ===========================================================================

class TestWriteFile:

    def test_write_creates_file(self, gw: ToolGateway, ws: Path):
        gw.write_file("output.py", "x = 1\n")
        assert (ws / "output.py").read_text() == "x = 1\n"

    def test_write_creates_parent_dirs(self, gw: ToolGateway, ws: Path):
        gw.write_file("deep/nested/file.txt", "content")
        assert (ws / "deep" / "nested" / "file.txt").exists()

    def test_write_overwrites_existing(self, gw: ToolGateway, ws: Path):
        gw.write_file("same.txt", "v1")
        gw.write_file("same.txt", "v2")
        assert (ws / "same.txt").read_text() == "v2"

    def test_write_charges_budget(self, gw: ToolGateway):
        before = gw._call_count
        gw.write_file("a.txt", "")
        assert gw._call_count == before + 1


# ===========================================================================
# D. Call Budget
# ===========================================================================

class TestCallBudget:

    def test_budget_exhausted_after_limit(self, ws: Path):
        """After max_tool_calls calls, the very next call raises ToolBudgetExhausted."""
        gw = ToolGateway(workspace_root=ws, max_tool_calls=3)
        for i in range(3):
            gw.write_file(f"f{i}.txt", "")
        with pytest.raises(ToolBudgetExhausted) as exc_info:
            gw.write_file("one_too_many.txt", "")
        assert exc_info.value.limit == 3

    def test_calls_remaining_counts_down(self, ws: Path):
        gw = ToolGateway(workspace_root=ws, max_tool_calls=5)
        assert gw.calls_remaining == 5
        gw.write_file("f.txt", "")
        assert gw.calls_remaining == 4

    def test_budget_error_message_includes_limit(self, ws: Path):
        gw = ToolGateway(workspace_root=ws, max_tool_calls=1)
        gw.write_file("f.txt", "")
        with pytest.raises(ToolBudgetExhausted) as exc_info:
            gw.write_file("g.txt", "")
        assert "1" in str(exc_info.value)

    def test_read_also_charges_budget(self, ws: Path):
        gw = ToolGateway(workspace_root=ws, max_tool_calls=1)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "f.txt").write_text("x")
        gw.read_file("f.txt")
        with pytest.raises(ToolBudgetExhausted):
            gw.read_file("f.txt")


# ===========================================================================
# E. Exec
# ===========================================================================

class TestExec:

    def test_exec_success(self, gw: ToolGateway):
        result = gw.exec("echo hello_orbit")
        assert result.ok
        assert "hello_orbit" in result.stdout

    def test_exec_nonzero_returncode(self, gw: ToolGateway):
        result = gw.exec("python3 -c 'import sys; sys.exit(42)'")
        assert result.returncode == 42
        assert not result.ok

    def test_exec_retry_on_nonzero_counts_attempts(self, ws: Path):
        """With retry_on_nonzero=True and always-failing command,
        attempts == max_retries + 1."""
        gw = ToolGateway(workspace_root=ws, max_tool_calls=50, max_retries=2)
        result = gw.exec(
            "python3 -c 'import sys; sys.exit(1)'",
            retry_on_nonzero=True,
        )
        assert result.attempts == 3      # 1 original + 2 retries
        assert result.returncode == 1

    def test_exec_retry_stops_on_success(self, ws: Path, tmp_path: Path):
        """Retry stops as soon as one attempt succeeds."""
        counter_file = tmp_path / "count.txt"
        counter_file.write_text("0")
        # Script: fail first time, succeed second
        script = textwrap.dedent(f"""\
            import sys
            from pathlib import Path
            p = Path(r'{counter_file}')
            n = int(p.read_text())
            p.write_text(str(n + 1))
            sys.exit(0 if n >= 1 else 1)
        """)
        script_file = ws / "counter.py"
        ws.mkdir(parents=True, exist_ok=True)
        script_file.write_text(script)

        gw = ToolGateway(workspace_root=ws, max_tool_calls=50, max_retries=3)
        result = gw.exec(f"python3 {script_file}", retry_on_nonzero=True)
        assert result.ok
        assert result.attempts == 2   # failed on 1, succeeded on 2

    def test_exec_charges_budget_per_attempt(self, ws: Path):
        """Each retry attempt is a separate budget charge."""
        gw = ToolGateway(workspace_root=ws, max_tool_calls=50, max_retries=2)
        gw.exec("python3 -c 'import sys; sys.exit(1)'", retry_on_nonzero=True)
        assert gw._call_count == 3  # 1 original + 2 retries


# ===========================================================================
# F. Run Tests
# ===========================================================================

class TestRunTests:

    def test_run_tests_passing_suite(self, gw: ToolGateway, ws: Path):
        """A trivially passing pytest suite → TestResult.passed=True."""
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "test_trivial.py").write_text("def test_ok(): assert 1 + 1 == 2\n")
        result = gw.run_tests(
            test_command=f"python3 -m pytest {ws}/test_trivial.py -q",
            cwd=ws,
        )
        assert result.passed
        assert result.returncode == 0
        assert result.duration_sec >= 0

    def test_run_tests_failing_suite(self, gw: ToolGateway, ws: Path):
        """A failing test → TestResult.passed=False, nonzero returncode."""
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "test_fail.py").write_text("def test_bad(): assert 1 == 2\n")
        result = gw.run_tests(
            test_command=f"python3 -m pytest {ws}/test_fail.py -q",
            cwd=ws,
        )
        assert not result.passed
        assert result.returncode != 0

    def test_run_tests_timeout_raises(self, ws: Path):
        """Test runner that sleeps longer than timeout → TestTimeoutError."""
        gw = ToolGateway(workspace_root=ws, max_tool_calls=20, test_timeout_sec=2)
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "test_slow.py").write_text(
            "import time\ndef test_slow(): time.sleep(30)\n"
        )
        with pytest.raises(TestTimeoutError) as exc_info:
            gw.run_tests(
                test_command=f"python3 -m pytest {ws}/test_slow.py -q",
                cwd=ws,
            )
        assert exc_info.value.timeout == 2

    def test_run_tests_charges_budget_once(self, gw: ToolGateway, ws: Path):
        """run_tests() counts as exactly one tool call even for a multi-file suite."""
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "test_t.py").write_text("def test_x(): pass\n")
        before = gw._call_count
        gw.run_tests(f"python3 -m pytest {ws}/test_t.py -q", cwd=ws)
        assert gw._call_count == before + 1


# ===========================================================================
# G. Search Code
# ===========================================================================

class TestSearchCode:

    def test_search_returns_hits(self, gw: ToolGateway, ws: Path):
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "module.py").write_text("def hello(): pass\ndef world(): pass\n")
        results = gw.search_code("def hello", search_root=ws)
        assert any("hello" in r.content for r in results)

    def test_search_no_match_returns_empty(self, gw: ToolGateway, ws: Path):
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "module.py").write_text("x = 1\n")
        results = gw.search_code("XYZZY_NOMATCH", search_root=ws)
        assert results == []

    def test_search_charges_budget(self, gw: ToolGateway, ws: Path):
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "f.py").write_text("def fn(): pass\n")
        before = gw._call_count
        gw.search_code("def", search_root=ws)
        assert gw._call_count == before + 1


# ===========================================================================
# H. Audit Log
# ===========================================================================

class TestAuditLog:

    def test_successful_write_logged(self, gw: ToolGateway):
        gw.write_file("x.txt", "hi")
        assert len(gw.log) == 1
        assert gw.log[0].tool == "write_file"
        assert gw.log[0].success is True

    def test_failed_escape_logged(self, gw: ToolGateway, tmp_path: Path):
        evil = tmp_path / "evil.txt"
        try:
            gw.write_file(evil, "x")
        except PathEscapeError:
            pass
        assert any(e.tool == "write_file" and not e.success for e in gw.log)

    def test_multiple_calls_all_logged(self, gw: ToolGateway, ws: Path):
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "f.txt").write_text("x")
        gw.read_file("f.txt")
        gw.write_file("g.txt", "y")
        gw.exec("echo z")
        assert len(gw.log) == 3
        tools = {e.tool for e in gw.log}
        assert tools == {"read_file", "write_file", "exec"}

    def test_log_timestamps_increase(self, gw: ToolGateway, ws: Path):
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "f.txt").write_text("x")
        gw.read_file("f.txt")
        time.sleep(0.01)
        gw.write_file("g.txt", "y")
        assert gw.log[1].timestamp >= gw.log[0].timestamp


# ===========================================================================
# I. Summary
# ===========================================================================

class TestSummary:

    def test_summary_has_required_keys(self, gw: ToolGateway):
        s = gw.summary()
        for key in ("workspace_root", "call_count", "max_tool_calls",
                    "budget_remaining", "max_retries", "test_timeout_sec",
                    "wall_clock_sec", "log"):
            assert key in s, f"Missing summary key: {key}"

    def test_budget_remaining_decrements(self, gw: ToolGateway):
        before = gw.summary()["budget_remaining"]
        gw.write_file("f.txt", "x")
        after = gw.summary()["budget_remaining"]
        assert after == before - 1

    def test_summary_log_entries(self, gw: ToolGateway):
        gw.write_file("a.txt", "1")
        gw.write_file("b.txt", "2")
        s = gw.summary()
        assert len(s["log"]) == 2
        assert all("tool" in entry for entry in s["log"])
