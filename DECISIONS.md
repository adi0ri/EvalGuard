# EvalGuard Architectural Decisions

Decisions are dated when accepted. A later change should add a superseding entry
rather than silently rewriting the reason for an earlier choice.

## 2026-08-18 — D001: Use a modular monolith with separate API and worker processes

**Status:** Accepted for Phase 0/1.

**Reason:** The API, evaluation orchestration, ParcelDesk tools, graders, and worker
share one domain and one data model. One Python package keeps the vertical slice
understandable while separate processes preserve the required asynchronous runtime
boundary.

**Alternatives:** A single synchronous process would be simpler but would violate
the non-blocking experiment contract. Microservices would separate deployment but
add network contracts and operational overhead before there is evidence they are
needed.

**Consequences:** API and worker use the same code and schema. Boundaries live in
the `api`, `core`, `db`, `domain`, `evaluation`, `providers`, and `workers` package
areas without mandatory repository/service/interface layers.

## 2026-08-18 — D002: Use PostgreSQL for truth and Redis/RQ for dispatch

**Status:** Accepted for Phase 0/1.

**Reason:** Evaluation records and ParcelDesk state require transactions, foreign
keys, uniqueness, and production-like relational integration. Redis plus RQ gives a
small, inspectable background queue while PostgreSQL remains the durable source of
truth.

**Alternatives:** SQLite is convenient for tests but does not prove PostgreSQL
behavior. An in-process task runner cannot demonstrate a real worker boundary.
Kafka or a larger workflow system would add unnecessary operational complexity.

**Consequences:** Integration tests require PostgreSQL; SQLite cannot substitute.
Docker Compose must run PostgreSQL and Redis. The system must tolerate the database
record and queue job having different lifecycles and later harden recovery.

## 2026-08-18 — D003: Persist first, enqueue, and return 202 Accepted

**Status:** Accepted for Phase 1.

**Reason:** A model/tool evaluation can be slow or fail independently of the HTTP
request. The client needs a durable identifier to poll, while the API should only
acknowledge accepted background work.

**Alternatives:** Synchronous evaluation would make request latency and failure
couple directly to provider/tool execution. Returning `201 Created` without making
the asynchronous contract clear would misrepresent the operation's state.

**Consequences:** Experiment and case-execution rows exist before `202` is returned.
Lifecycle states and retrieval endpoints must distinguish queued, running,
completed, and failed work. Enqueue failure cannot be hidden.

## 2026-08-18 — D004: Make a deterministic fake provider the Phase 1 default

**Status:** Accepted for Phase 1 and CI.

**Reason:** The infrastructure path must be reproducible, cost-free, and runnable
without credentials. A scripted provider can reliably invoke typed tools and make
failures attributable to the harness rather than model variance.

**Alternatives:** A live provider would test real model behavior but introduces
nondeterminism, credentials, network dependency, and possible cost. Mocking the
entire worker would fail to exercise the vertical slice.

**Consequences:** Fake-provider outcomes are labeled synthetic infrastructure
validation. They are not evidence of real-model quality. A real adapter remains an
optional, explicitly approved Phase 6 addition.

## 2026-08-18 — D005: Deterministic graders own primary correctness

**Status:** Accepted for V1.

**Reason:** Refund correctness, authorization, required escalation, forbidden
actions, and exact tool use can be checked against persisted facts. Deterministic
criteria are repeatable, auditable, and useful for regression tests.

**Alternatives:** An LLM judge may assess ambiguous language but is variable and can
misread authoritative business state. Comparing final text alone misses tool side
effects and policy violations.

**Consequences:** Graders inspect final PostgreSQL state plus ordered trace evidence
and store criterion-level results. An LLM judge may never override primary V1
correctness.

## 2026-08-18 — D006: Version and freeze evaluation suites

**Status:** Accepted for V1; first instance required in Phase 1.

**Reason:** Experiment comparisons are meaningful only when cases, fixtures,
prompts, and expectations can be identified exactly. Stable hashes expose silent
definition drift.

**Alternatives:** Mutable scenarios are easier to edit in place but make historical
results ambiguous. Source filenames alone do not prove content identity.

**Consequences:** The loader inserts a suite as unpublished, inserts/verifies its
case, and then publishes it. PostgreSQL trigger row locks serialize publication
with case membership changes and reject later suite edits/deletion and case
inserts/edits/moves/deletion for a published suite. Revisions require a new version,
and reproducibility-relevant case and prompt content receives a stable hash on the
immutable suite/case rows referenced by execution evidence.

## 2026-08-18 — D007: Isolate relational ParcelDesk state per case execution

**Status:** Accepted for V1; tested in Phase 1.

