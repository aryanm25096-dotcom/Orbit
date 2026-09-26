"""
workers/base_worker.py — Abstract Worker Base Class
=====================================================
Phase 4 implementation target.  Phase 0 stub only.

Each concrete worker (Database, Backend, Frontend) extends BaseWorker.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from models.spec import FailureContext, TaskContract


class BaseWorker(ABC):
    """Abstract base for all Orbit worker roles."""

    role: str = "base"

    def __init__(
        self,
        model: str,
        gateway,  # ToolGateway — forward-ref to avoid circular import
        base_url: str = "http://localhost:11434",
    ) -> None:
        self.model = model
        self.gateway = gateway
        self.base_url = base_url

    @abstractmethod
    def execute(self, contract: TaskContract) -> TaskContract:
        """Run the task described in *contract* and return an updated contract."""

    def recover(self, contract: TaskContract, ctx: FailureContext) -> TaskContract:
        """Targeted recovery pass. Default: re-run execute. Override for smarter logic."""
        return self.execute(contract)


class DatabaseWorker(BaseWorker):
    role = "database"

    def execute(self, contract: TaskContract) -> TaskContract:
        raise NotImplementedError("Phase 4")


class BackendWorker(BaseWorker):
    role = "backend"

    def execute(self, contract: TaskContract) -> TaskContract:
        raise NotImplementedError("Phase 4")


class FrontendWorker(BaseWorker):
    role = "frontend"

    def execute(self, contract: TaskContract) -> TaskContract:
        raise NotImplementedError("Phase 4")
