# EvalGuard Delivery Plan

Last updated: 2026-08-22

## Current milestone

**Phase 4 — implementation and local acceptance complete.** The PostgreSQL
transactional outbox, execution attempts/generations/leases, bounded retry and RQ
timeout policy, exact tool-step budget, worker/CLI recovery, typed development/test
fault plans, and retrieval evidence are implemented. Phase 4 adds no suite or case
content: published `parceldesk-refunds@1.0.0` and `@2.0.0` and their hashes remain
unchanged. All local acceptance checkboxes below are verified from exact evidence
in `PROGRESS.md`: 189 tests passed, revision `0007` completed its migration/backfill
round trip, and Compose/live timeout/interruption/outage recovery passed. A local
Phase 4 commit is pending; nothing from Phase 4 is pushed and no remote-CI result is
claimed. Phase 5 and later remain out of scope and have not started.

Phase 3 remains complete locally with its recorded 158-test, migration, Compose,
live-smoke, and readiness evidence. Commits `083808c` and `6655e79` are pushed;
local `main` and `origin/main` are at `6655e79`. No Phase 3 remote-CI result is
claimed; it is not a Phase 3 local-acceptance prerequisite.

## Phase 0 — repository and architecture foundation

### Repository and documentation

- [x] Inspect the initial folder and Git state.
- [x] Initialize Git after confirming the initial workspace was empty and was not
  already a repository.
- [x] Create `AGENTS.md`, `PROJECT_SPEC.md`, `PLAN.md`, `DECISIONS.md`,
  `PROVENANCE.md`, and `docs/LEARNING_NOTES.md`.
- [x] Create the implementation-facing `README.md`.
- [x] Complete the final review of every README command and behavior claim.
- [x] Create `PROGRESS.md` and record chronological commands and exact results.
- [x] Confirm `.gitignore` and `.env.example` contain no secrets or generated
  runtime artifacts.
- [x] Make the first local milestone commit if Git author identity is already
  configured.

### Python foundation

- [x] Establish the modular `src/evalguard` package.
- [x] Configure Python 3.12 dependencies in `pyproject.toml`.
- [x] Generate the `uv` lockfile.
- [x] Verify locked dependency installation with
  `uv sync --frozen --all-groups` (49 packages checked).
- [x] Configure Ruff, mypy, and pytest.
- [x] Configure typed settings through Pydantic v2 and `pydantic-settings`.
- [x] Add structured application logging.

### Runtime and persistence

- [x] Define Docker Compose services for API, worker, PostgreSQL, and Redis.
- [x] Add SQLAlchemy 2 engine/session lifecycle management.
- [x] Configure Alembic and create the initial schema migration.
- [x] Add a guarded, idempotent command and one-shot Compose service to prepare the
  isolated PostgreSQL test database.
- [x] Add FastAPI/Uvicorn application startup.
- [x] Add `GET /health/live`.
- [x] Add dependency-aware `GET /health/ready` for PostgreSQL and Redis.
- [x] Add initial unit tests.
- [x] Add GitHub Actions for lint, type checking, tests, and build verification.

## Phase 1 — one complete eligible-refund evaluation

### Versioned evaluation input

- [x] Define one immutable, versioned ParcelDesk evaluation suite.
- [x] Define one independently authored eligible-refund case and stable definition
  hashes.
- [x] Provide and document an idempotent command that loads the initial suite and
  case.

### Persistence and isolation

- [x] Persist one experiment and its case execution before queue dispatch.
- [x] Create execution-scoped synthetic customer and eligible order state.
- [x] Persist transactional refund state.
- [x] Persist ordered trace events and tool invocations.
- [x] Persist criterion-level deterministic grader results.
- [x] Enforce required foreign keys and uniqueness for execution identity and tool
  call identity.

### Execution path

- [x] Implement typed `lookup_order`.
- [x] Implement typed `issue_refund` with a transactional state change.
- [x] Implement the deterministic fake provider.
- [x] Implement the deterministic refund grader.
- [x] Enqueue experiment work through Redis and RQ.
- [x] Run the fake provider, typed tools, trace persistence, refund transaction, and
  grader in the RQ worker.
- [x] Return `202 Accepted` from experiment creation after durable records exist.
- [x] Add experiment retrieval.
- [x] Add execution retrieval with state, ordered traces, and grader evidence.

### Required Phase 1 tests

