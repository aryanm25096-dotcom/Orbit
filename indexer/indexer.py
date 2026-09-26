"""
indexer/indexer.py — Git-Agnostic Filesystem Indexer
======================================================
Phase 1 implementation target.  Phase 0 stub only.
"""

from __future__ import annotations

from pathlib import Path


class RepoIndex:
    """Represents a scanned local directory tree — git or plain."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.is_git: bool = (root / ".git").exists()
        self.file_list: list[Path] = []  # populated by scan()
        self.language_map: dict[str, list[Path]] = {}  # ext → [paths]

    def scan(self) -> "RepoIndex":
        """Walk the directory and build the index. Phase 1 implements filtering."""
        raise NotImplementedError("Phase 1")

    def summary(self) -> dict:
        """Return a JSON-serialisable summary suitable for the Architect prompt."""
        raise NotImplementedError("Phase 1")
