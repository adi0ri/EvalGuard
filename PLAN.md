# EvalGuard Delivery Plan

Last updated: 2026-08-22

## Current milestone

**Phase 0, Phase 1, and Phase 2 — complete.** The four typed domain tools,
relational Phase 2 state, correctness hardening, PostgreSQL coverage, clean
migration round trip, Compose startup, and live deterministic smoke are verified.
Phase 3 and later remain backlog only.

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

The queued RQ fake remains the single published `eligible-refund` case and selects
only `lookup_order` followed by `issue_refund`. Phase 2 tool behavior is verified
through PostgreSQL and API integration coverage; Phase 3 owns the new immutable
cases and deterministic graders that exercise those tools in queued evaluations.

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

## Backlog — do not implement during the current milestone

### Phase 3: complete scenario and grader coverage

- [ ] Expand to exactly 25 versioned original scenarios in the required category
  distribution.
- [ ] Add deterministic state, trace, policy, authorization, and escalation graders.
- [ ] Add intentionally defective fake-agent behaviors for regression validation.

### Phase 4: resilience

- [ ] Add bounded retries, timeouts, duplicate-delivery handling, worker recovery,
  maximum-step enforcement, and fault injection.

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
| API persistence succeeds but queue dispatch fails | Model lifecycle states explicitly, hold same-key serialization through dispatch outcome, and surface `enqueue_failed`; Phase 4 hardens wider crash-window recovery. |
| A worker repeats a state-changing call | Carry stable job/tool identities, require exact replay intent, lock decision rows, and enforce database uniqueness plus semantic convergence. |
| Parallel executions leak business state | Scope ownership through execution foreign keys, including composite ticket ownership, and test cross-execution access denial. |
| Publication races with a late case write | Serialize both paths on suite-row locks and test the concurrent insert boundary. |
| Destructive test cleanup targets development state | Require test mode, a `*_test` PostgreSQL database, a non-development Redis database, and the test-only queue before cleanup; Redis DB 15 is the tested default. |
| Fake-provider success is mistaken for model quality | Label results synthetic infrastructure validation throughout reports and provenance. |
| Documentation drifts from behavior | Update durable documents with the implementing change and gate completion on exercised commands. |
| A real-provider run introduces cost or secrets | Keep real providers in Phase 6 and require explicit user approval. |

No Phase 0/1/2 implementation or local-runtime blocker is known. One upstream
FastAPI/Starlette `TestClient` deprecation warning is recorded in `PROGRESS.md`;
the full local Phase 2 suite passes. Remote CI has not been rerun for the unpushed
Phase 2 change.

## Precise next action

The precise next unchecked phase is Phase 3: add the remaining independently
authored, immutable scenarios and deterministic policy, authorization, escalation,
and defect-detection graders. Do not start it until the user explicitly advances
the milestone.