- [x] Unit test the successful refund grader result.
- [x] Add negative grader tests for an absent or incorrect refund.
- [x] Test typed tool argument validation.
- [x] Test refund transaction behavior against PostgreSQL.
- [x] Prove one execution cannot access another execution's business state.
- [x] Prove published suite membership cannot be appended, moved, or raced during
  publication.
- [x] Prove experiment creation returns `202 Accepted`.
- [x] Prove evaluation and business records persist in PostgreSQL.
- [x] Test an RQ worker job using the deterministic fake provider.
- [x] Add an end-to-end submit, poll, retrieve, and verify test.
- [x] Test liveness and readiness behavior.
- [x] Exercise a live Docker startup and queued-evaluation smoke test.

## Phase 2 — complete domain tools and strengthen correctness

### Domain and persistence

- [x] Add validated typed arguments and results with closed object schemas for
  `update_shipping_address` and `escalate_ticket`.
- [x] Add relational order shipping-address fields and execution-scoped
  `support_tickets` through Alembic revision `20260822_0005`.
- [x] Protect ticket ownership with composite execution/customer, execution/order,
  and execution/tool-call foreign keys.
- [x] Preserve published-suite immutability and per-execution business-state
  isolation.

### Policy, transactions, and idempotency

- [x] Permit shipping-address changes only for `pending` and `processing` orders;
  reject later fulfillment states without mutation.
- [x] Require `high_value_refund` escalation instead of issuing refunds at or above
  50,000 cents.
- [x] Lock the execution row before tool-call identity/trace decisions and the order
  row before state-changing policy decisions.
- [x] Replay a stable call ID only when the tool name and validated arguments match
  exactly; reject conflicting reuse.
- [x] Make a new call ID for an already-applied address or escalation converge on
  the existing semantic state without a second mutation.
- [x] Persist failed invocations and ordered `tool_error` trace evidence for
  policy/ownership rejections reached after invocation start when the surrounding
  transaction is retained.
- [x] Serialize concurrent API submissions for one `Idempotency-Key` on a dedicated
  PostgreSQL connection/session through persistence, enqueue attempt, and dispatch
  outcome.

### Retrieval and required Phase 2 tests

- [x] Expose structured shipping addresses and support tickets through execution
  retrieval.
- [x] Test successful and rejected address changes against PostgreSQL.
- [x] Test required high-value escalation and duplicate escalation convergence.
- [x] Test stable-call replay, conflicting call intent, semantic duplicates, and
  concurrent state-changing calls.
- [x] Prove address and escalation tools cannot cross execution boundaries, and
  prove composite ticket ownership constraints reject cross-execution rows.
- [x] Test concurrent same-key API submission, retrieval, RQ worker compatibility,
  and the complete deterministic fake-provider path.
- [x] Continue using only the deterministic fake provider; no paid or real API was
  called.

The Phase 2 milestone closed with the queued RQ fake still using the single
published `eligible-refund` case. Phase 3 adds its new immutable suite version and
queued deterministic breadth without mutating that historical definition.

## Phase 0/1/2 acceptance gate

- [x] `docker compose up --build` starts API, worker, PostgreSQL, and Redis.
- [x] Alembic upgrades a clean PostgreSQL database.
- [x] Liveness and readiness behave correctly with dependencies available and with
  Redis unavailable.
- [x] A documented command loads the initial suite and scenario idempotently.
- [x] Experiment submission returns `202 Accepted`.
- [x] The worker completes the fake-provider execution.
- [x] Final business state, traces, and grader result are stored and retrievable.
- [x] The negative grader tests demonstrate incorrect behavior is detected.
- [x] Ruff lint and formatting checks pass.
- [x] mypy passes.
- [x] pytest passes without substituting SQLite for PostgreSQL integration behavior.
- [x] GitHub Actions uses commands consistent with local verification; the first
  remote workflow run passed all lint/type, migration/test, and Compose-build jobs.
- [x] Every README setup/check command has been exercised locally.
- [x] A final provenance and secret scan finds no proprietary material or secrets.
- [x] Durable documents match the verified implementation and evidence.
- [x] The locked install checked 49 packages in 4 ms; Ruff lint passed; Ruff format
  checked 52 files; mypy checked 27 source files; and pytest passed 49 tests with
  one dependency-originated warning in 15.85 seconds.
