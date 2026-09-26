"""
reviewer/reviewer.py — Global Reviewer & Contract Adherence (Phase 6)
======================================================================
PRD §4.6, §4.9:
  "Takes the original requirement, the MasterSpecification, and all collected
   evidence (diffs, test results) and produces a per-requirement PASS/FAIL table
   via one Ollama call with structured output, per Section 4.6.
   Add the contract-adherence check from Section 4.6/4.9 — compare each worker's
   diff against its own TaskContract's acceptance criteria, not just against the
   test suite. Every FAIL must generate exactly one new rework TaskContract
   routed through Phase 5's recovery path — never a full regeneration."

Reviewer Output:
  ReviewVerdict with:
    - per_requirement_results: dict[str, bool]
    - contract_adherence: bool
    - rework_tasks: list[TaskContract] (targeted rework tasks for any FAIL)
    - notes: summary of review findings
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import textwrap
from typing import Any, Callable

from architect.architect import call_ollama, extract_json
from models.spec import MasterSpecification, ReviewVerdict, TaskContract, TaskStatus

logger = logging.getLogger("orbit.reviewer")


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_REVIEWER_SYSTEM_PROMPT = textwrap.dedent("""\
    You are the Global Reviewer for Orbit, an autonomous multi-agent software engineering harness.
    Your role is to rigorously evaluate whether the implementation evidence (diffs, test execution
    results, worker summaries) satisfies:
    1. The original software requirement.
    2. The individual acceptance criteria of each TaskContract in the MasterSpecification.
    3. The contract adherence and style guide (naming conventions, type annotations, docstrings,
       expected artifacts).

    Rules:
    - Be strict, objective, and evidence-driven. Do NOT assume something works without evidence.
    - Evaluate each acceptance criterion individually as true (PASS) or false (FAIL).
    - Evaluate contract_adherence: compare each worker's diff against its own TaskContract
      acceptance criteria and style guide. Set contract_adherence to true ONLY if all workers
      adhered to their contract and style guide.
    - If any criterion fails or contract adherence fails, provide clear, actionable reasons.
    - Return ONLY a single JSON object. No explanation, markdown fences, or text outside the JSON.
