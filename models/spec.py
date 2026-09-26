"""
models/spec.py — Orbit Core Data Models (Phase 1)
==================================================
All contracts are Pydantic v2 models so that:
  - every downstream component can call .model_dump() / .model_validate()
  - schema can be embedded in LLM prompts via .model_json_schema()
  - validation is strict and caught early, not silently wrong

PRD references
  §3.1  — TaskContract fields: objective, inputs, dependencies,
           allowed_workspace, expected_artifact, acceptance_criteria
  §4.2  — MasterSpecification = spec + dependency graph + TaskContracts
  §4.5  — FailureContext structure (command, error, stack trace, changed files)
  §4.6  — ReviewVerdict: per-requirement PASS/FAIL table + contract adherence
  §4.9  — style_guide passed into every TaskContract
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, computed_field, model_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class TaskStatus(str, Enum):
    """Lifecycle state of a single TaskContract."""
    PENDING  = "pending"   # created, not yet ready
    READY    = "ready"     # all dependencies satisfied, can start
    RUNNING  = "running"   # worker actively executing
    DONE     = "done"      # worker finished, artifact produced
    FAILED   = "failed"    # worker finished, no passing artifact


class WorkerRole(str, Enum):
    """The three supported worker roles (Phase 4 scope)."""
    DATABASE = "database"
    BACKEND  = "backend"
    FRONTEND = "frontend"


# ---------------------------------------------------------------------------
# TaskContract  (PRD §3.1 / §4.2)
# ---------------------------------------------------------------------------

class TaskContract(BaseModel):
    """
    The atomic unit of work handed to one worker.

    Fields derived directly from PRD §3.1:
      objective            — one-sentence statement of what must be produced
      inputs               — file paths / artifact names the worker may read
      depends_on           — task_ids that must reach DONE before this starts
      allowed_workspace    — the isolated directory this worker may write to
      expected_artifact    — what the worker must produce (path or description)
      acceptance_criteria  — list of verifiable conditions for DONE status
      style_guide          — shared style/convention text (PRD §4.9)

    Runtime fields:
      task_id, role, status, result_summary, evidence
    """

    # Identity
    task_id: str = Field(
        ...,
        description="Unique identifier, e.g. 'task-db-001'.",
    )
    role: WorkerRole = Field(
        ...,
        description="Which worker role executes this task.",
    )

    # Core contract (PRD §3.1 verbatim)
    objective: str = Field(
        ...,
        description="One-sentence statement of what must be produced.",
    )
    inputs: list[str] = Field(
        default_factory=list,
        description="File paths or artifact names the worker is allowed to read.",
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="task_ids that must be DONE before this task may start.",
    )
    allowed_workspace: str = Field(
        ...,
        description=(
            "Absolute path to the isolated folder this worker may write to. "
            "No writes outside this path are permitted (enforced by ToolGateway)."
        ),
    )
    expected_artifact: str = Field(
        ...,
        description=(
            "The primary output: a file path, directory, or named deliverable "
            "that the Integrator will consume."
        ),
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description=(
            "Verifiable conditions that must all be true for status → DONE. "
            "Checked by the Integrator's test-runner and the Global Reviewer."
        ),
    )
    style_guide: str = Field(
        default="",
        description=(
            "Shared naming / formatting / project-idiom guide (PRD §4.9). "
            "Passed verbatim into the worker's system prompt."
        ),
    )

    # Runtime / result fields (populated by workers and the Integrator)
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        description="Current lifecycle state.",
    )
    result_summary: str = Field(
        default="",
        description="Human-readable one-liner produced by the worker on completion.",
    )
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Structured evidence: diff, token counts, test output, tool-call log. "
            "Keys are free-form but the Reviewer expects 'diff' and 'test_output'."
        ),
    )

    @model_validator(mode="after")
    def _workspace_required_for_non_pending(self) -> "TaskContract":
        """allowed_workspace must be a non-empty string."""
        if not self.allowed_workspace.strip():
            raise ValueError("allowed_workspace must not be empty.")
        return self

    model_config = {"use_enum_values": True}


# ---------------------------------------------------------------------------
# DependencyEdge  (PRD §4.2 — the explicit dependency graph)
# ---------------------------------------------------------------------------

class DependencyEdge(BaseModel):
    """
    One directed edge in the task dependency graph.
    from_task must be DONE before to_task may start.
    """
    from_task: str = Field(..., description="Upstream task_id (must finish first).")
    to_task: str   = Field(..., description="Downstream task_id (may start after).")
    artifact: str  = Field(
        default="",
        description=(
            "Optional: name of the specific artifact that signals readiness. "
            "Empty string means 'any artifact from from_task'."
        ),
    )


# ---------------------------------------------------------------------------
# MasterSpecification  (PRD §4.2)
# ---------------------------------------------------------------------------

class MasterSpecification(BaseModel):
    """
    Top-level output of the Master Architect.

    Contains:
      - the original requirement (verbatim)
      - an architecture summary (prose)
      - the flat list of TaskContracts
      - the explicit dependency graph (list of DependencyEdges)
      - the shared style guide (forwarded into every TaskContract)

    PRD §4.2: "One task becomes a MasterSpecification plus a dependency graph
    plus individual TaskContracts."
    """

    requirement: str = Field(
        ...,
        description="The verbatim task / issue / requirement handed to Orbit.",
    )
    architecture_summary: str = Field(
        default="",
        description=(
            "Prose description of the planned changes: which modules are touched, "
            "what the overall approach is, why this decomposition was chosen."
        ),
    )
    tasks: list[TaskContract] = Field(
        default_factory=list,
        description="All TaskContracts produced for this requirement.",
    )
    dependency_graph: list[DependencyEdge] = Field(
        default_factory=list,
        description=(
            "Explicit DAG edges. The scheduler uses this to build the ready-queue "
            "(PRD §4.2 default mode) or a topological sort (fallback)."
        ),
    )
    style_guide: str = Field(
        default="",
        description=(
            "Shared style/convention guide forwarded into every TaskContract "
            "and checked by the Global Reviewer (PRD §4.9)."
        ),
    )

    # Computed helpers -------------------------------------------------

    def ready_tasks(self, done_ids: set[str]) -> list[TaskContract]:
        """
        Return tasks that:
          - are not already finished (task_id not in done_ids), AND
          - have status PENDING or READY, AND
          - have every declared dependency present in done_ids.

        This is the core of the readiness-based scheduler (PRD §4.2).
        The caller does not have to mutate task.status for a task to be
        considered done — passing its id in done_ids is sufficient.
        """
        result = []
        for task in self.tasks:
            # Skip tasks that are already accounted for as done
            if task.task_id in done_ids:
                continue
            # Skip tasks that are running or have already failed/succeeded
            if task.status not in (TaskStatus.PENDING, TaskStatus.READY):
                continue
            # All declared dependencies must be in done_ids
            if all(dep in done_ids for dep in task.depends_on):
                result.append(task)
        return result

    def task_by_id(self, task_id: str) -> TaskContract | None:
        """Lookup helper."""
        for t in self.tasks:
            if t.task_id == task_id:
                return t
        return None

    @model_validator(mode="before")
    @classmethod
    def _clean_dependency_graph(cls, data: Any) -> Any:
        if isinstance(data, dict):
            graph = data.get("dependency_graph", [])
            if isinstance(graph, list):
                # Filter out placeholder edges where an endpoint is empty string
                data["dependency_graph"] = [
                    edge for edge in graph
                    if not (isinstance(edge, dict) and (not edge.get("from_task") or not edge.get("to_task")))
                ]
        return data

    @model_validator(mode="after")
    def _no_dangling_edges(self) -> "MasterSpecification":
        """Every edge endpoint must reference a real task_id."""
        ids = {t.task_id for t in self.tasks}
        for edge in self.dependency_graph:
            if edge.from_task not in ids:
                raise ValueError(
                    f"DependencyEdge.from_task '{edge.from_task}' "
                    f"does not match any TaskContract.task_id."
                )
            if edge.to_task not in ids:
                raise ValueError(
                    f"DependencyEdge.to_task '{edge.to_task}' "
                    f"does not match any TaskContract.task_id."
                )
        return self


# ---------------------------------------------------------------------------
# FailureContext  (PRD §4.5)
# ---------------------------------------------------------------------------

class FailureContext(BaseModel):
    """
    Structured description of a test/verification failure.

    The Integrator creates one of these when a test run fails.
    The Master Architect consumes it to produce exactly one targeted
    rework TaskContract — never a full regeneration (PRD §4.5).

    Fields match PRD §4.5: "command, error, stack trace, changed files".
    """

    task_id: str = Field(
        ...,
        description="The TaskContract that produced the failing output.",
    )
    command: str = Field(
        default="",
        description="The test command that was run (e.g. 'pytest tests/').",
    )
    error_output: str = Field(
        ...,
        description="Raw stderr / test-runner output that signals failure.",
    )
    stack_trace: str = Field(
        default="",
        description="Extracted stack trace, if available.",
    )
    changed_files: list[str] = Field(
        default_factory=list,
        description="Paths of files modified by this worker's workspace.",
    )
    recovery_attempt: int = Field(
        default=0,
        ge=0,
        description="How many rework loops have already been attempted.",
    )

    # Tie-breaker fields (PRD §4.5 ordered tie-breaker)
    passing_test_count: int = Field(
        default=0,
        ge=0,
        description="Number of tests passing in the best partial result so far.",
    )
    broken_file_count: int = Field(
        default=0,
        ge=0,
        description="Number of files left in a broken/uncompiling state.",
    )


# ---------------------------------------------------------------------------
# ReviewVerdict  (PRD §4.6)
# ---------------------------------------------------------------------------

class ReviewVerdict(BaseModel):
    """
    Output of the Global Reviewer (Phase 6).

    PRD §4.6: "per-requirement PASS/FAIL table … also checks each worker's
    output against its own task contract … every FAIL becomes exactly one
    rework task; the reviewer never triggers a full regeneration."
    """

    requirement: str = Field(
        ...,
        description="The verbatim requirement this verdict evaluates.",
    )
    per_requirement_results: dict[str, bool] = Field(
        default_factory=dict,
        description=(
            "Map of acceptance-criterion text → True/False. "
            "False means the criterion is not yet satisfied."
        ),
    )
    contract_adherence: bool = Field(
        default=True,
        description=(
            "True if every worker's output conforms to its TaskContract "
            "(style, naming, artifacts).  PRD §4.6 / §4.9."
        ),
    )
    rework_tasks: list[TaskContract] = Field(
        default_factory=list,
        description=(
            "Exactly one rework TaskContract per failing criterion. "
            "Never empty if passed==False; never more than one per criterion."
        ),
    )
    notes: str = Field(
        default="",
        description="Reviewer prose: gaps found, evidence consulted.",
    )

    @computed_field
    @property
    def passed(self) -> bool:
        """True only when every criterion passes AND contract adherence holds."""
        return (
            bool(self.per_requirement_results)
            and all(self.per_requirement_results.values())
            and self.contract_adherence
        )
