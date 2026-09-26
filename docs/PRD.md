**ORBIT**

**Autonomous Multi-Agent Software Engineering Harness**

Product Requirements Document \| v1.1 (Revised) \| LCC × DevClub — AI
Coding Harness Hackathon

*Same model. Different harnesses. Your engineering makes the
difference.*

**Changelog from v1.0:** locks an MVP floor and a hero artifact, stages
model routing to reduce debug surface, simplifies the scheduler's
default mode, makes offline/no-git operation an explicit requirement,
defines the recovery-loop tie-breaker, adds a style-consistency check,
and trims positioning content to protect build time.

1\. Problem Statement

Every team in this hackathon is given the same foundation model, the
same repository, the same issues, the same tests, and the same
evaluation conditions. The task is to build an autonomous coding-agent
harness — the system that surrounds that model — capable of
understanding a software-engineering task, navigating an existing
repository or local file tree, intelligently using tools, managing
context, orchestrating model interactions, recovering from failures, and
producing correct, verified changes with efficient use of resources.

Because the model and the repository are fixed, the only variable that
separates one team's result from another's is engineering quality of the
harness itself. Teams are judged on three axes:

- Correctness first — does the change actually solve the task, verified
  against tests.

- Evidence over claims — the harness must produce proof of what it did
  (diffs, logs, test results), not just a final answer.

- Efficiency matters — token usage, tool calls, and wall-clock time per
  task are part of the score.

In short: the model is the constant. Orbit is our answer to the variable
— the harness that turns that fixed model into a reliable,
evidence-producing software engineer.

2\. Solution Overview

Orbit is a self-hosted, model-agnostic control plane that sits around
the foundation model. It operates directly against a local file tree — a
git repository or any other directory — with no dependency on git, a
remote host, or network access. It is not a chatbot that generates code
and hopes for the best, and it is not another IDE — it is
infrastructure: a repo-aware planner, a pool of specialized workers that
can each be pointed at a different model, a tool gateway with
guardrails, an evidence-driven recovery loop, and a Global Reviewer that
checks the finished work against the original requirement rather than
just against a linter.

One task (an issue, a requirement, a prompt) goes in. Orbit explores the
existing file tree, produces a machine-readable specification and a
dependency graph, delegates independent pieces of work to parallel
workers, integrates their output, verifies it against the repository's
own tests, and — on failure — routes the smallest possible fix back to
exactly the worker responsible instead of regenerating everything. A
Global Review pass then confirms the result actually satisfies the
original requirement, and every step along the way is logged as
evidence: diffs, tool calls, token counts, and pass/fail results.

> **Fix — offline/no-git is now a stated requirement, not an assumption:
> Orbit must run against a directory that has no .git at all, and must
> run fully offline (Ollama, all roles) as one supported, demoable
> configuration.**

2.1 Is / Is Not

|                                                                                                                                                    |                                                                                          |
|----------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------|
| **IS**                                                                                                                                             | **IS NOT**                                                                               |
| A control plane around a foundation model: planner + task graph + worker runtime + verifier + reviewer, usable on any local directory, git or not. | A chatbot that only generates code, or a thin UI wrapper on one model's API.             |
| A system that delegates, parallelizes, integrates, and recovers from failure with logged evidence.                                                 | A claim to have invented multi-agent coding, or a full Cursor / Antigravity replacement. |
| Self-hosted and model-agnostic — any model can be plugged into any role; runs fully offline via local models.                                      | Locked to one vendor's model, one hosted backend, or a network connection.               |

2.2 Competitive Positioning (context only — not a build task)

Orbit sits closest to self-hosted, model-agnostic runtimes (the True
Forge reference point) rather than closed IDEs like Cursor or
Antigravity, whose orchestration is invisible and whose model choice is
locked. That comparison exists to explain our design instincts to
teammates and judges in a sentence or two — it is not scored and does
not get further build or writing time beyond this paragraph.

> **Fix — the old Is/Is-Not-vs-tools comparison table has been cut down
> to one paragraph. This content doesn't move the judging rubric
> (diffs/logs/tests do), so no further time goes here.**

