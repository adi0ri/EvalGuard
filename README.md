# EvalGuard

EvalGuard is a reproducible evaluation harness for tool-using AI agents in the
wholly fictional ParcelDesk support domain. Phases 0–2 provide the runtime and
correctness foundation. Phase 3 adds an immutable 25-case queued suite,
actor-scoped tools, six deterministic grader families, deliberate fake-agent
defects, and aggregate experiment lifecycle. Phase 4 now implements a PostgreSQL
transactional outbox, durable attempts/generations/leases, bounded execution
retries and RQ timeouts, exact tool-step enforcement, worker recovery, and typed
development/test fault plans. Phase 4 local acceptance is complete: all 189 tests,
the revision `0007` round trip, Compose startup, deterministic live flows, a real
RQ timeout, an actual worker SIGKILL, and Redis-outage recovery passed. Its local
implementation commit is `e276ba5`; local `main` remains ahead of `origin/main` at
`6655e79`, and Phase 4 remains unpushed with no remote-CI result
claimed. The recorded Phase 3 automated suite and live 25-case runtime smoke also
pass. Phase 3 commits `083808c` and `6655e79` are pushed; `origin/main` remains at
`6655e79`. No Phase 3 remote-CI result is claimed.

The recorded deterministic-fake results are **synthetic infrastructure
validation**. The Phase 3 and Phase 4 evidence proves that the local API, queue,
worker, tools, transaction, trace, grader, recovery, and retrieval paths worked
together; it is not evidence of real-model quality, latency, token usage, or cost.

## Implemented now

- A Python 3.12 modular application with separate FastAPI and RQ worker processes.
- PostgreSQL-backed suites, cases, experiments, executions, customers, orders,
  shipping addresses, refunds, support tickets, tool invocations, trace events, and
  criterion-level grader results.
- Redis/RQ background dispatch backed by a PostgreSQL transactional outbox.
  Experiment submission atomically persists executions and initial dispatch
  intents, makes a best-effort enqueue pass, and returns `202 Accepted` even when
  Redis publication must be retried later.
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
  connection/session through durable selection/creation. Phase 4 releases that
  lock before best-effort Redis I/O because compatible replay and worker/CLI
  reconciliation converge on the same outbox rows.
- Persisted experiment policy controls execution attempts (default 3, allowed
  1–5), per-job RQ timeout (default 30 seconds, allowed 1–300), tool steps (default
  8, allowed 1–100), and retry backoff (default 1 second, allowed 0–300). Execution
  retries are bounded; dispatch publication remains pending with capped backoff
  until Redis accepts it.
- Durable attempt and dispatch histories expose generation/job identity, leases,
  step counts, retryability, safe failure kinds, timestamps, and recovery traces.
  Stale, busy duplicate, and terminal deliveries do not invoke provider or tools.
- Typed deterministic fault plans can target one selected case only in development
  or test. They exercise transient pre-provider/post-tool failures, timeout,
  permanent failure, and worker interruption without becoming production behavior
  or immutable suite content.
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
- Seven Alembic revisions (`20260818_0001` through `20260822_0007`) for the initial
  schema, suite sealing, case-move protection, publication serialization, order
  address fields, relational support tickets, ticket/order customer ownership, and
  Phase 4 resilience state; JSON structured logging; Docker Compose; and a GitHub
  Actions workflow.

Revision `20260822_0007` preserves the old queue policy by backfilling existing
experiments with a 300-second timeout, requeues legacy `running` and `retrying`
executions, and creates pending generation-1 outbox rows only for queued or legacy
`enqueue_failed` work. New experiments use the Phase 4 application default of 30
seconds.

## Architecture

```text
POST /api/v1/experiments
  -> commit Experiment + CaseExecutions + generation-1 dispatch rows to PostgreSQL
  -> best-effort reconcile current pending rows to Redis/RQ
  -> return 202 Accepted

RQ worker
  -> claim one generation as a durable attempt + lease
  -> materialize isolated synthetic customer/order rows
  -> deterministic typed scenario plan
  -> actor-scoped production tools
  -> persist business state, tool invocations, and ordered trace/replay evidence
  -> run six deterministic evidence graders for v2
  -> reconcile the parent from every execution status

Worker maintenance or bounded CLI reconciliation
  -> recover expired running attempts
  -> publish due current-generation dispatch rows
  -> audit current dispatched-but-unclaimed RQ jobs after 30 seconds
```