- [x] A disposable clean PostgreSQL database upgraded through revisions
  `20260818_0001` to `20260822_0005`, downgraded from `0005` to `0004`, re-upgraded
  to head, and reported no model/migration drift.
- [x] `docker compose up --build -d --wait` exited successfully; API, PostgreSQL,
  and Redis were healthy, the worker was running, and one-shot `migrate` and
  `test-db` services exited successfully.
- [x] Seeding twice returned the same suite ID, case ID, and definition hash.
- [x] The Phase 2 live smoke returned liveness `200`, readiness `200`, experiment
  submission `202`, and a completed fake-provider execution with one customer, one
  order, one refund, a null shipping address, zero tickets, two baseline tool
  invocations, seven trace events, and four passing grader criteria.
- [x] The Phase 2 migration and PostgreSQL tests cover accepted/rejected address
  changes, required escalation, duplicate calls, concurrency, and isolation.
- [x] Dependency-unavailable readiness was not rerun for Phase 2; the verified
  Phase 1 liveness-`200`/readiness-`503` probe remains the current evidence.

## Phase 3 — complete scenario and grader coverage

### Immutable inputs and policy context

- [x] Preserve published `parceldesk-refunds@1.0.0` and its original hashes.
- [x] Add published `parceldesk-refunds@2.0.0` with exactly 25 independently
  authored cases in the required 7 normal / 5 ineligible / 5 boundary / 4
  authorization / 4 duplicate-or-recovery distribution.
- [x] Hash every v2 case's key, title, prompt, typed fixture, independent expected
  policy contract, and deterministic fake plan; hash suite membership by ordered
  case key and hash.
- [x] Use a fixed fixture `policy_as_of` timestamp and inclusive 30-day refund
  window so time-boundary cases never depend on the wall clock.
- [x] Add Alembic revision `20260822_0006` so a support ticket's customer must own
  its referenced order even when both rows share one execution; continue using the
  existing JSON definition/evidence columns for scenario contracts.

### Execution, authorization, grading, and regression behavior

- [x] Materialize multiple execution-owned customers/orders and relational starting
  addresses from closed typed fixtures.
- [x] Bind all four tools and cached replay to the trusted requester customer as
  well as the case execution.
- [x] Standardize tool lock order as execution row followed by order row, and emit
  one `tool_replay` trace after the original outcome for successful exact replay
  without a second invocation or mutation.
- [x] Execute typed scenario plans through the deterministic fake provider and the
  production `ToolRuntime`.
- [x] Add deterministic `state_v1`, `trace_v1`, `policy_v1`,
  `authorization_v1`, `escalation_v1`, and `resolution_v1` grader families.
- [x] Grade invocation success/rejection/duplicate outcomes through
  `trace_v1.invocation_outcomes_match`, grade ticket/order ownership through
  `escalation_v1.ticket_owner_matches_order`, and include ticket ownership in
  `state_v1.tickets_exact`.
- [x] Detect protected numeric as well as string values embedded in free-text
  resolutions, using token boundaries for strings and recognizing grouped numeric
  forms such as `9,125`.
- [x] Add eleven stable fake behavior IDs: one reference behavior and ten
  deliberately defective modes covering state, trace, policy, authorization,
  escalation, duplicate intent, and resolution regressions.
- [x] Derive aggregate experiment lifecycle from all executions under a PostgreSQL
  parent-row lock, preventing an early case from completing a 25-case experiment.
- [x] Reject an execution whose case does not belong to its experiment's suite in
  both queued and completed states, and perform that check before completed replay
  can return. Freeze typed tool/scenario contracts; bind the resolution and every
  expected state mutation to the policy order, and reject denied expectations that
  request address or other state mutation.

### Automated Phase 3 verification

- [x] Verify exact keys, 7/5/5/4/4 counts, complete-contract hashes, closed typed
  round trips, and cross-customer fixture construction.
- [x] Verify idempotent v2 PostgreSQL seeding, legacy preservation, exact persisted
  payloads, and definition-conflict rejection.
- [x] Verify actor-scoped lookup, mutation, failed-call evidence, and cached replay
  against PostgreSQL.
- [x] Verify the reference behavior and all ten defective behaviors through targeted
  PostgreSQL executions across all six grader families.
- [x] Verify successful/ineligible/boundary/authorization/duplicate/recovery cases,
  RQ execution, API submission/retrieval, aggregate lifecycle, and legacy
  compatibility.