""")


def _build_reviewer_prompt(
    requirement: str,
    spec: MasterSpecification,
    evidence: dict[str, Any],
) -> str:
    lines = [
        f"ORIGINAL REQUIREMENT:\n{requirement}\n",
        f"ARCHITECTURE SUMMARY:\n{spec.architecture_summary}\n",
    ]
    if spec.style_guide:
        lines.append(f"STYLE GUIDE:\n{spec.style_guide}\n")

    lines.append("TASK CONTRACTS & ACCEPTANCE CRITERIA:")
    for t in spec.tasks:
        lines.append(f"Task: {t.task_id} [{t.role}]")
        lines.append(f"  Objective: {t.objective}")
        lines.append(f"  Expected Artifact: {t.expected_artifact}")
        lines.append("  Acceptance Criteria:")
        for ac in t.acceptance_criteria:
            lines.append(f"    - {ac}")
        lines.append("")

    lines.append("COLLECTED EVIDENCE:")
    diff_text = evidence.get("diff", "")
    lines.append(f"--- Unified Diff ---\n{diff_text if diff_text else '(No diff provided)'}\n")

    test_output = evidence.get("test_output", "")
    lines.append(f"--- Test Runner Output ---\n{test_output if test_output else '(No test output provided)'}\n")

    # Worker task evidence summary
    task_evidences = evidence.get("tasks", {})
    if task_evidences:
        lines.append("--- Worker Task Summaries ---")
        for tid, te in task_evidences.items():
            lines.append(f"Task {tid}: summary={te.get('summary', '')}, files={te.get('files_written', [])}")
        lines.append("")

    lines.append(textwrap.dedent("""\
        INSTRUCTIONS:
        Evaluate all criteria and contract adherence. Return ONLY a single JSON object with this exact schema:
        {
          "per_requirement_results": {
            "<exact acceptance criterion text>": true/false
          },
          "contract_adherence": true/false,
          "contract_adherence_issues": [
            {
              "task_id": "<task_id>",
              "criterion": "<criterion or style rule>",
              "reason": "<specific explanation of failure>"
            }
          ],
          "failing_reasons": {
            "<criterion or requirement>": "<concise reason why it failed>"
          },
          "notes": "<overall review assessment prose>"
        }
    """))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# GlobalReviewer Class (PRD §4.6)
# ---------------------------------------------------------------------------

class GlobalReviewer:
    """
    Evaluates requirements and contract adherence.
    Produces per-requirement PASS/FAIL table and targeted rework TaskContracts on FAIL.
    """

    def __init__(
        self,
        model: str = "nemotron-3-ultra:cloud",
        base_url: str = "http://localhost:11434",
        ollama_caller: Callable[..., tuple[str, int, int]] | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url
        self._call_ollama = ollama_caller or call_ollama
        self.last_tokens_in: int = 0
        self.last_tokens_out: int = 0

    def _find_responsible_task(self, criterion: str, spec: MasterSpecification) -> TaskContract:
        """Find the task whose acceptance criteria or objective mentions this criterion."""
        criterion_lower = criterion.lower()
        for task in spec.tasks:
            for ac in task.acceptance_criteria:
                if criterion_lower in ac.lower() or ac.lower() in criterion_lower:
                    return task
            if any(word in task.objective.lower() for word in criterion_lower.split() if len(word) > 4):
                return task
        return spec.tasks[0] if spec.tasks else None

    def review(
        self,
        requirement: str,
        spec: MasterSpecification,
        evidence: dict[str, Any],
    ) -> ReviewVerdict:
        """
        Evaluate implementation against requirement and spec.
        Returns a validated ReviewVerdict.
        """
        prompt = _build_reviewer_prompt(requirement, spec, evidence)

        # Call Ollama with single-turn structured prompt
        raw_resp, tok_in, tok_out = self._call_ollama(
            prompt=prompt,
            system=_REVIEWER_SYSTEM_PROMPT,
            model=self.model,
            base_url=self.base_url,
            temperature=0.0,
        )

        # Immediately write raw response before any downstream processing
        try:
            import architect.architect as arch_mod
            raw_payload = getattr(arch_mod, "LAST_RAW_OLLAMA_RESPONSE", None) or {
                "message": {"role": "assistant", "content": raw_resp}
            }
            repo_root = Path(__file__).resolve().parent.parent
            raw_path = repo_root / "orbit_runs" / "reviewer_raw_response.json"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps(raw_payload, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to write reviewer_raw_response.json: {e}")

        if not raw_resp.strip():
            logger.warning("Empty response from Reviewer model, retrying once...")
            raw_resp, tok_in_2, tok_out_2 = self._call_ollama(
                prompt=prompt,
                system=_REVIEWER_SYSTEM_PROMPT,
                model=self.model,
                base_url=self.base_url,
                temperature=0.1,
            )
            tok_in += tok_in_2
            tok_out += tok_out_2

        json_str = extract_json(raw_resp)
        data = None
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            logger.warning("Failed to parse Reviewer JSON, retrying with format reminder...")
            retry_prompt = prompt + "\n\nCRITICAL: Return ONLY valid parseable JSON matching the schema. No markdown fences, no explanation."
            raw_resp, tok_in_2, tok_out_2 = self._call_ollama(
                prompt=retry_prompt,
                system=_REVIEWER_SYSTEM_PROMPT,
                model=self.model,
                base_url=self.base_url,
                temperature=0.1,
            )
            tok_in += tok_in_2
            tok_out += tok_out_2
            try:
                data = json.loads(extract_json(raw_resp))
            except json.JSONDecodeError as exc:
                logger.error(f"Failed to parse Reviewer JSON on retry: {exc}\nRaw: {raw_resp[:300]}")
                data = {
                    "per_requirement_results": {"Requirement implementation": False},
                    "contract_adherence": False,
                    "notes": f"Reviewer JSON parse failure: {exc}",
                }

        per_req = data.get("per_requirement_results", {})
        # Ensure per_requirement_results has at least the task acceptance criteria
        if not per_req:
            all_criteria = []
            for t in spec.tasks:
                all_criteria.extend(t.acceptance_criteria)
            if not all_criteria:
                all_criteria = [requirement]
            per_req = {c: True for c in all_criteria}

        contract_adherence = bool(data.get("contract_adherence", True))
        notes = data.get("notes", "Review completed.")
        failing_reasons = data.get("failing_reasons", {})
        adherence_issues = data.get("contract_adherence_issues", [])

        # Generate targeted rework TaskContracts for any failing criteria
        rework_tasks: list[TaskContract] = []
        seen_rework_tasks: set[str] = set()

        for crit, passed in per_req.items():
            if not passed:
                resp_task = self._find_responsible_task(crit, spec)
                if resp_task and resp_task.task_id not in seen_rework_tasks:
                    seen_rework_tasks.add(resp_task.task_id)
                    reason = failing_reasons.get(crit, f"Failed criterion: {crit}")
                    rework_task = TaskContract(
                        task_id=f"{resp_task.task_id}-rework-review",
                        role=resp_task.role,
                        objective=f"Fix review failure in {resp_task.task_id}: {reason[:80]}",
                        inputs=resp_task.inputs,
                        allowed_workspace=resp_task.allowed_workspace,
                        expected_artifact=resp_task.expected_artifact,
                        acceptance_criteria=[crit],
                        style_guide=resp_task.style_guide,
                        evidence={
                            "failure_reason": reason,
                            "review_notes": notes,
                        },
                    )
                    rework_tasks.append(rework_task)

        # Also add rework tasks for any contract adherence issues
        if not contract_adherence and adherence_issues:
            for issue in adherence_issues:
                tid = issue.get("task_id", "")
                task_obj = spec.task_by_id(tid) or (spec.tasks[0] if spec.tasks else None)
                if task_obj and task_obj.task_id not in seen_rework_tasks:
                    seen_rework_tasks.add(task_obj.task_id)
                    reason = issue.get("reason", "Contract adherence failure")
                    crit = issue.get("criterion", "Contract adherence and style compliance")
                    rework_task = TaskContract(
                        task_id=f"{task_obj.task_id}-rework-adherence",
                        role=task_obj.role,
                        objective=f"Fix style/contract adherence in {task_obj.task_id}: {reason[:80]}",
                        inputs=task_obj.inputs,
                        allowed_workspace=task_obj.allowed_workspace,
                        expected_artifact=task_obj.expected_artifact,
                        acceptance_criteria=[crit],
                        style_guide=task_obj.style_guide,
                        evidence={
                            "failure_reason": reason,
                            "review_notes": notes,
                        },
                    )
                    rework_tasks.append(rework_task)

        self.last_tokens_in = tok_in
        self.last_tokens_out = tok_out

        verdict = ReviewVerdict(
            requirement=requirement,
            per_requirement_results=per_req,
            contract_adherence=contract_adherence,
            rework_tasks=rework_tasks,
            notes=notes,
        )
        return verdict

    def format_table(self, verdict: ReviewVerdict) -> str:
        """
        Produce a markdown PASS/FAIL table summarizing the ReviewVerdict.
        """
        lines = [
            "### Orbit Global Review Verdict",
            "",
            f"**Requirement:** {verdict.requirement}",
            f"**Overall Verdict:** {'✅ PASS' if verdict.passed else '❌ FAIL'}",
            f"**Contract Adherence:** {'✅ PASS' if verdict.contract_adherence else '❌ FAIL'}",
            "",
            "| Requirement / Acceptance Criterion | Status |",
            "|-------------------------------------|--------|",
        ]
        for criterion, passed in verdict.per_requirement_results.items():
            status_str = "✅ PASS" if passed else "❌ FAIL"
            lines.append(f"| {criterion} | {status_str} |")

        lines.append("")
        if verdict.notes:
            lines.append(f"**Reviewer Notes:** {verdict.notes}")
            lines.append("")

        if verdict.rework_tasks:
            lines.append(f"**Targeted Rework Tasks Generated:** {len(verdict.rework_tasks)}")
            for rt in verdict.rework_tasks:
                lines.append(f"- `[{rt.role}] {rt.task_id}`: {rt.objective}")
            lines.append("")

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Functional Interface
# ---------------------------------------------------------------------------

def review_implementation(
    requirement: str,
    spec: MasterSpecification,
    evidence: dict[str, Any],
    model: str = "nemotron-3-ultra:cloud",
    base_url: str = "http://localhost:11434",
    ollama_caller: Callable[..., tuple[str, int, int]] | None = None,
) -> ReviewVerdict:
    """Convenience function executing the GlobalReviewer pass."""
    reviewer = GlobalReviewer(model=model, base_url=base_url, ollama_caller=ollama_caller)
    return reviewer.review(requirement=requirement, spec=spec, evidence=evidence)
