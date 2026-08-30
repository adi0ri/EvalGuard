# EvalGuard Delivery Plan

Last updated: 2026-08-30

## Current milestone

**Phase 6 — keyless implementation and local acceptance complete; credentialed
result blocked.** The user explicitly directed EvalGuard to finish
all phases, push the result, and not ask again. D027 records that this advances the
implementation milestone but does not authorize an unbounded or credentialless
paid call. Environment preflight checked presence only and found neither
`EVALGUARD_OPENAI_API_KEY` nor `OPENAI_API_KEY`; zero paid calls have been made.

The selected bounded track is OpenAI Responses with `gpt-5.6-luna`. The adapter,
immutable configuration/hash, price-snapshot cost estimator, durable provider-turn
ledger, short transaction executor, same-attempt recovery, API retrieval, usage
summary, provider-neutral grading, and keyless scripted tests are implemented.
Real execution is disabled by default. PostgreSQL and the API fence the one
authorized experiment to an idempotent submission, at most 25 cases, one trial,
one logical attempt, six turns per case, and a conservative $5 ceiling. The
deterministic fake remains the CI/default path and portfolio-demo results remain
synthetic infrastructure validation.

The earlier verified cost-free Phase 6 portfolio commits `5a058e0` and `31a4192`
are published on `origin/main`. Implementation commit `5479c9a` and verification
commit `73784c9` plus publication record `e301d25` are also published. GitHub
Actions run `32627986410` passed all three jobs at `e301d25`; exact remote evidence
is recorded in `PROGRESS.md`. The final local implementation gate passed on the
keyless tree; exact dependency, static, 247-test, migration, Compose, fake-smoke,
readiness, and audit evidence is also recorded there. Neither result manufactures
the separately blocked paid result.

Phase 4 remains complete with implementation commit `e276ba5` and documentation
commit `ac7d39e` published on `origin/main`. Earlier Phase 3 commits `083808c` and
`6655e79` remain published. No later remote-CI result is inferred from a push.

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
  secret, and proprietary-material audit. Record implementation commit `e276ba5`,
  Phase 4's unpushed status, and that no remote CI is claimed.

## Phase 5 — analysis and report

- [x] Add two source-controlled, published agent prompts with independent stable
  hashes while preserving both suite versions and all 25 immutable case requests.
- [x] Add revision `20260822_0008` for prompt rows plus persisted experiment trial
  count/prompt identity, PostgreSQL trial-range constraints, immutable experiment
  prompt/trial identity, and a parent-aware child-trial guard.
- [x] Seed/verify both prompts idempotently and reject mutation of a published
  prompt in PostgreSQL.
- [x] Accept one-to-three trials and create exactly one execution/outbox row for
  every case/trial coordinate; include prompt and trial count in idempotent intent.
- [x] Preserve compatible replay for one-trial pre-Phase-5 null-prompt experiments
  only when prompt fields remain omitted; keep fresh omitted-prompt requests bound
  to v2 and reject explicit prompt relabeling.
- [x] Make a Phase 4 fault plan target one exact `(case_key, trial_number)` and
  preserve compatible/conflicting replay behavior for multi-trial requests.
- [x] Trace agent-prompt name, version, hash, and instructions at the provider
  boundary while keeping the deterministic fake behavior identical across prompt
  variants.
- [x] Add aggregate metrics with explicit numerators/denominators, non-vacuous
  execution pass semantics, exact grader-row/atomic-completion-trace reconciliation,
  criterion-vector trial stability, grader/category
  breakdowns, attempts, and labeled infrastructure timing. Derive the expected
  grid from immutable suite membership, not observed executions. Keep token/cost
  unavailable rather than reporting zero.
- [x] Compare only compatible suite/provider/policy/trial grids, reject
  fault-injected arms, keep quality deltas unavailable until both arms are complete
  and fully graded, gate timing/attempt deltas on complete equal samples, and
  compute candidate-minus-baseline deltas from raw evidence.
- [x] Add the JSON summary/comparison endpoints and a small autoescaped Jinja2
  report with a prominent synthetic-validation limitation and raw execution links.
- [x] Add deterministic PostgreSQL/API/report coverage for exact grids, hashes,
  immutability, idempotency, provisional/final analysis, incompatible comparisons,
  missing/partial graders, completion-vector mismatch, vector instability, and
  report rendering.
- [x] Run the exact 25-case × two-prompt × three-trial automated workflow, producing
  and verifying 150 raw executions, 3,042 criterion rows, and prompt trace identity.
- [x] Run the locked install, Ruff, formatting, mypy, all 199 tests, disposable
  clean migration/downgrade/re-upgrade, Compose build/startup, repeated seed, live
  150-execution smoke, idempotent replay, report, and Redis readiness probe.
- [x] Update every durable document and preserve the no-real-provider/no-cost
  boundary. Record the initial duplicate-template packaging failure and verified
  correction.

## Phase 6 — optional real provider and portfolio finish

- [x] Receive explicit direction to begin Phase 6.
- [x] Push verified Phase 5 commits `c481ed4` and `270bbe4`; confirm local `HEAD`
  and `origin/main` both resolve to `270bbe4` without claiming remote CI.
