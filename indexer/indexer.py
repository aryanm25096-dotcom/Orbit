"""
indexer/indexer.py — Git-Agnostic Filesystem Indexer (Phase 1)
===============================================================
PRD §4.1:
  "Indexing is done with a filesystem walk plus code-search tooling
   (e.g. ripgrep / tree-sitter), not git-specific commands, so a
   directory with no .git works identically to a full repository."

Implementation contract:
  • Zero git calls anywhere in this module — not even `git ls-files`.
  • Uses os.walk / pathlib only for the tree walk.
  • Uses ripgrep (rg) for code search when available; pure-Python re fallback
    otherwise (auto-detected at import time via shutil.which + known locations).
  • Returns a RepoIndex with:
      - file_list      : list[Path] of all non-ignored files
      - language_map   : {extension → [Path]}
      - entry_points   : heuristically detected entry files
      - summary()      : JSON-serialisable dict fed into the Architect prompt

Ignore rules (hard-coded, no git):
  DEFAULT_IGNORE_DIRS  — dirs whose entire subtree is skipped
  DEFAULT_IGNORE_EXTS  — file extensions that produce no useful code signal
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_IGNORE_DIRS: frozenset[str] = frozenset(
    {
        ".git", ".hg", ".svn",                     # VCS metadata
        "__pycache__", ".mypy_cache", ".ruff_cache",
        ".pytest_cache", ".tox", ".nox",
        "node_modules", ".yarn",
        "dist", "build", ".build",
        ".venv", "venv", "env", ".env",
        ".idea", ".vscode",
        "*.egg-info",                               # matched by name startswith
    }
)

DEFAULT_IGNORE_EXTS: frozenset[str] = frozenset(
    {
        ".pyc", ".pyo", ".pyd",
        ".class", ".jar",
        ".o", ".a", ".so", ".dll", ".exe",
        ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
        ".pdf", ".docx", ".xlsx",
        ".zip", ".tar", ".gz", ".bz2",
        ".lock",       # lock files are noise for planning
        ".DS_Store",
    }
)

# Entry-point heuristics: files whose names strongly suggest "starts here"
ENTRY_POINT_NAMES: frozenset[str] = frozenset(
    {
        "main.py", "app.py", "server.py", "manage.py", "wsgi.py", "asgi.py",
        "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts",
        "main.go", "cmd/main.go",
        "Makefile", "Dockerfile",
        "setup.py", "setup.cfg", "pyproject.toml",
        "package.json", "go.mod", "Cargo.toml",
        "scheduler.py",  # Orbit itself
    }
)

# Extension → human-readable language label
EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "javascript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".md": "markdown",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".scss": "css",
}

# Known locations of bundled ripgrep binaries (checked in order)
_BUNDLED_RG_PATHS: list[str] = [
    "/Applications/Visual Studio Code.app/Contents/Resources/app/"
    "node_modules.asar.unpacked/@vscode/ripgrep-universal/bin/darwin-arm64/rg",
    "/Applications/Cursor.app/Contents/Resources/app/node_modules/@vscode/ripgrep/bin/rg",
    "/Applications/ChatGPT.app/Contents/Resources/rg",
]


# ---------------------------------------------------------------------------
# Ripgrep detection (called once at module import)
# ---------------------------------------------------------------------------

def _find_rg() -> str | None:
    """Return the path to a working rg binary, or None."""
    # 1. On PATH (normal install / brew)
    found = shutil.which("rg")
    if found:
        return found
    # 2. Known bundled locations
    for p in _BUNDLED_RG_PATHS:
        if Path(p).is_file():
            return p
    return None


_RG_BIN: str | None = _find_rg()


# ---------------------------------------------------------------------------
# CodeSearchResult
# ---------------------------------------------------------------------------

@dataclass
class CodeSearchResult:
    file: Path
    line_number: int
    line_content: str

    @property
    def content(self) -> str:
        return self.line_content


# ---------------------------------------------------------------------------
# RepoIndex
# ---------------------------------------------------------------------------

@dataclass
class RepoIndex:
    """
    Represents a fully scanned local directory tree — git or plain.

    All state is populated by scan().  Do not read attributes before calling it.
    """

    root: Path
    is_git: bool = field(init=False)
    file_list: list[Path] = field(default_factory=list)
    language_map: dict[str, list[Path]] = field(default_factory=dict)
    entry_points: list[Path] = field(default_factory=list)
    rg_available: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        # Resolve to absolute path; detect git without any git call
        self.root = self.root.resolve()
        self.is_git = (self.root / ".git").is_dir()
        self.rg_available = _RG_BIN is not None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> "RepoIndex":
        """
        Walk the directory tree and populate file_list, language_map,
        entry_points.  Zero git calls.
        """
        self.file_list = []
        self.language_map = {}
        self.entry_points = []

        for path in self._walk():
            self.file_list.append(path)

            ext = path.suffix.lower()
            if ext:
                self.language_map.setdefault(ext, []).append(path)

            if path.name in ENTRY_POINT_NAMES:
                self.entry_points.append(path)

        return self

    def search(self, pattern: str, file_glob: str = "") -> list[CodeSearchResult]:
        """
        Search for *pattern* (regex) across all indexed files.

        Uses rg when available; falls back to a pure-Python re scan.
        file_glob is passed to rg as --glob (e.g. '*.py'); ignored in
        the Python fallback (it already only scans indexed files).
        """
        if self.rg_available:
            return self._rg_search(pattern, file_glob)
        return self._python_search(pattern)

    def summary(self) -> dict:
        """
        Return a JSON-serialisable dict suitable for injection into an
        Architect prompt.  Keeps sizes small — paths relative to root.
        """
        rel = lambda p: str(p.relative_to(self.root))

        lang_counts: dict[str, int] = {}
        for ext, paths in self.language_map.items():
            lang = EXT_TO_LANG.get(ext, ext.lstrip(".") or "other")
            lang_counts[lang] = lang_counts.get(lang, 0) + len(paths)

        return {
            "root": str(self.root),
            "is_git": self.is_git,
            "total_files": len(self.file_list),
            "rg_available": self.rg_available,
            "languages": lang_counts,
            "entry_points": [rel(p) for p in self.entry_points],
            # Top-20 files by extension for the prompt (avoids prompt bloat)
            "file_sample": [rel(p) for p in self.file_list[:20]],
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _should_ignore_dir(self, name: str) -> bool:
        if name in DEFAULT_IGNORE_DIRS:
            return True
        # Handle glob-like patterns stored in the set (e.g. "*.egg-info")
        if name.endswith(".egg-info"):
            return True
        return False

    def _walk(self) -> Iterator[Path]:
        """
        Pure os.walk traversal.  Prunes ignored directories in-place
        (modifying dirs[:] stops os.walk from descending).
        """
        for dirpath, dirs, filenames in os.walk(self.root):
            # Prune ignored directories — modifying `dirs` in-place
            dirs[:] = [
                d for d in sorted(dirs)
                if not self._should_ignore_dir(d)
            ]
            for fname in sorted(filenames):
                path = Path(dirpath) / fname
                ext = path.suffix.lower()
                if ext in DEFAULT_IGNORE_EXTS or fname.startswith("."):
                    continue
                yield path

    def _rg_search(
        self, pattern: str, file_glob: str = ""
    ) -> list[CodeSearchResult]:
        """Call ripgrep subprocess; parse its --line-number output."""
        cmd = [
            _RG_BIN,
            "--line-number",
            "--no-heading",
            "--color=never",
            "--max-count=50",
        ]
        if file_glob:
            cmd += ["--glob", file_glob]
        cmd += [pattern, str(self.root)]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            # Fall through to Python fallback
            return self._python_search(pattern)

        results: list[CodeSearchResult] = []
        for line in proc.stdout.splitlines():
            # Format: /path/to/file:lineno:content
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            try:
                results.append(
                    CodeSearchResult(
                        file=Path(parts[0]),
                        line_number=int(parts[1]),
                        line_content=parts[2],
                    )
                )
            except ValueError:
                continue
        return results

    def _python_search(self, pattern: str) -> list[CodeSearchResult]:
        """Pure-Python regex search across indexed files (rg fallback)."""
        compiled = re.compile(pattern)
        results: list[CodeSearchResult] = []
        for path in self.file_list:
            if len(results) >= 50:
                break
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, PermissionError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if compiled.search(line):
                    results.append(
                        CodeSearchResult(
                            file=path,
                            line_number=lineno,
                            line_content=line,
                        )
                    )
                    if len(results) >= 50:
                        break
        return results
