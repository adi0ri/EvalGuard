# EvalGuard Project Specification

## Status

Phase 0, Phase 1, and Phase 2 are complete. Phase 3 implementation and local
acceptance are complete: 158 tests, locked installation, static checks, the clean
`20260822_0006` migration round trip, Compose startup, a live 25-case smoke, and a
Redis-unavailable readiness probe passed. Phase 3 commits `083808c` and `6655e79`
are pushed; `origin/main` remains at `6655e79`. No Phase 3 remote-CI result is
claimed. Phase 4
resilience implementation and local acceptance are complete, including revision
`20260822_0007`, 189 passing tests, its migration/backfill round trip, Compose and
live recovery checks, a real RQ timeout, an actual worker interruption, and Redis-
outage recovery. Local implementation commit `e276ba5` records verified Phase 4;
local `main` remains ahead of `origin/main` at `6655e79`. Phase 4 remains
unpushed and no remote-CI result is claimed. This specification defines the
implemented Phase 4 behavior and remaining V1 contract. Phase 5 has not started and
later requirements remain out of scope until explicitly advanced.

## Product outcome

EvalGuard's V1 target is a reproducible evaluation harness for tool-using AI
agents. It runs a support agent against versioned, stateful scenarios; records
responses, tool calls, database changes, failures, latency, token use, and cost
when those values exist; grades primary correctness deterministically; and
ultimately compares experiments by correctness, policy compliance, stability,
latency, and cost. The current system records lifecycle timestamps, durable
dispatch and execution-attempt evidence, safe failure classifications, and
terminal failure text, but it does not derive comparison/latency metrics or record
tokens/cost.

The domain is an independently invented business named ParcelDesk. Synthetic
business state contains customers, orders with relational shipping-address fields,
refunds, and support tickets. EvalGuard is a runnable system and test harness, not
a generic chatbot, RAG demonstration, UI mockup, or documentation-only architecture
exercise.

## Users and core workflow

The initial user is a developer or evaluator comparing agent behavior. In the
current Phase 4 workflow, that user:

1. loads the preserved one-case legacy suite and the immutable 25-case v2 suite;
2. submits an experiment;
3. receives `202 Accepted` after the experiment, all 25 case executions, and their
   initial PostgreSQL dispatch intents are stored;
4. lets RQ workers run the deterministic fake provider across those executions;
5. retrieves the aggregate experiment and individual executions; and
6. inspects the stored business outcome, ordered trace, criterion-level grader
   result, attempts, dispatch generations, leases, and safe failure evidence.

The deterministic fake follows each case's typed, hashed plan through the same four
production tools used by the worker. Six deterministic grader families then inspect
final PostgreSQL state, ordered trace, policy, actor authorization, escalation, and
structured resolution evidence. An experiment stays running while any of its case
executions is nonterminal, completes only when all complete, and fails when every
case is terminal and at least one failed. These results remain synthetic
infrastructure validation rather than model-quality evidence.

## Current milestone scope

### Phase 0: repository and architecture foundation

- Python 3.12 package using the modular `src/evalguard` layout.
- Dependency metadata and a committed lockfile, with Ruff, mypy, and pytest.
- Secret-free environment example and structured application logging.
- Docker Compose services for FastAPI/Uvicorn, an RQ worker, PostgreSQL, and Redis.
- SQLAlchemy 2 session management and Alembic migrations.
- Liveness and dependency-aware readiness endpoints.
- Initial unit tests and GitHub Actions for lint, types, tests, and build
  verification.
- A verified clean-database migration path and durable project documentation.

### Phase 1: one complete evaluation slice

- One evaluation suite and one eligible-refund case.
- One experiment and one isolated case execution per submission.
- Synthetic customer, order, and refund state scoped to that execution.
- Ordered trace events and criterion-level grader results.
- Typed `lookup_order` and `issue_refund` tools.
- A deterministic fake provider and deterministic refund grader.
- Redis/RQ background execution.
- Experiment and execution retrieval endpoints.
- Unit, negative, validation, transaction, isolation, API, PostgreSQL integration,
  worker, liveness/readiness, and end-to-end tests listed in `PLAN.md`.

