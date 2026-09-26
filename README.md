# Orbit — Autonomous Multi-Agent Software Engineering Harness

> PRD v1.1 | LCC × DevClub AI Coding Harness Hackathon

## What is Orbit?

Orbit is a self-hosted, model-agnostic control plane that wraps a foundation
model to produce a reliable, evidence-generating software engineer. It operates
against **any local file tree** — git repository or plain directory — with no
dependency on a remote host or network access.

## Repository Layout

```
orbit-harness/
├── scheduler.py            ← top-level entry point / ready-queue runner
├── config/
│   └── routing.yaml        ← model routing + guardrail config
├── models/
│   ├── __init__.py
│   └── spec.py             ← MasterSpecification, TaskContract, FailureContext, ReviewVerdict
├── indexer/
│   ├── __init__.py
│   └── indexer.py          ← git-agnostic filesystem indexer (Phase 1)
├── architect/
│   ├── __init__.py
│   └── architect.py        ← Master Architect: requirement → spec + task graph (Phase 2)
├── gateway/
│   ├── __init__.py
│   └── gateway.py          ← Tool Gateway with guardrails (Phase 3)
├── workers/
│   ├── __init__.py
│   └── base_worker.py      ← BaseWorker + Database/Backend/Frontend stubs (Phase 4)
├── integrator/
│   ├── __init__.py
│   └── integrator.py       ← merge workspaces + recovery loop (Phase 5)
├── reviewer/
│   ├── __init__.py
│   └── reviewer.py         ← Global Reviewer: PASS/FAIL table + targeted rework (Phase 6)
├── telemetry/
│   ├── __init__.py
│   └── collector.py        ← token counts, wall-clock time, tool-call log (Phase 7)
└── test_targets/
    ├── git_repo/           ← Target A: normal git repo (exercises git-aware path)
    │   ├── .git/
    │   ├── src/math_utils.py
    │   └── tests/test_math_utils.py
    └── plain_dir/          ← Target B: no .git (exercises offline/no-git path)
        ├── src/greeter.py
        └── tests/test_greeter.py
```

## Local Model

| Model | Provider | Status |
|---|---|---|
| `deepseek-coder-v2:latest` (15.7B) | Ollama | ✅ pulled, Stage A default |

## Usage (Phase 4+)

```bash
python scheduler.py --target test_targets/plain_dir --task "add a multiply function"
python scheduler.py --target test_targets/git_repo  --task "add a multiply function"
```

## Build Phases

| Phase | Deliverable | Status |
|---|---|---|
| **0** | Repo skeleton + Ollama + test targets | ✅ done |
| 1 | Data models + filesystem indexer | 🔜 |
| 2 | Master Architect LLM call | 🔜 |
| 3 | Tool Gateway with guardrails | 🔜 |
| 4 | Workers + Scheduler — **MVP Floor** | 🔜 |
| 5 | Integration + Recovery loop | 🔜 |
| 6 | Global Reviewer | 🔜 |
| 7 | Stage B routing + Telemetry + Demo | 🔜 |
