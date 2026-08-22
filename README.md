# EvalGuard

EvalGuard is a reproducible evaluation harness for tool-using AI agents in the
wholly fictional ParcelDesk support domain. Phases 0–2 implement the runtime
foundation, one complete queued evaluation, all four typed domain tools, relational
support state, and correctness hardening. The deterministic queued slice looks up
an eligible delivered order, issues its refund, and is graded from persisted
PostgreSQL state and ordered trace evidence.

This result is **synthetic infrastructure validation**. It proves that the local
API, queue, worker, tools, transaction, trace, grader, and retrieval path work
together; it is not evidence of real-model quality, latency, token usage, or cost.

## Implemented now

- A Python 3.12 modular application with separate FastAPI and RQ worker processes.
- PostgreSQL-backed suites, cases, experiments, executions, customers, orders,
  shipping addresses, refunds, support tickets, tool invocations, trace events, and
  criterion-level grader results.
- Redis/RQ background dispatch. Experiment submission persists records before
  enqueueing and returns `202 Accepted`.
- One published/immutable suite, `parceldesk-refunds@1.0.0`, containing one
  independently authored `eligible-refund` case with stable SHA-256 definition
  hashes. PostgreSQL trigger row locks serialize publication with case membership
  changes and reject edits, deletion, moves, or appended cases after publication.
- Typed `lookup_order`, `issue_refund`, `update_shipping_address`, and
  `escalate_ticket` tools with validated, closed Pydantic object contracts and a
  deterministic fake provider.
- Execution-scoped composite ownership for support tickets. Address changes are
  allowed only for `pending`/`processing` orders; refunds at or above 50,000 cents
  require a `high_value_refund` escalation.
- State-changing calls lock their execution/order rows, replay a call ID only for
  the exact same validated intent, converge semantic duplicates, and retain failed
  invocation plus ordered `tool_error` evidence for policy/ownership rejections
  reached after invocation start when the surrounding transaction is retained.
- Same-key experiment submissions use a PostgreSQL advisory lock on one dedicated
  connection/session through persistence, enqueue, and dispatch outcome.
- A deterministic grader with four criteria covering the refund record, amount,
  tool order, and structured final resolution.
- Dependency-independent liveness and PostgreSQL/Redis-aware readiness probes.
- Five Alembic revisions (`20260818_0001` through `20260822_0005`) for the initial
  schema, suite sealing, case-move protection, publication serialization, order
  address fields, and relational support tickets; JSON structured logging; Docker
  Compose; and a GitHub Actions workflow.

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
evaluation or ParcelDesk business state. The deterministic queued worker holds one
database transaction for the complete execution. A future network provider will
need shorter durable steps and explicit recovery behavior.

Phase 2 extends the production `ToolRuntime` with address change and escalation
paths and exposes their relational state through execution retrieval. The single
published queued fake-provider case still selects only `lookup_order` followed by
`issue_refund`; Phase 3 will add the immutable scenarios and deterministic graders
that select the other tools. This boundary keeps Phase 2 from claiming scenario or
model-quality coverage that has not been built.

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

It returns `202` with experiment and execution IDs. Concurrent compatible requests
using the same key converge on one experiment and initial enqueue. Poll
`GET /api/v1/experiments/{experiment_id}`, then retrieve the stored customer,
order (including a structured shipping address when present), refund, support
ticket, tool invocations, ordered trace, structured resolution, and grader results
from `GET /api/v1/executions/{execution_id}`.

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

The 2026-08-22 Phase 2 verification checked 49 locked packages in 4 ms; Ruff lint
passed; Ruff format checked 52 files; mypy succeeded on 27 source files; and pytest
passed all 49 collected tests in 15.85 seconds. Pytest emitted one dependency-originated
`StarletteDeprecationWarning` from FastAPI's `TestClient`; it did not fail the run.
The disposable migration check upgraded a clean database from `0001` through
`0005`, downgraded `0005` to `0004`, re-upgraded, and reported no drift. Compose
build/up/wait succeeded; the live smoke returned liveness/readiness `200`, accepted
a submission with `202`, and completed one baseline execution with one refund,
seven trace events, and four passing graders. Its null address and zero tickets are
expected because the queued fake selects only the Phase 1 tools. The dependency-down
probe was not rerun in Phase 2; the recorded Phase 1 probe remains valid evidence.
See `PROGRESS.md` for exact command output and the complete runtime record.

## API implemented through Phase 2

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health/live` | API-process liveness; does not depend on PostgreSQL or Redis |
| `GET` | `/health/ready` | Returns `200` only when PostgreSQL and Redis respond; otherwise `503` |
| `GET` | `/api/v1/suites` | List loaded suite versions and case counts |
| `POST` | `/api/v1/experiments` | Persist and enqueue one fake-provider experiment; return `202` |
| `GET` | `/api/v1/experiments/{experiment_id}` | Retrieve experiment lifecycle and executions |
| `GET` | `/api/v1/executions/{execution_id}` | Retrieve final state (including address/tickets), trace, tool calls, and grader evidence |

A compatible replay of an `Idempotency-Key` returns the existing experiment ID. If
the original dispatch reached `enqueue_failed`, replay moves the same execution
back to queued and dispatches it again. Concurrent same-key requests are serialized
on a dedicated PostgreSQL connection through the enqueue outcome. A stable unique
RQ job ID, completed-job replay guard, exact tool-call intent checks, row locks,
semantic convergence, and PostgreSQL uniqueness prevent duplicate effects.

## Planned, not implemented

Phase 3 expands to the target 25 original scenarios and adds deterministic
policy/authorization/escalation graders plus intentionally defective fake behavior.
Later phases exercise retries and duplicate delivery, compare experiments, and add
a small server-rendered report. A real-provider adapter is optional Phase 6 work
and requires explicit approval for credentials and possible spending.

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