### Phase 2: complete domain tools and correctness hardening

- Typed `update_shipping_address` and `escalate_ticket` arguments and results.
- Relational order shipping-address fields and `support_tickets` introduced by
  Alembic revision `20260822_0005`.
- Address changes allowed only while an order is `pending` or `processing`.
- Refunds of at least 50,000 cents are rejected by `issue_refund` and require an
  `escalate_ticket` call with reason code `high_value_refund`.
- Execution- and order-row locks around state-changing decisions.
- Stable call-ID replay only for the exact same tool and validated arguments;
  repeated semantic effects with new call IDs converge without a second mutation.
- Policy/ownership failures reached after invocation start persist a failed
  invocation and ordered `tool_error` evidence when their surrounding transaction
  is retained; validation and conflicting call-ID intent fail before a new
  invocation is created.
- Execution-scoped composite foreign keys for ticket customer, order, and tool-call
  ownership, plus PostgreSQL uniqueness for convergent escalation.
- At the Phase 2 checkpoint, concurrent `Idempotency-Key` submissions were
  serialized on one dedicated PostgreSQL connection/session through persistence,
  enqueue attempt, and dispatch outcome. Phase 4 supersedes the Redis portion with
  the durable outbox described below.
- Retrieval exposes structured order shipping addresses and support tickets.
- Deterministic PostgreSQL, API, RQ-worker, and end-to-end coverage listed in
  `PLAN.md`; no SQLite or real/paid provider is used.

### Phase 3: complete scenario and deterministic grader coverage

- The published `parceldesk-refunds@1.0.0` suite and its original definition
  hashes remain unchanged.
- A separately published `parceldesk-refunds@2.0.0` suite contains exactly 25
  source-controlled cases: 7 normal successes, 5 ineligible requests, 5 policy
  boundaries, 4 authorization/privacy cases, and 4 duplicate/recovery cases.
- Typed Phase 3 fixtures support multiple execution-owned customers and orders, an
  explicit requester identity, relational starting addresses, and a fixed
  `policy_as_of` timestamp with an inclusive 30-day refund window. Frozen
  cross-field contracts bind the resolution and every expected state mutation to
  the policy target, and forbid denied outcomes from expecting address or other
  state mutations.
- The production `ToolRuntime` scopes lookup, mutation, and cached replay to the
  trusted requester customer as well as the case execution. Successful exact replay
  emits one `tool_replay` event after the original outcome without another
  invocation or mutation.
- Six deterministic grader families independently inspect state, trace, policy,
  authorization/privacy, escalation, and final structured resolution. Trace grading
  checks invocation outcomes and replay ordering, escalation grading checks that
  each ticket's customer owns its order, and privacy grading detects protected
  string or numeric values embedded in free text. String comparison uses token
  boundaries so a short value such as `US` does not match inside `CUSTOMER`, while
  numeric comparison recognizes grouped forms such as `9,125`.
- The deterministic fake has one reference behavior and ten deliberately defective
  behavior IDs for regression validation. It uses typed plans and the production
  tools; it does not call a model or paid API.
- Experiment lifecycle is derived under a PostgreSQL row lock from every case
  execution, preventing one early completion from terminating a 25-case run.
- Revision `20260822_0006` adds a composite order/customer/execution relationship
  so PostgreSQL rejects a ticket whose customer does not own its order, even when
  all rows share one execution. Other Phase 3 definitions reuse the existing JSON
  fixture/evidence columns and relational business tables.

### Phase 4: durable dispatch and bounded execution recovery