- [x] Run focused unit coverage: 84 tests passed with one dependency warning in
  0.38 seconds.
- [x] Run focused Phase 3 integration coverage: 38 tests passed with one dependency
  warning in 24.87 seconds.
- [x] Run the full automated suite: 158 tests passed with one dependency warning in
  52.51 seconds.

### Phase 3 final acceptance

- [x] Run the locked dependency installation from `uv.lock`: 49 packages checked.
- [x] Run final-tree Ruff lint and formatting checks after formatting two files;
  both checks passed. mypy passed all 29 source files.
- [x] Run Compose configuration plus `docker compose up --build -d --wait`; API,
  PostgreSQL, and Redis became healthy, the worker ran, and one-shot `migrate` and
  `test-db` exited 0.
- [x] Upgrade a blank disposable database through `20260822_0006`, confirm no drift,
  downgrade `0006` to `0005`, re-upgrade `0005` to `0006`, confirm no drift again,
  and drop the disposable database.
- [x] Run the documented loader twice and verify stable v2 suite identity/hash and
  exactly 25 cases while preserving v1. The verified suite hashes are
  `28289cfdcd98be852b00e451b2f52ae8a9af65a454a6642bb96acb4991a19782`
  for v2 and
  `aaedf653770166eb7e967352b9242509a94dd9ba3e4ee833576617c403ec6abd`
  for v1.
- [x] Submit and retrieve one live 25-case deterministic-fake experiment. Initial
  and replay requests returned `202` with the same identifiers; 25/25 synthetic
  executions completed with 507/507 passing grader results across six families,
  44 tool invocations, 164 trace events including one `tool_replay`, 7 refunds, and
  5 tickets.
- [x] Stop Redis while the API remained live: liveness returned `200`, readiness
  returned `503` with Redis unavailable, and readiness returned `200` after restore.
  Four expected worker Redis connection `ERROR` lines occurred during the outage;
  zero suspicious lines appeared after restoration.
- [x] Complete the final documentation/provenance/secret audit and local Phase 3
  commit `083808c`.

## Phase 4 — resilience

### Durable acceptance and queue dispatch

- [x] Add revision `20260822_0007` with persisted experiment retry/timeout/step/
  backoff policy, case-execution attempt/generation/lease summaries,
  `execution_attempts`, `execution_dispatches`, and development/test
  `execution_fault_plans`. Preserve the legacy 300-second timeout on existing
  experiments, reset legacy `running`/`retrying` executions to `queued`, and
  backfill pending generation-1 dispatches only for queued or legacy
  `enqueue_failed` work; new experiments default to 30 seconds.
- [x] Atomically create every generation-1 dispatch intent with its case execution,
  then return `202 Accepted` after PostgreSQL commit even when immediate Redis
  publication fails.
- [x] Reconcile current pending dispatches one locked row/transaction at a time,
  using `FOR UPDATE SKIP LOCKED` for general sweeps, blocking locks for explicit
  IDs, capped publication backoff, and early stop after a Redis/transport failure.
- [x] Retain the legacy generation-1 RQ job ID and use generation-qualified IDs for
  retries; accept a duplicate only when origin queue, function, positional
  arguments, empty keyword arguments, configured timeout, and runnable status match
  exactly. Preserve the pre-Phase-4 one-argument generation-1 identity only for a
  persisted 300-second policy; reject it for a new 30-second policy. Validate live
  RQ delivery against the persisted dispatch and timeout before claim. Persist
  identity collisions as pending safe failures rather than running a foreign job
  or consuming an execution attempt.
- [x] Make compatible `Idempotency-Key` replay return the original identifiers and
  trigger targeted reconciliation of the same durable executions without
  publishing a `retrying` execution before `available_at`; only queued or legacy
  `enqueue_failed` initial work receives the override. Treat resilience policy and
  typed fault intent as part of the conflict check, and release the API advisory
  lock before best-effort Redis I/O now that PostgreSQL owns recovery.
- [x] Audit current dispatched-but-unclaimed queued/retrying/legacy
  `enqueue_failed` generations after 30 seconds. Leave exact runnable RQ jobs alone;
  turn a missing or exact-but-
  unrunnable job into one generation/job-checked pre-claim interruption that
  consumes the bounded attempt budget, then advances or exhausts. Never claim an
  identity mismatch.

### Attempts, retries, timeouts, and recovery

