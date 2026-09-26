# Orbit — Autonomous Multi-Agent Software Engineering Harness

> **LCC × DevClub AI Coding Harness Hackathon | PRD v1.1**  
> A self-hosted, offline-capable, model-agnostic multi-agent control plane that transforms a single software engineering prompt into verified, evidence-backed repository modifications.

---

## 1. What is Orbit?

Orbit is an autonomous software engineering harness designed to overcome the core limitations of single-agent code generation: context exhaustion, runaway looping, hallucinated dependency chains, and lack of verifiable evidence.

Instead of running an unconstrained model in a loose prompt-and-retry loop, Orbit orchestrates a structured multi-agent pipeline:

```
                  ┌────────────────────────────────────────┐
                  │          User Requirement Prompt       │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │       Git-Optional Code Indexer        │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │        Master Architect Planner        │
                  │   Decomposes into MasterSpecification  │
                  │       and Dependency-Graph DAG         │
                  └───────────────────┬────────────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │          Ready-Queue Scheduler         │
                  │       Executes Parallel Task Waves     │
                  └───────┬────────────────────────┬───────┘
                          │                        │
                          ▼                        ▼
                ┌──────────────────┐      ┌──────────────────┐
                │  Backend Worker  │      │ Frontend Worker  │
                │ (Isolated Tree)  │      │ (Isolated Tree)  │
                └─────────┬────────┘      └────────┬─────────┘
                          │                        │
                          └───────────┬────────────┘
                                      │
                                      ▼
                  ┌────────────────────────────────────────┐
                  │         Integrator & Verifier          │
                  │  Filesystem Snapshot + Merge + Pytest  │
                  └───────────────────┬────────────────────┘
                                      │ (If tests fail: Targeted FailureContext Rework)
                                      ▼
                  ┌────────────────────────────────────────┐
                  │            Global Reviewer             │
                  │  Contract Adherence + PASS/FAIL Table  │
                  └───────────────────┬────────────────────┘
                                      │ (If review fails: Reviewer-Triggered Rework)
                                      ▼
                  ┌────────────────────────────────────────┐
                  │  Verified Merge + Full Telemetry Log   │
                  └────────────────────────────────────────┘
```

### Core Architectural Principles

1. **Git-Optional & Offline-First**: Operates seamlessly on plain directories (`git=False`) or full git repositories (`git=True`). All phases run 100% locally on Ollama without requiring remote cloud API keys or external network connectivity.
2. **Strict Contract-Driven Decomposition**: The Master Architect produces typed, Pydantic-validated `MasterSpecification` and `TaskContract` objects with formal input/output schemas and acceptance criteria.
3. **Sandboxed Tool Gateway**: Every worker executes inside an isolated workspace with strict guardrails: hard caps on tool calls (`MAX_TOOL_CALLS`), path escape prevention (`PathEscapeError`), and execution timeouts.
4. **Targeted Closed-Loop Recovery**: When unit tests fail, Orbit isolates the exact failure into a structured `FailureContext` (command, exit code, stderr, changed files) and dispatches a single targeted rework task to the responsible worker. **No full regenerations.**
5. **Global Reviewer with Closed-Loop Rework**: An independent audit pass evaluates unified filesystem diffs against the original prompt and individual worker contracts. If criteria are unmet, targeted rework tasks are dispatched and re-integrated before final verdict.
6. **Transparent Evidence & Telemetry**: Every run outputs complete filesystem diffs, per-requirement PASS/FAIL matrices, and granular token/call logs (`demo_telemetry.json`).

---

## 2. Repository Layout

```
orbit-harness/
├── scheduler.py             # Top-level entry point / ready-queue runner
├── config/
│   ├── router.py            # ModelRouter: Stage A (uniform) and Stage B (per-role) routing
│   └── routing.yaml         # Provider configuration and model assignments
├── models/
│   ├── __init__.py
│   └── spec.py              # Pydantic models: MasterSpecification, TaskContract, ReviewVerdict
├── indexer/
│   ├── __init__.py
│   └── indexer.py           # Git-agnostic filesystem indexer & code search
├── architect/
│   ├── __init__.py
│   └── architect.py         # Master Architect: Prompt → Spec + Dependency Graph DAG
├── gateway/
│   ├── __init__.py
│   └── gateway.py           # Sandboxed Tool Gateway with path-traversal & timeout guardrails
├── workers/
│   ├── __init__.py
│   └── base_worker.py       # BaseWorker, BackendWorker, FrontendWorker, DatabaseWorker
├── integrator/
│   ├── __init__.py
│   └── integrator.py        # Workspace merge, pre-edit snapshots, pytest verifier, recovery loop
├── reviewer/
│   ├── __init__.py
│   └── reviewer.py          # Global Reviewer: Contract checking & requirement PASS/FAIL tables
├── telemetry/
│   ├── __init__.py
│   └── collector.py         # Structured telemetry collector (tokens, wall-clock, tool calls)
├── scripts/
│   ├── run_demo_sequence.py # End-to-end live rehearsal script (Steps 1–6 + recovery + rework)
│   └── run_hero_artifact.py # Comparative baseline vs. Orbit hero artifact runner
├── orbit_runs/              # Saved evidence: demo_telemetry.json, reviewer_raw_response.json
├── test_targets/
│   ├── plain_dir/           # Synthetic non-git target (greeter library + unit tests)
│   └── git_repo/            # Synthetic git repository target (math utility + unit tests)
└── tests/                   # 129 comprehensive unit & integration tests
```