PostgreSQL is the durable source of truth. Redis transports work; it does not own
evaluation or ParcelDesk business state. A crash after Redis accepts a job but
before the outbox update is safe: the next pass accepts only an existing job whose
queue, function, positional arguments, empty keyword arguments, configured timeout,
and runnable status exactly match. Generation 1 also accepts its pre-Phase-4 one-
argument identity when the persisted timeout policy is 300 seconds; a new
30-second experiment rejects that legacy shape. The worker repeats the persisted
dispatch/timeout check before claim. A mismatched collision consumes no execution
attempt and reconciliation keeps it pending with a safe error. Reconcilers process
one locked row/transaction at a time; ordinary sweeps
skip locked rows and stop after a transport failure so completed publications stay
committed.

Maintenance also rechecks a current dispatched row after 30 seconds when its
execution is still queued, retrying, or legacy `enqueue_failed` and no attempt was
claimed. An exact queued, started, deferred, or scheduled RQ job remains untouched.
A missing or exact-but-unrunnable job becomes one generation/job-fenced interrupted
attempt, then advances to a new generation or fails at attempt exhaustion. An
identity mismatch never consumes an execution attempt.

The deterministic queued worker still holds one database transaction for the
complete provider/tool/grader execution. A failed attempt rolls back synthetic
fixture state, business effects, invocations, ordinary trace, grader rows, and
resolution together; a separate transaction persists attempt failure and retry or
terminal evidence. Before any provider/tool work, the executor locks the execution
again and revalidates its attempt number, generation, and `running` status, fencing
a paused worker whose claim recovery already superseded. A future network provider
will need shorter durable steps and explicit resume semantics.

The v2 suite's typed fixtures can materialize more than one customer/order while
retaining execution isolation. Its expected policy oracle is stored independently
from the fake plan, and complete-contract hashes cover the key, title, prompt,
fixture, oracle, and plan. Frozen cross-field validation binds the resolution and
every expected state mutation to the policy target, and prevents denied outcomes
from expecting state mutation. The executor validates case/suite membership for
queued and completed executions before returning completed replay.
Phase 4 operational policies and fault plans do not change either published suite
or case hash.

## Phase 4 resilience semantics

Execution retry and queue publication are deliberately separate:

- A provider/tool attempt retries only for an explicitly retryable transient,
  timeout, or worker-interruption classification and only while its number is below
  `max_attempts`. Its delay starts at `retry_backoff_seconds`, doubles by failed
  attempt, and caps at 300 seconds. Permanent, maximum-step, and unexpected errors
  fail without another attempt.
- Each retry/recovery increments `dispatch_generation`; generation 1 keeps the
  original `case-execution-{uuid}` job ID and later jobs use a `-gN` suffix. An
  execution-row lock makes stale, busy duplicate, and terminal delivery safe.