**Reason:** Each scenario must begin from known fixtures and concurrent trials must
not contaminate each other. Relational rows allow database constraints and graders
to inspect actual state transitions.

**Alternatives:** Resetting shared global rows is vulnerable to concurrency. Keeping
all business state in one JSON document hides relationships and weakens database
invariants.

**Consequences:** Customer, order, refund, and later ticket ownership includes the
case-execution boundary and is enforced with foreign keys and query predicates.
Tests must attempt cross-execution access, not just compare fixture values.

## 2026-08-18 — D008: Protect state-changing tools with transactions and stable identities

**Status:** Accepted as an invariant; initial refund behavior is Phase 1 and broader
hardening is Phase 2/4.

**Reason:** Queue work or tool calls may be repeated around failures. Refund state
must never be partially applied or created twice.

**Alternatives:** Application-only prechecks have a race between checking and
writing. Assuming exactly-once delivery moves correctness onto an unreliable
operational assumption.

**Consequences:** Refund changes occur in one database transaction. Execution and
tool-call uniqueness are enforced in PostgreSQL. Phase 1 establishes the invariant;
idempotency keys, duplicate delivery, and recovery receive deeper later-phase tests.

## 2026-08-18 — D009: Use typed tools and an ordered, append-oriented trace

**Status:** Accepted for Phase 1.

**Reason:** Typed inputs reject malformed actions at the boundary. An explicit
ordered trace makes successful tool arguments/results and provider/grader events
available to graders and humans. Terminal execution failures also need a durable
summary even though the atomic Phase 1 transaction rolls back partial events.

**Alternatives:** Free-form dictionaries reduce schema work but move errors deeper
into execution. Application logs alone are not durable, execution-scoped grading
evidence.

**Consequences:** Every persisted event has execution identity and deterministic
ordering. Tool invocation identity is unique within an execution. Trace payloads
may use JSON where their shape is naturally structured, while core relationships
remain relational. The final structured resolution is stored on the execution and
also appears in the successful provider-response payload. Durable partial-step
failure traces are deferred to later recovery hardening.

## 2026-08-18 — D010: Defer UI breadth and real-provider breadth

**Status:** Accepted for the current milestone and V1 scope.

**Reason:** Correct stateful evaluation and reproducibility are the portfolio value.
A small server-rendered report is sufficient later, and one optional real adapter
is enough to demonstrate the boundary.

**Alternatives:** React, a generic plugin system, multiple providers, or cloud
deployment would expand scope without strengthening the Phase 1 correctness proof.

**Consequences:** No React, generic provider plugin framework, or deployment work is
planned. Jinja2/static HTML is deferred to Phase 5. One real adapter is deferred to
Phase 6 and requires explicit approval for credentials and possible spending.

## 2026-08-18 — D011: Keep the deterministic Phase 1 execution in one transaction

**Status:** Accepted for the Phase 1 fake-provider slice; must be revisited before a
network provider.

**Reason:** The deterministic provider performs a short, local, fixed sequence. One
worker transaction makes fixture materialization, tool invocations, the refund,
successful trace evidence, grading, and completion atomic. If execution fails, that
partial work rolls back; a separate transaction records the durable terminal
failure summary.

**Alternatives:** Committing after every step would retain more partial evidence and
shorten lock duration, but it requires a durable step state machine, explicit resume
semantics, and idempotency across intermediate commits. That machinery is not needed
to prove the first local vertical slice.

**Consequences:** Phase 1 does not promise durable partial tool-call history after a
failed execution, and refund creation does not mutate the order row. A later real
provider must use shorter durable steps so network waits do not hold a database
transaction; richer partial-failure evidence, bounded retries, and recovery belong
to Phase 2/4 hardening.

## 2026-08-18 — D012: Isolate and explicitly prepare destructive test targets

**Status:** Accepted for Phase 0/1.

**Reason:** PostgreSQL/RQ integration tests truncate application tables and remove
queued jobs. Reusing development targets would risk user data and make test order
affect local behavior. A fresh Compose volume also needs an explicit way to create
and migrate the separate test database.

**Alternatives:** Creating a new database/container per test offers stronger
physical isolation but adds substantial startup cost. Using the development
database with cleanup is faster to set up but unsafe. SQLite would be convenient
but would not exercise the PostgreSQL constraints, JSONB, triggers, and transactions
that the harness relies on.

**Consequences:** `prepare-test-db` accepts only PostgreSQL and an allowlisted
database name ending in `_test`, creates it idempotently, and applies Alembic head.
pytest refuses destructive cleanup unless test mode, the `_test` suffix, a
non-development Redis database, and the `evalguard-tests` queue are in use. The
tested default is Redis database 15. Compose exposes preparation as a one-shot
`test-db` service, so repeated local runs work on both fresh and reused volumes.

