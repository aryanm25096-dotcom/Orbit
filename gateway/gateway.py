"""
gateway/gateway.py — Tool Gateway (Phase 3)
============================================
PRD §4.4:
  "A scoped set of tools — file read/write/patch, terminal exec,
   git diff (when available), test runner, code search — each confined
   to one worker's own workspace (isolated folders, or git worktrees
   when the target has a .git directory)."

  Hard limits enforced from day one:
    MAX_TOOL_CALLS    — caps runaway tool usage per task.
    MAX_RETRIES       — caps how many times a single failing call is retried.
    TEST_TIMEOUT_SEC  — caps wall-clock spent per verification pass.

Design contracts
----------------
1. PATH ISOLATION — every write/exec is resolved to an absolute path and
   must be a descendant of workspace_root.  Violations raise
   PathEscapeError immediately; no retry, no fallback.

2. CALL BUDGET — every public tool method increments _call_count.
   When it hits max_tool_calls the gateway raises ToolBudgetExhausted.
   Read-only calls (read_file, search_code) are still counted.

3. RETRIES — exec() and run_tests() accept a retry_on_nonzero flag.
   When True they will retry up to max_retries times on a non-zero exit
   code, each attempt counted separately against the call budget.

4. TEST TIMEOUT — run_tests() kills the subprocess after TEST_TIMEOUT_SEC
   and raises TestTimeoutError.

5. AUDIT LOG — every tool call is appended to self.log as a GatewayEvent
   dataclass.  Workers and telemetry consume this.

6. PATCH — patch_file() applies a unified diff string via the standard
   `patch` command (or falls back to in-process apply for simple cases).

Public interface
----------------
    read_file(rel_or_abs_path)  -> str
    write_file(rel_or_abs_path, content)
    patch_file(rel_or_abs_path, unified_diff)
    exec(command, cwd=None, retry_on_nonzero=False) -> ExecResult
    run_tests(test_command, cwd=None)  -> TestResult
    search_code(pattern, glob="")     -> list[CodeSearchResult]
    summary()                          -> dict   (for telemetry)
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from indexer.indexer import CodeSearchResult, RepoIndex


# ---------------------------------------------------------------------------
# Exceptions — all hard stops (no silent recovery)
# ---------------------------------------------------------------------------

class PathEscapeError(PermissionError):
    """
    Raised when a requested path resolves outside the workspace root.
    This is a hard security boundary; no retry, no fallback.
    """
    def __init__(self, requested: Path, workspace: Path) -> None:
        super().__init__(
            f"Path escape attempt blocked.\n"
            f"  Requested : {requested}\n"
            f"  Workspace : {workspace}\n"
            f"  Requested path is not under the workspace root."
        )
        self.requested = requested
        self.workspace = workspace


class ToolBudgetExhausted(RuntimeError):
    """Raised when max_tool_calls is hit for this gateway instance."""
    def __init__(self, limit: int) -> None:
        super().__init__(
            f"Tool budget exhausted: {limit} calls reached. "
            f"Increase MAX_TOOL_CALLS or decompose the task."
        )
        self.limit = limit


class TestTimeoutError(TimeoutError):
    """Raised when the test runner exceeds TEST_TIMEOUT_SEC."""
    __test__ = False

    def __init__(self, timeout: int, command: str) -> None:
        super().__init__(
            f"Test runner timed out after {timeout}s: {command!r}"
        )
        self.timeout = timeout
        self.command = command


class PatchError(ValueError):
    """Raised when patch_file() cannot apply the supplied diff."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ExecResult:
    """Return value of ToolGateway.exec()."""
    returncode: int
    stdout: str
    stderr: str
    command: str
    attempts: int = 1

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass
class TestResult:
    """Return value of ToolGateway.run_tests()."""
    __test__ = False

    passed: bool
    output: str          # combined stdout + stderr
    returncode: int
    command: str
    duration_sec: float
    timed_out: bool = False


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

@dataclass
class GatewayEvent:
    """One entry in the gateway's immutable audit log."""
    timestamp: float            # monotonic seconds since gateway creation
    tool: str                   # "read_file", "write_file", "exec", etc.
    args: dict[str, Any]        # call arguments (paths sanitised to str)
    success: bool
    error: str = ""


# ---------------------------------------------------------------------------
# Test Runner Auto-Detection (PRD §4.4, §7)
# ---------------------------------------------------------------------------

