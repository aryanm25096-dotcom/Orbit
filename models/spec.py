"""
models/spec.py — Orbit Core Data Models
========================================
Phase 1 will flesh these out fully.  Phase 0 declares the dataclass
shells so the rest of the skeleton can import them without errors.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class WorkerRole(str, Enum):
    DATABASE = "database"
    BACKEND = "backend"
    FRONTEND = "frontend"


@dataclass
class TaskContract:
    """Minimal contract handed to a single worker."""

    task_id: str
    role: WorkerRole
    description: str
    depends_on: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    workspace_path: str | None = None
    result_summary: str | None = None
    # Phase 1+: add input_artifacts, output_artifacts, style_guide_ref


@dataclass
class MasterSpecification:
    """Top-level output of the Master Architect."""

    requirement: str
    architecture_summary: str = ""
    tasks: list[TaskContract] = field(default_factory=list)
    # Phase 1+: dependency_graph, style_guide, contract_schema_version


@dataclass
class FailureContext:
    """Produced by Integrator when a test suite fails; consumed by recovery loop."""

    task_id: str
    error_output: str
    relevant_files: list[str] = field(default_factory=list)
    recovery_attempt: int = 0


@dataclass
class ReviewVerdict:
    """Output of the Global Reviewer."""

    requirement: str
    per_requirement_results: dict[str, bool] = field(default_factory=dict)
    contract_adherence: bool = True
    rework_tasks: list[TaskContract] = field(default_factory=list)
    notes: str = ""

    @property
    def passed(self) -> bool:
        return all(self.per_requirement_results.values()) and self.contract_adherence
