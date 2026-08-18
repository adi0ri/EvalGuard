# EvalGuard Project Specification

## Status

Phase 0 and Phase 1 are complete: implementation, local runtime checks, final
repository audit, and the verified milestone commit all closed successfully. This
specification defines both the current vertical slice and the complete V1 contract.
Later-phase features are requirements to preserve in the design, not authorization
to implement them now.

## Product outcome

EvalGuard's V1 target is a reproducible evaluation harness for tool-using AI
agents. It runs a support agent against versioned, stateful scenarios; records
responses, tool calls, database changes, failures, latency, token use, and cost
when those values exist; grades primary correctness deterministically; and
ultimately compares experiments by correctness, policy compliance, stability,
latency, and cost. Phase 1 currently records lifecycle timestamps and terminal
failure text, but it does not derive latency metrics or record tokens/cost.

The domain is an independently invented business named ParcelDesk. Phase 1's
synthetic business state contains customers, orders, and refunds; support tickets
are planned for Phase 2. EvalGuard is a runnable system and test harness, not a
generic chatbot, RAG demonstration, UI mockup, or documentation-only architecture
exercise.

## Users and core workflow

The initial user is a developer or evaluator comparing agent behavior. In the
Phase 1 vertical slice, that user:

1. loads an immutable evaluation suite containing one original eligible-refund
   scenario;
2. submits an experiment;
3. receives `202 Accepted` after the experiment and its case execution are stored;
4. lets an RQ worker run the deterministic fake provider;
5. retrieves the experiment and execution; and
6. inspects the stored business outcome, ordered trace, and criterion-level grader
   result.

The evaluated fake agent looks up the eligible ParcelDesk order and issues the
refund through typed tools. The refund is committed transactionally. The grader
then determines correctness from final PostgreSQL state and trace evidence.

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

## Target V1 domain contract

The completed product exposes these four typed tools:

- `lookup_order`
- `update_shipping_address`
- `issue_refund`
- `escalate_ticket`

The completed product contains exactly 25 independently authored scenarios:

| Category | Count |
| --- | ---: |
| Normal successful actions | 7 |
| Ineligible requests | 5 |
| Policy-boundary cases | 5 |
| Authorization/privacy cases | 4 |
| Duplicate, timeout, or recovery cases | 4 |
| **Total** | **25** |

This is a target inventory, not a claim that those scenarios currently exist. The
inventory covers eligible and expired refunds, high-value escalation, address
changes before shipment, cross-customer access rejection, duplicate-refund
prevention, temporary provider/tool recovery, and escalation when information is
missing.

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
| `SupportTicket` | Later escalation and resolution state |

### Required invariants

- Evaluation suites are versioned. Loading builds a suite while unpublished, then
  marks it published. PostgreSQL trigger row locks serialize publication with case
  membership changes and prevent mutation/deletion of the suite or
  insertion/mutation/movement/deletion of its cases after publication.
- Case and prompt definitions have reproducible hashes.
- Operational customer, order, refund, and ticket rows are isolated by case
  execution.
- `(experiment_id, case_id, trial_number)` is unique.
- `(case_execution_id, tool_call_id)` is unique.
- Duplicate delivery cannot repeat a state-changing operation.
- Refund creation and its associated tool invocation/evidence occur in one
  transaction. Phase 1 does not mutate the order row when creating a refund.
- Foreign keys preserve ownership and execution boundaries.
- Indexes support uniqueness and expected experiment/execution/trace retrieval.
- Relational business state is not hidden inside one giant JSON document.
- JSON is allowed for naturally structured fixtures, provider/tool payloads, trace
  details, and grader evidence.

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

Phase 1 provides liveness/readiness, experiment submission, and sufficient
experiment/execution retrieval to verify the complete slice. Experiment submission
returns `202 Accepted` only after durable records exist and work has been accepted
for background processing. The Phase 1 creation endpoint accepts an
`Idempotency-Key`: compatible replay returns the existing experiment, and replay of
an `enqueue_failed` experiment re-dispatches its existing execution. The current
one-suite, literal-fake-provider API does not exercise conflicting-key intent as a
public behavior. Broader duplicate-delivery and recovery behavior is scheduled for
later hardening.

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
- **Regression tests** begin with negative grader cases that prove absent and
  incorrect refunds are detected; later intentionally defective agent behaviors
  will broaden this layer.
- **End-to-end tests** cross the API, queue, worker, tools, persistence, grader, and
  retrieval boundary with the deterministic fake provider.
- **Smoke tests** verify container startup, clean migration, liveness, and readiness
  where the environment permits them.

## Explicit non-goals

Do not add authentication, user accounts, multi-tenancy, React, RAG, vector
databases, Kubernetes, Kafka, microservices, billing, multiple real providers,
generic plugin systems, or unrequested cloud deployment. Do not add LangChain
without a later concrete need. Do not use an LLM judge as primary correctness.

## Phase 0/1 definition of done

Phase 0/1 is complete only when every item below has been exercised and recorded:

- Docker Compose builds and starts API, worker, PostgreSQL, and Redis.
- Alembic upgrades a clean PostgreSQL database.
- Liveness and readiness behave correctly.
- A documented command loads the initial suite and scenario.
- Experiment submission returns `202 Accepted`.
- The RQ worker completes the fake-provider execution.
- Final state, ordered traces, and grader results are persisted and retrievable.
- A negative grader test proves incorrect refund behavior is detected.
- Ruff, mypy, and pytest pass.
- GitHub Actions invokes commands consistent with local verification.
- README setup commands have actually been exercised.
- No secrets or proprietary material are present.
- Durable project documents match the actual implementation.

No checklist item may be inferred from code review alone when the criterion requires
a running service or command.

## Full V1 definition of done

In addition to the Phase 0/1 foundation, V1 is done when all four typed tools and
all 25 versioned scenarios exist; state, trace, policy, authorization, escalation,
and recovery graders are deterministic; bounded retries, timeout handling,
duplicate-delivery protection, worker recovery, maximum-step enforcement, and
fault injection are verified; aggregate experiment comparison and a small
server-rendered report exist; and the documented 25-case, two-prompt,
three-trial comparison can produce 150 raw, reproducible executions. The optional
real-provider adapter and real run occur only after explicit approval. Final
documentation must explain architecture, methodology, limitations, demo steps,
provenance, and the precise source of every reported number.
