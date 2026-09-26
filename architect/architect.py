"""
architect/architect.py — Master Architect (Phase 2)
====================================================
PRD §4.1 / §4.2:
  "One task becomes a MasterSpecification plus a dependency graph plus
   individual TaskContracts."

Public API
----------
    plan(requirement, repo_summary, workspace_root, style_guide="") -> MasterSpecification

Retry contract (PRD §3.2 — 'fail loudly'):
  - Attempt 1: strict JSON prompt → parse → Pydantic validate.
  - Attempt 2 (on any failure): append the exact error to the prompt,
    retry once.
  - If attempt 2 also fails: raise ArchitectError immediately.
  - No silent fallback to a looser model, no partial object returned.

Telemetry
---------
  Every LLM call records tokens_in / tokens_out via the passed-in
  TelemetryCollector (optional; skipped when None).
"""

from __future__ import annotations

import json
import re
import textwrap
import time
from pathlib import Path
from typing import Any

import requests
from pydantic import ValidationError

from models.spec import MasterSpecification, TaskContract, WorkerRole


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ArchitectError(RuntimeError):
    """
    Raised when the Architect cannot produce a valid MasterSpecification
    after all retries.  Contains the raw LLM response for debugging.
    """
    def __init__(self, message: str, raw_response: str = "", validation_error: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.validation_error = validation_error


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

# Inline minimal schema — derived from MasterSpecification.model_json_schema()
# but hand-trimmed to fit in a model context without runtime fields the LLM
# should never set (status, result_summary, evidence).
_COMPACT_SCHEMA = """\
{
  "requirement": "<string — verbatim task>",
  "architecture_summary": "<string — prose: which files change and why>",
  "style_guide": "<string — naming/formatting guide for all workers>",
  "tasks": [
    {
      "task_id": "<string — e.g. task-be-001>",
      "role": "<one of: database | backend | frontend>",
      "objective": "<one-sentence goal>",
      "inputs": ["<file path or artifact name the worker may READ>"],
      "depends_on": ["<task_id of upstream task, or [] if independent>"],
      "allowed_workspace": "<absolute path — MUST be workspace_root/<task_id>>",
      "expected_artifact": "<primary output file or directory path>",
      "acceptance_criteria": ["<verifiable condition 1>", "..."]
    }
  ],
  "dependency_graph": [
    {"from_task": "<upstream task_id>", "to_task": "<downstream task_id>", "artifact": ""}
  ]
}"""

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are the Master Architect for Orbit, an autonomous multi-agent software engineering harness.
    Your sole job is to analyse a software-engineering requirement and the existing codebase map,
    then decompose the work into a MasterSpecification JSON document.

    Rules — read carefully, every rule is enforced by Pydantic validation:
    1. Respond with ONLY a single JSON object. No markdown fences, no prose, no explanation.
    2. The JSON must match the schema shown below exactly.
    3. role must be one of: "database", "backend", "frontend" — no other value is valid.
    4. Every task_id in dependency_graph.from_task and dependency_graph.to_task must exist
       in the tasks array.
    5. allowed_workspace for each task must be: {workspace_root}/<task_id>
       (use the exact workspace_root value supplied in the user message).
    6. acceptance_criteria must be a list of strings (may be empty list []).
    7. Do NOT include fields: status, result_summary, evidence — those are runtime fields.
    8. Use 1-3 tasks only. Do not invent tasks beyond what the requirement needs.

    Schema:
""")


def _build_prompt(
    requirement: str,
    repo_summary: dict,
    workspace_root: str,
    style_guide: str,
    previous_error: str = "",
) -> str:
    """Compose the full user-turn prompt string."""
    repo_json = json.dumps(repo_summary, indent=2)
    parts = [
        f"REQUIREMENT:\n{requirement}\n",
        f"CODEBASE MAP:\n{repo_json}\n",
        f"WORKSPACE ROOT: {workspace_root}\n",
    ]
    if style_guide:
        parts.append(f"STYLE GUIDE:\n{style_guide}\n")
    if previous_error:
        parts.append(
            f"YOUR PREVIOUS RESPONSE FAILED VALIDATION WITH THIS ERROR:\n"
            f"{previous_error}\n"
            f"Fix the JSON and return only the corrected object.\n"
        )
    parts.append(
        "Return the MasterSpecification JSON object now. "
        "No markdown. No explanation. Only JSON."
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON extraction helper
# ---------------------------------------------------------------------------

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```", re.IGNORECASE)


def _extract_json(text: str) -> str:
    """
    Return the best JSON string from *text*.

    Tries, in order:
      1. The whole text (model obeyed the 'only JSON' instruction).
      2. A ```json … ``` or ``` … ``` fenced block.
      3. The first { … } span.
    """
    text = text.strip()
    # 1. Direct parse — fastest path
    if text.startswith("{"):
        return text
    # 2. Fenced block
    m = _JSON_BLOCK_RE.search(text)
    if m:
        return m.group(1).strip()
    # 3. Brace extraction (last resort)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text   # give it to json.loads and let that produce a clear error


# ---------------------------------------------------------------------------
# Ollama HTTP client (thin, no SDK dependency)
# ---------------------------------------------------------------------------

def _call_ollama(
    prompt: str,
    system: str,
    model: str,
    base_url: str,
    temperature: float = 0.0,
    num_predict: int = 2048,
) -> tuple[str, int, int]:
    """
    POST to /api/generate with format=json.
    Returns (response_text, tokens_in, tokens_out).
    Raises requests.RequestException on network / HTTP failure.
    """
    payload = {
        "model": model,
        "system": system,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
        },
    }
    resp = requests.post(
        f"{base_url.rstrip('/')}/api/generate",
        json=payload,
        timeout=300,
    )
    resp.raise_for_status()
    data = resp.json()
    return (
        data.get("response", ""),
        data.get("prompt_eval_count", 0),
        data.get("eval_count", 0),
    )


# ---------------------------------------------------------------------------
# MasterArchitect
# ---------------------------------------------------------------------------

class MasterArchitect:
    """
    Converts a raw requirement + repo map into a validated MasterSpecification.

    Parameters
    ----------
    model       : Ollama model tag (default from config/routing.yaml Stage A).
    base_url    : Ollama base URL.
    telemetry   : optional TelemetryCollector; pass None to skip telemetry.
    """

    PHASE = "architect"

    def __init__(
        self,
        model: str = "deepseek-coder-v2:latest",
        base_url: str = "http://localhost:11434",
        telemetry=None,           # TelemetryCollector | None
        temperature: float = 0.0,
        num_predict: int = 2048,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self.telemetry = telemetry
        self.temperature = temperature
        self.num_predict = num_predict

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def plan(
        self,
        requirement: str,
        repo_summary: dict,
        workspace_root: str,
        style_guide: str = "",
    ) -> MasterSpecification:
        """
        Call the LLM, validate, retry once on failure, raise ArchitectError
        if both attempts fail.

        Parameters
        ----------
        requirement    : Verbatim task string from the user.
        repo_summary   : dict from RepoIndex.summary().
        workspace_root : Absolute path string; injected into each task's
                         allowed_workspace as {workspace_root}/{task_id}.
        style_guide    : Optional shared style guide forwarded to every
                         TaskContract (PRD §4.9).

        Returns
        -------
        MasterSpecification — fully validated Pydantic model.

        Raises
        ------
        ArchitectError  — after two failed attempts, with raw response +
                          validation error attached.
        """
        system = _SYSTEM_PROMPT.format(workspace_root=workspace_root) + _COMPACT_SCHEMA

        previous_error: str = ""
        last_raw: str = ""

        for attempt in (1, 2):
            prompt = _build_prompt(
                requirement=requirement,
                repo_summary=repo_summary,
                workspace_root=workspace_root,
                style_guide=style_guide,
                previous_error=previous_error,
            )

            t0 = time.monotonic()
            try:
                raw, tok_in, tok_out = _call_ollama(
                    prompt=prompt,
                    system=system,
                    model=self.model,
                    base_url=self.base_url,
                    temperature=self.temperature,
                    num_predict=self.num_predict,
                )
            except requests.RequestException as exc:
                raise ArchitectError(
                    f"Ollama network error on attempt {attempt}: {exc}"
                ) from exc

            elapsed = time.monotonic() - t0
            last_raw = raw

            # Log telemetry if collector is attached
            if self.telemetry is not None:
                self.telemetry.record_llm_call(
                    phase=self.PHASE,
                    tokens_in=tok_in,
                    tokens_out=tok_out,
                    model=self.model,
                )
                self.telemetry.log(
                    self.PHASE,
                    f"attempt_{attempt}_elapsed",
                    seconds=round(elapsed, 2),
                )

            # Parse JSON
            json_str = _extract_json(raw)
            try:
                data = json.loads(json_str)
            except json.JSONDecodeError as exc:
                previous_error = (
                    f"JSON parse error: {exc}\n"
                    f"The text you returned was:\n{raw[:800]}"
                )
                if attempt == 2:
                    raise ArchitectError(
                        f"JSON parse failed on attempt {attempt}.",
                        raw_response=raw,
                        validation_error=previous_error,
                    ) from exc
                continue

            # Inject workspace paths if the model left them blank / wrong
            data = self._fix_workspaces(data, workspace_root)

            # Pydantic validate
            try:
                spec = MasterSpecification.model_validate(data)
            except ValidationError as exc:
                previous_error = (
                    f"Pydantic validation error:\n{exc}\n"
                    f"Fix every field listed above."
                )
                if attempt == 2:
                    raise ArchitectError(
                        f"Pydantic validation failed on attempt {attempt}.",
                        raw_response=raw,
                        validation_error=str(exc),
                    ) from exc
                continue

            # Success
            if self.telemetry is not None:
                self.telemetry.log(
                    self.PHASE,
                    "plan_success",
                    attempt=attempt,
                    task_count=len(spec.tasks),
                )
            return spec

        # Should be unreachable, but satisfy type checker
        raise ArchitectError(
            "Architect exhausted all retries.",
            raw_response=last_raw,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _fix_workspaces(self, data: dict, workspace_root: str) -> dict:
        """
        Ensure every task's allowed_workspace is {workspace_root}/{task_id}.
        The model sometimes ignores the instruction; we correct silently here
        (the validate step will still catch structural issues).
        """
        root = workspace_root.rstrip("/")
        for task in data.get("tasks", []):
            tid = task.get("task_id", "")
            expected = f"{root}/{tid}"
            if task.get("allowed_workspace", "") != expected:
                task["allowed_workspace"] = expected
        return data
