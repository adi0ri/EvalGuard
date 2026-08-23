# EvalGuard Learning Notes

These notes explain the architecture in interview-ready language. Phase 0–4 result
counts below come from recorded automated and live synthetic evidence. Phase
3 commits `083808c` and `6655e79` are pushed; `origin/main` remains at `6655e79`.
No Phase 3 remote-CI result is claimed. Phase 4 resilience behavior and
local acceptance are complete, and implementation commit `e276ba5` records that
verified state. Local `main` remains ahead of `origin/main` at `6655e79`;
Phase 4 remains unpushed and no remote-CI result is claimed. Phase 5 analysis/
reporting and Phase 6 real-provider work remain future scope and have not started.

## Implemented concepts through Phase 4

### FastAPI request flow

- **Problem it solves:** The harness needs a typed HTTP boundary for submitting
  long-running experiments and retrieving their state without exposing database or
  queue details to clients.
- **How it works in EvalGuard:** FastAPI validates an input model, resolves settings
  and a request-scoped SQLAlchemy session, calls focused application/domain code,
  persists and commits the experiment, its selected executions, and generation-1
  dispatch intents, then makes a best-effort queue reconciliation and serializes a
  response. A keyed submission uses a dedicated PostgreSQL connection/session and
  holds its advisory lock through durable selection/creation, then releases it
  before Redis I/O because the outbox owns recovery. Retrieval routes read durable
  state, including policies, attempts, dispatches, leases, shipping addresses,
  support tickets, and aggregate case status; workers evaluate outside the request.
- **Why this approach:** FastAPI and Pydantic make validation and response contracts
  explicit while dependency injection gives each request a clear resource
  lifecycle.
- **Alternative and tradeoff:** Flask can be smaller and flexible, but typed
  validation and OpenAPI wiring require more manual choices. Running the evaluation
  directly in the route is simpler initially but makes timeouts and failures part
  of the HTTP request.
- **Common failure modes:** Sharing a session across requests, doing slow work in
  the route, returning before the persistence commit, leaking internal exceptions,
  and allowing response models to drift from stored lifecycle states.
- **Interview explanation:** “The API is a validation and orchestration boundary.
  It durably records accepted work and hands execution to RQ; polling reads the
  source-of-truth state from PostgreSQL.”

### `202 Accepted`

- **Problem it solves:** Evaluation is asynchronous, so the server cannot truthfully
  say the result is complete when it only accepted work.
- **How it works in EvalGuard:** Experiment creation returns HTTP 202 with a durable
  experiment identifier and every selected execution identifier after records exist
  with their initial dispatch intents. Immediate Redis/RQ publication is best
  effort, not an acceptance precondition. If it fails, the response remains 202,
  executions remain queued, and pending outbox rows retain safe error evidence for
  later API replay or worker/CLI reconciliation. The client follows retrieval
  endpoints until the aggregate reaches a terminal state.
- **Why this approach:** It cleanly separates request latency from provider/tool
  latency and gives failures a durable lifecycle that can be inspected later.
- **Alternative and tradeoff:** `200 OK` with a completed response is appropriate
  for synchronous work but risks HTTP timeouts here. `201 Created` can describe the
  experiment resource, but 202 communicates that background processing remains.
- **Common failure modes:** Returning 202 before anything durable exists, omitting a
  polling identifier, treating 202 as proof of success, treating Redis failure as
  terminal evaluation failure, or accepting work without a recoverable dispatch
  intent.
- **Interview explanation:** “202 means accepted, not completed. The response points
  to durable state, and a worker advances that state independently.”

### PostgreSQL schemas, indexes, and foreign keys

- **Problem it solves:** Stateful evaluations need durable relationships,
  concurrency-safe invariants, and efficient retrieval—not just serialized blobs.
- **How it works in EvalGuard:** Current tables model suites, cases, experiments,
  executions, traces, invocations, grader results, customers, orders with
  relational shipping-address fields, refunds, and support tickets. Composite
  foreign keys require a ticket's customer, order, and originating tool call to
  belong to the same execution. Revision `20260822_0006` additionally requires the
  ticket customer to own that order. Unique constraints protect experiment/trial,
  tool-call, refund, and execution/order/reason ticket identities. Phase 4 adds
  experiment retry policy; execution attempt/generation/lease summaries; durable
  attempt and dispatch tables; and one development/test fault-plan row per
  execution. Unique execution/generation and job IDs protect dispatch identity,
  unique execution/attempt numbers protect attempt history, and due/lease indexes
  support bounded recovery sweeps.
- **Why this approach:** PostgreSQL can enforce the same rules under concurrent API
  and worker processes that the application expects in normal code paths.
- **Alternative and tradeoff:** A single JSON document is easy to snapshot but
  makes cross-row ownership, uniqueness, transactional updates, and grader queries
  harder to enforce. JSON remains useful for naturally structured payload evidence.
- **Common failure modes:** Missing foreign-key indexes on common joins, nullable
  ownership columns that permit leakage, uniqueness enforced only in Python,
  unbounded JSON with no schema validation, and deletes that orphan evidence.
- **Interview explanation:** “Relational columns hold identities and business
  invariants; JSON holds variable-shaped evidence. PostgreSQL is an active
  correctness boundary, not merely storage.”

### SQLAlchemy sessions and transactions

- **Problem it solves:** API and worker operations need a controlled unit of work so
  related writes either all commit or all roll back.