- [x] Claim attempts under an execution-row lock and persist attempt number,
  dispatch generation/job ID, lease, status, step count, retryability, safe failure
  kind/message, and completion time. Before provider/tool work, revalidate the
  expected attempt number, generation, and `running` status under that same lock so
  recovery fences a paused worker.
- [x] Ignore stale generations, concurrent/busy duplicates, and terminal replay
  without invoking provider/tools; increment the generation exactly once for each
  scheduled retry or interruption recovery.
- [x] Retry only typed transient, timeout, and worker-interruption failures while
  `attempt_number < max_attempts`; fail unexpected, injected permanent, and
  maximum-step errors without retry. Double execution retry delay from the
  persisted base and cap it at 300 seconds.
- [x] Pass `job_timeout_seconds` explicitly to RQ, persist timeout as retryable
  before re-raising RQ's timeout exception, and derive a PostgreSQL-time lease from
  the timeout plus recovery grace.
- [x] Recover the exact generation/job from RQ's work-horse termination hook, and
  recover only expired running attempts during bounded startup/maintenance or CLI
  sweeps while respecting execution locks and attempt budgets. Recover exact
  current work that fails before claim as a zero-step interrupted attempt.

### Tool budget, fault plans, atomicity, and evidence

- [x] Define one tool step as entry into any public `ToolRuntime` method, consumed
  before lock/replay lookup. Count success, policy/ownership errors, semantic
  no-ops, cached-success replay, and persisted-error replay; do not count typed
  validation rejected before method entry. Allow exactly the configured limit and
  reject counted step N+1 before invocation/mutation.
- [x] Add one typed deterministic fault plan for one selected case, accepted only
  in development/test, with transient-before-provider, transient-after-tools,
  timeout, permanent-before-provider, and worker-interruption modes. Make the
  executor independently fail closed if a persisted plan appears elsewhere.
- [x] Preserve the atomic deterministic execution transaction so a failed attempt
  rolls back fixtures, state mutations, invocations, normal trace, graders, and
  resolution together, then record retry/failure evidence separately. Reuse the
  existing execution/actor isolation, execution-then-order lock order, exact
  replay, semantic convergence, and PostgreSQL uniqueness on retry.
- [x] Expose experiment resilience policy and execution attempt count; expose
  execution policy, generation, lease/failure summary, ordered attempts, and
  dispatch history.
- [x] Keep immutable v1/v2 suites, case payloads, fake plans, grader families, and
  verified hashes unchanged. Label all Phase 4 deterministic fault behavior as
  synthetic infrastructure validation, not model reliability.
- [x] Add focused and PostgreSQL/Redis/RQ/API/end-to-end regression coverage for the
  implementation contracts above, including initial-only force, dispatched-job
  loss/non-runnable recovery, pre-claim failure, and environment fail-closed
  behavior, plus two parametrized legacy `enqueue_failed` delivery cases and one
  paused-worker fence case, without adding a real or paid provider.

### Phase 4 final acceptance — verified locally

- [x] Run locked installation from `uv.lock`, final Ruff lint/format checks, mypy,
  and the complete pytest suite against isolated PostgreSQL and Redis; record exact
  commands, counts, durations, and warnings in `PROGRESS.md`.
- [x] Upgrade a blank database through `20260822_0007`, verify the legacy
  300-second experiment timeout, `running`/`retrying` requeue, and pending
  generation-1 dispatch backfill only for queued/legacy `enqueue_failed` work; run
  `alembic check`, downgrade `0007` to `0006`, re-upgrade to head, and confirm no
  drift.
- [x] Verify the transactional outbox under immediate Redis failure, partial
  multi-job publication, compatible/concurrent replay, matching RQ duplicates,
  mismatched collisions, timeout/status identity, initial-only force behavior,
  missing/non-runnable dispatched delivery, and concurrent reconcilers.
- [x] Verify bounded transient recovery and exact exhaustion, permanent failure,
  real RQ timeout enforcement, post-tool rollback, exact N/N+1 step behavior, and
  duplicate/stale delivery without a second business effect.
- [x] Verify fresh leases are not reclaimed, expired leases recover once, and an
  actual RQ work-horse termination reaches the generation/job-checked fast path or
  the documented lease fallback without duplicating state. Verify pre-claim
  interruption consumes exactly one attempt and persisted fault plans fail closed
  outside development/test. Verify a recovered paused worker is fenced before
  provider/tool work and legacy `enqueue_failed` missing/terminal delivery recovers.
