# EvalGuard Delivery Plan

Last updated: 2026-08-18

## Current milestone

**Phase 0 and Phase 1 — complete.** Implementation, local runtime verification,
final audit, and the verified local milestone commit are complete. Phase 2 and
later are backlog only.

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

## Phase 0/1 acceptance gate

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
- [x] GitHub Actions uses commands consistent with local verification (remote
  workflow execution is not claimed).
- [x] Every README setup/check command has been exercised locally.
- [x] A final provenance and secret scan finds no proprietary material or secrets.
- [x] Durable documents match the verified implementation and evidence.

## Backlog — do not implement during the current milestone

### Phase 2: complete domain tools and strengthen correctness

- [ ] Add `update_shipping_address` and `escalate_ticket`.
- [ ] Strengthen transaction boundaries, state isolation, and idempotency.
- [ ] Add support-ticket and escalation state.

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
| API persistence succeeds but queue dispatch fails | Model lifecycle states explicitly and surface the failure; later phases harden recovery. |
| A worker repeats a state-changing call | Carry stable job/tool identities and enforce database uniqueness plus transactional checks. |
| Parallel executions leak business state | Scope ownership through execution foreign keys and test cross-execution access denial. |
| Publication races with a late case write | Serialize both paths on suite-row locks and test the concurrent insert boundary. |
| Destructive test cleanup targets development state | Require test mode, a `*_test` PostgreSQL database, a non-development Redis database, and the test-only queue before cleanup; Redis DB 15 is the tested default. |
| Fake-provider success is mistaken for model quality | Label results synthetic infrastructure validation throughout reports and provenance. |
| Documentation drifts from behavior | Update durable documents with the implementing change and gate completion on exercised commands. |
| A real-provider run introduces cost or secrets | Keep real providers in Phase 6 and require explicit user approval. |

No Phase 0/1 implementation or local-runtime blocker is known. One upstream
FastAPI/Starlette `TestClient` deprecation warning is recorded in `PROGRESS.md`;
the full test suite still passes. Remote GitHub Actions execution remains
unverified and is not claimed.

## Precise next action

The precise next unchecked phase is Phase 2: add `update_shipping_address`,
`escalate_ticket`, support-ticket/escalation state, and the planned correctness
hardening. Do not start it until the user explicitly advances the milestone.