- Revision `20260822_0007` adds persisted experiment policies
  (`max_attempts`, `job_timeout_seconds`, `max_tool_steps`, and
  `retry_backoff_seconds`), per-execution attempt/generation/lease state, durable
  `execution_attempts` and `execution_dispatches`, and one optional typed
  `execution_fault_plans` row per execution. Its data migration gives existing
  experiments the pre-Phase-4 300-second queue timeout, resets legacy `running`
  and `retrying` executions to `queued`, and creates pending generation-1
  dispatches only for queued or legacy `enqueue_failed` work. New experiments use
  the Phase 4 application default of 30 seconds.
- Experiment creation commits every selected case execution and generation-1
  dispatch intent atomically in PostgreSQL. Immediate RQ publication is best
  effort: Redis failure does not change accepted work to terminal failure or turn
  the response into `503`; the API still returns `202` and leaves pending outbox
  rows for reconciliation. Initial API reconciliation may bypass `available_at`
  only for queued/legacy `enqueue_failed` work, never for a delayed `retrying`
  execution.
- A worker startup/maintenance cycle and the bounded `evalguard reconcile` CLI
  entrypoint first recover stale leased attempts, then publish due current-
  generation dispatch rows. General sweeps use row locks with `SKIP LOCKED` and
  commit one dispatch attempt per transaction so multiple reconcilers can converge
  without one Redis failure rolling back prior publications.
- Generation 1 retains the legacy RQ job ID. Each retry/recovery increments the
  execution's dispatch generation and uses a generation-qualified job ID. An RQ
  duplicate is accepted only when queue origin, function, positional arguments,
  empty keyword arguments, timeout, and runnable status exactly match. The legacy
  one-argument generation-1 job remains compatible only when the persisted
  experiment timeout is its legacy 300 seconds; a new 30-second experiment rejects
  that shape. Before claim, a live RQ delivery is checked again against its
  persisted dispatch and experiment timeout. A collision with different intent
  consumes no execution attempt and reconciliation leaves the dispatch pending
  with a safe error kind.
- Maintenance rechecks a current dispatched row after 30 seconds when its execution
  is still queued, retrying, or legacy `enqueue_failed` and no durable attempt was
  claimed. Exact runnable RQ state is left alone. A missing or exact-but-unrunnable
  job is recorded as one pre-claim interruption that consumes the bounded attempt
  budget, then advances generation or fails at exhaustion; identity-mismatched work
  is never claimed.
- Each delivery claims an execution row before creating a durable attempt. Current-
  generation concurrent duplicates return busy, older generations return stale,
  and terminal executions replay without invoking the provider or tools. Only
  explicitly classified transient, timeout, or worker-interruption failures retry,
  and only while `attempt_number < max_attempts`; unexpected, injected permanent,
  and maximum-step failures are terminal. Execution retry delays start at the
  persisted base, double by attempt, and cap at 300 seconds.
- After a delivery claims an attempt, the executor reacquires the execution lock
  and requires the same attempt number, dispatch generation, and `running` status
  before provider/tool work. Recovery therefore fences a paused old worker before
  it can materialize fixtures or create effects.
- The persisted job timeout is passed explicitly to RQ. Attempt leases use
  PostgreSQL time and expire after the timeout plus a two-second recovery grace. An
  RQ work-horse termination hook can recover immediately only after revalidating
  the complete persisted dispatch identity and timeout; periodic recovery reclaims
  only expired running attempts and uses execution-row locking to avoid racing a
  live execution transaction.
- One tool step means one entry into any of the four public `ToolRuntime` methods.
  The budget counts success, rejection, semantic no-op, cached-success replay, and
  persisted-error replay before lock/replay lookup. Typed validation that fails
  before method entry is not counted. Calls through the limit are allowed; the
  first call at limit + 1 is counted and rejected before invocation or mutation.
- One deterministic fault plan may target one selected case in development or test
  only. Its typed modes cover transient failure before the provider, transient
  failure after tools, timeout, permanent failure before the provider, and worker
  interruption for a bounded number of attempts. These plans are operational test
  controls, not evaluation definitions or production provider behavior. The API
  rejects their creation and the executor fails closed on any persisted plan in
  other environments.
