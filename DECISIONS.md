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