## 2026-08-22 — D013: Keep Phase 2 support state relational and execution-owned

**Status:** Accepted for Phase 2.

**Reason:** A shipping address is business state on one synthetic order, and an
escalation is a durable support record that graders and retrieval APIs must be able
to inspect. Both need the same execution-isolation guarantees as customers, orders,
and refunds. Storing them only in a trace or fixture JSON document would hide
ownership relationships and make cross-execution leakage harder for PostgreSQL to
reject.

**Alternatives:** A generic JSON ticket payload would be quick to add but would
weaken foreign keys, indexing, and deterministic queries. A separate ticket service
or repository layer would add a boundary without a Phase 2 need. A global support
ticket keyed only by order ID would allow parallel case executions to contaminate
one another.

**Consequences:** Address fields are nullable relational columns on the
execution-owned order. `support_tickets` has direct execution ownership plus
composite foreign keys to the execution-owned customer, order, and originating
tool invocation. PostgreSQL enforces one escalation per execution/order/reason
code and one ticket per execution/tool-call identity. Runtime predicates repeat
the execution boundary, API retrieval returns the relational rows, and tests prove
both application-level denial and database-level rejection of cross-execution
ownership. Published evaluation-suite tables and their immutability triggers are
unchanged.

## 2026-08-22 — D014: Serialize state-changing tools and distinguish exact replay from semantic duplication

**Status:** Accepted for the implemented Phase 2 tools; this completes the Phase 2
portion of D008 without claiming later worker-recovery work.

**Reason:** Queue delivery, provider retries, and concurrent calls can repeat an
operation. A stable call ID is safe to replay only when it names the same tool and
the same validated arguments. Separate call IDs may still express the same
business intent, so the mutation also needs a semantic idempotency rule. Expected
policy rejection is part of evaluation evidence and must be persisted rather than
existing only as an exception or application log.

**Alternatives:** Trusting exactly-once delivery would leave races unhandled.
Checking for existing state without a lock would permit two writers to pass the
same precondition. Replaying any request that reuses a call ID could silently return
a result for different arguments or even a different tool. Recording only
successful invocations would make policy failures invisible to deterministic
graders.

**Consequences:** Each tool call locks its case-execution row before inspecting a
stable call ID, and state-changing calls lock the execution-owned order. An exact
same-tool/same-arguments replay returns the persisted result without a second
invocation or second `tool_call`/`tool_result` pair and appends one explicit
`tool_replay` event; conflicting reuse raises an error. A repeated requested
address is a successful no-op, and escalation converges on one ticket per
execution/order/reason code even when call IDs differ. Address changes are allowed
only for `pending` or `processing` orders. Refunds at or above 50,000 cents create
no refund and require escalation. Expected rejection leaves a failed
`ToolInvocation` plus `tool_error`; replay raises the persisted error. Business
state and invocation/trace evidence share the caller's transaction, so a rollback
removes them together. PostgreSQL uniqueness and composite ownership constraints
remain the final backstop, and concurrent/rollback tests exercise these boundaries.

## 2026-08-22 — D015: Serialize experiment idempotency keys across persistence and dispatch outcome

**Status:** Accepted for Phase 2; current Redis-dispatch handling is superseded by
D020 while the durable same-key serialization principle remains.

**Reason:** A unique database constraint prevents two experiments from committing
with the same idempotency key, but it does not by itself give two simultaneous HTTP
requests a coherent enqueue outcome. A retry must not inspect an intermediate
record and race the first request's Redis dispatch or `enqueue_failed` persistence.
PostgreSQL session-level advisory locks require lock and unlock to use the same
physical connection, including across intermediate commits.

**Alternatives:** Catching a unique-constraint error and immediately rereading the
row can observe a dispatch outcome that has not happened yet. A process-local mutex
would fail across multiple API processes. Holding an ordinary database transaction
open across Redis enqueue would make the durable experiment invisible to the
worker and would couple a network call to an open write transaction. An outbox is a
stronger recovery design but exceeds the Phase 2 milestone.

**Consequences:** The API derives a signed 64-bit PostgreSQL advisory-lock key from
a SHA-256 namespaced hash of the opaque `Idempotency-Key`. One dedicated
connection-bound SQLAlchemy session holds that key lock across durable lookup or
creation, the commit that makes work visible, Redis dispatch, persistence of the
dispatch outcome, and explicit unlock. Different keys remain independent. Tests
prove two concurrent successful submissions produce one experiment, one execution,
and one enqueue, and that a concurrent retry waits for a first dispatch failure
before safely reusing and enqueueing the same durable execution. PostgreSQL and
Redis remain separate systems; automated outbox-style recovery is still later
resilience work.

