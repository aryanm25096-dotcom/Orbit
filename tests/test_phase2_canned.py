"""
tests/test_phase2_canned.py — Phase 2 Canned-Input Unit Tests
==============================================================
PRD requirement: "Write 2-3 canned-input unit tests before adding any
orchestration around this call."

These tests exercise the Architect's parsing, validation, retry, and
error logic WITHOUT any live Ollama call.  We monkey-patch _call_ollama
so the suite is fully offline and deterministic.

Test groups
-----------
A. TestExtractJson       — _extract_json() helper handles bare, fenced,
                           and brace-extraction cases.
B. TestArchitectCanned   — plan() with mocked _call_ollama responses:
     1. valid JSON on first attempt  → returns MasterSpecification
     2. bad JSON on attempt 1, valid on attempt 2  → returns spec
     3. bad JSON on both attempts  → raises ArchitectError
     4. valid JSON but bad Pydantic (dangling edge)  → attempt 2 → ArchitectError
     5. workspace path normalisation (_fix_workspaces)
C. TestBuildPrompt       — prompt builder injects requirement, repo map,
                           workspace, style guide, and error text correctly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from architect.architect import (
    ArchitectError,
    MasterArchitect,
    _build_prompt,
    _extract_json,
)
from models.spec import MasterSpecification, TaskStatus, WorkerRole

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WS_ROOT = "/tmp/orbit/runs/run-001"

VALID_SPEC_DICT: dict = {
    "requirement": "Add a multiply function to math_utils.",
    "architecture_summary": "Extend src/math_utils.py with multiply(); update tests.",
    "style_guide": "Use snake_case; type-annotate all functions.",
    "tasks": [
        {
            "task_id": "task-be-001",
            "role": "backend",
            "objective": "Add multiply(a, b) to src/math_utils.py.",
            "inputs": ["src/math_utils.py"],
            "depends_on": [],
            "allowed_workspace": f"{WS_ROOT}/task-be-001",
            "expected_artifact": "src/math_utils.py",
            "acceptance_criteria": [
                "multiply(3, 4) == 12",
                "All existing tests still pass",
            ],
        }
    ],
    "dependency_graph": [],
}

REPO_SUMMARY: dict = {
    "root": "/fake/repo",
    "is_git": False,
    "total_files": 3,
    "rg_available": False,
    "languages": {"python": 2, "markdown": 1},
    "entry_points": [],
    "file_sample": ["src/math_utils.py", "tests/test_math_utils.py"],
}


def _make_ollama_response(data: dict | str, tok_in: int = 100, tok_out: int = 200):
    """Return a mock _call_ollama return value."""
    text = json.dumps(data) if isinstance(data, dict) else data
    return (text, tok_in, tok_out)


def _architect() -> MasterArchitect:
    return MasterArchitect(model="deepseek-coder-v2:latest", base_url="http://localhost:11434")


# ===========================================================================
# A. _extract_json helper
# ===========================================================================

class TestExtractJson:
    def test_bare_json_returned_as_is(self):
        raw = '{"requirement": "x", "tasks": []}'
        assert _extract_json(raw).startswith("{")

    def test_fenced_json_block_extracted(self):
        raw = 'Here you go:\n```json\n{"ok": true}\n```'
        result = _extract_json(raw)
        assert result.strip() == '{"ok": true}'

    def test_fenced_no_lang_extracted(self):
        raw = "```\n{\"a\": 1}\n```"
        result = _extract_json(raw)
        assert '"a"' in result

    def test_brace_extraction_fallback(self):
        raw = 'Some prose {"key": "val"} more prose'
        result = _extract_json(raw)
        assert result == '{"key": "val"}'

    def test_plain_text_returned_unchanged(self):
        """When no JSON at all, return input; json.loads will raise clearly."""
        raw = "I cannot produce JSON right now."
        result = _extract_json(raw)
        assert result == raw


# ===========================================================================
# B. MasterArchitect.plan() — canned responses
# ===========================================================================

class TestArchitectCanned:

    # -----------------------------------------------------------------------
    # Test 1 — valid JSON on first attempt
    # -----------------------------------------------------------------------
    def test_valid_on_first_attempt(self):
        """plan() succeeds immediately when the model returns valid JSON."""
        arch = _architect()

        with patch("architect.architect._call_ollama") as mock_call:
            mock_call.return_value = _make_ollama_response(VALID_SPEC_DICT)
            spec = arch.plan(
                requirement="Add a multiply function to math_utils.",
                repo_summary=REPO_SUMMARY,
                workspace_root=WS_ROOT,
            )

        assert isinstance(spec, MasterSpecification)
        assert mock_call.call_count == 1
        assert len(spec.tasks) == 1
        assert spec.tasks[0].task_id == "task-be-001"
        assert spec.tasks[0].role == WorkerRole.BACKEND
        assert spec.tasks[0].status == TaskStatus.PENDING

    # -----------------------------------------------------------------------
    # Test 2 — bad JSON attempt 1, valid attempt 2
    # -----------------------------------------------------------------------
    def test_retry_on_bad_json_then_succeeds(self):
        """
        Attempt 1 returns malformed JSON.
        Attempt 2 returns correct JSON.
        plan() must succeed and call _call_ollama exactly twice.
        """
        arch = _architect()
        bad_response = "Sorry, I cannot produce the JSON right now."

        with patch("architect.architect._call_ollama") as mock_call:
            mock_call.side_effect = [
                _make_ollama_response(bad_response),        # attempt 1 — bad
                _make_ollama_response(VALID_SPEC_DICT),     # attempt 2 — good
            ]
            spec = arch.plan(
                requirement="Add a multiply function to math_utils.",
                repo_summary=REPO_SUMMARY,
                workspace_root=WS_ROOT,
            )

        assert isinstance(spec, MasterSpecification)
        assert mock_call.call_count == 2
        # The second prompt must include the previous error
        second_call_prompt = mock_call.call_args_list[1][1]["prompt"]
        assert "JSON parse error" in second_call_prompt or \
               "FAILED VALIDATION" in second_call_prompt

    # -----------------------------------------------------------------------
    # Test 3 — bad JSON on BOTH attempts → ArchitectError raised loudly
    # -----------------------------------------------------------------------
    def test_raises_architect_error_after_two_failures(self):
        """
        Both attempts produce unparseable JSON.
        plan() must raise ArchitectError — no silent fallback.
        """
        arch = _architect()

        with patch("architect.architect._call_ollama") as mock_call:
            mock_call.return_value = _make_ollama_response("not json at all !!!")
            with pytest.raises(ArchitectError) as exc_info:
                arch.plan(
                    requirement="Some task",
                    repo_summary=REPO_SUMMARY,
                    workspace_root=WS_ROOT,
                )

        assert mock_call.call_count == 2
        err = exc_info.value
        assert "attempt 2" in str(err).lower() or "failed" in str(err).lower()
        assert err.raw_response != ""

    # -----------------------------------------------------------------------
    # Test 4 — valid JSON but Pydantic validation fails (dangling edge) → ArchitectError
    # -----------------------------------------------------------------------
    def test_raises_on_dangling_dependency_edge(self):
        """
        Model returns JSON with a dependency_graph edge that references a
        non-existent task_id.  Pydantic's model_validator must reject it.
        Both attempts fail → ArchitectError.
        """
        bad_spec = dict(VALID_SPEC_DICT)
        bad_spec = json.loads(json.dumps(bad_spec))  # deep copy
        bad_spec["dependency_graph"] = [
            {"from_task": "task-be-001", "to_task": "NONEXISTENT_TASK"}
        ]

        arch = _architect()
        with patch("architect.architect._call_ollama") as mock_call:
            mock_call.return_value = _make_ollama_response(bad_spec)
            with pytest.raises(ArchitectError) as exc_info:
                arch.plan(
                    requirement="Some task",
                    repo_summary=REPO_SUMMARY,
                    workspace_root=WS_ROOT,
                )

        assert mock_call.call_count == 2
        err = exc_info.value
        assert err.validation_error != ""

    # -----------------------------------------------------------------------
    # Test 5 — workspace path normalisation (_fix_workspaces)
    # -----------------------------------------------------------------------
    def test_workspace_path_normalised(self):
        """
        Even if the model provides wrong allowed_workspace values,
        _fix_workspaces corrects them before validation.
        """
        wrong_ws = dict(VALID_SPEC_DICT)
        wrong_ws = json.loads(json.dumps(wrong_ws))
        wrong_ws["tasks"][0]["allowed_workspace"] = "/wrong/path"

        arch = _architect()
        with patch("architect.architect._call_ollama") as mock_call:
            mock_call.return_value = _make_ollama_response(wrong_ws)
            spec = arch.plan(
                requirement="Add multiply",
                repo_summary=REPO_SUMMARY,
                workspace_root=WS_ROOT,
            )

        assert spec.tasks[0].allowed_workspace == f"{WS_ROOT}/task-be-001"

    # -----------------------------------------------------------------------
    # Test 6 — telemetry collector is called when attached
    # -----------------------------------------------------------------------
    def test_telemetry_called_on_success(self):
        arch = _architect()
        mock_telemetry = MagicMock()
        arch.telemetry = mock_telemetry

        with patch("architect.architect._call_ollama") as mock_call:
            mock_call.return_value = _make_ollama_response(VALID_SPEC_DICT)
            arch.plan(
                requirement="Add multiply",
                repo_summary=REPO_SUMMARY,
                workspace_root=WS_ROOT,
            )

        mock_telemetry.record_llm_call.assert_called_once()
        assert mock_telemetry.log.call_count >= 2   # elapsed + plan_success


# ===========================================================================
# C. _build_prompt
# ===========================================================================

class TestBuildPrompt:

    def test_requirement_appears_in_prompt(self):
        p = _build_prompt("Add multiply", REPO_SUMMARY, WS_ROOT, "")
        assert "Add multiply" in p

    def test_repo_summary_json_appears(self):
        p = _build_prompt("req", REPO_SUMMARY, WS_ROOT, "")
        assert "math_utils" in p

    def test_workspace_root_appears(self):
        p = _build_prompt("req", REPO_SUMMARY, WS_ROOT, "")
        assert WS_ROOT in p

    def test_style_guide_included_when_given(self):
        p = _build_prompt("req", REPO_SUMMARY, WS_ROOT, "Use snake_case.")
        assert "snake_case" in p

    def test_style_guide_absent_when_empty(self):
        p = _build_prompt("req", REPO_SUMMARY, WS_ROOT, "")
        assert "STYLE GUIDE" not in p

    def test_previous_error_injected_on_retry(self):
        p = _build_prompt("req", REPO_SUMMARY, WS_ROOT, "", previous_error="Bad field X")
        assert "Bad field X" in p
        assert "FAILED VALIDATION" in p

    def test_no_error_block_on_first_attempt(self):
        p = _build_prompt("req", REPO_SUMMARY, WS_ROOT, "", previous_error="")
        assert "FAILED VALIDATION" not in p
