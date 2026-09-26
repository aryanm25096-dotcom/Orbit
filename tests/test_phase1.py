"""
tests/test_phase1.py — Phase 1 Unit Tests
==========================================
Covers:
  1. Pydantic models (TaskContract, MasterSpecification, DependencyEdge,
     FailureContext, ReviewVerdict) — field validation and helper logic.
  2. RepoIndex.scan() against BOTH test targets:
       - test_targets/git_repo/   (has .git — is_git=True)
       - test_targets/plain_dir/  (no .git  — is_git=False)
  3. RepoIndex.search() — code-search via rg or Python fallback.

Run with:
    python3 -m pytest tests/test_phase1.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make sure the project root is importable
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from indexer.indexer import RepoIndex
from models.spec import (
    DependencyEdge,
    FailureContext,
    MasterSpecification,
    ReviewVerdict,
    TaskContract,
    TaskStatus,
    WorkerRole,
)

# ---------------------------------------------------------------------------
# Paths to the two test targets
# ---------------------------------------------------------------------------
GIT_TARGET   = ROOT / "test_targets" / "git_repo"
PLAIN_TARGET = ROOT / "test_targets" / "plain_dir"


# ===========================================================================
# 1. Pydantic model tests
# ===========================================================================

class TestTaskContract:
    """TaskContract field validation and defaults."""

    def _minimal(self, **overrides) -> dict:
        base = dict(
            task_id="task-be-001",
            role=WorkerRole.BACKEND,
            objective="Add a multiply endpoint to the API.",
            allowed_workspace="/tmp/orbit/workspace/task-be-001",
            expected_artifact="src/api.py",
        )
        base.update(overrides)
        return base

    def test_create_minimal(self):
        tc = TaskContract(**self._minimal())
        assert tc.task_id == "task-be-001"
        assert tc.role == WorkerRole.BACKEND
        assert tc.status == TaskStatus.PENDING
        assert tc.depends_on == []
        assert tc.inputs == []
        assert tc.acceptance_criteria == []
        assert tc.evidence == {}

    def test_empty_workspace_raises(self):
        with pytest.raises(Exception):
            TaskContract(**self._minimal(allowed_workspace=""))

    def test_full_contract_roundtrip(self):
        """model_dump / model_validate round-trip preserves all fields."""
        tc = TaskContract(
            task_id="task-db-002",
            role=WorkerRole.DATABASE,
            objective="Create users table migration.",
            inputs=["db/schema.sql"],
            depends_on=[],
            allowed_workspace="/tmp/orbit/workspace/task-db-002",
            expected_artifact="db/migrations/0001_users.sql",
            acceptance_criteria=["Migration runs without error", "Table exists after apply"],
            style_guide="Use snake_case for table names.",
            status=TaskStatus.RUNNING,
            result_summary="",
            evidence={},
        )
        dumped = tc.model_dump()
        restored = TaskContract.model_validate(dumped)
        assert restored.task_id == tc.task_id
        assert restored.acceptance_criteria == tc.acceptance_criteria

    def test_json_schema_has_required_fields(self):
        schema = TaskContract.model_json_schema()
        required = set(schema.get("required", []))
        # PRD §3.1 mandates these fields on every contract
        for field in ("task_id", "role", "objective", "allowed_workspace", "expected_artifact"):
            assert field in required, f"'{field}' missing from TaskContract JSON schema"


class TestMasterSpecification:
    """MasterSpecification — task list, dependency graph, ready-queue logic."""

    def _build_spec(self) -> MasterSpecification:
        ws = "/tmp/orbit/workspace"
        tasks = [
            TaskContract(
                task_id="task-db-001",
                role=WorkerRole.DATABASE,
                objective="Create schema.",
                allowed_workspace=f"{ws}/task-db-001",
                expected_artifact="db/schema.sql",
            ),
            TaskContract(
                task_id="task-be-001",
                role=WorkerRole.BACKEND,
                objective="Add API endpoint.",
                depends_on=["task-db-001"],
                allowed_workspace=f"{ws}/task-be-001",
                expected_artifact="src/api.py",
            ),
            TaskContract(
                task_id="task-fe-001",
                role=WorkerRole.FRONTEND,
                objective="Add UI form.",
                depends_on=["task-be-001"],
                allowed_workspace=f"{ws}/task-fe-001",
                expected_artifact="src/App.tsx",
            ),
        ]
        edges = [
            DependencyEdge(from_task="task-db-001", to_task="task-be-001"),
            DependencyEdge(from_task="task-be-001", to_task="task-fe-001"),
        ]
        return MasterSpecification(
            requirement="Add a user-registration feature.",
            architecture_summary="DB → Backend → Frontend chain.",
            tasks=tasks,
            dependency_graph=edges,
        )

    def test_ready_tasks_empty_done(self):
        spec = self._build_spec()
        ready = spec.ready_tasks(done_ids=set())
        # Only task-db-001 has no dependencies
        assert len(ready) == 1
        assert ready[0].task_id == "task-db-001"

    def test_ready_tasks_after_db_done(self):
        spec = self._build_spec()
        ready = spec.ready_tasks(done_ids={"task-db-001"})
        assert len(ready) == 1
        assert ready[0].task_id == "task-be-001"

    def test_ready_tasks_all_done(self):
        spec = self._build_spec()
        ready = spec.ready_tasks(done_ids={"task-db-001", "task-be-001", "task-fe-001"})
        assert ready == []

    def test_dangling_edge_raises(self):
        ws = "/tmp/orbit/workspace/t1"
        with pytest.raises(Exception):
            MasterSpecification(
                requirement="test",
                tasks=[
                    TaskContract(
                        task_id="task-a",
                        role=WorkerRole.BACKEND,
                        objective="x",
                        allowed_workspace=ws,
                        expected_artifact="x.py",
                    )
                ],
                dependency_graph=[
                    DependencyEdge(from_task="task-a", to_task="DOES_NOT_EXIST")
                ],
            )

    def test_task_by_id(self):
        spec = self._build_spec()
        assert spec.task_by_id("task-be-001") is not None
        assert spec.task_by_id("no-such-task") is None

    def test_json_schema_shape(self):
        schema = MasterSpecification.model_json_schema()
        props = schema.get("properties", {})
        for key in ("requirement", "tasks", "dependency_graph", "style_guide"):
            assert key in props, f"'{key}' missing from MasterSpecification schema"


class TestReviewVerdict:
    """ReviewVerdict.passed logic."""

    def test_passed_when_all_true(self):
        vd = ReviewVerdict(
            requirement="r",
            per_requirement_results={"criterion A": True, "criterion B": True},
            contract_adherence=True,
        )
        assert vd.passed is True

    def test_fails_when_any_false(self):
        vd = ReviewVerdict(
            requirement="r",
            per_requirement_results={"criterion A": True, "criterion B": False},
            contract_adherence=True,
        )
        assert vd.passed is False

    def test_fails_when_contract_broken(self):
        vd = ReviewVerdict(
            requirement="r",
            per_requirement_results={"A": True},
            contract_adherence=False,
        )
        assert vd.passed is False

    def test_fails_when_empty_criteria(self):
        """An empty per_requirement_results is not 'passed'."""
        vd = ReviewVerdict(requirement="r", per_requirement_results={})
        assert vd.passed is False


# ===========================================================================
# 2. RepoIndex tests — both test targets
# ===========================================================================

class TestRepoIndexGitRepo:
    """RepoIndex against test_targets/git_repo (has .git directory)."""

    @pytest.fixture(scope="class")
    def index(self):
        return RepoIndex(root=GIT_TARGET).scan()

    def test_is_git_true(self, index):
        assert index.is_git is True

    def test_files_found(self, index):
        assert len(index.file_list) >= 2, "Expected at least src + test files"

    def test_python_files_in_language_map(self, index):
        assert ".py" in index.language_map
        assert len(index.language_map[".py"]) >= 1

    def test_git_dir_not_indexed(self, index):
        """Sanity: no file under .git/ should appear in the index."""
        for f in index.file_list:
            assert ".git" not in f.parts, f"Git internals leaked into index: {f}"

    def test_summary_keys(self, index):
        s = index.summary()
        for key in ("root", "is_git", "total_files", "languages", "entry_points"):
            assert key in s

    def test_summary_is_git_true(self, index):
        assert index.summary()["is_git"] is True

    def test_summary_has_python(self, index):
        assert "python" in index.summary()["languages"]


class TestRepoIndexPlainDir:
    """RepoIndex against test_targets/plain_dir (NO .git — offline/no-git path)."""

    @pytest.fixture(scope="class")
    def index(self):
        return RepoIndex(root=PLAIN_TARGET).scan()

    def test_is_git_false(self, index):
        assert index.is_git is False

    def test_files_found(self, index):
        assert len(index.file_list) >= 2

    def test_python_files_in_language_map(self, index):
        assert ".py" in index.language_map

    def test_no_git_dir_in_plain_dir(self, index):
        """The plain_dir must not accidentally have a .git dir."""
        assert not (PLAIN_TARGET / ".git").exists()

    def test_summary_is_git_false(self, index):
        assert index.summary()["is_git"] is False

    def test_summary_file_count_matches_list(self, index):
        s = index.summary()
        assert s["total_files"] == len(index.file_list)


# ===========================================================================
# 3. Code search — search() on both targets
# ===========================================================================

class TestCodeSearch:
    """search() — ripgrep or Python fallback, on both targets."""

    @pytest.fixture(scope="class")
    def git_index(self):
        return RepoIndex(root=GIT_TARGET).scan()

    @pytest.fixture(scope="class")
    def plain_index(self):
        return RepoIndex(root=PLAIN_TARGET).scan()

    def test_search_finds_function_in_git_target(self, git_index):
        results = git_index.search(r"def add")
        assert len(results) >= 1, "Expected to find 'def add' in math_utils.py"
        assert any("math_utils" in str(r.file) for r in results)

    def test_search_finds_function_in_plain_dir(self, plain_index):
        results = plain_index.search(r"def greet")
        assert len(results) >= 1, "Expected to find 'def greet' in greeter.py"
        assert any("greeter" in str(r.file) for r in results)

    def test_search_no_match_returns_empty(self, git_index):
        results = git_index.search(r"ABSOLUTELY_UNIQUE_NONEXISTENT_SYMBOL_XYZ")
        assert results == []

    def test_search_result_has_line_number(self, plain_index):
        results = plain_index.search(r"def farewell")
        assert len(results) >= 1
        assert results[0].line_number >= 1

    def test_search_result_has_content(self, plain_index):
        results = plain_index.search(r"def farewell")
        assert "farewell" in results[0].line_content