## 2026-08-22 — D016: Publish a complete v2 suite without mutating the legacy suite

**Status:** Accepted for Phase 3.

**Reason:** Expanding the one-case published suite in place would break the
immutability contract and make its existing execution evidence ambiguous. Phase 3
also needs richer typed fixtures, expected policy oracles, and deterministic fake
plans than the legacy row stores. Time-dependent refund cases must remain
reproducible regardless of when a worker runs them.

**Alternatives:** Appending 24 rows to `parceldesk-refunds@1.0.0` would violate the
database trigger and the product's comparison contract. Replacing the legacy case
would silently redefine historical results. Reading the current clock for refund
eligibility would make a stable case change outcome over time.

**Consequences:** `parceldesk-refunds@1.0.0` and its original hashes remain
unchanged. `parceldesk-refunds@2.0.0` is a separately published suite containing
exactly 25 cases in the required 7/5/5/4/4 distribution. Each case hash covers its
key, title, prompt, typed fixture, independent expected policy contract, and fake
plan; the suite hash covers ordered key/hash membership. Fixtures carry a fixed
`policy_as_of` timestamp and an inclusive 30-day refund window. These definitions
fit the existing JSONB case columns and relational business state. A later Phase 3
review found a pre-existing relational ownership gap and added the separate
hardening migration recorded in D019.

## 2026-08-22 — D017: Grade independent evidence dimensions and prove defects are detected

**Status:** Accepted for Phase 3.

**Reason:** One refund-specific grader cannot explain whether a failure came from
state, call sequence, policy, authorization, escalation, or an inconsistent final
answer. Green reference executions alone also do not prove that an evaluator can
detect the defects it claims to cover.

**Alternatives:** A single monolithic grader would couple unrelated rules and make
criterion failures harder to interpret. Deriving the grader oracle from the fake
provider's plan would let the same mistake contaminate both sides. An LLM judge
would be variable and cannot replace exact state and authorization checks.

**Consequences:** Phase 3 persists results from six deterministic families:
`state_v1`, `trace_v1`, `policy_v1`, `authorization_v1`, `escalation_v1`, and
`resolution_v1`. The expected policy contract is stored independently from the
fake plan. The deterministic provider exposes eleven stable behavior IDs: one
reference behavior and ten intentional defects covering omitted action, wrong
order or arguments, forbidden action, privacy disclosure, missing escalation,
duplicate mutation intent, and mismatched resolution evidence. Production tools
receive the requester customer as trusted execution context and recheck that actor
on lookup, mutation, and cached replay. The final automated suite passes 158 tests;
this is synthetic infrastructure validation, not model-quality data.

## 2026-08-22 — D018: Derive experiment lifecycle from every case execution

**Status:** Accepted for Phase 3.

**Reason:** A v2 experiment creates 25 independently queued executions. Allowing
the first completed worker to mark the parent completed would expose a false
terminal result while most cases were still queued or running. Concurrent workers
must reach the same aggregate decision.

**Alternatives:** Trusting RQ job counts would move product lifecycle truth out of
PostgreSQL. Keeping the experiment `running` forever and deriving status only at
read time would leave no durable terminal state. Updating the parent without a row
lock would allow workers to overwrite one another's conclusions.

**Consequences:** Worker success and failure paths lock the parent experiment and
reconcile it from all case statuses. Any nonterminal child keeps the experiment
`running`; after every child is terminal, any `failed` or `enqueue_failed` child
makes the experiment `failed`, otherwise it becomes `completed`. This is aggregate
lifecycle correctness, not Phase 4 worker crash recovery or outbox handling.

## 2026-08-22 — D019: Make replay, suite membership, and ticket ownership independently provable

**Status:** Accepted for Phase 3; strengthens D013, D014, D016, and D017.

**Reason:** Final review found three boundaries that application intent alone did
not prove strongly enough. A same-execution ticket could name a customer who did
not own its order; an exact cached tool replay returned the right result without a
dedicated replay event; and an inconsistent case/execution/suite reference could
reach execution orchestration. Grading also needed to check invocation outcomes and
ticket/order ownership directly rather than infer them from other criteria.

**Alternatives:** Application-only ticket validation would leave direct SQL writes
able to violate ownership. Re-emitting a normal call/result pair for cached replay
would falsely imply another invocation. Deriving outcome or ownership correctness
from provider prose would couple the oracle to the behavior under test. Adding an
outbox in this review would address a different distributed crash window and exceed
Phase 3 scope.