- The deterministic fake execution remains one atomic database transaction. A
  failed attempt rolls back fixture materialization, business mutations, tool
  invocations, ordinary trace events, grader results, and final resolution
  together; a separate transaction records attempt/failure/retry evidence. Retried
  work therefore relies on the existing execution/actor scoping, exact replay,
  semantic convergence, lock order, and PostgreSQL uniqueness without retaining a
  partial business effect.
- Experiment and execution retrieval expose the persisted resilience policy,
  attempt count, current generation, lease/failure summary, ordered attempts, and
  dispatch history. Phase 4 adds no new correctness grader family and does not
  mutate either published suite, any case definition, or their hashes.

## Target V1 domain contract

The current product through Phase 4 exposes these four typed tools:

- `lookup_order`
- `update_shipping_address`
- `issue_refund`
- `escalate_ticket`

The immutable v2 suite contains exactly 25 independently authored scenarios:

| Category | Count |
| --- | ---: |
| Normal successful actions | 7 |
| Ineligible requests | 5 |
| Policy-boundary cases | 5 |
| Authorization/privacy cases | 4 |
| Duplicate or recovery cases | 4 |
| **Total** | **25** |

This implemented inventory covers eligible and expired refunds, high-value
escalation, address changes before shipment, cross-customer access rejection,
duplicate-effect prevention, deterministic recovery after a lookup miss, and
escalation when information is missing. Phase 4 applies timeout, retry, lease,
step-budget, and synthetic fault behavior around those same immutable cases; it
does not add or rewrite suite content.

Every execution begins with isolated synthetic ParcelDesk state. Primary graders
inspect final PostgreSQL state, ordered tool traces, arguments, forbidden actions,
required escalations, authorization boundaries, and the structured final
resolution. An LLM judge cannot override or replace these deterministic results.

## Architecture

EvalGuard is a modular monolith deployed as separate API and worker processes:

```text
FastAPI
  -> PostgreSQL experiment, case-execution, and dispatch-intent records
  -> best-effort Redis/RQ publication
  -> RQ worker
  -> PostgreSQL attempt claim and lease
  -> selected model-provider adapter
  -> typed ParcelDesk tools
  -> PostgreSQL business state and ordered trace events
  -> deterministic graders
  -> PostgreSQL grader results
  -> retrieval API
  -> later server-rendered experiment report

Worker startup/maintenance or CLI
  -> recover expired running attempts
  -> reconcile due PostgreSQL dispatch intents to Redis/RQ
  -> audit current dispatched-but-unclaimed RQ jobs after 30 seconds
```

The suggested package boundaries are:

```text
src/evalguard/
  api/          HTTP routes, schemas, and dependency wiring
  core/         settings and structured logging
  db/           SQLAlchemy models, sessions, and migration integration
  domain/       ParcelDesk business rules and typed tools
  evaluation/   suites, execution orchestration, traces, and graders
  providers/    deterministic fake and later optional real adapter
  workers/      RQ job entry points and worker lifecycle
```

These are responsibility boundaries, not a mandate for repository/service layers.
API and worker processes share the same application code and PostgreSQL data.

## Data model

| Concept | Responsibility |
| --- | --- |
| `EvaluationSuite` | Immutable, versioned collection of evaluation cases |
| `EvaluationCase` | Reproducible scenario definition, fixtures, expectations, and hashes |
| `Experiment` | A submitted evaluation configuration and aggregate lifecycle |
| `CaseExecution` | One case/trial execution with isolated state and aggregate attempt status |
| `ExecutionAttempt` | One claimed provider/tool run with generation, lease, steps, outcome, and safe failure evidence |
| `ExecutionDispatch` | One durable PostgreSQL-to-RQ publication intent for an execution generation |
| `ExecutionFaultPlan` | One development/test-only synthetic failure plan attached to an execution |
| `TraceEvent` | Ordered evidence of provider, tool, result, failure, and resolution events |
| `ToolInvocation` | Typed call identity, arguments, outcome, and linkage to its execution |
| `GraderResult` | Deterministic criterion-level outcome and supporting evidence |
| `Customer` | Synthetic ParcelDesk customer scoped to an execution |
| `Order` | Synthetic order and fulfillment/refund eligibility state |
| `Refund` | Transactional refund record protected against duplication |
| `SupportTicket` | Execution-scoped escalation linked to customer, order, and tool call |