---

## 3. Quick Start & Prerequisites

### Prerequisites

- **Python**: 3.12+
- **Local Model Runner**: [Ollama](https://ollama.com/) running locally:
  ```bash
  ollama serve
  ollama pull deepseek-coder-v2:latest
  # or: ollama pull qwen2.5-coder:7b
  ```

### Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/your-org/orbit-harness.git
cd orbit-harness
pip install -r requirements.txt
```

*(Core dependencies: `pydantic>=2.0`, `pyyaml`, `pytest`, `requests`)*

### Run the Test Suite

Orbit includes a 129-test verification suite covering indexers, architects, gateway security, scheduling waves, integration merges, recovery loops, reviewer schemas, and telemetry collectors:

```bash
python3 -m pytest -W ignore
# ........................................................................ [ 55%]
# .........................................................                [100%]
# 129 passed in 3.95s
```

---

## 4. Running the Demo Sequence Yourself

Orbit provides an automated demonstration script (`scripts/run_demo_sequence.py`) that steps through the full lifecycle:
1. File tree indexing (git-agnostic)
2. Architect task decomposition into a dependency DAG
3. Parallel execution across worker waves
4. Injection of a deliberate syntax/logic error to exercise targeted recovery
5. Verification & pre-edit filesystem diff generation
6. Global Reviewer audit and requirement verification
7. Structured telemetry recording with zero cloud leaks

### Non-Git Plain Directory (Offline Default)

```bash
python3 scripts/run_demo_sequence.py --target test_targets/plain_dir
```

### Git Repository Target

```bash
python3 scripts/run_demo_sequence.py --target test_targets/git_repo
```

---

## 5. Worked Example: Live Plain Directory Execution

Below is the verbatim console output from a live end-to-end execution of Orbit against `test_targets/plain_dir` using local `deepseek-coder-v2:latest` running on Ollama with **zero cloud API keys set**:

```
===========================================================================
  ORBIT HARNESS — FULL DEMO SEQUENCE (PRD SECTION 8)
  Target Directory: /Users/aryanmudgal/Desktop/Orbit harness/test_targets/plain_dir (is_git=False)
  Model           : deepseek-coder-v2:latest
===========================================================================

[Step 1: One Prompt & Repo Indexing]
  Requirement: 'Add a multiply function that takes two numbers and returns their product to src/greeter.py, and add a test_multiply unit test to tests/test_greeter.py.'
  Indexed 3 files (git=False) in 0.00s

[Step 2: Task Graph Decomposition]
  Architecture Summary: src/greeter.py will be modified to include a multiply function, and tests/test_greeter.py will be modified to include a test_multiply unit test.
  Generated 2 TaskContracts:
    • [backend] task-be-001: Implement a multiply function in src/greeter.py that takes two numbers and returns their product.
    • [backend] task-be-002: Add a test_multiply unit test in tests/test_greeter.py to ensure the multiply function works correctly. (depends on: task-be-001)

[Step 3: Worker Execution via ReadyQueueScheduler]
[scheduler] Starting ready-queue execution for 2 tasks...

[scheduler] === Wave 1 (1 ready tasks) ===
  • Launching task-be-001 [backend]: Implement a multiply function in src/greeter.py that takes t...
  ✔ task-be-001 [backend] DONE — Implemented a multiply function in src/greeter.py that takes

[scheduler] === Wave 2 (1 ready tasks) ===
  • Launching task-be-002 [backend]: Add a test_multiply unit test in tests/test_greeter.py to en...
  ✔ task-be-002 [backend] DONE — Added a unit test for the multiply function in the greeter m

[scheduler] Finished: 2 done, 0 failed.


[Step 4 & 5: Integrator Merge, Forced Failure & Targeted Recovery]
  Injecting deliberate syntax/logic bug to exercise targeted recovery...
Verification failed on initial merge. Entering targeted rework loop...
  --> Targeted rework pass #1 triggered for worker task-be-002-rework-1
      Failing command: pytest
  Integrator Result: SUCCESS
  Targeted Recovery Loops Run: 1

[Step 6: Global Reviewer Final PASS/FAIL Verdict]
### Orbit Global Review Verdict

**Requirement:** Add a multiply function that takes two numbers and returns their product to src/greeter.py, and add a test_multiply unit test to tests/test_greeter.py.
**Overall Verdict:** ✅ PASS
**Contract Adherence:** ✅ PASS

| Requirement / Acceptance Criterion | Status |
|-------------------------------------|--------|
| The multiply function exists in src/greeter.py and takes two numbers as arguments and returns their product. | ✅ PASS |
| The function is type-annotated. | ✅ PASS |
| A test_multiply function exists in tests/test_greeter.py that tests the multiply function. | ✅ PASS |
| The test verifies the correct operation of the multiply function with various inputs. | ✅ PASS |

**Reviewer Notes:** The implementation of the multiply function and the corresponding unit test in tests/test_greeter.py satisfies the original requirement and the individual acceptance criteria of each TaskContract. The function is correctly type-annotated and the test verifies the expected functionality with various inputs. All workers adhered to their respective TaskContracts and style guide, indicating a successful implementation.


[telemetry] Report saved to /Users/aryanmudgal/Desktop/Orbit harness/orbit_runs/demo_telemetry.json
[telemetry] Total LLM calls: 4
  • Phase: architect       | Model: deepseek-coder-v2:latest  | Provider: ollama     | Endpoint: http://localhost:11434
  • Phase: worker_backend  | Model: deepseek-coder-v2:latest  | Provider: ollama     | Endpoint: http://localhost:11434
  • Phase: worker_backend  | Model: deepseek-coder-v2:latest  | Provider: ollama     | Endpoint: http://localhost:11434
  • Phase: reviewer        | Model: deepseek-coder-v2:latest  | Provider: ollama     | Endpoint: http://localhost:11434

===========================================================================
  DEMO SEQUENCE COMPLETE — VERDICT: ✅ PASS
===========================================================================
```

---

## 6. Generated Evidence & Telemetry

Orbit automatically serializes machine-readable evidence for every run into `orbit_runs/`:

- **`orbit_runs/demo_telemetry.json`**:
  - Full trace of every role invocation (prompt tokens, completion tokens, duration, model, endpoint).
  - Verifiable proof of 0 external network requests (`endpoint: http://localhost:11434`).
  - Unified diff between pre-edit filesystem snapshot and final verified tree.
- **`orbit_runs/reviewer_raw_response.json`**:
  - Preserves the unedited thinking block (`<thought>...</thought>`) and structured JSON schema returned by the reviewer model before parsing.

---

## 7. Metrics & Verification Status (PRD Section 7)

| Metric | Status | Evidence File / Backing |
|---|---|---|
| **Correctness** | **Verified (Synthetic)** | `orbit_runs/demo_telemetry.json`: Both `plain_dir` and `git_repo` reached final PASS after targeted recovery. *(Top Open Risk: evaluated on synthetic targets; real hackathon repo/issue set remains unavailable).* |
| **Evidence Quality** | **Verified** | Full console transcripts, `demo_telemetry.json` (unified diffs, PASS/FAIL tables, LLM call log), and `reviewer_raw_response.json`. |
| **Recovery Effectiveness** | **Verified** | Both demo runs logged `Targeted Recovery Loops Run: 1` + Reviewer rework dispatch; verified at test-failure and requirement-review levels on git and non-git targets. |
| **Efficiency** | **Open (Observed Counts)** | `demo_telemetry.json` records 4–5 calls per run. Multi-run stability benchmark comparison (3 consecutive runs) remains open pending execution on the official hackathon benchmark set. |
| **Routing Impact** | **Open** | Stage B per-role routing is implemented in `config/router.py` and unit tested in `tests/test_phase7_stage_b.py`, but quantitative delta comparison between uniform Stage A and heterogeneous Stage B across a shared benchmark set is not yet measured. |
| **Offline Capability** | **Verified** | Confirmed with empty cloud keys (`MATCHING_ENV_VARS: []`) and 100% Ollama endpoint calls in telemetry logs. |

---

*Orbit — Autonomous Multi-Agent Software Engineering Harness*