**Consequences:** Alembic revision `20260822_0006` adds a unique parent key on
`orders(id, customer_id, case_execution_id)` and a matching composite foreign key
from `support_tickets`, so the ticket customer must own its order. Every tool
standardizes lock order as execution row followed by order row. Successful exact
replay appends one `tool_replay` event after the original outcome containing the
persisted tool name, arguments, and result without a second invocation or mutation.
The executor rejects a case outside its experiment suite before normal execution or
completed replay; tests exercise both queued and completed states. Tool and scenario
contracts are frozen and cross-field validated: resolution order and every expected
state mutation must match policy order, and denied outcomes cannot expect address
or other state mutation. `trace_v1.invocation_outcomes_match` and
`escalation_v1.ticket_owner_matches_order` are persisted for every v2 case, while
`state_v1.tickets_exact` includes customer ownership. Privacy checks use token
boundaries for strings to avoid substring false positives such as `US` inside
`CUSTOMER`, and recognize grouped numeric values such as `9,125` in free text.
Targeted PostgreSQL runs now exercise all ten defective fake behaviors. The final
local suite passes 158 tests, and the final synthetic live run persists 507 passing
criteria. At the Phase 3 close, multi-job Redis dispatch was still non-atomic and
the partial-enqueue crash window was explicitly deferred. D020 records the Phase 4
transactional-outbox decision that subsequently closes that window.

## 2026-08-22 — D020: Make PostgreSQL dispatch intent durable before best-effort RQ publication

**Status:** Accepted for Phase 4; supersedes D015's current requirement to hold an
idempotency advisory lock through Redis outcome.

**Reason:** Committing 25 executions and then enqueueing 25 independent Redis jobs
left a process-crash window in which only a prefix was dispatched and no durable
record identified the remainder. A same-key advisory lock could serialize HTTP
requests but could neither make PostgreSQL and Redis atomic nor recover work after
the API process disappeared. Accepted work must remain recoverable even when Redis
is unavailable.

**Alternatives:** Holding the experiment transaction open across all Redis calls
would hide rows from workers, lengthen locks, and still not give a distributed
commit. Treating RQ as truth would make acceptance depend on Redis retention.
Marking executions `enqueue_failed` would turn a transport outage into product
failure and still not repair a partial prefix. A two-phase distributed transaction
would add machinery unsupported by this topology.

**Consequences:** The API atomically commits each new `CaseExecution` with one
generation-1 `ExecutionDispatch`, then releases any idempotency advisory lock and
makes a best-effort initial reconciliation pass. It returns `202` after the
PostgreSQL commit even if immediate publication fails. That pass may bypass
`available_at` only for pending executions still `queued` (or legacy
`enqueue_failed`), never for an execution already `retrying`. Compatible same-key
replay returns the original identifiers and targets those same durable executions,
so it repairs initial publication without collapsing a scheduled retry delay.

Each dispatch is unique by execution/generation and by stable job ID. Generation 1
preserves the legacy ID; later generations add a suffix. If RQ reports a duplicate,
EvalGuard accepts it only after queue origin, function, positional arguments, and
empty keyword arguments, configured timeout, and runnable status match exactly.
Generation-1 jobs using the pre-Phase-4 one-argument/300-second-timeout identity
remain compatible only when their experiment's persisted timeout policy is 300
seconds; a new 30-second experiment rejects that legacy shape. Before claim, the
worker validates the live job against the persisted dispatch identity and
experiment timeout again. This closes the enqueue-success/database-commit crash
window without trusting queue contents; a mismatch consumes no execution attempt,
and reconciliation keeps the dispatch pending with
`DispatchIdentityError` rather than running someone else's intent.

General reconcilers lock one due row with `FOR UPDATE SKIP LOCKED`; explicit ID
replay uses a blocking row lock. Each attempt commits in its own transaction.
Identity errors defer that row and allow the pass to continue; Redis/OSError
transport failures record a safe class, apply base backoff with a multiplier capped
at 32, and stop the pass to avoid hammering the transport. Publication transport
attempts are open-ended and do not consume the bounded provider/tool attempt budget.

Worker startup/maintenance and the bounded `evalguard reconcile` command also
audit a current dispatched row when its execution is still queued, retrying, or
legacy `enqueue_failed` and no durable claim appears within 30 seconds. An exact RQ job in queued, started,
deferred, or scheduled status remains untouched. A missing or exact-but-unrunnable
job is an abandoned pre-claim delivery: recovery verifies execution generation and
persisted job ID, creates one interrupted attempt (thereby consuming the bounded
execution budget), then advances the generation or fails at exhaustion. An
identity-mismatched job is never converted into an attempt. This audit covers Redis
job disappearance and terminal RQ state without resurrecting a stale generation.