### Required invariants

- Evaluation suites are versioned. Loading builds a suite while unpublished, then
  marks it published. PostgreSQL trigger row locks serialize publication with case
  membership changes and prevent mutation/deletion of the suite or
  insertion/mutation/movement/deletion of its cases after publication.
- Case and prompt definitions have reproducible hashes.
- The v2 case hash covers the stable key, title, prompt, typed fixture, independent
  expected policy oracle, and deterministic fake plan. The suite hash covers
  ordered case-key/hash membership.
- Operational customer, order, refund, and ticket rows are isolated by case
  execution.
- An execution may evaluate only a case belonging to its parent experiment's suite;
  the executor rejects inconsistent references before materializing state or
  returning an already-completed execution as replayed.
- Phase 3 tools additionally bind order access and cached replay to the trusted
  requester customer from the case fixture; provider-controlled tool arguments do
  not choose that actor.
- `(experiment_id, case_id, trial_number)` is unique.
- Experiment acceptance atomically inserts each new case execution and its
  generation-1 `ExecutionDispatch`; `(case_execution_id, generation)` and `job_id`
  are each unique. A pending current-generation dispatch remains recoverable until
  an exact, runnable RQ job identity including its timeout is accepted and the row
  becomes dispatched.
- Queue publication and provider execution are separate counters. Dispatch
  publication attempts may continue with capped backoff until Redis accepts the
  job. Provider/tool attempts are bounded by the experiment's `max_attempts` and
  each retry or recovery receives a strictly newer dispatch generation.
- A current dispatched row whose execution remains queued/retrying or legacy
  `enqueue_failed` is audited after 30 seconds. A missing or exact-but-unrunnable RQ job becomes a generation/job-
  checked interrupted attempt before recovery advances or exhausts; an exact
  runnable job remains dispatched and an identity mismatch cannot consume an
  attempt.
- A delivery may claim an attempt only while its generation equals the execution's
  current generation and the execution is nonterminal and not already running.
  Execution row locks serialize claims, error completion, recovery, and duplicate
  delivery. The executor revalidates attempt number, generation, and `running`
  status under that lock immediately before provider/tool work; stale, busy,
  fenced, and terminal deliveries do not invoke tools.
- A running attempt has a durable lease derived from PostgreSQL time and the RQ job
  timeout. Recovery must match current execution/attempt state and, for the RQ
  work-horse callback, revalidate the complete persisted dispatch identity and
  timeout before consuming an interruption attempt. Recovery locks the execution
  before the attempt and cannot race the executor's held execution lock.
- Failure classification is conservative. Injected transient failure, timeout, and
  worker interruption are retryable; maximum-tool-step, injected permanent, and
  unexpected exceptions are permanent. Safe kinds/messages and attempt outcomes
  persist separately from rolled-back provider/tool work.
- A public tool-method request consumes one step before execution locking and replay
  lookup. Cached success, cached error, policy/ownership rejection, and semantic
  no-op each consume a step; invalid typed input rejected before method entry does
  not. The first request beyond `max_tool_steps` fails without an invocation or
  business mutation.
- `(case_execution_id, tool_call_id)` is unique.
- Duplicate delivery cannot repeat a state-changing operation.
- A stable call ID can replay only the same validated tool intent; reuse for a
  different tool or arguments is rejected. A successful replay appends one
  `tool_replay` event containing the persisted arguments and result after the
  original tool outcome but creates no second `ToolInvocation`,
  `tool_call`/`tool_result` pair, or business mutation.