3\. Our Approach

We are not trying to out-model anyone — the model is fixed for every
team. Orbit wins on control-plane design: a machine-readable master
spec, dependency-aware scheduling kept intentionally simple, artifact
contracts instead of raw chat handoffs between roles, and a global
reviewer that checks the original requirement rather than just code
style.

3.1 Architecture at a glance

|                       |                                                                                                                                                                             |
|-----------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Stage**             | **Job in one line**                                                                                                                                                         |
| Master Architect      | Walks the local file tree (git or not), then turns the task into a spec + dependency graph + per-worker task contracts.                                                     |
| Specialized Workers   | Independent workers (frontend / backend / database, etc.) build in parallel wherever no dependency blocks them. Model assignment starts uniform and is staged in — see 4.3. |
| Integrator            | Merges every worker's output into the canonical file tree; snapshots the pre-edit tree so a diff always exists, even without git.                                           |
| Build / Test / Verify | Lint, type-check, unit + integration tests against the repository's own acceptance criteria; an optional browser-automation pass for UI-facing changes.                     |
| Global Review         | Compares requirement → architecture → implementation; finds real gaps, not style nits; also checks contract adherence for cross-worker consistency.                         |
| Targeted Rework       | The smallest possible fix task goes back to the one responsible worker, then the whole pipeline re-verifies — never a full regeneration.                                    |

Context discipline matters as much as the diagram. Each worker receives
a small task contract — objective, inputs, dependencies, allowed
workspace, expected artifact, acceptance criteria — never the whole
project. Workers return an artifact plus status plus evidence, not raw
conversation, which is what keeps the Architect's own context small and
keeps recovery cheap.

3.2 Build philosophy — MVP floor and hero artifact, committed now

- Correctness first — every feature exists to raise the odds the final
  diff actually passes the repo's own tests.

- Evidence over claims — every stage logs what it did (diffs, tool
  calls, tokens, pass/fail), so the harness can prove its result rather
  than assert it.

- Efficiency matters — token and time budgets are enforced with
  guardrails, not left to hope.

- Lean by design — 3–4 workers, not 7, and a scheduler that stays as
  simple as the worker count allows (see 4.3 and 5.1).

> **Fix — MVP floor: Phase 4 (parallel workers + integration, uniform
> model, no recovery loop) is the committed stopping point if time runs
> out. The team rehearses a demo for this floor before attempting Phase
> 5+, so there is always a working fallback.**
>
> **Fix — Hero artifact, decided now, not at demo time: one before/after
> run — naive single-agent baseline vs. Orbit — on the same task and the
> same underlying model, reporting the token and wall-clock delta. This
> is the one exhibit we guarantee we can hand a judge; it is built and
> tested by end of Phase 4, not left to whatever telemetry happens to
> exist by demo time.**

4\. Key Features

4.1 Repo-Aware Master Architect (git-optional)

Before planning anything, Orbit indexes the target directory — file
tree, entry points, relevant modules via code search — and feeds a
condensed map into the Architect. Indexing is done with a filesystem
walk plus code-search tooling (e.g. ripgrep / tree-sitter), not
git-specific commands, so a directory with no .git works identically to
a full repository. This turns planning from “scaffold something new”
into “plan a change to this specific, already-existing system,” which is
the actual shape of every task in this hackathon.

4.2 Dependency-Graph Task Decomposition (kept intentionally simple)

One task becomes a MasterSpecification plus a dependency graph plus
individual TaskContracts. Independent pieces of work run in parallel
instead of one long chain of prompts. With only 3–4 workers, the
scheduler is deliberately kept to the simplest thing that works:

- Default mode: a readiness-based release — a worker starts the moment
  its declared dependency artifacts exist; no custom scheduler
  infrastructure beyond a small ready-queue.

- Fallback mode, if the graph ever gets complex enough to need it: full
  topological sort. This is treated as a stretch upgrade, not a Phase-1
  requirement.