Local Phase 4 acceptance exercised the decision with Redis stopped: submission
still returned `202`, the execution remained queued with a pending dispatch and no
attempt, and worker reconciliation completed it once Redis returned. Compatible
replay and the 25-case baseline retained one logical delivery per generation.

## 2026-08-22 — D021: Own retries in PostgreSQL with attempt generations and leases

**Status:** Accepted and locally verified for Phase 4.

**Reason:** RQ can interrupt a timed-out job and can observe unexpected work-horse
termination, but RQ job state alone cannot express EvalGuard's durable attempt
budget, failure classification, business rollback, or current execution
generation. Duplicate/stale delivery and a worker that dies after claiming work
must converge under PostgreSQL locks.

**Alternatives:** RQ's generic retry configuration is useful, but it would make
Redis own the retry count/schedule rather than the retrievable experiment contract.
Retrying every exception would hide defects and risk repeated effects. Never
retrying would make explicit transient faults and timeouts terminal. Treating every
running row as abandoned would race live execution.

**Consequences:** An experiment persists `max_attempts`, `job_timeout_seconds`, and
`retry_backoff_seconds`. Revision `20260822_0007` preserves the former queue
default by backfilling existing experiments at 300 seconds, while newly created
experiments use the 30-second application default. The revision requeues legacy
`running` and `retrying` executions because neither has a durable Phase 4 lease,
then creates pending generation-1 dispatch rows only for queued or legacy
`enqueue_failed` executions. A delivery locks its execution, requires the current
dispatch generation, refuses a second claim while running, and creates one unique
`ExecutionAttempt`. Terminal, busy, and stale deliveries return without invoking
provider/tools. After claim, the executor reacquires the execution lock and
revalidates attempt number, dispatch generation, and `running` status before any
provider/tool work, so recovery fences a paused former owner. Retry/recovery
advances the generation exactly once. Execution
delays start at the configured base, double per failed attempt, and cap at 300
seconds; a new attempt is scheduled only while `attempt_number < max_attempts`.

If a current job disappears or becomes non-runnable before it commits a claim, the
delivery audit records an `interrupted` attempt with zero steps before advancing
the generation. The job wrapper also attempts that exact pre-claim recovery when
claim setup itself raises. This deliberately spends one execution attempt because
RQ accepted a generation whose outcome cannot otherwise be proven; generation/job
checks and the execution lock prevent duplicate charging.

Failure classification is deliberately conservative: injected transient failures,
RQ timeout, and worker interruption are retryable; maximum-tool-step, injected
permanent, and unexpected exceptions are terminal. Safe kind/message, retryability,
step count, attempt outcome, and retry/recovery trace persist after the atomic
provider/tool transaction rolls back. EvalGuard does not claim arbitrary network
exceptions are transient.