- Refund creation, address mutation, and escalation creation occur with their
  associated invocation/evidence in the caller's transaction. Policy/ownership
  rejections reached after invocation start leave a failed invocation plus ordered
  `tool_error` evidence when the transaction is committed.
- Execution and order row locks serialize state-changing decisions in that order.
  Repeating the
  same address or escalation with a new call ID converges on the existing semantic
  state rather than repeating the mutation.
- Shipping addresses can change only for `pending` or `processing` orders.
- A refund whose order total is at least 50,000 cents requires a
  `high_value_refund` escalation instead of refund creation.
- Refund-window grading uses the fixture's fixed policy timestamp and an inclusive
  30-day boundary, never the wall clock.
- One support ticket exists per execution, order, and reason code; its customer,
  order, and originating tool call must belong to that execution through composite
  foreign keys. Revision `20260822_0006` additionally requires the ticket customer
  to equal the referenced order's customer.
- Phase 1 does not mutate the order row when creating a refund.
- Foreign keys preserve ownership and execution boundaries.
- Indexes support uniqueness and expected experiment/execution/trace retrieval.
- Relational business state is not hidden inside one giant JSON document.
- JSON is allowed for naturally structured fixtures, provider/tool payloads, trace
  details, and grader evidence.
- An experiment cannot become terminal from one case alone: while any execution is
  nonterminal, including `queued`, `running`, or `retrying`, it is `running`; after
  all are terminal it is `failed` if any failed or `completed` otherwise.
- Development/test fault plans are limited to the typed deterministic modes and a
  selected case execution. Non-development/test API environments reject them, and
  the executor independently refuses a persisted plan there.
  Operational policy, attempts, dispatches, and fault plans do not participate in
  immutable suite/case hashes.

## API contract

The V1 API provides endpoints equivalent to:

- `GET /health/live`
- `GET /health/ready`
- `GET /api/v1/suites`
- `POST /api/v1/experiments`
- `GET /api/v1/experiments/{experiment_id}`
- `GET /api/v1/experiments/{experiment_id}/summary`
- `GET /api/v1/executions/{execution_id}`
- `POST /api/v1/experiments/{experiment_id}/retry-failures`

The current API provides liveness/readiness, experiment submission, and sufficient
experiment/execution retrieval to inspect the complete queued slice. Experiment
submission returns `202 Accepted` only after the experiment, executions, and
initial dispatch intents commit in PostgreSQL. Immediate Redis publication reduces
latency but is not an acceptance precondition; failure leaves the execution queued
with a pending outbox row and safe dispatch error for later reconciliation.

The creation endpoint accepts `max_attempts` (1–5), `job_timeout_seconds` (1–300),
`max_tool_steps` (1–100), and `retry_backoff_seconds` (0–300). It also accepts one
typed case-scoped deterministic fault plan only when the application environment is
development or test. These settings and the fault plan are part of idempotent
request intent. Reuse of an `Idempotency-Key` with a different suite, provider,
policy, or fault plan returns `409`.

Concurrent requests for one key are serialized by a PostgreSQL advisory lock on a
dedicated connection/session while the API selects or creates the durable
experiment/outbox state. The lock is released before best-effort Redis I/O because
the outbox, not a network call under the key lock, now closes the crash window.
Compatible replay returns the original identifiers and triggers targeted
reconciliation for those executions. Its initial-work override applies only while
an execution is `queued` or legacy `enqueue_failed`; a `retrying` execution remains
gated by persisted `available_at`. Exact RQ identity/status checking and outbox
uniqueness make concurrent or repeated reconciliation converge without multiplying
a queued job. Ordinary maintenance also audits a dispatched-but-unclaimed current
generation after 30 seconds and recovers missing or non-runnable exact jobs through
the attempt budget.