> **Fix — scheduling complexity is capped to match worker count. We
> default to the simplest correct mechanism and only add
> topological-sort infrastructure if the task graph actually demands it,
> so build time isn't spent on parallelism machinery bigger than the 3–4
> workers it coordinates.**

4.3 Per-Role Model Routing — staged, not all-at-once

Every role in the pipeline — Architect, Reviewer, and each worker —
calls the model through one unified adapter interface. Model assignment
is config-driven, but rollout happens in two stages to reduce debugging
surface during the build:

|                                         |                                                                                                                                                                                                                                                                                  |
|-----------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Stage**                               | **Configuration**                                                                                                                                                                                                                                                                |
| Stage A (default, build target)         | All roles run on one reliable model (a single free-tier or local model) while the pipeline itself — planner, workers, integrator, verifier, reviewer — is being built and proven end-to-end. This isolates “is the harness logic broken” from “is a provider/rate-limit broken.” |
| Stage B (enabled once Stage A is green) | Per-role/per-domain model assignment via config, e.g. architect/reviewer on a stronger reasoning model, frontend/backend/database workers on different domain-tuned models.                                                                                                      |

Example Stage B config:

> model_routing:  
> architect: claude-opus \# reasoning-heavy -\> keep strong  
> reviewer: claude-opus \# reasoning-heavy -\> keep strong  
> frontend_worker: model-x \# user-assignable  
> backend_worker: model-y \# user-assignable  
> database_worker: model-z  
> default: free-tier-model \# fallback
>
> **Fix — Stage A / Stage B staging directly addresses the
> multi-provider debugging tax: the full pipeline is proven correct on
> one model before heterogeneous routing is turned on, so a failure
> during core-logic development is never confused with a rate-limit or
> provider outage.**

4.4 Tool Gateway with Guardrails

A scoped set of tools — file read/write/patch, terminal exec, git diff
(when available), test runner, code search — each confined to one
worker's own workspace (isolated folders, or git worktrees when the
target has a .git directory). Hard limits are enforced from day one:

- MAX_TOOL_CALLS — caps runaway tool usage per task.

- MAX_RETRIES / MAX_RECOVERY_LOOPS — caps how many times a failing task
  can be reworked before Orbit stops and reports its best partial result
  with evidence, rather than looping silently.

- TEST_TIMEOUT_SEC — caps wall-clock spent per verification pass.

4.5 Evidence-Driven Recovery Loop — with a defined tie-breaker

A failing test becomes a structured FailureContext — command, error,
stack trace, changed files — which the Master Architect turns into
exactly one targeted rework task for the responsible worker only, then
re-verifies. No full regeneration, ever.

When MAX_RECOVERY_LOOPS is hit and nothing fully passes, “best partial
result” is defined by an explicit, ordered tie-breaker rather than left
ambiguous:

- 1st: highest count of passing tests (most acceptance criteria
  satisfied).

- 2nd (tie): fewest files left in a broken/uncompiling state.

- 3rd (tie): most recently verified candidate (last known-good
  checkpoint before the final failed attempt).

Whatever is returned is accompanied by the full evidence report (diffs,
logs, PASS/FAIL table) so the gap is visible, not hidden.

> **Fix — the previously undefined “best partial result” now has a
> concrete, ordered rule, decided before it's needed live rather than
> improvised during a demo.**

4.6 Global Reviewer (Requirement + Contract Adherence, Not Just Style)

Fed three things — the original requirement, the master architecture,
and the implementation evidence (diffs, test results, screenshots) — the
reviewer produces a per-requirement PASS/FAIL table. It also checks each
worker's output against its own task contract explicitly, which is the
concrete mechanism for catching style/convention drift between workers
running on different models, rather than relying on hope. Every FAIL
becomes exactly one rework task; the reviewer never triggers a full
regeneration.

> **Fix — contract-adherence checking is now a named, explicit reviewer
> responsibility (not just an assumption), directly mitigating the
> cross-model style-consistency risk.**

4.7 Optional Visual/Browser Verification

