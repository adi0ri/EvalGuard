# EvalGuard Learning Notes

These notes explain the architecture in interview-ready language. Sections marked
**Phase 0/1/2 and Phase 3 implementation** describe behavior exercised by the final
automated suite and the live 25-case synthetic smoke. Phase 3 is locally committed
at `083808c`; it is not pushed, and no Phase 3 remote-CI result is claimed.
Sections marked **Future hardening** describe constraints the current design must
leave room for; they are
not claims that the later behavior has been implemented.

## Phase 0–3 concepts

### FastAPI request flow

- **Problem it solves:** The harness needs a typed HTTP boundary for submitting
  long-running experiments and retrieving their state without exposing database or
  queue details to clients.
- **How it works in EvalGuard:** FastAPI validates an input model, resolves settings
  and a request-scoped SQLAlchemy session, calls focused application/domain code,
  persists and commits the experiment with its selected executions, then arranges
  queue dispatch and serializes a response. A keyed submission uses a dedicated
  PostgreSQL connection/session and holds its advisory lock across persistence,
  enqueue, and the recorded dispatch outcome. Retrieval routes read durable state,
  including shipping addresses, support tickets, and aggregate case status;
  workers evaluate outside the request.
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
  and queue submission has been accepted. The client follows retrieval endpoints
  until the aggregate reaches a terminal state.
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
  belong to the same execution. Revision `20260822_0006` additionally requires the
  ticket customer to own that order. Unique constraints protect experiment/trial,
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
  tickets and their execution-scoped ownership constraints. Revision
  `20260822_0006` adds an order/customer/execution key and matching ticket foreign
  key so a same-execution non-owner cannot be attached to an order. The final clean
  check upgraded a blank database through `0006`, downgraded to `0005`, re-upgraded
  to `0006`, and confirmed no model/migration drift after both upgrades.
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
  terminal failure summary. Each worker also locks and reconciles the parent from
  all child statuses: any nonterminal child keeps it running; once all are terminal,
  any failed child makes the parent fail, otherwise it completes. Recovery across
  process-crash windows remains Phase 4 work.
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
  without another invocation or mutation.
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
  case-key/hash membership. Hashes are not copied onto every execution row.
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
  absent/wrong refunds; Phase 3 tests add exact inventory/hashes, actor-scoped
  PostgreSQL calls, six evidence graders, eleven fake behavior IDs, aggregate
  lifecycle, a 25-case RQ/API path, cross-suite execution rejection, relational
  ticket-owner enforcement, frozen/cross-validated contracts, and legacy
  compatibility. Focused unit coverage passes 84 tests in 0.38 seconds, focused
  Phase 3 integration coverage passes 38 tests in 24.87 seconds, and the full suite
  passes 158 tests in 52.51 seconds. Each run emits only the recorded dependency
  warning.
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
  connection/session, held through record persistence, enqueue attempt, and the
  stored dispatch outcome. Compatible requests converge on one experiment and one
  initial enqueue per selected execution; a conflicting experiment intent is
  rejected. At the tool layer, execution-then-order locks serialize decisions, a
  call ID can replay only the same tool and validated arguments, and successful
  replay adds an ordered `tool_replay` event. A new call ID
  requesting an already-applied
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
  parent running. Once every child is terminal, any failed or enqueue-failed child
  makes the parent fail; otherwise it completes.
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

## Future hardening concepts

The following topics are planned constraints. Phase 0–3 preserves stable
identities, relational evidence, and transactional boundaries, but full operational
recovery and analysis/reporting breadth belong to later phases.

### At-least-once job delivery — Phase 4 hardening

- **Problem it solves:** A worker can fail after doing work but before every system
  observes the acknowledgement, so safe recovery may cause the job to run again.
- **How it works in EvalGuard:** The design assumes an execution job or tool call can
  be observed more than once. Stable execution/tool identities and transactional
  uniqueness make replay safe; Phase 4 will test duplicate delivery and recovery.
  RQ behavior must be verified for the pinned version rather than described as a
  magical exactly-once guarantee.
- **Current boundary:** Creating all execution rows is transactional, but enqueueing
  25 Redis jobs is not one atomic operation. A process crash after only some jobs
  are accepted needs Phase 4 outbox/reconciliation recovery.
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

### Resilience and fault-recovery graders — Phase 4

- **Problem it solves:** Phase 3 covers deterministic domain errors and one scripted
  correction after a lookup miss, but it does not prove provider timeouts, worker
  crashes, queue redelivery, or bounded retry behavior.
- **How it works in EvalGuard:** Phase 4 will add controlled infrastructure faults,
  retry/attempt evidence, maximum-step checks, and recovery criteria while reusing
  Phase 3's state, trace, and idempotency foundations.
- **Why this approach:** Domain-policy coverage and distributed-systems resilience
  are separate claims and should fail independently.
- **Alternative and tradeoff:** Mixing faults into every Phase 3 case would broaden
  the matrix prematurely and make ordinary policy failures harder to diagnose.
- **Common failure modes:** Calling scripted correction “timeout recovery,” retrying
  permanent authorization failures, failing to cap attempts, and reporting
  duplicate-safe tools as proof of worker crash recovery.
- **Interview explanation:** “Phase 3 proves deterministic evaluator breadth;
  Phase 4 will prove bounded recovery under injected operational faults.”

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
27. Which partial multi-job enqueue crash window still requires an outbox or
    reconciliation design?
