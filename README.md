# EvalGuard

EvalGuard is a reproducible evaluation harness for tool-using AI agents in the
wholly fictional ParcelDesk support domain. Phases 0–2 provide the runtime and
correctness foundation. Phase 3 adds an immutable 25-case queued suite,
actor-scoped tools, six deterministic grader families, deliberate fake-agent
defects, and aggregate experiment lifecycle. The full automated suite and live
25-case runtime smoke pass. Phase 3 remains uncommitted and unpushed, and no Phase 3
remote-CI result is claimed.

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
- Two published immutable suite versions. `parceldesk-refunds@1.0.0` preserves the
  original `eligible-refund` case and hashes;
  `parceldesk-refunds@2.0.0` contains exactly 25 independently authored cases: 7
  normal successes, 5 ineligible requests, 5 policy boundaries, 4
  authorization/privacy cases, and 4 duplicate-or-recovery cases. PostgreSQL
  trigger row locks reject edits, deletion, moves, or appended cases after
  publication.
- Typed `lookup_order`, `issue_refund`, `update_shipping_address`, and
  `escalate_ticket` tools with validated, closed Pydantic object contracts and a
  deterministic fake provider.
- Execution-scoped composite ownership for support tickets, including a PostgreSQL
  requirement that the ticket customer own the referenced order. Address changes
  are allowed only for `pending`/`processing` orders; refunds at or above 50,000
  cents require a `high_value_refund` escalation.
- Trusted requester identity additionally scopes all four tool lookups, mutations,
  and cached replay. Cross-customer attempts persist safe failure evidence without
  disclosing or mutating the other customer's order; privacy grading also detects
  protected string and numeric values embedded in free text. String matches use
  token boundaries, and grouped numeric forms such as `9,125` are recognized.
- State-changing calls lock their execution/order rows, replay a call ID only for
  the exact same validated intent, converge semantic duplicates, and retain failed
  invocation plus ordered `tool_error` evidence for policy/ownership rejections
  reached after invocation start when the surrounding transaction is retained.
  Lock order is consistently execution then order. A successful exact replay adds
  one `tool_replay` event after the original outcome without another invocation or
  mutation.
- Same-key experiment submissions use a PostgreSQL advisory lock on one dedicated
  connection/session through persistence, enqueue, and dispatch outcome.
- The preserved four-criterion legacy refund grader plus six v2 grader families
  for state, trace, policy, authorization/privacy, escalation, and structured final
  resolution. V2 explicitly grades invocation outcomes, replay evidence, and
  ticket/order customer ownership.
- Eleven stable deterministic fake behavior IDs: one reference behavior and ten
  intentional defects. All ten defects have targeted PostgreSQL executions that
  demonstrate failures across all six evidence families.
- A fixed, hashed policy clock with an inclusive 30-day refund window. Boundary
  results do not depend on the machine's current date.
- Aggregate experiment lifecycle derived from all case executions: one early case
  cannot complete a 25-case experiment.
- Dependency-independent liveness and PostgreSQL/Redis-aware readiness probes.
- Six Alembic revisions (`20260818_0001` through `20260822_0006`) for the initial
  schema, suite sealing, case-move protection, publication serialization, order
  address fields, relational support tickets, and ticket/order customer ownership;
  JSON structured logging; Docker Compose; and a GitHub Actions workflow.

## Architecture

```text
POST /api/v1/experiments
  -> commit Experiment + all selected CaseExecutions to PostgreSQL
  -> enqueue each execution ID in Redis/RQ
  -> return 202 Accepted

RQ worker
  -> materialize isolated synthetic customer/order rows
  -> deterministic typed scenario plan
  -> actor-scoped production tools
  -> persist business state, tool invocations, and ordered trace/replay evidence
  -> run six deterministic evidence graders for v2
  -> reconcile the parent from every execution status
```

PostgreSQL is the durable source of truth. Redis transports work; it does not own
evaluation or ParcelDesk business state. The deterministic queued worker holds one
database transaction for the complete execution. A future network provider will
need shorter durable steps and explicit recovery behavior.

The v2 suite's typed fixtures can materialize more than one customer/order while
retaining execution isolation. Its expected policy oracle is stored independently
from the fake plan, and complete-contract hashes cover the key, title, prompt,
fixture, oracle, and plan. Frozen cross-field validation binds the resolution and
every expected state mutation to the policy target, and prevents denied outcomes
from expecting state mutation. The executor validates case/suite membership for
queued and completed executions before returning completed replay.
Generic retries, timeouts, worker recovery, and fault injection remain Phase 4 work.