For tasks that touch a UI, an optional browser-automation pass (headless
browser, screenshot capture, accessibility scan) gives the Global
Reviewer a stronger form of evidence than unit tests alone. Explicitly a
Phase 6+ stretch item — not on the MVP floor.

4.8 Efficiency Telemetry

Tokens, tool calls, wall-clock time, and recovery-loop counts are logged
per task and per role from the first prototype, not bolted on for the
demo. This is the data source for the Section 3.2 hero artifact — the
single before/after run is built directly on top of this telemetry, not
a separate effort.

4.9 Style-Consistency Check (new)

A shared style/convention guide (naming, formatting, project-specific
idioms) is passed into every worker's task contract, and a lightweight
automated pass — linter/formatter plus the Global Reviewer's
contract-adherence check from 4.6 — runs before a worker's output is
accepted into the Integrator. This turns “different models may produce
inconsistent style” from an assumed risk into a checked one, with a
concrete gate rather than a hope.

5\. Technical Architecture / Stack

5.1 Core Runtime

|                      |                                                                                                                                                     |                                                                                                                                        |
|----------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------|
| **Layer**            | **Choice**                                                                                                                                          | **Why**                                                                                                                                |
| Language / runtime   | Python 3.12+                                                                                                                                        | Fast to iterate; strong ecosystem for tooling, tests, and LLM orchestration.                                                           |
| Data models          | Pydantic                                                                                                                                            | MasterSpecification and TaskContract shapes need to be strict and validated early — everything downstream depends on this being right. |
| Scheduler            | Readiness-based ready-queue by default (4.2); topological sort only if the graph complexity demands it                                              | Releases a task the moment its dependency artifacts exist; matched to a 3–4-worker scale instead of over-built.                        |
| Isolation per worker | Default: plain filesystem copy / isolated folder per worker — works on any directory. Git worktrees used only when the target has a .git directory. | Makes offline, no-git operation the default path rather than an edge case; git worktrees remain an optimization when available.        |

> **Fix — filesystem isolation (not git worktrees) is now the documented
> default, so Orbit's core loop never assumes version control exists. A
> pre-edit snapshot of the working tree is kept per task so a diff can
> always be produced as evidence, git or not.**

5.2 Model & Tool Layer

|                              |                                                                                                                                    |
|------------------------------|------------------------------------------------------------------------------------------------------------------------------------|
| **Tool / Model**             | **Best for**                                                                                                                       |
| OpenCode                     | Default coding agent — provider-agnostic, MIT-licensed, works with any free/local model.                                           |
| Aider                        | Git-native, surgical multi-file edits with auto-commit, used when a target has git; filesystem-diff fallback used when it doesn't. |
| Ollama (local)               | Zero rate limits, fully offline — the default for Stage A (4.3) and the required path for an offline/no-network demo.              |
| OpenRouter / Groq free tiers | Stage B per-role routing, added once the pipeline is proven on Ollama/one model.                                                   |
| Claude Opus (paid, targeted) | Reserved for the Architect and Reviewer roles once Stage B is enabled — the reasoning-heavy, judged-hardest parts of the pipeline. |

Cost-control and sequencing rule of thumb: prove the whole pipeline on
one local/free model first (Stage A); only then reserve paid or
heterogeneous routing for the Architect's planning call and the
Reviewer's final pass.

5.3 Verification & Telemetry

|                               |                                                                                                                                                                               |
|-------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Component**                 | **Choice**                                                                                                                                                                    |
| Test / lint / type-check      | Repository's own existing suite (pytest / jest / eslint / mypy, as applicable).                                                                                               |
| Diff evidence, git-optional   | git diff when .git is present; otherwise a diff against the pre-edit filesystem snapshot taken by the Integrator (5.1).                                                       |
| Optional browser verification | Playwright (Python) + axe-core for accessibility scanning — Phase 6+ stretch.                                                                                                 |
| Telemetry                     | Structured JSON/markdown log per task — tasks, parallel count, recovery loops, review findings, token/time counts; this is the data source for the Section 3.2 hero artifact. |