- **How it works in EvalGuard:** A session is created for one request or worker unit
  of work, passed explicitly to focused functions, committed at an intentional
  boundary, rolled back on exception, and closed. Fixture creation, successful tool
  evidence, refund creation, grading, and completion share one deterministic-worker
  transaction. State-changing tools first lock the execution row to serialize call
  identity and trace order, then lock the execution-owned order before policy and
  mutation decisions. Address, refund, or escalation state and its invocation/trace
  evidence therefore commit together. Policy/ownership rejections reached after
  invocation start mark it failed and append `tool_error`; callers that retain the
  transaction can commit that rejection evidence. The refund does not mutate the
  order row. Phase 4 commits the attempt claim/lease separately, then still runs
  fixture/provider/tool/grader work atomically. Any failed attempt rolls that unit
  back and uses a new transaction to store its safe failure, step count, and either
  retry scheduling or terminal outcome. A post-tool transient fault therefore
  cannot leave a refund, invocation, partial trace, or grader row from the failed
  attempt; a later generation starts from clean execution-owned state.
- **Why this approach:** SQLAlchemy 2 provides explicit transaction behavior while
  keeping models and queries typed enough to maintain. Short session lifetimes
  avoid cross-request state and stale identity-map surprises.
- **Alternative and tradeoff:** Raw SQL offers maximum visibility and control but
  requires more mapping and repeated query plumbing. A global session looks simple
  but is unsafe across concurrent requests and jobs.
- **Common failure modes:** Committing halfway through a business operation, catching
  an exception without rollback, accessing lazy relationships after the session
  closes, sharing sessions across threads/processes, and confusing `flush` with
  `commit`.
- **Interview explanation:** “The session scopes database work; the transaction
  scopes atomic business change. Flushing sends SQL, while committing makes the
  complete unit durable.”

The current deterministic-worker transaction is deliberately long enough to cover
the complete local, deterministic execution. That keeps the first slice atomic and
easy to reason about, but it would be a poor fit for a slow network model call
because it would hold a connection and locks while waiting. A real-provider phase
should persist shorter steps and make resume/retry behavior explicit.

### Alembic migrations

- **Problem it solves:** Database structure must evolve reproducibly across fresh
  development, CI, and existing databases.
- **How it works in EvalGuard:** SQLAlchemy models express the application mapping,
  while committed Alembic revisions define the ordered schema changes. Revision
  `20260822_0005` adds six nullable order-address columns plus relational support
  tickets and their execution-scoped ownership constraints. Revision
  `20260822_0006` adds an order/customer/execution key and matching ticket foreign
  key so a same-execution non-owner cannot be attached to an order. Revision
  `20260822_0007` adds Phase 4 policy, attempt, dispatch, lease, failure, and fault-
  plan state. During upgrade it preserves the former queue policy by assigning
  existing experiments a 300-second timeout, resets pre-lease legacy `running` and
  `retrying` rows to `queued`, and creates generation-1 pending dispatches only for
  queued or legacy `enqueue_failed` executions. New experiments instead use the
  30-second application default. The disposable Phase 4 check upgraded legacy rows
  from `0006` to `0007`, reported no drift, downgraded/re-upgraded, repeated
  normalization of restored `running`/`retrying` rows, and removed its exact
  disposable database without touching normal data.
- **Why this approach:** Versioned migrations are reviewable and let schema state
  travel with code. They make deployment changes explicit rather than relying on
  ad hoc table creation.
- **Alternative and tradeoff:** `metadata.create_all()` is useful in tiny throwaway
  tests but cannot express safe upgrades or a historical path. Handwritten SQL files
  are viable but lose Alembic's revision graph and SQLAlchemy integration.
- **Common failure modes:** Trusting autogenerate without review, editing an applied
  revision, model/migration drift, migrations that assume existing data shape, and
  testing only against an already-populated database.
- **Interview explanation:** “Models describe current code expectations; migrations
  are the audited procedure for moving a real database between versions.”

### Redis and RQ

- **Problem it solves:** HTTP handling must be separated from potentially slow,
  failure-prone evaluation work.