The configured timeout is passed explicitly as RQ `job_timeout`, whose official
[job documentation](https://python-rq.org/docs/jobs/) defines it as the maximum
runtime before interruption/failure. A caught `JobTimeoutException` is recorded and
scheduled under EvalGuard's budget before being re-raised for RQ. The attempt lease
uses PostgreSQL start time plus the timeout and two seconds of grace. Periodic
recovery locks only expired running work; the executor's execution-row lock prevents
a live transaction from being reclaimed. RQ's documented
[work-horse killed handler](https://python-rq.org/docs/exceptions/) provides a fast
path that revalidates the same persisted job identity and timeout before consuming
an interruption attempt. Worker startup and the custom
[maintenance cycle](https://python-rq.org/docs/workers/) run stale recovery before
dispatch reconciliation. Local acceptance proved the timeout path with a real
one-second RQ limit and the termination path by sending SIGKILL to an actively
leased worker; both persisted the retryable failure, advanced from generation 1 to
2, and completed without a duplicate refund.

## 2026-08-22 — D022: Count attempted public tool calls and keep resilience faults operational

**Status:** Accepted for Phase 4.

**Reason:** A maximum-step policy is ambiguous unless replay, rejection, and the
first over-budget call have deterministic treatment. Fault injection also must not
silently alter an immutable evaluation definition or expose synthetic failure
controls as production behavior.

**Alternatives:** Counting only successful invocations would let cached/error loops
evade the budget. Counting provider planning or typed validation would couple the
limit to adapter internals rather than tool requests. Adding faults to v2 case plans
would change published hashes; enabling them in production would make the API a
general chaos-control surface. A new resilience grader family would overstate the
implemented design, which stores operational attempt/dispatch evidence and tests
but no new `GraderResult` family.

**Consequences:** One step is consumed at entry to any public `ToolRuntime` method,
before execution locking or cached invocation lookup. Successful mutation/read,
policy or ownership error, semantic no-op, cached-success replay, and persisted-
error replay all count. Typed argument validation that fails before method entry
does not. Requests through N are allowed; request N+1 increments the attempted
count and raises `MaximumToolStepsExceeded` before creating an invocation, trace, or
business mutation. Step count is per execution attempt.

Successful attempts, maximum-step exceptions, and injected post-tool failures
provide exact attempted-step counts. If some other exception does not carry an
`attempted_steps` or `steps_attempted` attribute, its durable attempt records 0
rather than inferring from rolled-back trace evidence.

One execution-scoped `ExecutionFaultPlan` may select a typed deterministic mode:
transient before provider, transient after tools, timeout, permanent before
provider, or worker interruption, with an explicit number of affected attempts.
The API accepts it only in development/test and includes the complete plan in
idempotency intent. The executor independently refuses a persisted plan outside
development/test, so a direct database write cannot silently enable synthetic
fault behavior in another environment. A post-tool injected failure demonstrates
that the original single execution transaction rolls back fixtures, state, tool
evidence, grader rows, and resolution before separate retry evidence commits.

Fault plans, retry policy, attempts, dispatches, and leases are operational state;
they do not change `parceldesk-refunds@1.0.0`, `@2.0.0`, any case/fake plan/grader
definition, or the verified hashes. Their deterministic-fake output remains
synthetic infrastructure validation. Phase 5 comparison/reporting and Phase 6 real
provider work remain outside this decision; Phase 5 has not started.

The final Phase 4 suite and live checks verified exact N/N+1 accounting,
post-tool rollback before retry, single-case fault isolation in the 25-case suite,
and production-environment fault-plan rejection. Those results validate this
operational decision without adding a grader family or changing either suite hash.

Local implementation commit `e276ba5` records the verified Phase 4 decisions.
Local `main` remains ahead of `origin/main` at `6655e79`; the milestone
remains unpushed and no remote-CI result is claimed.

## 2026-08-22 — D023: Version agent prompts independently from immutable cases

**Status:** Accepted and locally verified for Phase 5.

**Reason:** A case prompt describes the fictional customer's request, while an
agent prompt describes the system instructions being compared. Combining them
would either mutate a published suite or make it impossible to distinguish the
experimental treatment from the task definition. Repeated trials also need an
explicit identity so every case/prompt cell is complete and auditable.

**Alternatives:** Adding prompt variants to the published v2 cases would invalidate
their hashes and historical results. Storing free-form prompt text directly on an
experiment would permit silent drift. Putting both prompt arms in one experiment
would blur lifecycle, idempotency, and comparison identity. Making the new foreign
key non-null immediately would invent a prompt for legacy experiments.

**Consequences:** `AgentPrompt` is relational, source-controlled, versioned by
`name` and `version`, content-hashed, and immutable after publication. The two
independently authored Phase 5 definitions have hashes
`08724bf556610d9563f27189eb5b298e62a1ce57952885f481c5f8cf93f5ab69`
and
`f93d1378329f1156219063cebabfde0cd65e44d08c382541ed600c05140038ad`.
New API-created experiments must select one published prompt; the nullable foreign
key honestly represents pre-Phase-5 rows. Prompt identity and `trials` participate
in idempotency intent. Revision `20260822_0008` constrains trials to 1–3 and each
execution's trial number to 1–3, makes experiment prompt/trial identity immutable,
and rejects a child trial beyond its parent's declared count. A v2 experiment with
three trials creates the exact 25 × 3 grid, and a fault target identifies one case
and one exact trial. For backward compatibility, an omitted-prompt one-trial request
may replay an otherwise identical legacy null-prompt row; either explicitly
supplied prompt field conflicts, while new omitted-prompt creation still binds v2.

The provider-request trace records the prompt name, version, hash, and instructions
used by that execution. The deterministic fake deliberately follows the same typed
case plan for either prompt: it validates orchestration and comparison plumbing but
does not measure prompt quality. The published v1/v2 suite definitions and hashes
remain unchanged.

## 2026-08-22 — D024: Derive transparent aggregates from one coherent evidence load

**Status:** Accepted and locally verified for Phase 5.

**Reason:** Summary and comparison values must be reproducible from raw rows and
must not silently treat missing graders as success. Reading separate aggregates in
separate transactions can also produce a mixed-time snapshot while workers are
finishing.

**Alternatives:** Persisted rollup tables would make reads cheap but introduce a
second source of truth and invalidation/recovery machinery. Multiple convenient
queries would be simpler to write but could observe different lifecycle moments.
Counting only completed rows as a denominator would hide queued, failed, or
ungraded work. Comparing only a single aggregate pass bit would miss criteria that
flip in opposite directions across trials.

**Consequences:** One joined statement loads both requested experiments, prompt
identity, executions, observed cases, criterion rows, and fault-plan presence.
One bounded follow-up reads the selected suites' immutable case membership so the
expected grid cannot shrink to the observed rows. Published-suite triggers make
that membership stable, while both arms' changing execution evidence comes from
the same joined read. The application then performs one shared reduction. Every
ratio exposes numerator, denominator, and value. An execution is graded only when
it is completed and exactly one valid atomic `grader_completed` trace vector
matches its complete unique grader rows; it passes only when every reconciled
criterion passes. Missing, partial, mismatched, ungraded, and nonterminal work
never pass vacuously. Trial stability compares the complete ordered
`(grader, criterion, passed)` vector for every case across all requested trials.
Expected coordinates come from immutable suite membership × declared trials, not
the observed execution rows, so missing or unexpected cells invalidate comparison.
Queue, run, and end-to-end timing expose count/min/mean/median/nearest-rank-p95/max
and are labeled synthetic infrastructure timing, not model latency. Token and cost
availability are explicitly false because the fake emits neither.

Comparison requires two distinct, published, same-family prompt versions/hashes
with the same suite version/hash, provider, trial count, resilience policy, and
exact case/trial grid. Null/same-prompt and fault-injected arms are rejected because
they do not represent the intended comparable treatment. Quality deltas remain
unavailable until both arms are terminal and fully graded; partial counts and
timing may still be inspected per arm without implying a final result. Timing and
attempt deltas are also withheld until both arms have complete equal samples.

## 2026-08-22 — D025: Render one server-side comparison report from shared analysis

**Status:** Accepted and locally verified for Phase 5.

**Reason:** A portfolio report should be readable without adding a frontend build,
and its values must not diverge from the JSON comparison API.

**Alternatives:** A React application would add build/runtime surface without
improving the milestone's evidence. A second report-specific aggregation would risk
metric drift. Client-side calls would complicate loading/error behavior. Persisted
HTML would become stale as queued executions finish.

**Consequences:** FastAPI renders a Jinja2 template with autoescaping, inline CSS,
and no JavaScript. The report and JSON endpoint call the same comparison function;
the page includes prompt/suite hashes, explicit denominators, limitations, and one
raw-execution link per selected execution. It is derived on request rather than
stored. The package relies on Hatch's ordinary package-data inclusion for the
template; an explicit duplicate force-include was removed after the first Docker
wheel build correctly rejected the duplicate destination.

Phase 5 intentionally does not add manual retry-failures, token/cost accounting, a
real provider, or model-quality claims. Those require later scope, and Phase 6
still requires separate user approval for credentials and possible cost.

Local implementation commit `c481ed4` records the verified Phase 5 decisions and
behavior. At that checkpoint it remained unpushed and `origin/main` remained at
published Phase 4 commit `ac7d39e`; D026 records the later publication boundary.
No Phase 5 remote-CI result is claimed.

## 2026-08-22 — D026: Split Phase 6 portfolio work from the real-provider approval gate

**Status:** Accepted for the Phase 6 start; the real-provider implementation and
run remain unapproved.

Phase 5 commits `c481ed4` and `270bbe4` were published before this decision; no
Phase 5 remote-CI result is inferred from that push.

**Reason:** The portfolio architecture, methodology, limitations, code tour, and
deterministic demo can be completed without a credential or cost. The original
contract independently requires approval before adding or invoking one real
provider. The existing `execute_case` function also holds one transaction and
execution lock across its local fake-provider/tool/grader path, which is not a safe
boundary for a slow or ambiguous network request.

**Alternatives:** Treating “start Phase 6” as permission to select a provider and
spend would erase the contract's separate approval condition. Pausing all Phase 6
work would leave useful cost-free portfolio work undone. Plugging a network client
into `DeterministicFakeProvider`'s slot would be smaller initially, but would hold a
database connection and locks during network latency and could repeat an unknown
billable outcome after a timeout.

**Consequences:** Phase 6 starts with an evidence-centered portfolio guide and a
PowerShell demo hard-coded to `deterministic-fake`. No SDK, API-key setting, model,
credential, or network call is added. Before the real-provider track can start, the
user must separately approve one provider/model, secret delivery, bounded request
scope, and possible-spend ceiling.

The later approved implementation must use shorter durable turns: persist and
commit provider intent, make the network request without an open database
transaction, revalidate the execution claim before persisting the response and
measured usage, run persisted tool calls in short transactions, and repeat until a
final resolution can be graded. Real results must remain separate from synthetic
infrastructure evidence. This decision defines the safety boundary; it does not
claim that the durable real-provider state machine exists yet.