- [x] Finish and review architecture, methodology, limitations, evidence, demo,
  and interview-facing portfolio documentation.
- [x] Exercise the deterministic-fake PowerShell demo end to end and record its
  exact results without presenting them as model quality.
- [x] Rerun proportionate static, automated, packaging, and live checks for the
  Phase 6 documentation/demo slice.
- [x] Receive explicit direction to finish all phases and implement the optional
  adapter while preserving the separate paid-invocation boundary.
- [x] Select the official OpenAI Responses API and `gpt-5.6-luna`; persist a
  server-owned non-secret configuration/hash and the dated pricing snapshot used
  only for estimated cost.
- [x] Add revision `20260823_0009` with immutable experiment provider identity,
  one-authorized-run uniqueness, durable `ProviderTurn` states, append-only tool
  outputs, same-attempt recovery identity, and PostgreSQL transition guards.
- [x] Commit and validate the exact request before the send fence; perform network
  I/O with no open database transaction; persist the response before tools; run
  each tool result in its own short transaction.
- [x] Make unknown send outcomes terminal and non-retryable; retain malformed and
  late responses as evidence; permit at most three same-attempt worker resumes only
  from safe committed states.
- [x] Require a real-provider `Idempotency-Key`; limit the single experiment to at
  most 25 cases, one trial, one attempt, 300 seconds, six turns per case, and a $5
  conservative spend ceiling; disable SDK retries, storage, parallel tools, and
  deterministic fault injection.
- [x] Record provider-reported token usage, returned model, client-observed
  latency, and immutable-price-snapshot estimated cost without presenting the
  estimate as billing data.
- [x] Add provider-neutral deterministic grading that ignores provider-generated
  IDs/free text and removes fake-only fault choreography while preserving exact
  semantic state, policy, authorization, typed-tool, trace, and resolution checks.
- [x] Add keyless PostgreSQL/API/worker tests for API fencing, isolation, network
  transaction boundaries, malformed/indeterminate responses, usage/cost,
  returned-model mismatch, exact durable resume prefixes, and fake-provider
  regression behavior.
- [x] Finish the final-tree locked sync, static checks, full pytest run, clean
  migration round trip/drift check, Compose build/startup, and live fake smoke;
  record exact results in `PROGRESS.md` and reconcile every durable document.
- [ ] Receive an externally supplied worker-only credential plus explicit paid-run
  authorization within the recorded provider/model, scope, and $5 ceiling.
- [ ] Run one bounded real experiment and record provider/model, configuration,
  revision, hashes, raw evidence, measured usage/cost sources, and limitations
  separately from synthetic infrastructure validation. Blocker: no credential is
  available and zero paid calls have been made.

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
| A real-provider run introduces cost or secrets | Disable it by default; keep the key worker-only; require an idempotency key; permit one PostgreSQL-fenced experiment; cap cases/trials/attempts/turns and conservative estimated spend; never call without a credential and paid-run authorization. |
| A provider timeout has an unknown paid outcome | Commit `request_started` before send, never resend an outcome without durable response evidence, and terminalize it as non-retryable indeterminate. |
| A worker exits after a safe real-provider boundary | Resume the same logical attempt only from committed safe states, fence each delivery generation, preserve the paid-call/tool prefix, and stop after three recoveries. |
| A floating model alias resolves differently | Persist requested config and every returned model; fail tool execution and cost attribution closed on an unapproved model, and require identical returned-model sets for comparisons. |
| Estimated cost is mistaken for a provider bill | Derive it only from the immutable dated pricing snapshot, expose it as estimated USD, and keep provider usage separate from billing evidence. |
| Fake-only retry choreography contaminates model quality | Use provider-neutral semantic expectations for real responses while retaining the original immutable suite and deterministic-fake resilience graders. |

No Phase 0–5 local-acceptance blocker is known. One
upstream FastAPI/Starlette `TestClient` deprecation warning is recorded in
`PROGRESS.md`; the final Phase 5 suite passed all 199 tests. Phase 2 is published at
`26c3e86`; Phase 3 commits `083808c` and `6655e79` are published; Phase 4 commits
`e276ba5` and `ac7d39e` are published; and Phase 5 commits `c481ed4` and `270bbe4`
are published. Phase 6 portfolio commits `5a058e0` and `31a4192` are also
published. The Phase 6 keyless implementation gate is locally complete in commit
`5479c9a` and published through `e301d25`. GitHub Actions run `32627986410` passed
at that exact tip with all three jobs successful.
Phase 5 implementation and final-acceptance criteria are checked above from exact
local evidence and recorded in implementation commit `c481ed4` plus its durable
documentation follow-up `270bbe4`. No Phase 4 or Phase 5 remote-CI result is
claimed.

## Precise next action

The only unchecked Phase 6 outcome is the credentialed experiment. Do not invoke
it unless a worker-only credential and explicit paid-run authorization become
available.
Until then, keep it visibly blocked, report zero paid calls, and do not invent a
live result. The current Phase 6 remote CI result is recorded from the observed run.