- **How it works in EvalGuard:** Redis holds RQ queue/job coordination. FastAPI stores
  the experiment, executions, and stable generation-specific dispatch intents in
  PostgreSQL, then best-effort enqueues a small job containing execution ID and
  generation. A separate RQ worker claims a durable attempt/lease, loads
  authoritative state, runs the fake provider and tools, grades the execution, and
  persists lifecycle changes. Worker startup/maintenance and the bounded CLI first
  recover expired attempts, then publish due pending dispatches and audit current
  dispatched-but-unclaimed queued/retrying/legacy `enqueue_failed` deliveries after
  30 seconds. RQ's official
  [jobs](https://python-rq.org/docs/jobs/),
  [exceptions](https://python-rq.org/docs/exceptions/), and
  [workers](https://python-rq.org/docs/workers/) documentation define the timeout,
  unexpected work-horse, and maintenance hooks used at this boundary.
- **Why this approach:** RQ is intentionally small for one background workflow and
  makes the worker boundary real without introducing a distributed platform.
- **Alternative and tradeoff:** In-process background tasks are easier but disappear
  with the API process and do not model an independent worker. Celery offers more
  routing/scheduling features at greater configuration and operational cost.
- **Common failure modes:** Passing ORM objects through the queue, assuming Redis is
  the source of business truth, deploying incompatible API/worker code, failing to
  expose job failure in PostgreSQL, accepting work without a durable outbox, and
  trusting a duplicate job ID without checking its complete dispatch identity.
- **Interview explanation:** “PostgreSQL owns durable evaluation state; Redis/RQ only
  dispatches stable IDs to a separate worker that rehydrates the job.”

### Background-worker lifecycle

- **Problem it solves:** A queued execution needs observable states and cleanup even
  when provider, tool, database, or worker operations fail.
- **How it works in EvalGuard:** The case execution moves through explicit states
  such as queued, running, retrying, completed, or failed. A generation-matched
  delivery locks and claims one numbered attempt with a lease; older deliveries
  return stale, concurrent current delivery returns busy, and terminal delivery
  replays without provider/tool work. Before invoking typed components, the
  executor locks the execution and rechecks the claimed attempt number, generation,
  and `running` status. A recovered paused worker therefore returns stale before
  fixtures or effects. The job commits the completed path atomically.
  Policy/ownership rejections reached after invocation start can be retained as
  failed invocations with ordered `tool_error` evidence. On a fatal worker error,
  its transaction rolls back and a fresh transaction stores failed status plus a
  classified attempt failure and retry/terminal summary. Explicit transient,
  timeout, and interruption failures may schedule a newer generation while the
  attempt budget remains; permanent, maximum-step, and unknown failures stop. Each
  worker also locks and reconciles the parent from all child statuses: any
  nonterminal child keeps it running; once all are terminal, any failed child makes
  the parent fail, otherwise it completes. Fresh leases are left alone; expired
  running attempts or an exact generation/job work-horse callback can schedule one
  recovery generation under the same execution lock. A current delivery lost or
  terminal in RQ before claim is fenced by the same IDs, recorded as an interrupted
  zero-step attempt, and then advanced or exhausted rather than left queued forever.
- **Why this approach:** A durable state machine lets clients and operators explain
  what happened independently of RQ's transient process output.
- **Alternative and tradeoff:** Treating the RQ job status as the only lifecycle
  removes duplicate state, but it separates operational status from evaluation
  evidence and makes retrieval depend on queue retention.
- **Common failure modes:** Jobs stuck in running after process death, terminal state
  written before all evidence commits, database sessions created in the API process
  and reused in the worker, swallowed exceptions, and non-atomic status/result
  updates.
- **Interview explanation:** “RQ runs work, but our PostgreSQL lifecycle is the
  product contract. Each job owns fresh resources and records a durable terminal
  outcome.”

### Dependency readiness

- **Problem it solves:** A running process is not necessarily able to serve useful
  traffic when PostgreSQL or Redis is unavailable.
- **How it works in EvalGuard:** Liveness answers whether the API process can respond.
  Readiness executes `SELECT 1` against PostgreSQL and `PING` against Redis, then
  returns `503` when either dependency is unavailable. The live smoke verified that
  stopping Redis leaves liveness at `200` while readiness becomes `503`.
- **Why this approach:** Separating the probes avoids restarting a healthy process
  merely because a dependency is briefly unavailable while preventing traffic from
  reaching an unusable instance.
- **Alternative and tradeoff:** A single health endpoint is easier but cannot
  distinguish process failure from dependency unavailability. Deep checks of every
  downstream behavior provide more confidence but can be slow and fragile.
- **Common failure modes:** Readiness returning success from cached state, no timeout,
  checking only TCP connectivity when a query/command is required, exposing secrets
  in error output, or making liveness depend on external services.
- **Interview explanation:** “Liveness asks ‘should this process be restarted?’;
  readiness asks ‘can it safely receive work now?’”

### Typed tools and argument validation

- **Problem it solves:** Model-produced tool input is untrusted structured data and
  must not reach business mutations with missing or malformed fields.
- **How it works in EvalGuard:** All four tools—`lookup_order`, `issue_refund`,
  `update_shipping_address`, and `escalate_ticket`—accept validated Pydantic v2
  argument models and return typed result models; their object schemas reject
  undeclared fields. Validation happens before domain logic. Successful
  calls/results are traced, and policy/ownership rejections after
  invocation start record a failed invocation plus `tool_error` in the surrounding
  transaction. A stable call ID replays only when its tool name and validated
  arguments are exactly the same; conflicting reuse is rejected. Every tool takes
  locks in execution-then-order order. Successful exact replay rechecks actor
  ownership and appends one `tool_replay` event after the original tool outcome,
  without another invocation or mutation. Phase 4 defines one tool step as one
  request entering any of these four public methods. The step is consumed before
  the execution lock and cached-invocation lookup, so successes, rejected calls,
  semantic no-ops, cached successes, and persisted-error replays count. Typed
  validation rejected before method entry does not count. Calls through the limit
  run; the counted limit+1 request fails before invocation, trace, or mutation.
- **Why this approach:** A narrow typed boundary makes tool contracts testable and
  gives deterministic, explainable failures instead of scattered dictionary access.
- **Alternative and tradeoff:** Raw dictionaries are flexible but defer errors and
  weaken schemas. Provider-specific tool objects may reduce adapter code but couple
  domain logic to one vendor.
- **Common failure modes:** Coercing dangerous values silently, validating syntax but
  not ownership/business eligibility, letting provider schemas drift from runtime
  models, or recording only successful calls.
- **Interview explanation:** “Pydantic validates shape at the adapter boundary;
  domain code still enforces authorization and business rules inside the
  transaction.”

### Deterministic fake provider

- **Problem it solves:** CI needs to exercise the real orchestration path without
  network variance, credentials, or spend.
- **How it works in EvalGuard:** The preserved v1 path emits the original lookup and
  refund sequence. Each of the 25 v2 cases stores a typed deterministic plan that
  can invoke any of the four production tools and construct a structured result
  from the selected tool result. Eleven stable behavior IDs provide one reference
  path and ten controlled defects without bypassing production tool boundaries.
  Separately, an execution-level Phase 4 fault plan can deterministically fail a
  configured number of attempts before the provider, after tools, by timeout, by
  permanent error, or by interruption. The API permits this plan only in
  development/test; it is operational test state rather than a hashed scenario or
  another fake behavior ID.
- **Why this approach:** It makes failures reproducible while testing more than a
  mocked route or worker. The result establishes infrastructure correctness, not
  model competence.
- **Alternative and tradeoff:** A live model is representative but nondeterministic,
  slower, credentialed, and potentially costly. Mocking every collaborator makes
  tests fast but does not validate integration.
- **Common failure modes:** Giving the fake a privileged shortcut around tools,
  varying output by wall-clock state, treating its pass rate as model quality, or
  coupling the orchestration directly to fake-only methods, enabling fault controls
  in production, or treating injected recovery as evidence of real-provider
  reliability.
- **Interview explanation:** “The fake is a deterministic contract test double that
  traverses the production-shaped path. Its results are synthetic infrastructure
  validation.”

### Deterministic grading

- **Problem it solves:** Natural-language answers alone cannot prove that the right
  authorized side effect occurred or that forbidden actions were avoided.
- **How it works in EvalGuard:** The legacy refund grader remains intact. V2 stores
  an expected policy oracle independently from its fake plan, then six grader
  families inspect committed state, exact calls and ordered trace, policy,
  authorization/privacy, escalation, and final structured resolution. Targeted
  PostgreSQL executions for all ten fake defects fail criteria across all six
  families. `invocation_outcomes_match` checks success, rejection, and duplicate
  results; `ticket_owner_matches_order` checks relational ownership; and privacy
  grading detects protected string or numeric values embedded in free text. String
  matching uses token boundaries so a short protected value such as `US` is not a
  false positive inside `CUSTOMER`; numeric matching recognizes grouped forms such
  as `9,125` without treating digits inside a larger number as the same value.
- **Why this approach:** Rules over authoritative state are repeatable, inspectable,
  and suitable for regression detection. Criterion-level output shows why the
  aggregate result passed or failed.
- **Alternative and tradeoff:** An LLM judge can assess subjective phrasing, but it
  is variable and cannot be trusted as primary correctness for exact policy and
  state changes. Final-text matching is deterministic but too shallow.
- **Common failure modes:** Grading intended calls instead of committed state,
  checking only that some refund exists, ignoring execution/customer ownership,
  losing trace order, and writing tests that can never observe an incorrect case.
- **Interview explanation:** “The database and trace are evidence. The grader is a
  pure, named set of criteria over that evidence, and the negative test proves it
  can catch the target defect.”

### Versioned evaluation suites and reproducible hashes

- **Problem it solves:** Results cannot be compared honestly if a case or prompt
  changes under the same name.
- **How it works in EvalGuard:** A suite version identifies immutable case
  definitions. The loader preserves v1 and creates/verifies all 25 v2 cases while
  that suite is unpublished,
  then publishes the suite. PostgreSQL trigger row locks serialize that publication
  with concurrent case membership changes, then reject later suite edits/deletion
  and case inserts/edits/moves/deletion. Canonically serialized
  reproducibility-relevant content is hashed. An execution references the immutable
  case row, which stores the case hash; the parent suite row stores the version and
  suite hash. A v2 case hash covers its key, title, prompt, typed fixture,
  independent policy oracle, and fake plan. Its suite hash covers ordered
  case-key/hash membership. Hashes are not copied onto every execution row. Phase
  4 retry policy, attempts, generations, leases, dispatch rows, and fault plans are
  operational execution state and do not alter either published suite or hash.
- **Why this approach:** Explicit identity makes historical results auditable and
  turns definition changes into deliberate new versions.
- **Alternative and tradeoff:** Git commit identity can locate repository state but
  is coarse and may not identify dynamically loaded content. Mutable database rows
  are easy to edit but destroy experimental comparability.
- **Common failure modes:** Hashing non-canonical JSON, excluding a relevant prompt
  field, allowing post-run edits, using environment-dependent serialization, or
  confusing a display version with content identity.
- **Interview explanation:** “The human-readable version communicates intent; the
  content hash detects exact drift. An execution points to the immutable case that
  stores that identity.”

### Per-execution state isolation

- **Problem it solves:** Reused customer/order rows let one trial affect another and
  invalidate correctness comparisons.
- **How it works in EvalGuard:** Each case execution receives newly materialized
  synthetic rows linked to its execution identity. Tool queries require the same
  execution scope, and graders inspect only that scope. Phase 3 fixtures can hold
  more than one customer/order; the requester customer becomes trusted runtime
  context, so lookup, mutation, and cached replay also require actor ownership.
  Ticket rows use composite foreign keys so their customer, order, and tool call
  cannot be borrowed from another execution even if application code is bypassed,
  and the ticket customer must own the referenced order. The executor also checks
  that a case belongs to its experiment suite before normal execution or completed
  replay can return.
- **Why this approach:** It supports concurrency, repeatability, and an unambiguous
  before/after state without global resets.
- **Alternative and tradeoff:** Rebuilding the entire database for every case gives
  strong physical isolation but is slow. Shared fixtures plus cleanup are fast but
  brittle and unsafe under parallel runs.
- **Common failure modes:** Looking up an order by public ID without execution or
  actor scope, trusting a provider-supplied actor argument, replaying another
  actor's cached result, globally unique fixture identifiers masking a missing
  ownership check, incomplete cleanup, and graders joining across scopes.
- **Interview explanation:** “Isolation is encoded in ownership and query keys, not
  just in naming conventions. A negative cross-execution test proves the boundary.”

### Ordered trace events and structured resolution

- **Problem it solves:** Final state says what exists, but not how the agent got
  there, which arguments it used, or whether it attempted a forbidden action.
- **How it works in EvalGuard:** Execution-scoped events receive deterministic
  sequence numbers. The completed queued path records provider request/response,
  tool calls/results, successful exact `tool_replay`, and grader completion; the
  structured final resolution also lives on the execution. Replay evidence must
  follow the original tool outcome and match its call ID, tool, arguments, and
  result. Policy/ownership rejections reached after invocation
  start record `tool_call` followed by `tool_error` and a failed invocation. A fatal
  attempt transaction cannot retain partial events, so the worker records a
  separate `execution_retry_scheduled`, `execution_recovery_scheduled`, or terminal
  `execution_failed` summary after rollback. Attempt and dispatch tables carry the
  operational history; Phase 4 does not add another deterministic `GraderResult`
  family. Naturally structured details use JSON while identity and order remain
  relational.
- **Why this approach:** Graders and interviewers can reconstruct causality without
  scraping application logs. State plus trace supports both outcome and process
  correctness.
- **Alternative and tradeoff:** Plain logs are easy to emit but may be reordered,
  rotated, or lack durable execution linkage. A single trace JSON blob is easy to
  return but awkward for incremental writes and constraints.
- **Common failure modes:** Ordering only by timestamps, writing tool results without
  their call identity, omitting failures, storing secrets/raw credentials, and
  declaring completion before the final events commit.
- **Interview explanation:** “State proves the side effect; the ordered trace proves
  the path. Both are persisted evaluation evidence, distinct from operational
  logs.”

### Unit, integration, regression, and end-to-end testing

- **Problem it solves:** Different defects live at different boundaries, so one test
  style cannot provide fast feedback and realistic system confidence at once.
- **How it works in EvalGuard:** Unit tests cover pure graders and validation;
  PostgreSQL integration tests cover accepted/rejected address changes, required
  escalation, exact replay, semantic and concurrent duplicates, failed-call
  evidence, constraints, and isolation; negative grader tests lock in detection of
  absent/wrong refunds; Phase 3 tests add exact inventory/hashes, actor-scoped
  PostgreSQL calls, six evidence graders, eleven fake behavior IDs, aggregate
  lifecycle, a 25-case RQ/API path, cross-suite execution rejection, relational
  ticket-owner enforcement, frozen/cross-validated contracts, and legacy
  compatibility. Phase 4 adds authored unit and PostgreSQL/Redis/RQ/API coverage for
  failure classification, exact steps, outbox identity/collision/partial recovery,
  bounded retry/exhaustion, timeout, rollback, duplicate/stale delivery, leases,
  initial-only replay, pre-claim and missing/non-runnable delivery recovery,
  environment fail-closed fault intent, and retrieval evidence. Focused Phase 3
  unit coverage passes 84
  tests in 0.38 seconds, focused
  Phase 3 integration coverage passes 38 tests in 24.87 seconds, and the full suite
  at the Phase 3 checkpoint passes 158 tests in 52.51 seconds. Each recorded Phase
  3 run emits only the dependency warning. The final Phase 4 run collected and
  passed all 189 tests in 135.79 seconds with the same one known Starlette
  `TestClient`/httpx deprecation warning. The final count rose by three PostgreSQL
  regressions: two legacy `enqueue_failed` delivery states and one paused-worker
  claim fence.
- **Why this approach:** The pyramid keeps logic failures cheap to diagnose while a
  small number of boundary-crossing tests catch wiring and lifecycle errors.
- **Alternative and tradeoff:** Only end-to-end tests are realistic but slow and
  difficult to debug. Only unit tests are fast but can leave database, queue, and
  serialization contracts unproven.
- **Common failure modes:** Replacing PostgreSQL with SQLite, mocking away the worker
  in the only end-to-end test, flaky polling without a deadline, shared test state,
  destructive cleanup accidentally targeting development data, a blank test
  database on reused Compose volumes, and calling a test a regression test without
  a known defect it protects.
- **Interview explanation:** “Unit tests prove decisions, integration tests prove
  boundaries, regression tests preserve learned behavior, and end-to-end tests
  prove the user-visible workflow.”

Stateful tests use a dedicated PostgreSQL database ending in `_test`, Redis database
15, and the `evalguard-tests` queue. `prepare-test-db` creates/migrates that database
idempotently, while fixtures reject unsafe environment/database/queue combinations
before truncating or deleting anything. This is defense in depth: isolation comes
from separate targets, and validation prevents a configuration mistake from turning
test cleanup into development-data cleanup.

### Address and escalation policy

- **Problem it solves:** A typed mutation is not correct merely because its input
  has the right shape; it must enforce fulfillment and escalation policy against
  authoritative state.
- **How it works in EvalGuard:** `update_shipping_address` locks and scopes the order,
  then permits a change only in `pending` or `processing`. `issue_refund` rejects an
  order total at or above 50,000 cents, which requires a ticket with reason code
  `high_value_refund` instead. `escalate_ticket` derives the customer from the
  execution-owned order and creates one relational ticket per execution, order,
  and reason code. Phase 3 policy grading additionally uses a fixed fixture
  timestamp and inclusive 30-day refund window rather than reading the wall clock.
  API retrieval renders address and ticket state explicitly.
- **Why this approach:** Policy lives next to the transaction that owns the side
  effect, so every caller receives the same rule and graders can inspect durable
  state instead of trusting prose.
- **Alternative and tradeoff:** Encoding policy only in a prompt is flexible but
  unenforceable. A generic ticket JSON payload is easy to add, but it cannot enforce
  ownership and semantic uniqueness as strongly as relational keys.
- **Common failure modes:** Checking status before acquiring the row lock, trusting
  a caller-supplied customer, creating both a high-value refund and escalation,
  treating `shipped` as changeable, and storing an escalation outside execution
  scope.
- **Interview explanation:** “The model proposes an action; the transaction enforces
  policy. Fulfillment state decides whether an address can change, and high-value
  refunds become one execution-scoped escalation rather than a payment mutation.”

### Idempotency and semantic convergence

- **Problem it solves:** HTTP clients, queues, and tool providers may repeat a
  request after a timeout even when the first attempt reached durable state.
- **How it works in EvalGuard:** Concurrent experiment requests sharing an
  `Idempotency-Key` take a deterministic PostgreSQL advisory lock on one dedicated
  connection/session, held through durable selection or creation of the experiment,
  executions, and outbox rows. The lock is released before best-effort Redis I/O.
  Compatible requests converge on one experiment and one dispatch row per selected
  execution generation, then target those durable executions for reconciliation.
  The initial-work override applies only to queued/legacy `enqueue_failed` work;
  `retrying` remains `available_at` gated. A conflicting suite, provider, retry
  policy, or fault plan is rejected. At the tool layer, execution-then-order locks
  serialize decisions, a
  call ID can replay only the same tool and validated arguments, and successful
  replay adds an ordered `tool_replay` event. A new call ID
  requesting an already-applied
  address or escalation returns a duplicate result without a second mutation.
  PostgreSQL uniqueness remains the final invariant boundary.
- **Why this approach:** Idempotency preserves request identity while semantic
  convergence handles providers that legitimately assign a new call ID on retry.
  The durable outbox replaces the earlier need to hold the API key lock across a
  network call and closes the partial multi-job crash window.
- **Alternative and tradeoff:** A pre-insert lookup without a lock races under
  concurrency. Holding a session-level advisory lock through Redis serialized the
  Phase 2 outcome but could not recover an API process crash. The outbox adds rows
  and reconciliation, while limiting key serialization to durable intent.
- **Common failure modes:** Reusing a call ID for different arguments, inserting an
  execution without its dispatch intent, checking semantic duplicates without
  locking, keeping the idempotency key only in Redis, trusting a job-ID collision,
  letting replay collapse delayed retry backoff, and ignoring the dispatched-job
  audit's detection interval.
- **Interview explanation:** “Retries are expected. Stable identity proves exact
  replay, semantic checks make new-call retries converge, and database locks plus
  uniqueness decide the outcome under concurrency.”

### Scenario taxonomy and fixed policy time

- **Problem it solves:** A broad evaluation suite can look impressive while hiding
  duplicated happy paths, and time-dependent rules can change merely because the
  test ran on another day.
- **How it works in EvalGuard:** The immutable v2 contract contains exactly 25
  cases in a deliberate 7 normal / 5 ineligible / 5 policy-boundary / 4
  authorization-privacy / 4 duplicate-or-recovery distribution. Every fixture
  stores `policy_as_of`; refund-window grading compares that value with
  `delivered_at` using an inclusive 30-day rule. Frozen cross-field validation
  requires the final resolution and every expected mutation to name the policy
  order, and prevents denied outcomes from expecting mutation. Case hashes include
  the fixture, expected policy oracle, and fake plan.
- **Why this approach:** Category counts make breadth reviewable, while a fixed
  clock makes boundary outcomes reproducible across machines and years.
- **Alternative and tradeoff:** Calling `datetime.now()` is convenient but turns a
  stable test into a moving target. A loose pile of scenarios is easier to grow but
  provides no coverage contract.
- **Common failure modes:** Counting suite rows across versions instead of cases in
  the selected version, omitting category from the hashed contract, using local
  time without an offset, or defining the last eligible day inconsistently.
- **Interview explanation:** “The taxonomy makes coverage intentional; the fixed
  policy clock makes time-based correctness reproducible.”

### Independent grader families and defective fake behaviors

- **Problem it solves:** One aggregate pass bit cannot distinguish a wrong side
  effect from a forbidden attempt, privacy leak, missing escalation, or misleading
  final answer. Reference-only tests do not prove that a grader detects defects.
- **How it works in EvalGuard:** V2 stores its expected policy contract separately
  from the fake plan. `state_v1`, `trace_v1`, `policy_v1`,
  `authorization_v1`, `escalation_v1`, and `resolution_v1` each persist named
  criteria. Trace criteria verify invocation outcomes and replay ordering;
  escalation and state criteria verify ticket/order/customer ownership. Privacy
  criteria search both keys and protected string/numeric values in structured or
  free-text output, with token-aware string comparison and grouped-number
  recognition. The fake exposes one reference behavior and ten stable defect IDs
  for omitted actions, wrong order/arguments, forbidden actions, privacy disclosure,
  missing escalation, repeated mutation intent, and mismatched resolution links.
- **Why this approach:** Independent dimensions produce explainable failures, and
  intentional defects demonstrate sensitivity instead of assuming it.
- **Alternative and tradeoff:** One large grader shares more setup but couples
  unrelated rules. Generating expectations from the provider plan reduces typing
  but risks reproducing the same bug in both agent and oracle.
- **Common failure modes:** A defect that bypasses production tools, criteria that
  inspect only final prose, counting a safely rejected forbidden call as success,
  or reporting a fake defect-detection rate as model quality.
- **Interview explanation:** “The plan drives behavior; the independent oracle
  grades evidence. Deliberate defects prove each important evaluator dimension can
  turn red.”

### Aggregate lifecycle for multi-case experiments

- **Problem it solves:** With 25 queued executions, the first finished case cannot
  truthfully decide that the whole experiment is complete.
- **How it works in EvalGuard:** Success and failure paths lock the parent
  experiment and read every child status. Any queued or running child keeps the
  parent running; Phase 4 `retrying` is nonterminal too. Once every child is
  terminal, any failed child makes the parent fail; otherwise it completes. New
  submissions no longer use Redis `enqueue_failed` as the transport-outage state;
  queued work remains paired with a pending outbox row.
- **Why this approach:** PostgreSQL remains the source of lifecycle truth, and the
  parent-row lock serializes conclusions from concurrent workers.
- **Alternative and tradeoff:** Reading RQ job counts couples product state to queue
  retention. Computing status only in the GET route avoids a write but leaves no
  durable terminal record.
- **Common failure modes:** Marking the parent complete in one case executor,
  calculating before flushing the current child status, overwriting a failure with
  a later success, or treating partial enqueue recovery as solved by aggregation.
- **Interview explanation:** “A case owns its result; the parent is a reduction over
  every case under a lock. Terminal means all children are terminal.”

## Phase 4 resilience concepts

These mechanisms are implemented and locally verified. The final automated suite,
disposable migration, Compose topology, deterministic live cases, real RQ timeout,
actual worker SIGKILL, and Redis-outage recovery are recorded in `PROGRESS.md`.

### Transactional outbox and repeatable delivery

- **Problem it solves:** PostgreSQL cannot atomically commit 25 execution records
  with 25 Redis enqueues. An API process can die after publishing only a prefix, or
  after Redis accepts a job but before PostgreSQL records that fact.
- **How it works in EvalGuard:** Each execution and its generation-1 dispatch row
  commit in the same PostgreSQL transaction. The API then performs best-effort
  publication and returns 202 whether that immediate pass succeeds or defers.
  Worker/CLI reconcilers select only pending rows whose
  generation is still current, lock and commit one row at a time, and let concurrent
  general sweeps skip locked work. A transport error is recorded and stops the pass;
  an identity collision is recorded but does not masquerade as transport failure.
  If a stable job ID already exists, its queue, function, positional arguments,
  empty kwargs, timeout, and runnable status must exactly match before the dispatch
  is called successful. The legacy one-argument generation-1 shape is accepted only
  when the persisted experiment policy is its former 300 seconds; a new 30-second
  experiment rejects it. The worker validates that same persisted identity again
  before claim, so a foreign or wrong-timeout job consumes no execution attempt
  and reconciliation records a pending safe collision. In the live Redis-outage
  check, submission still returned 202 with zero execution attempts and a pending
  dispatch; worker reconciliation completed that same work after Redis returned.
- **Why this approach:** PostgreSQL records accepted intent while Redis remains a
  replaceable transport. An exact duplicate closes the enqueue/commit crash window,
  and one-row transactions preserve earlier progress during a partial outage.
- **Alternative and tradeoff:** Holding a database transaction or idempotency lock
  through Redis does not produce a distributed commit and lengthens lock time. The
  outbox adds reconciliation state and a 30-second detection window before a
  current dispatched-but-unclaimed delivery is audited.
- **Common failure modes:** Returning 503 after durable acceptance, terminalizing a
  queued execution because Redis is down, trusting job ID alone, treating a
  terminal job as runnable, retrying a stale generation, processing many rows in
  one transaction, and assuming dispatched means the job need not be audited.
- **Interview explanation:** “Acceptance and publication are different commits. We
  durably store intent, publish idempotently, and make a crash on either side
  replayable.”

### Attempt generations, leases, and duplicate delivery

- **Problem it solves:** A queued job may arrive twice, arrive after a newer retry,
  or leave PostgreSQL `running` when its worker process disappears.
- **How it works in EvalGuard:** One delivery locks the execution and claims only
  its current generation. It increments `attempt_count`, creates a unique attempt,
  and sets a PostgreSQL-time lease to job timeout plus two seconds. Another current
  delivery sees busy; an older generation sees stale; terminal delivery is a replay.
  Retry or recovery increments the generation before creating its durable dispatch.
  Worker maintenance reclaims only expired running rows, and recovery locks the
  execution so it cannot race the live executor transaction. The RQ work-horse
  callback is a faster path that revalidates the persisted dispatch identity and
  timeout before spending an interruption attempt. The same maintenance pass
  audits a current dispatched job after 30 seconds when a queued, retrying, or
  legacy `enqueue_failed` execution has no claim: exact runnable state is kept,
  while a missing or exact-unrunnable job records a zero-step pre-claim interruption
  that consumes one attempt before advancing or exhausting. A claim-setup exception
  attempts the same fenced recovery. After claim, the executor rechecks attempt
  number, generation, and `running` status under the execution lock before provider/
  tool work. Local acceptance sent SIGKILL to a worker holding a live lease and
  observed one interrupted generation followed by one successful generation, with
  one refund rather than a repeated effect.
- **Why this approach:** Generation is a fencing token, the row lock serializes
  ownership, and the lease distinguishes fresh work from an abandoned claim without
  depending on Redis registry retention.
- **Alternative and tradeoff:** A process heartbeat can detect failure sooner but
  adds writes and still needs fencing. A long lease delays recovery; a short lease
  risks false expiry, so lock acquisition remains the final live-work check.
- **Common failure modes:** Incrementing attempts on stale or identity-mismatched
  delivery, recovering a fresh lease, matching only execution ID in a kill
  callback, treating an exact terminal RQ job as still runnable, scheduling two
  recovery generations, or reading application-clock time across hosts.
- **Interview explanation:** “The generation fences old jobs; the lease finds
  abandoned current work; the execution lock decides who may advance it.”

### Bounded execution retry versus durable dispatch retry

- **Problem it solves:** Temporary execution failure may improve, permanent failure
  will not, and Redis publication failure must not consume provider attempts.
- **How it works in EvalGuard:** Failure classification is conservative. Injected
  transient, RQ timeout, and worker interruption are retryable; maximum-step,
  injected permanent, and unexpected exceptions are terminal. A retry is scheduled
  only while `attempt_number < max_attempts`; its delay starts at the experiment's
  base, doubles, and caps at 300 seconds. RQ receives the persisted hard timeout.
  EvalGuard records a caught timeout, schedules a next generation only while the
  attempt budget remains, then re-raises the RQ exception. Dispatch publication has
  its own open-ended pending state and a backoff
  multiplier capped at 32 because losing accepted work is worse than retrying Redis.
  A live one-second RQ timeout persisted a retryable timeout on generation 1 and
  succeeded on generation 2 with one refund and four passing legacy graders.
- **Why this approach:** The database exposes exactly why and how often provider/
  tool work ran, while transport recovery does not spend that budget.
- **Alternative and tradeoff:** Retrying every exception hides bugs and may repeat
  cost; relying only on RQ retry state makes Redis authoritative. Conservative
  classification can fail an unrecognized transient condition, which is safer than
  silently repeating unknown work.
- **Common failure modes:** Calling all `OSError`s execution-transient, confusing
  publish attempts with provider attempts, retrying after the cap, losing the first
  failure kind, assuming configured backoff is jittered, or letting compatible API
  replay publish a delayed retry before `available_at`.
- **Interview explanation:** “Provider attempts are bounded and classified;
  publication attempts are durable and open-ended. They solve different failures.”

### Exact tool steps, fault plans, and rollback

- **Problem it solves:** A step cap is meaningless if replay/error loops are free,
  and recovery tests are weak if faults bypass production tools or leave partial
  state.
- **How it works in EvalGuard:** Every entry into a public tool method consumes one
  per-attempt step before locking or replay lookup. Successful calls, policy and
  ownership errors, semantic no-ops, cached-success replay, and persisted-error
  replay count. Typed validation before method entry does not. The first call over
  the limit is counted and rejected before invocation or mutation. A typed
  development/test-only fault plan targets one case execution for a fixed number of
  attempts using one of five modes. Provider/tool/grader work stays in one
  transaction, so a post-tool fault rolls it all back; separate retry evidence then
  commits. The API rejects fault-plan creation and the executor refuses any
  persisted plan outside development/test, so direct database state fails closed.
  Successful attempts, maximum-step failures, and injected post-tool
  failures carry an exact attempted-step count. An unrelated exception that does
  not carry `attempted_steps` or `steps_attempted` is conservatively stored with
  step count 0 rather than reconstructed from rolled-back trace. Final regressions
  inspected intermediate post-tool rollback, single-fault isolation across all 25
  v2 cases, and production-environment fault-plan rejection with no side effects.
- **Why this approach:** The budget measures requests that could consume tool
  capacity, and deterministic faults exercise the same transaction, isolation,
  actor checks, replay rules, and uniqueness constraints as ordinary work.
- **Alternative and tradeoff:** Counting only stored invocations undercounts cached
  and rejected requests. Adding faults to hashed case plans would mutate the suite.
  Phase 4 intentionally adds no resilience grader family; attempts, dispatches,
  traces, and tests are operational evidence.
- **Common failure modes:** Allowing N+1 to mutate before checking, resetting budget
  within one attempt, retaining a failed attempt's refund or grader row, enabling
  fault plans outside development/test, or calling injected success evidence real-
  provider reliability.
- **Interview explanation:** “Every requested tool call spends budget. Faults are
  operational controls, and transactional rollback makes a retry start clean.”

## Interview questions to practice

1. Why does experiment creation return 202 rather than waiting for the agent?
2. What is durable before the API acknowledges an experiment, and what can still
   fail afterward?
3. Why is PostgreSQL integration coverage necessary when unit tests could use
   SQLite?
4. Where are SQLAlchemy session and transaction boundaries in an API request and an
   RQ job?
5. How do liveness and readiness answer different operational questions?
6. What does the deterministic fake provider prove, and what does it explicitly not
   prove?
7. Why does the grader inspect both final state and ordered trace evidence?
8. How does the negative grader test demonstrate evaluator value?
9. How do suite versions and content hashes prevent misleading comparisons?
10. How does a tool query prove execution isolation rather than merely relying on
    globally unique fixture IDs?
11. Why should queue payloads carry stable IDs instead of ORM objects?
12. How would a duplicated job avoid issuing a second refund?
13. Which failures deserve a retry, and which should become permanent immediately?
14. What different confidence do unit, integration, regression, and end-to-end
    tests provide?
15. Which claims can fake-provider results support in a portfolio, and how must they
    be labeled?
16. Why does an address change check fulfillment status while holding an order-row
    lock?
17. Why does a high-value refund become an escalation rather than a refund record?
18. What is the difference between exact call-ID replay and semantic convergence
    under a new call ID?
19. Why did Phase 2 hold one advisory-lock connection through enqueue, and why can
    Phase 4 release it before Redis I/O?
20. Why does v2 use a new immutable suite version instead of appending cases to v1?
21. Why is the expected policy oracle stored separately from the deterministic fake
    plan?
22. How does a fixed `policy_as_of` make refund-window boundaries reproducible?
23. Why must cached tool replay recheck the trusted requester customer?
24. How does the parent experiment avoid completing when only one of 25 executions
    has finished?
25. Why does ticket execution ownership not prove that its customer owns its order?
26. Why is exact replay represented by `tool_replay` instead of another tool-call
    pair, and why must it follow the original outcome?
27. How does the transactional outbox close both the partial multi-job enqueue
    window and the enqueue-success/database-commit window?
28. Why are dispatch publication attempts open-ended while provider/tool attempts
    are bounded?
29. How do dispatch generations, leases, and execution-row locks prevent duplicate
    or stale recovery from applying a second business effect?
30. Which requests consume a tool step, and why does the rejected N+1 request count?
31. Why are fault plans development/test operational state rather than immutable
    suite definitions or a new grader family?
32. Why does a missing or terminal RQ job before claim consume one interrupted
    execution attempt, while a Redis publication error does not?
