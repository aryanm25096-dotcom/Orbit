"""
tests/test_phase4_workers_scheduler.py — Phase 4 Worker & Scheduler Tests
==========================================================================
Tests the Phase 4 MVP floor:
  1. 3 Worker roles (BackendWorker, FrontendWorker, DatabaseWorker)
  2. Workspace isolation and ToolGateway guardrail enforcement (PathEscapeError, ToolBudgetExhausted)
  3. ReadyQueueScheduler plain ready-queue release (wave execution, dependency artifact checking)
  4. Failure handling when dependencies fail
  5. Artifact integration
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gateway.gateway import ToolGateway
from models.spec import DependencyEdge, MasterSpecification, TaskContract, TaskStatus, WorkerRole
from scheduler import ReadyQueueScheduler, integrate_artifacts
from workers.base_worker import (
    BackendWorker,
    BaseWorker,
    DatabaseWorker,
    FrontendWorker,
    run_backend_worker,
    run_database_worker,
    run_frontend_worker,
    run_worker,
    run_worker_async,
    seed_workspace,
)


# ---------------------------------------------------------------------------
# Canned LLM Mock Callers
# ---------------------------------------------------------------------------

def make_canned_caller(files: list[dict[str, str]], summary: str = "Canned implementation"):
    """Return a mock Ollama caller returning specific files in JSON."""
    def _caller(prompt: str, system: str, model: str, base_url: str, **kwargs) -> tuple[str, int, int]:
        data = {
            "summary": summary,
            "files": files,
        }
        return json.dumps(data), 50, 100
    return _caller


def evil_path_caller(prompt: str, system: str, model: str, base_url: str, **kwargs) -> tuple[str, int, int]:
    """Mock caller attempting to write outside the workspace."""
    data = {
        "summary": "Evil escape",
        "files": [{"path": "/tmp/malicious.txt", "content": "pwned"}],
    }
    return json.dumps(data), 20, 20


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def ws(tmp_path: Path) -> Path:
    p = tmp_path / "worker_ws"
    p.mkdir(parents=True, exist_ok=True)
    return p


@pytest.fixture
def target_dir(tmp_path: Path) -> Path:
    p = tmp_path / "target_repo"
    p.mkdir(parents=True, exist_ok=True)
    (p / "src").mkdir(parents=True, exist_ok=True)
    (p / "src" / "math_mod.py").write_text("def add(a, b): return a + b\n")
    return p


# ---------------------------------------------------------------------------
# Worker Role Tests
# ---------------------------------------------------------------------------

class TestWorkerRoles:

    def test_worker_roles_instantiation(self):
        bw = BackendWorker()
        fw = FrontendWorker()
        dw = DatabaseWorker()
        assert bw.role == "backend"
        assert fw.role == "frontend"
        assert dw.role == "database"

    def test_backend_worker_execution(self, ws: Path):
        contract = TaskContract(
            task_id="task-be-1",
            role=WorkerRole.BACKEND,
            objective="Add multiply function to math_mod.py",
            allowed_workspace=str(ws),
            expected_artifact="src/math_mod.py",
        )
        canned_files = [{
            "path": "src/math_mod.py",
            "content": "def add(a, b): return a + b\ndef multiply(a, b): return a * b\n",
        }]
        worker = BackendWorker(
            ollama_caller=make_canned_caller(canned_files, "Added multiply"),
        )
        updated = worker.execute(contract)

        assert updated.status == TaskStatus.DONE
        assert "Added multiply" in updated.result_summary
        assert (ws / "src" / "math_mod.py").exists()
        assert "def multiply" in (ws / "src" / "math_mod.py").read_text()
        assert "src/math_mod.py" in updated.evidence["files_written"]

    def test_frontend_worker_execution(self, ws: Path):
        contract = TaskContract(
            task_id="task-fe-1",
            role=WorkerRole.FRONTEND,
            objective="Create CLI display for calculator",
            allowed_workspace=str(ws),
            expected_artifact="cli.py",
        )
        canned_files = [{
            "path": "cli.py",
            "content": "def run_cli(): print('Calculator CLI')\n",
        }]
        worker = FrontendWorker(
            ollama_caller=make_canned_caller(canned_files, "Created CLI"),
        )
        updated = worker.execute(contract)

        assert updated.status == TaskStatus.DONE
        assert (ws / "cli.py").exists()
        assert updated.evidence["files_written"] == ["cli.py"]

    def test_database_worker_execution(self, ws: Path):
        contract = TaskContract(
            task_id="task-db-1",
            role=WorkerRole.DATABASE,
            objective="Create SQLite schema for calculations",
            allowed_workspace=str(ws),
            expected_artifact="schema.sql",
        )
        canned_files = [{
            "path": "schema.sql",
            "content": "CREATE TABLE history (id INTEGER PRIMARY KEY, expr TEXT);\n",
        }]
        worker = DatabaseWorker(
            ollama_caller=make_canned_caller(canned_files, "Created schema"),
        )
        updated = worker.execute(contract)

        assert updated.status == TaskStatus.DONE
        assert (ws / "schema.sql").exists()

    def test_worker_path_escape_blocked(self, ws: Path):
        contract = TaskContract(
            task_id="task-evil-1",
            role=WorkerRole.BACKEND,
            objective="Attempt path escape",
            allowed_workspace=str(ws),
            expected_artifact="evil.txt",
        )
        worker = BackendWorker(ollama_caller=evil_path_caller)
        updated = worker.execute(contract)

        assert updated.status == TaskStatus.FAILED
        assert "PathEscapeError" in updated.result_summary
        assert updated.evidence["error"] == "PathEscapeError"

    def test_worker_budget_exhaustion(self, ws: Path):
        contract = TaskContract(
            task_id="task-budget-1",
            role=WorkerRole.BACKEND,
            objective="Write multiple files exceeding tiny budget",
            allowed_workspace=str(ws),
            expected_artifact="file1.txt",
        )
        canned_files = [
            {"path": "file1.txt", "content": "1"},
            {"path": "file2.txt", "content": "2"},
            {"path": "file3.txt", "content": "3"},
        ]
        # Gateway with max_tool_calls = 1
        gw = ToolGateway(workspace_root=ws, max_tool_calls=1)
        worker = BackendWorker(
            gateway=gw,
            ollama_caller=make_canned_caller(canned_files),
        )
        updated = worker.execute(contract)

        assert updated.status == TaskStatus.FAILED
        assert "ToolBudgetExhausted" in updated.result_summary

    def test_functional_run_worker(self, ws: Path):
        contract = TaskContract(
            task_id="task-fn-1",
            role=WorkerRole.BACKEND,
            objective="Run via functional interface",
            allowed_workspace=str(ws),
            expected_artifact="fn.py",
        )
        canned = [{"path": "fn.py", "content": "x = 42\n"}]
        res = run_backend_worker(
            contract=contract,
            ollama_caller=make_canned_caller(canned, "Ran functional"),
        )
        assert res.status == TaskStatus.DONE
        assert (ws / "fn.py").read_text() == "x = 42\n"

    def test_seed_workspace_copies_baseline(self, ws: Path, target_dir: Path):
        seed_workspace(workspace_root=ws, target_dir=target_dir)
        assert (ws / "src" / "math_mod.py").exists()
        assert (ws / "src" / "math_mod.py").read_text() == (target_dir / "src" / "math_mod.py").read_text()


# ---------------------------------------------------------------------------
# ReadyQueueScheduler Tests
# ---------------------------------------------------------------------------

class TestReadyQueueScheduler:

    @pytest.mark.asyncio
    async def test_independent_tasks_run_in_parallel(self, tmp_path: Path):
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"

        t1 = TaskContract(
            task_id="task-1",
            role=WorkerRole.BACKEND,
            objective="Independent task 1",
            allowed_workspace=str(ws1),
            expected_artifact="t1.txt",
        )
        t2 = TaskContract(
            task_id="task-2",
            role=WorkerRole.FRONTEND,
            objective="Independent task 2",
            allowed_workspace=str(ws2),
            expected_artifact="t2.txt",
        )
        spec = MasterSpecification(
            requirement="Run independent tasks",
            tasks=[t1, t2],
            dependency_graph=[],
        )

        canned = [{"path": "out.txt", "content": "ok"}]
        sched = ReadyQueueScheduler(
            spec=spec,
            ollama_caller=make_canned_caller(canned),
        )
        results = await sched.run()

        assert len(results) == 2
        assert results[0].status == TaskStatus.DONE
        assert results[1].status == TaskStatus.DONE

    @pytest.mark.asyncio
    async def test_dependent_task_runs_after_upstream_artifact(self, tmp_path: Path):
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"

        t1 = TaskContract(
            task_id="task-up",
            role=WorkerRole.BACKEND,
            objective="Upstream task",
            allowed_workspace=str(ws1),
            expected_artifact="upstream.txt",
        )
        t2 = TaskContract(
            task_id="task-down",
            role=WorkerRole.FRONTEND,
            objective="Downstream task depending on task-up",
            depends_on=["task-up"],
            allowed_workspace=str(ws2),
            expected_artifact="downstream.txt",
        )
        spec = MasterSpecification(
            requirement="Chained tasks",
            tasks=[t1, t2],
            dependency_graph=[
                DependencyEdge(from_task="task-up", to_task="task-down"),
            ],
        )

        def mock_caller(prompt: str, system: str, **kwargs):
            if "Upstream task" in prompt:
                return json.dumps({"files": [{"path": "upstream.txt", "content": "artifact data"}]}), 10, 10
            else:
                return json.dumps({"files": [{"path": "downstream.txt", "content": "received data"}]}), 10, 10

        sched = ReadyQueueScheduler(
            spec=spec,
            ollama_caller=mock_caller,
        )
        results = await sched.run()

        assert results[0].status == TaskStatus.DONE
        assert results[1].status == TaskStatus.DONE
        assert (ws1 / "upstream.txt").exists()
        assert (ws2 / "downstream.txt").exists()

    @pytest.mark.asyncio
    async def test_failure_in_upstream_blocks_downstream(self, tmp_path: Path):
        ws1 = tmp_path / "ws1"
        ws2 = tmp_path / "ws2"

        t1 = TaskContract(
            task_id="task-bad",
            role=WorkerRole.BACKEND,
            objective="Will fail path escape",
            allowed_workspace=str(ws1),
            expected_artifact="bad.txt",
        )
        t2 = TaskContract(
            task_id="task-blocked",
            role=WorkerRole.FRONTEND,
            objective="Should never run",
            depends_on=["task-bad"],
            allowed_workspace=str(ws2),
            expected_artifact="blocked.txt",
        )
        spec = MasterSpecification(
            requirement="Failure propagation",
            tasks=[t1, t2],
            dependency_graph=[
                DependencyEdge(from_task="task-bad", to_task="task-blocked"),
            ],
        )

        sched = ReadyQueueScheduler(
            spec=spec,
            ollama_caller=evil_path_caller,
        )
        results = await sched.run()

        assert results[0].status == TaskStatus.FAILED
        assert results[1].status == TaskStatus.PENDING  # blocked, never executed


# ---------------------------------------------------------------------------
# Integration Tests
# ---------------------------------------------------------------------------

class TestIntegration:

    def test_integrate_artifacts_copies_to_target(self, tmp_path: Path):
        ws = tmp_path / "worker_ws"
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "src").mkdir(parents=True, exist_ok=True)
        (ws / "src" / "new_feature.py").write_text("# new feature code\n")

        contract = TaskContract(
            task_id="task-done",
            role=WorkerRole.BACKEND,
            objective="Feature",
            allowed_workspace=str(ws),
            expected_artifact="src/new_feature.py",
            status=TaskStatus.DONE,
            evidence={"files_written": ["src/new_feature.py"]},
        )
        spec = MasterSpecification(
            requirement="Feature spec",
            tasks=[contract],
        )

        target = tmp_path / "final_target"
        target.mkdir(parents=True, exist_ok=True)

        applied = integrate_artifacts(spec, target)

        assert applied == ["src/new_feature.py"]
        assert (target / "src" / "new_feature.py").exists()
        assert (target / "src" / "new_feature.py").read_text() == "# new feature code\n"
