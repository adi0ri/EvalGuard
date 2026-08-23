# EvalGuard Project Specification

## Status

Phase 0, Phase 1, and Phase 2 are complete. Phase 3 implementation and local
acceptance are complete: 158 tests, locked installation, static checks, the clean
`20260822_0006` migration round trip, Compose startup, a live 25-case smoke, and a
Redis-unavailable readiness probe passed. Phase 3 is locally committed at
`083808c`; it is not pushed, and no Phase 3 remote-CI result is claimed. This
specification defines the implemented Phase 3 breadth and the remaining V1
contract. Phase 4 and later
requirements are constraints to preserve, not authorization to implement them now.

## Product outcome

EvalGuard's V1 target is a reproducible evaluation harness for tool-using AI
agents. It runs a support agent against versioned, stateful scenarios; records
responses, tool calls, database changes, failures, latency, token use, and cost
when those values exist; grades primary correctness deterministically; and
ultimately compares experiments by correctness, policy compliance, stability,
latency, and cost. The current system records lifecycle timestamps and terminal
failure text, but it does not derive latency metrics or record tokens/cost.

The domain is an independently invented business named ParcelDesk. Synthetic
business state contains customers, orders with relational shipping-address fields,
refunds, and support tickets. EvalGuard is a runnable system and test harness, not
a generic chatbot, RAG demonstration, UI mockup, or documentation-only architecture
exercise.

## Users and core workflow

The initial user is a developer or evaluator comparing agent behavior. In the
current Phase 3 workflow, that user:

1. loads the preserved one-case legacy suite and the immutable 25-case v2 suite;
2. submits an experiment;
3. receives `202 Accepted` after the experiment and all 25 case executions are
   stored;
4. lets RQ workers run the deterministic fake provider across those executions;
5. retrieves the aggregate experiment and individual executions; and
6. inspects the stored business outcome, ordered trace, and criterion-level grader
   result.

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
- Concurrent `Idempotency-Key` submissions serialized on one dedicated PostgreSQL
  connection/session through persistence, enqueue attempt, and dispatch outcome.
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

## Target V1 domain contract

The current Phase 3 product exposes these four typed tools:

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
escalation when information is missing. Generic timeouts, bounded retries, worker
recovery, and fault injection remain Phase 4 work.

Every execution begins with isolated synthetic ParcelDesk state. Primary graders
inspect final PostgreSQL state, ordered tool traces, arguments, forbidden actions,
required escalations, authorization boundaries, and the structured final
resolution. An LLM judge cannot override or replace these deterministic results.

## Architecture

EvalGuard is a modular monolith deployed as separate API and worker processes:

```text
FastAPI
  -> PostgreSQL experiment and case-execution records
  -> Redis queue
  -> RQ worker
  -> selected model-provider adapter
  -> typed ParcelDesk tools
  -> PostgreSQL business state and ordered trace events
  -> deterministic graders
  -> PostgreSQL grader results
  -> retrieval API
  -> later server-rendered experiment report
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
| `CaseExecution` | One case/trial attempt with isolated state and execution status |
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
  nonterminal it is `running`; after all are terminal it is `failed` if any failed
  or `completed` otherwise.

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
experiment/execution retrieval to verify the complete slice. Experiment submission
returns `202 Accepted` only after durable records exist and work has been accepted
for background processing. The creation endpoint accepts an
`Idempotency-Key`: compatible replay returns the existing experiment, and replay of
an `enqueue_failed` experiment re-dispatches its existing executions. Concurrent
requests for the same key are serialized by a PostgreSQL advisory lock held on one
dedicated connection/session across durable persistence, enqueue, and dispatch
outcome, so compatible requests converge on one experiment and one initial enqueue
per selected execution.
The current literal-fake-provider request shape still does not expose a broad
conflicting-intent matrix. Wider crash-window recovery remains Phase 4 work.
In particular, dispatching a multi-case experiment is not atomic across all Redis
enqueue calls: a process failure after only some jobs are accepted still requires
outbox-style reconciliation or recovery in Phase 4.
Execution retrieval includes structured order shipping addresses and support
tickets, and experiment retrieval exposes every v2 case execution.

## Reproducibility and evidence

- Local development and CI select a deterministic fake provider and require no API
  key.
- A case execution references an immutable evaluation case whose row stores the
  prompt, fixtures, expectations, and case hash; the suite row stores its version
  and suite hash. The execution stores provider selection, trial number, ordered
  trace, final resolution, business-state linkage, and criterion-level results.
  Phase 1 does not duplicate the case/prompt hash onto the execution row.
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
- Concurrent same-key API submissions converge while holding serialization through
  the enqueue outcome, and execution retrieval exposes address/ticket state.
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

## Full V1 definition of done

In addition to the Phase 0/1/2/3 foundation and the four implemented typed tools,
V1 is done when bounded retries, timeout handling, duplicate-delivery protection,
worker recovery, maximum-step enforcement,
and fault injection are verified; aggregate experiment comparison and a small
server-rendered report exist; and the documented 25-case, two-prompt,
three-trial comparison can produce 150 raw, reproducible executions. The optional
real-provider adapter and real run occur only after explicit approval. Final
documentation must explain architecture, methodology, limitations, demo steps,
provenance, and the precise source of every reported number.
