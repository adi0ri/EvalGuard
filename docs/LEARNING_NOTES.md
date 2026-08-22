# EvalGuard Learning Notes

These notes explain the architecture in interview-ready language. Sections marked
**Phase 0/1/2** describe the verified implementation. Sections marked **Future
hardening** describe constraints the current design must leave room for; they are
not claims that the later behavior has been implemented.

## Phase 0/1/2 concepts

### FastAPI request flow

- **Problem it solves:** The harness needs a typed HTTP boundary for submitting
  long-running experiments and retrieving their state without exposing database or
  queue details to clients.
- **How it works in EvalGuard:** FastAPI validates an input model, resolves settings
  and a request-scoped SQLAlchemy session, calls focused application/domain code,
  persists the experiment and execution, arranges queue dispatch, commits, and
  serializes a response. A keyed submission uses a dedicated PostgreSQL
  connection/session and holds its advisory lock across persistence, enqueue, and
  the recorded dispatch outcome. Retrieval routes read durable state, including
  shipping addresses and support tickets; the worker evaluates outside the request.
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
- **How it works in EvalGuard:** Experiment creation returns HTTP 202 with durable
  experiment/execution identifiers after records exist and queue submission has
  been accepted. The client follows retrieval endpoints until a terminal state.
- **Why this approach:** It cleanly separates request latency from provider/tool
  latency and gives failures a durable lifecycle that can be inspected later.
- **Alternative and tradeoff:** `200 OK` with a completed response is appropriate
  for synchronous work but risks HTTP timeouts here. `201 Created` can describe the
  experiment resource, but 202 communicates that background processing remains.
- **Common failure modes:** Returning 202 before anything durable exists, omitting a
  polling identifier, treating 202 as proof of success, or leaving an enqueue
  failure looking permanently queued.
- **Interview explanation:** “202 means accepted, not completed. The response points
  to durable state, and a worker advances that state independently.”

### PostgreSQL schemas, indexes, and foreign keys

- **Problem it solves:** Stateful evaluations need durable relationships,
  concurrency-safe invariants, and efficient retrieval—not just serialized blobs.
- **How it works in EvalGuard:** Current tables model suites, cases, experiments,
  executions, traces, invocations, grader results, customers, orders with
  relational shipping-address fields, refunds, and support tickets. Composite
  foreign keys require a ticket's customer, order, and originating tool call to
  belong to the same execution. Unique constraints protect experiment/trial,
  tool-call, refund, and execution/order/reason ticket identities; indexes support
  status and ordered execution reads.
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
  order row. A fatal worker failure still rolls back its unit and uses a separate
  transaction for the terminal execution failure.
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
  tickets and their composite ownership constraints. The Phase 2 check upgraded a
  disposable clean PostgreSQL database through `0005`, downgraded to `0004`,
  re-upgraded to head, and confirmed no model/migration drift.
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
  the experiment in PostgreSQL and enqueues a small job containing stable database
  identifiers. A separate RQ worker loads authoritative state, runs the fake
  provider and tools, grades the execution, and persists lifecycle changes.
- **Why this approach:** RQ is intentionally small for one background workflow and
  makes the worker boundary real without introducing a distributed platform.
- **Alternative and tradeoff:** In-process background tasks are easier but disappear
  with the API process and do not model an independent worker. Celery offers more
  routing/scheduling features at greater configuration and operational cost.
- **Common failure modes:** Passing ORM objects through the queue, assuming Redis is
  the source of business truth, deploying incompatible API/worker code, failing to
  expose job failure in PostgreSQL, and enqueuing before the database commit is
  visible.
- **Interview explanation:** “PostgreSQL owns durable evaluation state; Redis/RQ only
  dispatches stable IDs to a separate worker that rehydrates the job.”

### Background-worker lifecycle

- **Problem it solves:** A queued execution needs observable states and cleanup even
  when provider, tool, database, or worker operations fail.