## Tested local setup (PowerShell)

Prerequisites are Docker Desktop with Compose and
[uv](https://docs.astral.sh/uv/). `uv` installs/uses the repository's pinned Python
3.12 runtime, so a separate `python` command does not need to be on `PATH`.

From the repository root, this setup was exercised for Phase 3 on Windows:

```powershell
uv sync --frozen --all-groups
docker compose config --quiet
docker compose up --build -d --wait
docker compose exec -T api python -m evalguard.cli seed
```

The one-shot `migrate` service applies Alembic to the development database before
the API and worker start. The one-shot `test-db` service safely creates (when
needed) and migrates the dedicated `evalguard_test` database. The seed command now
loads/verifies both immutable versions. Automated PostgreSQL tests prove v2 returns
the same suite and 25 case IDs on replay, preserves v1, and rejects hash drift. The
live container loader also returned stable v2 identity, hash, and count across two
runs.

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
  "suite_version": "2.0.0",
  "provider": "deterministic-fake",
  "trials": 1
}
```

It returns `202` with an experiment ID and 25 execution IDs for v2. Concurrent
compatible requests using the same key converge on one experiment and one initial
enqueue per selected execution. Poll
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

The final Phase 3 tree passed 84 focused unit tests in 0.38 seconds, 38 focused
Phase 3 integration tests in 24.87 seconds, and all 158 tests in 52.51 seconds;
each run emitted the one recorded dependency warning. Coverage
includes PostgreSQL scenario execution, actor authorization, all six grader
families, all ten deliberate fake defects, RQ/API integration, aggregate lifecycle,
exact immutable suite loading, ticket/order ownership, replay evidence, and legacy
compatibility. Locked sync checked 49 packages; Ruff lint and the post-format check
passed after two files were formatted; and mypy passed 29 source files.

A disposable blank database upgraded through `20260822_0006`, reported no drift,
downgraded from `0006` to `0005`, re-upgraded to `0006`, reported no drift again,
and was dropped. The stable suite hashes were
`28289cfdcd98be852b00e451b2f52ae8a9af65a454a6642bb96acb4991a19782`
for v2 and
`aaedf653770166eb7e967352b9242509a94dd9ba3e4ee833576617c403ec6abd`
for v1. Compose configuration/build/startup passed with API, PostgreSQL, and Redis
healthy, the worker running, and both one-shot services completed successfully.
The live submission and replay returned `202` with identical identifiers, then
completed 25/25 synthetic executions with 507/507 passing grader results across six
families, 44 tool invocations, 164 trace events including one `tool_replay`, 7
refunds, and 5 tickets. A Redis-down probe kept liveness at `200`, changed readiness
to `503`, and restored readiness to `200`; no suspicious log lines remained after
restoration. These are synthetic infrastructure results, not model-quality data.

## API implemented through Phase 3

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health/live` | API-process liveness; does not depend on PostgreSQL or Redis |
| `GET` | `/health/ready` | Returns `200` only when PostgreSQL and Redis respond; otherwise `503` |
| `GET` | `/api/v1/suites` | List loaded suite versions and case counts |
| `POST` | `/api/v1/experiments` | Persist and enqueue every case in one fake-provider experiment; return `202` |
| `GET` | `/api/v1/experiments/{experiment_id}` | Retrieve experiment lifecycle and executions |
| `GET` | `/api/v1/executions/{execution_id}` | Retrieve final state (including address/tickets), trace, tool calls, and grader evidence |

A compatible replay of an `Idempotency-Key` returns the existing experiment ID. If
the original dispatch reached `enqueue_failed`, replay moves the same executions
back to queued and dispatches them again. Concurrent same-key requests are serialized
on a dedicated PostgreSQL connection through the enqueue outcome. A stable unique
RQ job ID, completed-job replay guard, exact tool-call intent checks, row locks,
semantic convergence, and PostgreSQL uniqueness prevent duplicate effects.
Enqueueing 25 Redis jobs is not one atomic operation; recovery after a process dies
with only some jobs dispatched remains Phase 4 outbox/crash-window work.

## Planned, not implemented

Phase 4 adds bounded retries, timeouts, duplicate job delivery, worker recovery,
maximum-step enforcement, and fault injection. Phase 5 compares experiments and
adds a small server-rendered report. A real-provider adapter is optional Phase 6
work and requires explicit approval for credentials and possible spending.

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