def detect_test_command(target_dir: str | Path) -> str:
    """
    Detect the appropriate test command from the target directory structure:
    1. pytest.ini or pyproject.toml -> 'pytest'
    2. package.json with a 'test' script -> 'npm test'
    3. Cargo.toml -> 'cargo test'
    4. Makefile with a 'test' target -> 'make test'
    5. fallback -> 'pytest'
    """
    p = Path(target_dir).resolve()

    # 1. pytest.ini or pyproject.toml -> pytest
    if (p / "pytest.ini").exists() or (p / "pyproject.toml").exists():
        return "pytest"

    # 2. package.json with a "test" script -> npm test
    pkg_json = p / "package.json"
    if pkg_json.exists():
        try:
            with open(pkg_json, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "test" in data.get("scripts", {}):
                return "npm test"
        except Exception:
            pass

    # 3. Cargo.toml -> cargo test
    if (p / "Cargo.toml").exists():
        return "cargo test"

    # 4. Makefile with a "test" target -> make test
    for mf in ["Makefile", "makefile", "GNUmakefile"]:
        makefile = p / mf
        if makefile.exists():
            try:
                content = makefile.read_text(encoding="utf-8", errors="replace")
                if re.search(r"^\s*test\s*:", content, re.MULTILINE):
                    return "make test"
            except Exception:
                pass

    # 5. fallback -> pytest
    return "pytest"


# ---------------------------------------------------------------------------
# ToolGateway
# ---------------------------------------------------------------------------

class ToolGateway:
    """
    Scoped tool dispatcher for one worker's isolated workspace.

    All write operations are confined to workspace_root.  Reads may
    reference files anywhere on disk (workers need to read the target
    repo), but writes, patches, and exec cwd are workspace-scoped.

    Parameters
    ----------
    workspace_root   : Absolute path the worker may write to.
    max_tool_calls   : Hard call budget (PRD §4.4).
    max_retries      : Max retry attempts per exec/test call.
    test_timeout_sec : Wall-clock limit for run_tests() (PRD §4.4).
    """

    def __init__(
        self,
        workspace_root: Path,
        max_tool_calls: int = 30,
        max_retries: int = 3,
        test_timeout_sec: int = 120,
    ) -> None:
        self.workspace_root: Path = Path(workspace_root).resolve()
        self.max_tool_calls: int = max_tool_calls
        self.max_retries: int = max_retries
        self.test_timeout_sec: int = test_timeout_sec

        self._call_count: int = 0
        self._start: float = time.monotonic()
        self.log: list[GatewayEvent] = []

        # Ensure workspace exists on disk
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal guards
    # ------------------------------------------------------------------

    def _stamp(self) -> float:
        return round(time.monotonic() - self._start, 4)

    def _charge(self, tool: str, args: dict, success: bool, error: str = "") -> None:
        """Increment call counter and append to audit log. Raises if budget hit."""
        self._call_count += 1
        self.log.append(GatewayEvent(
            timestamp=self._stamp(),
            tool=tool,
            args=args,
            success=success,
            error=error,
        ))
        if self._call_count > self.max_tool_calls:
            raise ToolBudgetExhausted(self.max_tool_calls)

    def _safe_write_path(self, path: str | Path) -> Path:
        """
        Resolve *path* relative to workspace_root and verify it is a
        descendant of workspace_root.  Raises PathEscapeError if not.

        Symlinks are followed before the check so a symlink pointing
        outside the workspace is caught even if the link itself is inside.
        """
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace_root / p
        # resolve() follows symlinks; strict=False so the file needn't exist yet
        resolved = p.resolve()
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise PathEscapeError(resolved, self.workspace_root)
        return resolved

    def _safe_read_path(self, path: str | Path) -> Path:
        """
        Resolve *path* for reading.  Reads may reference files anywhere
        (workers read from the target repo), so no workspace restriction.
        Returns an absolute Path.
        """
        p = Path(path)
        if not p.is_absolute():
            p = self.workspace_root / p
        return p.resolve()

    def _safe_exec_cwd(self, cwd: str | Path | None) -> Path:
        """
        Resolve exec cwd.  Must be inside workspace_root (or workspace_root
        itself).  Defaults to workspace_root when None.
        """
        if cwd is None:
            return self.workspace_root
        return self._safe_write_path(cwd)

    # ------------------------------------------------------------------
    # Public tools
    # ------------------------------------------------------------------

    def read_file(self, path: str | Path) -> str:
        """
        Read and return the text content of *path*.
        Reads are not workspace-restricted (workers must read the target
        repo), but every call counts against the tool budget.
        """
        tool = "read_file"
        args = {"path": str(path)}
        resolved = self._safe_read_path(path)
        try:
            content = resolved.read_text(encoding="utf-8", errors="replace")
            self._charge(tool, args, success=True)
            return content
        except (OSError, PermissionError) as exc:
            self._charge(tool, args, success=False, error=str(exc))
            raise

    def write_file(self, path: str | Path, content: str) -> None:
        """
        Write *content* to *path* (text, UTF-8).
        *path* MUST resolve inside workspace_root — raises PathEscapeError
        otherwise.  Parent directories are created automatically.
        """
        tool = "write_file"
        args = {"path": str(path)}
        try:
            resolved = self._safe_write_path(path)   # raises PathEscapeError if outside
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
            self._charge(tool, args, success=True)
        except PathEscapeError as exc:
            # charge for the attempt, then re-raise (no retry for security violations)
            self._charge(tool, args, success=False, error=str(exc))
            raise
        except (OSError, PermissionError) as exc:
            self._charge(tool, args, success=False, error=str(exc))
            raise

    def patch_file(self, path: str | Path, unified_diff: str) -> None:
        """
        Apply *unified_diff* (unified diff format) to *path*.

        Strategy:
          1. Write the diff to a temp file inside workspace.
          2. Try `patch --unified -i <diff_file> <target>` (standard POSIX tool).
          3. On failure: raise PatchError with the patch output.

        *path* must be inside workspace_root.
        """
        tool = "patch_file"
        args = {"path": str(path)}
        try:
            resolved = self._safe_write_path(path)
        except PathEscapeError as exc:
            self._charge(tool, args, success=False, error=str(exc))
            raise

        # Write temp diff file inside workspace
        diff_file = self.workspace_root / ".orbit_patch.diff"
        diff_file.write_text(unified_diff, encoding="utf-8")

        try:
            result = subprocess.run(
                ["patch", "--unified", str(resolved), str(diff_file)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.workspace_root),
            )
            if result.returncode != 0:
                self._charge(tool, args, success=False, error=result.stderr)
                raise PatchError(
                    f"patch command failed (rc={result.returncode}):\n"
                    f"{result.stderr}\n{result.stdout}"
                )
            self._charge(tool, args, success=True)
        finally:
            if diff_file.exists():
                diff_file.unlink()

    def exec(
        self,
        command: str | list[str],
        cwd: str | Path | None = None,
        retry_on_nonzero: bool = False,
        env: dict[str, str] | None = None,
    ) -> ExecResult:
        """
        Run *command* in a subprocess.

        - cwd defaults to workspace_root; any explicit cwd must be inside it.
        - If retry_on_nonzero=True, retries up to max_retries times on
          non-zero exit (each attempt charges the call budget).
        - Raises ToolBudgetExhausted if retries exhaust the budget.

        Returns ExecResult with returncode, stdout, stderr, attempts count.
        """
        tool = "exec"
        if isinstance(command, list):
            cmd_str = " ".join(command)
            cmd_list = command
        else:
            cmd_str = command
            cmd_list = shlex.split(command)

        try:
            safe_cwd = self._safe_exec_cwd(cwd)
        except PathEscapeError as exc:
            self._charge(tool, {"command": cmd_str, "cwd": str(cwd)}, success=False, error=str(exc))
            raise

        args = {"command": cmd_str, "cwd": str(safe_cwd)}

        max_attempts = (self.max_retries + 1) if retry_on_nonzero else 1
        last_result: ExecResult | None = None

        for attempt in range(1, max_attempts + 1):
            try:
                proc = subprocess.run(
                    cmd_list,
                    capture_output=True,
                    text=True,
                    cwd=str(safe_cwd),
                    timeout=self.test_timeout_sec,
                    env=env,
                )
                result = ExecResult(
                    returncode=proc.returncode,
                    stdout=proc.stdout,
                    stderr=proc.stderr,
                    command=cmd_str,
                    attempts=attempt,
                )
                success = proc.returncode == 0
                self._charge(tool, {**args, "attempt": attempt}, success=success)
                last_result = result
                if success or not retry_on_nonzero:
                    return result
            except subprocess.TimeoutExpired:
                self._charge(tool, {**args, "attempt": attempt},
                             success=False, error="timeout")
                raise TestTimeoutError(self.test_timeout_sec, cmd_str)
            except (OSError, FileNotFoundError) as exc:
                self._charge(tool, {**args, "attempt": attempt},
                             success=False, error=str(exc))
                raise

        # All retries exhausted — return the last (failed) result
        assert last_result is not None
        return last_result

    def run_tests(
        self,
        test_command: str | list[str] | None = None,
        cwd: str | Path | None = None,
    ) -> TestResult:
        """
        Run the test suite and return a TestResult.

        - If test_command is None, auto-detects from cwd / workspace_root.
        - Killed (SIGKILL) after TEST_TIMEOUT_SEC → timed_out=True.
        - cwd defaults to workspace_root.
        - Does NOT retry on failure (recovery loop is the Integrator's job).
        - Counts as one tool call regardless of duration.
        """
        tool = "run_tests"

        try:
            safe_cwd = self._safe_exec_cwd(cwd)
        except PathEscapeError as exc:
            cmd_label = test_command if isinstance(test_command, str) else "auto-detect"
            self._charge(tool, {"command": cmd_label, "cwd": str(cwd)}, success=False, error=str(exc))
            raise

        if test_command is None:
            test_command = detect_test_command(safe_cwd)

        if isinstance(test_command, list):
            cmd_str = " ".join(test_command)
            cmd_list = list(test_command)
        else:
            cmd_str = test_command
            cmd_list = shlex.split(test_command)

        # Bridge: if running pytest and pytest binary not found in PATH, use sys.executable -m pytest
        if cmd_list and cmd_list[0] == "pytest" and shutil.which("pytest") is None:
            cmd_exec = [sys.executable, "-m", "pytest"] + cmd_list[1:]
        else:
            cmd_exec = cmd_list

        args = {"command": cmd_str, "cwd": str(safe_cwd)}

        t0 = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                cmd_exec,
                capture_output=True,
                text=True,
                cwd=str(safe_cwd),
                timeout=self.test_timeout_sec,
            )
            duration = time.monotonic() - t0
            output = proc.stdout + proc.stderr
            passed = proc.returncode == 0
            result = TestResult(
                passed=passed,
                output=output,
                returncode=proc.returncode,
                command=cmd_str,
                duration_sec=round(duration, 2),
                timed_out=False,
            )
            self._charge(tool, args, success=passed)
            return result

        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - t0
            output = (exc.stdout or b"").decode(errors="replace") + \
                     (exc.stderr or b"").decode(errors="replace")
            result = TestResult(
                passed=False,
                output=output + f"\n[orbit] Test runner killed after {self.test_timeout_sec}s",
                returncode=-1,
                command=cmd_str,
                duration_sec=round(duration, 2),
                timed_out=True,
            )
            self._charge(tool, args, success=False, error="timeout")
            raise TestTimeoutError(self.test_timeout_sec, cmd_str)

    def search_code(
        self,
        pattern: str,
        glob: str = "",
        search_root: Path | None = None,
    ) -> list[CodeSearchResult]:
        """
        Regex code search using the Phase 1 RepoIndex.
        search_root defaults to workspace_root; pass the target repo
        root to search the source being edited.
        Counts as one tool call.
        """
        tool = "search_code"
        root = Path(search_root) if search_root else self.workspace_root
        args = {"pattern": pattern, "glob": glob, "root": str(root)}
        try:
            idx = RepoIndex(root=root).scan()
            results = idx.search(pattern, file_glob=glob)
            self._charge(tool, args, success=True)
            return results
        except Exception as exc:
            self._charge(tool, args, success=False, error=str(exc))
            raise

    # ------------------------------------------------------------------
    # Introspection / telemetry
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """
        Return a JSON-serialisable summary for telemetry and Reviewer evidence.
        """
        return {
            "workspace_root": str(self.workspace_root),
            "call_count": self._call_count,
            "total_calls": self._call_count,
            "max_tool_calls": self.max_tool_calls,
            "budget_remaining": max(0, self.max_tool_calls - self._call_count),
            "max_retries": self.max_retries,
            "test_timeout_sec": self.test_timeout_sec,
            "wall_clock_sec": round(time.monotonic() - self._start, 2),
            "log": [
                {
                    "t": e.timestamp,
                    "tool": e.tool,
                    "ok": e.success,
                    "err": e.error or None,
                }
                for e in self.log
            ],
        }

    @property
    def calls_remaining(self) -> int:
        return max(0, self.max_tool_calls - self._call_count)