- **How it works in EvalGuard:** The case execution moves through explicit states
  such as queued, running, completed, or failed. The job opens its own database
  resources, invokes typed components, and commits the completed path atomically.
  Policy/ownership rejections reached after invocation start can be retained as
  failed invocations with ordered `tool_error` evidence. On a fatal worker error,
  its transaction rolls back and a fresh transaction stores failed status plus a
  terminal failure summary.
  Recovery across process-crash windows remains Phase 4 work.
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
  arguments are exactly the same; conflicting reuse is rejected.
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
- **How it works in EvalGuard:** For the one published case, the fake provider emits
  a repeatable sequence that calls `lookup_order`, then `issue_refund`, and returns
  a structured resolution through the same provider/tool boundary intended for a
  later real adapter. Phase 2 implements and PostgreSQL-tests the other two tools,
  but the fake does not select them until Phase 3 adds versioned scenarios and
  graders for those paths.
- **Why this approach:** It makes failures reproducible while testing more than a
  mocked route or worker. The result establishes infrastructure correctness, not
  model competence.
- **Alternative and tradeoff:** A live model is representative but nondeterministic,
  slower, credentialed, and potentially costly. Mocking every collaborator makes
  tests fast but does not validate integration.
- **Common failure modes:** Giving the fake a privileged shortcut around tools,
  varying output by wall-clock state, treating its pass rate as model quality, or
  coupling the orchestration directly to fake-only methods.
- **Interview explanation:** “The fake is a deterministic contract test double that
  traverses the production-shaped path. Its results are synthetic infrastructure
  validation.”

### Deterministic grading

- **Problem it solves:** Natural-language answers alone cannot prove that the right
  authorized side effect occurred or that forbidden actions were avoided.
- **How it works in EvalGuard:** The refund grader reads final execution-scoped
  PostgreSQL state and ordered trace evidence, evaluates named criteria, and stores
  pass/fail outcomes with evidence. A negative test removes or changes the expected
  refund and must fail.
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
  definitions. The loader creates/verifies the case while its suite is unpublished,
  then publishes the suite. PostgreSQL trigger row locks serialize that publication
  with concurrent case membership changes, then reject later suite edits/deletion
  and case inserts/edits/moves/deletion. Canonically serialized
  reproducibility-relevant content is hashed. An execution references the immutable
  case row, which stores the case hash; the parent suite row stores the version and
  suite hash. Phase 1 does not copy those hashes directly onto every execution row.
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
  execution scope, and graders inspect only that scope. Ticket rows also use
  composite foreign keys so their customer, order, and tool call cannot be borrowed
  from another execution even if application code is bypassed.
- **Why this approach:** It supports concurrency, repeatability, and an unambiguous
  before/after state without global resets.
- **Alternative and tradeoff:** Rebuilding the entire database for every case gives
  strong physical isolation but is slow. Shared fixtures plus cleanup are fast but
  brittle and unsafe under parallel runs.
- **Common failure modes:** Looking up an order by public ID without execution scope,
  globally unique fixture identifiers masking a missing ownership check, incomplete
  cleanup, and graders joining across scopes.
- **Interview explanation:** “Isolation is encoded in ownership and query keys, not
  just in naming conventions. A negative cross-execution test proves the boundary.”

### Ordered trace events and structured resolution

- **Problem it solves:** Final state says what exists, but not how the agent got
  there, which arguments it used, or whether it attempted a forbidden action.
- **How it works in EvalGuard:** Execution-scoped events receive deterministic
  sequence numbers. The completed queued path records provider request/response,
  tool calls/results, and grader completion; the structured final resolution also
  lives on the execution. Policy/ownership rejections reached after invocation
  start record `tool_call` followed by `tool_error` and a failed invocation. A fatal
  worker transaction cannot retain partial events, so the worker records a separate
  terminal failure summary. Naturally structured details use JSON while identity
  and order remain relational.
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
  absent/wrong refunds; API tests cover same-key concurrency and Phase 2 retrieval;
  and an end-to-end test submits, queues, works, polls, retrieves, and verifies a
  fake-provider execution. Later defective-agent cases will expand regression
  coverage.
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
  and reason code. API retrieval renders address and ticket state explicitly.
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
  connection/session, held through record persistence, enqueue attempt, and the
  stored dispatch outcome. Compatible requests converge on one experiment and one
  initial enqueue; a conflicting experiment intent is rejected. At the tool layer,
  execution/order locks serialize decisions, a call ID can replay only the same
  tool and validated arguments, and a new call ID requesting an already-applied
  address or escalation returns a duplicate result without a second mutation.
  PostgreSQL uniqueness remains the final invariant boundary.