6\. Non-Goals

- Orbit is not a chatbot or thin UI wrapper on a single model API.

- Orbit does not require git, a remote host, or network access — offline
  operation against any local directory is a stated requirement, not an
  edge case.

- Orbit does not require or prioritize a graphical dashboard — a
  CLI/script-driven run with a structured evidence report satisfies the
  deliverable.

- Orbit does not claim to have invented multi-agent coding, and is not
  positioned as a full Cursor / Antigravity replacement.

- Orbit does not attempt greenfield, from-scratch application
  scaffolding as its primary use case.

- Orbit's Global Reviewer never triggers full regeneration on failure —
  only the smallest targeted rework task.

- Orbit does not build topological-sort scheduling infrastructure unless
  the task graph's complexity actually requires it beyond the default
  ready-queue.

7\. Success Metrics

- Correctness: **Verified (Synthetic Benchmark)**
  - Evidence: [orbit_runs/demo_telemetry.json](file:///Users/aryanmudgal/Desktop/Orbit%20harness/orbit_runs/demo_telemetry.json) — Both `test_targets/plain_dir` and `test_targets/git_repo` runs reached final PASS after targeted recovery.
  - **Top Open Risk**: Evaluated strictly on synthetic test targets because the official hackathon repository and issue set remain unavailable; real-repo validation is pending release of the evaluation benchmark.

- Evidence quality: **Verified**
  - Evidence: Full console transcripts, [orbit_runs/demo_telemetry.json](file:///Users/aryanmudgal/Desktop/Orbit%20harness/orbit_runs/demo_telemetry.json), and [orbit_runs/reviewer_raw_response.json](file:///Users/aryanmudgal/Desktop/Orbit%20harness/orbit_runs/reviewer_raw_response.json) providing complete unified diffs, structured per-requirement PASS/FAIL evaluation tables, and granular LLM call logs with token and endpoint telemetry.

- Recovery effectiveness: **Verified**
  - Evidence: Both runs' `Targeted Recovery Loops Run` (=1) and Reviewer-triggered rework tasks. Closed-loop recovery is demonstrated at both the test-failure level (Integrator syntax/logic failure recovery) and requirement-review level (Reviewer contract/test gap recovery), proven across both git (`test_targets/git_repo`) and non-git (`test_targets/plain_dir`) targets.

- Efficiency: **Open (Call Counts Observed)**
  - Evidence: [orbit_runs/demo_telemetry.json](file:///Users/aryanmudgal/Desktop/Orbit%20harness/orbit_runs/demo_telemetry.json) records per-run call counts: 5 LLM calls for `plain_dir` (including 1 reviewer rework loop) and 4 LLM calls for `git_repo`. Multi-run stability benchmark comparison (3 consecutive runs) remains open pending execution on the official hackathon benchmark set.

- Routing impact: **Open**
  - Evidence: Stage B per-role routing is implemented in [config/router.py](file:///Users/aryanmudgal/Desktop/Orbit%20harness/config/router.py) and unit tested in [tests/test_phase7_stage_b.py](file:///Users/aryanmudgal/Desktop/Orbit%20harness/tests/test_phase7_stage_b.py), but quantitative delta comparison between uniform Stage A and heterogeneous Stage B across a shared benchmark set is not yet measured.

- Offline capability: **Verified**
  - Evidence: Explicitly verified in this session with empty cloud keys (`OPENROUTER_API_KEY=""`, `GROQ_API_KEY=""`, `OPENAI_API_KEY=""`, `ANTHROPIC_API_KEY=""`), confirmed by `MATCHING_ENV_VARS: []`, and backed by [orbit_runs/demo_telemetry.json](file:///Users/aryanmudgal/Desktop/Orbit%20harness/orbit_runs/demo_telemetry.json) showing 100% of LLM calls dispatched strictly to local Ollama (`http://localhost:11434`, `deepseek-coder-v2:latest`) with zero external network calls.

8\. Build Plan / Milestones

Phased, not hour-by-hour. Phase 4 is the committed MVP floor (3.2) — a
demo is rehearsed for that state before any Phase 5+ work begins, so
there is always a working fallback.

|                                        |                                                                                                                                                                                                                           |
|----------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Phase**                              | **Deliverable**                                                                                                                                                                                                           |
| 0 — Setup                              | Repo/local directory, coding agent, and Ollama (or another local/free model) in place as the Stage A default; test target includes at least one non-git directory. Every phase ends in one commit.                        |
| 1 — Seed                               | MasterSpecification and TaskContract data models written first; filesystem indexer built to work without git.                                                                                                             |
| 2 — Master Architect                   | One (or two-step) LLM call: requirement → spec + dependency graph + task contracts, with canned-input unit tests.                                                                                                         |
| 3 — Tool Gateway                       | File ops, exec, test runner, code search — scoped per worker via filesystem isolation by default; MAX_TOOL_CALLS / MAX_RETRIES / MAX_RECOVERY_LOOPS / TEST_TIMEOUT_SEC guardrails in from the start.                      |
| 4 — Workers + Scheduler (MVP FLOOR)    | 3 worker roles (Database, Backend, Frontend) on one Stage-A model; readiness-based ready-queue runner. Hero-artifact baseline run captured here. Demo rehearsed for this state.                                           |
| 5 — Integration + Recovery             | Integrator merges workspaces with pre-edit snapshot for diffing; failing tests become FailureContext → targeted rework → re-verify, using the 4.5 tie-breaker.                                                            |
| 6 — Global Reviewer                    | Requirement + architecture + evidence → per-requirement PASS/FAIL table, plus contract-adherence check (4.6/4.9); every FAIL → exactly one rework task.                                                                   |
| 7 — Stage B Routing + Telemetry + Demo | Per-role model routing enabled only now; task/recovery/review log surfaced; demo rehearsed as one prompt → task graph → parallel workers → forced failure → targeted recovery → final verdict, including the offline run. |

9\. Risks — Status After Revisions

|                                                                                          |                                                                                                                                                                                                |
|------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Risk**                                                                                 | **Status / Mitigation**                                                                                                                                                                        |
| Contract-schema plumbing between roles eats build time.                                  | Open — keep TaskContract and MasterSpecification minimal from Phase 1; resist adding fields until a real need appears.                                                                         |
| Longer pipeline has more failure points than a single-agent tool.                        | Reduced — MVP floor locked at Phase 4; worker count capped at 3–4; scheduler kept to a ready-queue by default (4.2).                                                                           |
| Different models per role produce inconsistent style/conventions.                        | Addressed — 4.9 adds a named style-consistency gate (shared guide + linter + reviewer contract-adherence check), and Stage B routing is delayed until the pipeline is proven (4.3).            |
| Recovery loop runs indefinitely on a genuinely unsolvable task.                          | Addressed — MAX_RECOVERY_LOOPS hard cap; explicit ordered tie-breaker for “best partial result” defined in 4.5, not left ambiguous.                                                            |
| Free-tier model limits change without notice.                                            | Reduced — Stage A default runs entirely on local Ollama, removing dependency on free-tier availability for the MVP floor; Stage B free-tier/paid routing is optional upside, not a dependency. |
| Multiple providers make debugging ambiguous (harness bug vs. provider/rate-limit issue). | Addressed — new risk, resolved by the Stage A / Stage B split in 4.3: the full pipeline is proven on one model before heterogeneous routing is introduced.                                     |
| No pre-committed evidence artifact to show a judge.                                      | Addressed — the Section 3.2 hero artifact (baseline vs. Orbit token/time delta) is captured at Phase 4, guaranteeing one strong exhibit exists even if later phases don't land.                |
| Time spent on positioning/competitive-comparison content that doesn't affect scoring.    | Addressed — Section 2.2 cut to one paragraph; no further writing or build time allocated to it.                                                                                                |

*Orbit — Autonomous Multi-Agent Software Engineering Harness \| PRD v1.1
(Revised)*
