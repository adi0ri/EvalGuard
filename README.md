# EvalGuard

EvalGuard is a reproducible evaluation harness for tool-using AI agents in the
wholly fictional ParcelDesk support domain. Phase 0 and Phase 1 implement one real,
queued vertical slice: a deterministic fake provider looks up an eligible delivered
order, issues its refund through typed tools, and is graded from persisted
PostgreSQL state and ordered trace evidence.

This result is **synthetic infrastructure validation**. It proves that the local
API, queue, worker, tools, transaction, trace, grader, and retrieval path work
together; it is not evidence of real-model quality, latency, token usage, or cost.

## Implemented now

- A Python 3.12 modular application with separate FastAPI and RQ worker processes.
- PostgreSQL-backed suites, cases, experiments, executions, customers, orders,
  refunds, tool invocations, trace events, and criterion-level grader results.
- Redis/RQ background dispatch. Experiment submission persists records before
  enqueueing and returns `202 Accepted`.
- One published/immutable suite, `parceldesk-refunds@1.0.0`, containing one
  independently authored `eligible-refund` case with stable SHA-256 definition
  hashes. PostgreSQL trigger row locks serialize publication with case membership
  changes and reject edits, deletion, moves, or appended cases after publication.
- Typed `lookup_order` and `issue_refund` tools, execution-scoped business state,
  duplicate-refund constraints, and a deterministic fake provider.
- A deterministic grader with four criteria covering the refund record, amount,
  tool order, and structured final resolution.
- Dependency-independent liveness and PostgreSQL/Redis-aware readiness probes.
- Four Alembic revisions (`20260818_0001` through `20260818_0004`) for the initial
  schema, suite sealing, case-move protection, and publication serialization; JSON
  structured logging; Docker Compose; and a GitHub Actions workflow.

## Architecture

```text
POST /api/v1/experiments
  -> commit Experiment + CaseExecution to PostgreSQL
  -> enqueue the execution ID in Redis/RQ
  -> return 202 Accepted

RQ worker
  -> materialize isolated synthetic customer/order rows
  -> deterministic fake provider
  -> lookup_order -> issue_refund
  -> persist refund, tool invocations, and ordered trace
  -> deterministically grade persisted evidence
  -> mark the execution and experiment completed
```

PostgreSQL is the durable source of truth. Redis transports work; it does not own
evaluation or ParcelDesk business state. The deterministic Phase 1 worker holds one
database transaction for the complete execution. A future network provider will
need shorter durable steps and explicit recovery behavior.

## Tested local setup (PowerShell)

Prerequisites are Docker Desktop with Compose and
[uv](https://docs.astral.sh/uv/). `uv` installs/uses the repository's pinned Python
3.12 runtime, so a separate `python` command does not need to be on `PATH`.

From the repository root, the following setup was exercised on Windows:

```powershell
uv sync --frozen --all-groups
docker compose config --quiet
docker compose up --build -d --wait
docker compose exec -T api python -m evalguard.cli seed
```

The one-shot `migrate` service applies Alembic to the development database before
the API and worker start. The one-shot `test-db` service safely creates (when
needed) and migrates the dedicated `evalguard_test` database. The seed command is
idempotent: rerunning it returns the same suite ID, case ID, and definition hash,
and rejects hash drift for an existing version.

Check the running API from PowerShell:

```powershell
Invoke-RestMethod -Uri 'http://localhost:8000/health/live'
Invoke-RestMethod -Uri 'http://localhost:8000/health/ready'
```

The experiment endpoint accepts this JSON shape and an optional
`Idempotency-Key` header:

```json
{
  "suite_name": "parceldesk-refunds",
  "suite_version": "1.0.0",
  "provider": "deterministic-fake",
  "trials": 1
}
```

It returns `202` with experiment and execution IDs. Poll
`GET /api/v1/experiments/{experiment_id}`, then retrieve the stored customer,
order, refund, tool invocations, ordered trace, structured resolution, and grader
results from `GET /api/v1/executions/{execution_id}`.

## Checks

With PostgreSQL and Redis running through Compose, the local verification path uses
an explicit test-database preparation step before pytest:

```powershell
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run alembic check
docker compose run --build --rm test-db
uv run pytest
```

`prepare-test-db` accepts only PostgreSQL URLs whose database name ends in
`_test`. pytest additionally refuses destructive cleanup unless it is in test mode,
the database ends in `_test`, and Redis uses a non-development database/queue. The
default isolated targets are PostgreSQL `evalguard_test`, Redis database 15, and
queue `evalguard-tests`.

The 2026-08-18 verification checked 49 locked packages; Ruff lint passed; Ruff
format checked 50 files; mypy succeeded on 27 source files; and pytest passed all
32 collected tests in 9.73 seconds. Pytest emitted one dependency-originated
`StarletteDeprecationWarning` from FastAPI's `TestClient`; it did not fail the run.
See `PROGRESS.md` for the exact command evidence, clean-database migration check,
live API/worker smoke result, dependency-failure probe, and final repository audit.

## API implemented in Phase 1

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health/live` | API-process liveness; does not depend on PostgreSQL or Redis |
| `GET` | `/health/ready` | Returns `200` only when PostgreSQL and Redis respond; otherwise `503` |
| `GET` | `/api/v1/suites` | List loaded suite versions and case counts |
| `POST` | `/api/v1/experiments` | Persist and enqueue one fake-provider experiment; return `202` |
| `GET` | `/api/v1/experiments/{experiment_id}` | Retrieve experiment lifecycle and executions |
| `GET` | `/api/v1/executions/{execution_id}` | Retrieve final state, trace, tool calls, and grader evidence |

A compatible replay of an `Idempotency-Key` returns the existing experiment ID. If
the original dispatch reached `enqueue_failed`, replay moves the same execution
back to queued and dispatches it again. A stable unique RQ job ID, completed-job
replay guard, and PostgreSQL refund/tool uniqueness keep that Phase 1 effect from
being duplicated.

## Planned, not implemented

Phase 2 adds `update_shipping_address`, `escalate_ticket`, support-ticket state,
and stronger isolation/idempotency hardening. Later phases expand to the target 25
original scenarios, add policy/authorization/escalation and recovery graders,
exercise retries and duplicate delivery, compare experiments, and add a small
server-rendered report. A real-provider adapter is optional Phase 6 work and
requires explicit approval for credentials and possible spending.

There is currently no real model, UI, report, summary/retry API, multi-trial run,
token/cost accounting, or claim of model performance.

## Project records

- `PROJECT_SPEC.md` — product contract, invariants, scope, and definitions of done
- `PLAN.md` — checked implementation/verification work and later backlog
- `PROGRESS.md` — commands actually run, exact results, warnings, and next action
- `DECISIONS.md` — architectural choices and tradeoffs
- `docs/LEARNING_NOTES.md` — interview-ready explanations of the implemented ideas
- `PROVENANCE.md` — independent origin, public dependencies/references, and result
  labeling rules