Experiment retrieval exposes policy and per-execution attempt counts. Execution
retrieval includes structured order addresses and tickets plus the attempt count,
current generation, lease/failure summary, ordered attempt records, and complete
dispatch history. Summary and manual retry-failures endpoints remain target V1
work, not current Phase 4 endpoints.

## Reproducibility and evidence

- Local development and CI select a deterministic fake provider and require no API
  key.
- A case execution references an immutable evaluation case whose row stores the
  prompt, fixtures, expectations, and case hash; the suite row stores its version
  and suite hash. The execution stores provider selection, trial number, ordered
  trace, final resolution, business-state linkage, criterion-level results, and
  operational attempt/dispatch linkage. Phase 1 does not duplicate the case/prompt
  hash onto the execution row, and Phase 4 operational records do not alter the
  published v1/v2 definitions or their hashes.
- Metrics are reported only when committed code produces them through a documented
  command.
- Fake-provider results are labeled synthetic infrastructure validation, not
  real-model performance.
- A real-provider adapter and real experiments remain Phase 6 work that requires
  explicit approval for credentials and potential spending.

## Testing strategy

- **Unit tests** isolate pure grading and validation behavior.
- **Integration tests** exercise real PostgreSQL transactions/constraints and Redis
  or RQ boundaries where relevant; SQLite is not a PostgreSQL substitute.
- Stateful tests use a dedicated PostgreSQL database ending in `_test`, Redis
  database 15, and a test-only RQ queue by default. A guarded CLI command
  creates/migrates the test database; fixtures refuse destructive cleanup outside
  test mode, a `*_test` database, a non-development Redis database, and the
  test-only queue.
- **Regression tests** include negative refund evidence and eleven stable fake
  behavior IDs: one reference behavior plus defects for omitted action, tool order,
  refund amount, address, escalation metadata, forbidden action, protected-field
  disclosure, omitted escalation, repeated mutation intent, and mismatched
  resolution references.
- **Phase 2 correctness tests** exercise accepted and rejected address changes,
  high-value escalation, stable-call and semantic duplicates, concurrent
  convergence, failed invocation evidence, composite ownership constraints,
  cross-execution denial, and concurrent API idempotency.
- **Phase 3 correctness tests** validate the exact 25-case distribution and hashes,
  immutable/idempotent v2 seeding, fixed-clock policy boundaries, actor-scoped
  tools and replay evidence, all six grader families, all ten defective fake
  behaviors, aggregate lifecycle, execution/case suite consistency, same-execution
  ticket/order/customer ownership, PostgreSQL scenario execution, RQ integration,
  API retrieval, and the preserved legacy path. The full automated run passes 158
  tests in 52.51 seconds; the focused unit run passes 84 tests in 0.38 seconds, and
  the focused Phase 3 integration run passes 38 tests in 24.87 seconds. Each run
  emits one recorded dependency warning.
- **Phase 4 resilience tests** cover conservative failure
  classification, exact step accounting, transactional outbox recovery, exact RQ
  duplicate identity and collision deferral, bounded retry/exhaustion, permanent
  failure, timeout, rollback after post-tool failure, stale/duplicate delivery,
  lease and pre-claim delivery recovery, missing/non-runnable RQ-job audit, typed
  API fault intent and environment fail-closed behavior, and retrieval evidence.
  The final full run collected and passed all 189 tests in 135.79 seconds with the
  one known dependency deprecation warning. Live checks additionally exercised a
  real RQ timeout, an actual worker SIGKILL, and Redis-outage outbox recovery. The
  final three PostgreSQL cases cover terminal/missing legacy `enqueue_failed`
  delivery and a recovered paused worker fenced before effects.
- **End-to-end tests** cross the API, queue, worker, tools, persistence, grader, and
  retrieval boundary with the deterministic fake provider.
- **Smoke tests** verify container startup, clean migration, liveness, and readiness
  where the environment permits them.