- **Why this approach:** Idempotency preserves request identity while semantic
  convergence handles providers that legitimately assign a new call ID on retry.
  Holding the API lock through dispatch closes the tested same-key race between the
  durable row and enqueue outcome.
- **Alternative and tradeoff:** A pre-insert lookup without a lock races under
  concurrency. Transaction-scoped locks would release at each commit and cannot
  cover the following queue call. Holding a session-level advisory lock longer is
  deliberate but serializes all work for that key.
- **Common failure modes:** Reusing a call ID for different arguments, releasing the
  key lock before enqueue, checking semantic duplicates without locking, keeping
  the idempotency key only in Redis, and assuming this eliminates every distributed
  crash window.
- **Interview explanation:** “Retries are expected. Stable identity proves exact
  replay, semantic checks make new-call retries converge, and database locks plus
  uniqueness decide the outcome under concurrency.”

## Future hardening concepts

The following topics are planned constraints. Phase 0/1/2 preserves stable
identities, relational evidence, and transactional boundaries, but full recovery
and scenario/grader breadth belong to later phases.

### At-least-once job delivery — Phase 4 hardening

- **Problem it solves:** A worker can fail after doing work but before every system
  observes the acknowledgement, so safe recovery may cause the job to run again.
- **How it works in EvalGuard:** The design assumes an execution job or tool call can
  be observed more than once. Stable execution/tool identities and transactional
  uniqueness make replay safe; Phase 4 will test duplicate delivery and recovery.
  RQ behavior must be verified for the pinned version rather than described as a
  magical exactly-once guarantee.
- **Why this approach:** At-least-once thinking favors eventual progress without
  trusting a fragile exactly-once boundary between Redis, a worker, and PostgreSQL.
- **Alternative and tradeoff:** At-most-once avoids duplicates but can permanently
  lose work after a crash. End-to-end exactly-once processing is generally replaced
  by at-least-once delivery plus idempotent effects.
- **Common failure modes:** Generating a new tool-call ID on retry, marking the job
  complete before the database commit, retrying permanent validation errors, and
  assuming queue acknowledgement and database commit are atomic.
- **Interview explanation:** “Delivery and side effects are separate. We allow
  delivery to repeat, then make the database effect idempotent and observable.”

### Retries versus permanent failures — Phase 4 hardening

- **Problem it solves:** Temporary outages may recover, while malformed input or a
  policy rejection will not improve by trying again.
- **How it works in EvalGuard:** Failures will be classified. Bounded retries with
  backoff apply to explicitly transient provider, Redis, or tool conditions;
  validation, authorization, and invariant violations become durable terminal
  failures. Retry attempts are traced and capped.
- **Why this approach:** Classification improves recovery without creating retry
  storms, hiding defects, or repeating unsafe mutations.
- **Alternative and tradeoff:** Retrying every exception is easy but wasteful and
  dangerous. Never retrying is predictable but turns brief outages into permanent
  experiment failures.
- **Common failure modes:** Broad exception matching, no attempt limit, synchronized
  retries without jitter, losing the original error, retrying inside an open failed
  transaction, and charging real-provider cost repeatedly.
- **Interview explanation:** “Retry only when another attempt can plausibly change
  the outcome, cap and trace it, and rely on idempotency before retrying mutations.”

### Full policy, authorization, escalation, and recovery graders — Phase 3/4

- **Problem it solves:** A successful refund slice does not cover forbidden actions,
  privacy boundaries, required escalation, or resilience behavior.
- **How it works in EvalGuard:** Later independently authored cases add specialized
  deterministic criteria over state, ordered calls, arguments, forbidden actions,
  escalations, and failure/recovery traces, including intentionally defective fake
  behaviors.
- **Why this approach:** Focused graders make failures explainable and demonstrate
  that the harness detects defects rather than merely producing green examples.
- **Alternative and tradeoff:** One giant grader centralizes logic but becomes hard
  to reason about and reuse. An LLM judge could assess nuance but cannot be primary
  correctness.
- **Common failure modes:** Criteria sharing the same bug as the agent, no negative
  fixtures, authorization checks based only on final text, and aggregate scores that
  hide a critical forbidden action.
- **Interview explanation:** “Each important invariant becomes an explicit
  deterministic criterion, and defective agents prove the evaluator's detection
  power.”

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
19. Why must the API hold one advisory-lock connection through the enqueue outcome,
    not merely through experiment insertion?