- Each RQ job receives the persisted timeout explicitly. RQ documents
  [`job_timeout`](https://python-rq.org/docs/jobs/) as the maximum runtime before a
  job is interrupted and failed. EvalGuard records a caught timeout as retryable,
  then re-raises it for RQ; an exact
  [work-horse termination handler](https://python-rq.org/docs/exceptions/) and an
  expired PostgreSQL lease provide immediate and periodic interruption recovery.
- PostgreSQL-to-Redis publication is not capped by `max_attempts`. A pending outbox
  row records each failed publish, delays its next ordinary sweep with a multiplier
  capped at 32, and remains accepted work until an exact job is present. Worker
  startup and RQ's documented
  [maintenance cycle](https://python-rq.org/docs/workers/) run bounded recovery;
  the `evalguard reconcile` CLI exposes the same stale-attempt-then-dispatch order.
- API creation/replay uses an initial-work override only for pending executions
  still `queued` (or legacy `enqueue_failed`). A `retrying` execution remains gated
  by `available_at`. Ordinary maintenance additionally audits dispatched-but-
  unclaimed current generations after 30 seconds as described above.

A tool step is exactly one request that enters `lookup_order`, `issue_refund`,
`update_shipping_address`, or `escalate_ticket`. It is consumed before locking or
cached-invocation lookup, so successful calls, policy/ownership errors, semantic
no-ops, cached-success replays, and persisted-error replays all count. Typed input
that fails validation before entering a method does not count. Calls 1 through N
are allowed; request N+1 is counted and produces the durable failure kind
`maximum_tool_steps` before creating an invocation, trace event, or business
mutation.

The optional `fault_injection` request selects one suite case plus a typed mode and
`fail_attempts`. It is accepted only when `ENVIRONMENT` is `development` or `test`.
It controls the in-repository deterministic fake and is synthetic infrastructure
validation—not a chaos facility for production and not evidence of model
reliability. The API rejects it and the executor refuses even a persisted plan in
other environments. No Phase 4 fault plan, attempt, dispatch, or policy field
participates in the immutable v1/v2 definition hashes.

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
  "trials": 1,
  "max_attempts": 3,
  "job_timeout_seconds": 30,
  "max_tool_steps": 8,
  "retry_backoff_seconds": 1
}
```

An optional `fault_injection` object has `case_key`, one of the five typed modes
listed above, and `fail_attempts` (1–5); non-development/test environments reject
it. It is intentionally omitted from the ordinary request.

The endpoint returns `202` with an experiment ID and 25 execution IDs for v2 after
the durable outbox commit, not after all Redis calls succeed. Concurrent compatible
requests using the same key converge on one experiment and one outbox row per
execution generation. Poll
`GET /api/v1/experiments/{experiment_id}`, then retrieve the stored customer,
order (including a structured shipping address when present), refund, support
ticket, tool invocations, ordered trace, structured resolution, and grader results
from `GET /api/v1/executions/{execution_id}`. Experiment retrieval also exposes the
resilience policy and attempt counts; execution retrieval adds current generation,
lease/failure summary, ordered attempts, and dispatch history.

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

The final Phase 4 tree resolved 50 packages offline in 2 ms, checked 49 locked
packages in 8 ms, passed Ruff lint, found 67 files already formatted, passed mypy
on 31 source files, and passed all 189 tests in 135.79 seconds with the one known
Starlette `TestClient`/httpx deprecation warning. `git diff --check` was clean apart
from expected LF-to-CRLF notices. The final count rose by three PostgreSQL cases:
two parametrized legacy `enqueue_failed` missing/terminal delivery recoveries and
one paused-worker claim-fence regression.

The disposable `evalguard_phase4_final_migration_20260822` database verified the
legacy 300-second timeout, active-state normalization/outbox backfill, completed-
row preservation, downgrade/re-upgrade behavior, and no Alembic drift before it was
dropped. The normal database remained untouched. Final Compose config/build/wait,
head `20260822_0007`, schema check, repeated immutable seeding, and two no-work CLI
reconciliation passes succeeded.

The final live image returned liveness/readiness `200`; its baseline v2 run
completed 25/25 executions with 25 attempts, 25 dispatches, 507/507 graders, 44
tool calls, 7 refunds, and 5 tickets. Same-key replay kept the same experiment and
25 attempts. Separate v1 runs proved transient recovery, a real one-second RQ
timeout, actual worker-SIGKILL recovery, and automatic recovery of a `202`-accepted
pending outbox after Redis restoration, each without a duplicate refund. These are
synthetic infrastructure checks. Implementation commit `e276ba5` records verified
Phase 4. It remains unpushed and no remote-CI result is claimed; publication
requires explicit approval.

## API implemented through Phase 4

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/health/live` | API-process liveness; does not depend on PostgreSQL or Redis |
| `GET` | `/health/ready` | Returns `200` only when PostgreSQL and Redis respond; otherwise `503` |
| `GET` | `/api/v1/suites` | List loaded suite versions and case counts |
| `POST` | `/api/v1/experiments` | Persist experiment/executions/outbox and best-effort dispatch; return `202` |
| `GET` | `/api/v1/experiments/{experiment_id}` | Retrieve lifecycle, retry/timeout/step policy, and execution attempt counts |
| `GET` | `/api/v1/executions/{execution_id}` | Retrieve state, attempts, dispatches, lease/failure summary, trace, tool calls, and graders |

A compatible replay of an `Idempotency-Key` returns the existing experiment ID and
triggers targeted reconciliation for its durable executions; a different
resilience policy or fault plan conflicts. Concurrent same-key requests are
serialized on a dedicated PostgreSQL connection through durable selection/creation,
then Redis work happens after lock release. The outbox, generation-aware RQ job ID,
exact job-identity check, attempt claim lock, exact tool-call intent checks,
semantic convergence, and PostgreSQL uniqueness make partial or duplicate dispatch
converge. The API's initial-work override applies only to queued/legacy
`enqueue_failed` executions; `retrying` work remains `available_at` gated.

## Planned, not implemented

Phase 5 has not started. It compares experiments and adds a small server-rendered report. A
real-provider adapter is optional Phase 6 work and requires explicit approval for
credentials and possible spending.

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