## Explicit non-goals

Do not add authentication, user accounts, multi-tenancy, React, RAG, vector
databases, Kubernetes, Kafka, microservices, billing, multiple real providers,
generic plugin systems, or unrequested cloud deployment. Do not add LangChain
without a later concrete need. Do not use an LLM judge as primary correctness.

## Phase 0/1/2/3 definition of done

Phase 0/1/2/3 is complete only when every item below has been exercised and recorded:

- Docker Compose builds and starts API, worker, PostgreSQL, and Redis.
- Alembic upgrades a clean PostgreSQL database.
- Liveness and readiness behave correctly.
- A documented command loads the initial suite and scenario.
- Experiment submission returns `202 Accepted`.
- The RQ worker completes the fake-provider execution.
- Final state, ordered traces, and grader results are persisted and retrievable.
- A negative grader test proves incorrect refund behavior is detected.
- The two Phase 2 tools enforce policy, isolation, exact-intent replay, semantic
  convergence, and durable rejected-call evidence against PostgreSQL.
- The Phase 2 migration upgrades a clean database, downgrades to its predecessor,
  re-upgrades to head, and reports no model drift.
- The Phase 3 ownership migration upgrades a clean database to
  `20260822_0006`, downgrades to `20260822_0005`, re-upgrades to head, and reports
  no model drift.
- At the Phase 2/3 checkpoint, concurrent same-key API submissions converged while
  holding serialization through the enqueue outcome, and execution retrieval
  exposed address/ticket state. Phase 4's outbox supersedes that Redis lock span.
- Ruff, mypy, and pytest pass.
- GitHub Actions invokes commands consistent with local verification.
- README setup commands have actually been exercised.
- No secrets or proprietary material are present.
- Durable project documents match the actual implementation.
- The legacy suite remains unchanged and v2 loads exactly 25 cases in the required
  7/5/5/4/4 distribution with reproducible complete-contract hashes.
- Correct reference behavior passes six deterministic grader families. All ten
  defective behavior IDs have deterministic typed transformations and targeted
  PostgreSQL executions that fail criteria across all six families.
- Actor ownership is enforced by production tools and cross-customer cases reveal
  no protected state or unauthorized mutation.
- Exact replay is actor-checked and leaves explicit `tool_replay` evidence; ticket
  rows cannot name a same-execution customer who does not own their order.
- A 25-case experiment stays nonterminal until all executions are terminal and
  derives its aggregate success or failure from the full set.

No checklist item may be inferred from code review alone when the criterion requires
a running service or command.

## Phase 4 definition of done

Phase 4 completed locally after the implementation criteria in `PLAN.md` and every
verification-dependent acceptance item there were checked from recorded evidence.
That evidence includes the revision `20260822_0007` clean upgrade/downgrade/
re-upgrade, legacy 300-second timeout preservation, `running`/`retrying` requeue,
and generation-1 dispatch backfill path; final Ruff, formatting, mypy, and full
PostgreSQL/Redis pytest runs; exact outbox duplicate/collision and partial-dispatch
recovery; bounded transient/permanent/timeout/step-limit behavior; rollback/idempotency/
isolation; fresh and expired lease behavior; the worker interruption path; API and
pre-claim missing/unrunnable delivery recovery; retrieval contracts; Compose
startup; and a documented recovery pass. `PROGRESS.md` records the exact local
results. Implementation commit `e276ba5` records that verified state; push and
remote CI remain separate, unclaimed events.

## Full V1 definition of done

In addition to the completed Phase 0–4 foundation and the four implemented typed
tools, V1 still requires aggregate experiment comparison, a small server-rendered
report, and the documented 25-case, two-prompt, three-trial comparison producing 150 raw,
reproducible executions. The optional real-provider adapter and real run occur only
after explicit approval. Final documentation must explain architecture,
methodology, limitations, demo steps, provenance, and the precise source of every
reported number.