- [x] Exercise and record the worker startup/maintenance recovery cycle and the
  bounded `evalguard reconcile` CLI entrypoint.
- [x] Rebuild/start the Compose topology with revision `0007`, exercise a queued
  deterministic-fake submission/retrieval through Phase 4 evidence, and repeat
  dependency readiness behavior where required.
- [x] Complete the Phase 4 durable-document, provenance, dependency/reference,
  secret, and proprietary-material audit. Record that the local commit is pending,
  Phase 4 remains unpushed, and no remote CI is claimed.

## Backlog — do not implement during the current milestone

### Phase 5: analysis and report

- [ ] Add aggregate metrics and experiment comparison.
- [ ] Compare prompt v1 and v2 across 25 cases and three trials each, producing 150
  raw reproducible executions.
- [ ] Add a small Jinja2 or static server-rendered report.

### Phase 6: optional real provider and portfolio finish

- [ ] After explicit approval for credentials and spending, add one real-provider
  adapter and run the real experiment.
- [ ] Finish architecture, methodology, limitations, demo, and portfolio
  documentation.

## Risks and mitigations

| Risk | Current mitigation |
| --- | --- |
| Docker or service tooling is unavailable locally | Detect early, record exact failure, and do not infer runtime success from static configuration. |
| Windows/PowerShell commands diverge from CI | Exercise documented local commands on Windows and keep CI commands equivalent in behavior. |
| API persistence succeeds but queue dispatch fails | Commit one generation-1 PostgreSQL outbox row with each execution, return `202`, and let API replay or worker/CLI reconciliation publish pending current generations one row at a time. |
| A worker repeats a state-changing call | Validate the dispatch generation/job identity, serialize attempt claims on the execution row, carry stable tool identities, require exact replay intent, and enforce database uniqueness plus semantic convergence. |
| Redis remains unavailable during dispatch | Leave the outbox pending with a safe error and capped publication backoff; stop the current sweep after a transport failure instead of terminalizing accepted work or hammering Redis. |
| Redis loses a job after dispatch is marked | After 30 seconds, audit current dispatched work that is still queued/retrying or legacy `enqueue_failed`; retain exact runnable jobs and convert missing/exact-unrunnable delivery into one fenced interrupted attempt before advancing or exhausting. |
| A worker exits while an attempt is running | Persist a PostgreSQL-time lease, use RQ's exact job/generation work-horse callback as a fast path, and reclaim only expired locked attempts during periodic recovery. |
| API replay arrives while a retry is delayed | Target the same durable executions, but limit the initial-work override to queued/legacy rows; `retrying` remains current-generation and `available_at` aware. |
| Parallel executions leak business state | Scope ownership through execution foreign keys, including composite ticket ownership, and test cross-execution access denial. |
| Publication races with a late case write | Serialize both paths on suite-row locks and test the concurrent insert boundary. |
| Destructive test cleanup targets development state | Require test mode, a `*_test` PostgreSQL database, a non-development Redis database, and the test-only queue before cleanup; Redis DB 15 is the tested default. |
| Fake-provider success is mistaken for model quality | Label results synthetic infrastructure validation throughout reports and provenance. |
| Documentation drifts from behavior | Update durable documents with the implementing change and gate completion on exercised commands. |
| A real-provider run introduces cost or secrets | Keep real providers in Phase 6 and require explicit user approval. |

No Phase 0–4 local-acceptance blocker is known. One
upstream FastAPI/Starlette `TestClient` deprecation warning is recorded in
`PROGRESS.md`; the final Phase 4 suite passes all 189 tests. Phase 2 is pushed at
`26c3e86` at the Phase 2 close. Phase 3 commits `083808c` and `6655e79` are pushed,
and local `main` plus `origin/main` are at `6655e79`; no Phase 3 remote-CI result is
claimed. Phase 4 implementation and final-acceptance criteria are checked above
from the exact local evidence in `PROGRESS.md`; its local commit remains pending,
it is unpushed, and no Phase 4 remote-CI result is claimed.

## Precise next action

Record the pending local Phase 4 commit when it occurs, then keep Phase 4 unpushed
until the user explicitly asks to publish. Record remote CI only if actually
observed, and do not begin Phase 5 until the user explicitly advances it.
