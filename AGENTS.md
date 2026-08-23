# EvalGuard Repository Instructions

These instructions apply to the entire repository. EvalGuard is a portfolio-grade,
reproducible evaluation harness for tool-using AI agents in the wholly fictional
ParcelDesk support domain.

## Current scope

- Phase 0, Phase 1, and Phase 2 are complete.
- Phase 0 establishes the repository, runtime services, database foundation,
  checks, health endpoints, and durable documentation.
- Phase 1 delivers one complete queued evaluation: an eligible ParcelDesk refund
  handled by a deterministic fake provider through `lookup_order` and
  `issue_refund`, then graded from persisted state and trace evidence.
- Phase 2 adds typed `update_shipping_address` and `escalate_ticket` tools,
  relational shipping-address and support-ticket state, stronger transactional
  idempotency, and PostgreSQL-backed correctness coverage.
- Phase 3 implementation and local acceptance are complete: the immutable legacy
  `parceldesk-refunds@1.0.0` suite remains available, and
  `parceldesk-refunds@2.0.0` contains exactly 25 independently authored cases in
  the required 7/5/5/4/4 distribution. Actor-scoped tools, six deterministic
  grader families, eleven deterministic fake-behavior IDs, and aggregate
  experiment lifecycle handling are covered by the 158-test suite. Revision
  `20260822_0006` enforces that each ticket customer owns its order. Locked
  installation, static checks, clean migration round trip, Compose startup, the
  live 25-case smoke, and the Redis-unavailable readiness probe all passed. Phase 3
  commits `083808c` and `6655e79` are pushed; `origin/main` remains at `6655e79`.
  No Phase 3 remote-CI result is claimed.
- Phase 4 implementation and local acceptance are complete. Revision
  `20260822_0007` adds durable dispatch, execution
  attempt, lease, retry-policy, and development/test fault-plan state. The API now
  commits each execution and its PostgreSQL dispatch intent together, returns
  `202 Accepted` independently of immediate Redis availability, and leaves pending
  work for worker or CLI reconciliation. The revision preserves the pre-Phase-4
  300-second timeout for existing experiments, requeues legacy `running` and
  `retrying` executions, and backfills pending generation-1 dispatches for queued
  or legacy `enqueue_failed` work. The final 189-test run, migration round trip,
  Compose startup, deterministic live flows, real RQ timeout, actual worker
  interruption, and Redis-outage recovery are recorded in `PROGRESS.md`. Phase 4
  implementation commit `e276ba5` records the verified milestone. Local `main`
  remains ahead of `origin/main` at `6655e79`; Phase 4 remains unpushed and no
  remote CI is claimed.
- Do not begin Phase 5 or later work unless the user explicitly advances the
  milestone. Phase 5 has not started; wait for explicit direction before either a
  Phase 4 push or Phase 5 work, and record later ideas in `PLAN.md` instead.

## Engineering rules

- Preserve user-owned files and unrelated working-tree changes.
- Keep the repository runnable after each milestone and build one verified
  vertical slice at a time.
- Prefer focused functions and a straightforward modular package. Do not add a
  repository, service, interface, or framework layer without a concrete need.
- Use Python 3.12, `pyproject.toml`, and `uv` when available. Commit the dependency
  lockfile.
- Required local workflows must work from PowerShell on Windows. GitHub Actions
  may run on Ubuntu; do not make Bash the only supported path.
- Use current mutually compatible dependencies. If behavior is version-sensitive,
  consult the library's official documentation instead of guessing.
- Do not weaken, skip, or rewrite a valid test simply to make a check pass.
- Diagnose failures and record unresolved ones; never describe an unrun command
  as successful.
- Never print, copy, or commit secrets. `.env.example` contains names and safe
  examples only.
- Do not push, publish, deploy, buy services, or invoke a paid API without the
  user's explicit permission.
- If Git author identity is already configured, small local commits are allowed
  after a milestone is verified. Never invent identity, rewrite history, or
  backdate a commit.

## Architecture guardrails

- Keep API and worker code in one modular Python application, run as separate
  processes.
- PostgreSQL is the source of truth for evaluation records and ParcelDesk business
  state. Do not use SQLite as a substitute for PostgreSQL integration coverage.
- Redis and RQ provide background dispatch. Experiment submission transactionally
  persists each execution with a PostgreSQL outbox row before making a best-effort
  enqueue attempt and returns `202 Accepted`; it does not wait for Redis or for
  evaluation. Pending outbox rows remain the durable recovery source.
- Each case execution receives isolated synthetic business state relevant to its
  case. Customer, order, refund, and support-ticket ownership is relational and
  execution scoped.
- Business entities stay relational. JSON is reserved for naturally structured
  fixtures, model/tool payloads, trace payloads, and similar evidence.
- State-changing tools run transactionally and must be safe against repeated job
  or tool delivery. Enforce important uniqueness rules in PostgreSQL as well as in
  application logic. Acquire the execution lock before the order lock consistently.
- Treat RQ delivery as repeatable. One current dispatch generation may claim at
  most one execution attempt; stale generations, concurrent duplicate delivery,
  and already-terminal delivery must not repeat provider or tool effects. Accept an
  existing RQ job as an idempotent enqueue only after its queue, function,
  positional arguments, empty keyword arguments, configured timeout, and runnable
  status match exactly. Preserve the legacy one-argument generation-1 identity only
  when the persisted experiment timeout is its legacy 300 seconds. Validate the
  live RQ delivery against the persisted dispatch and timeout before claim; an
  identity mismatch consumes no execution attempt and must be reconciled as a
  pending safe collision. Before provider or tool work, reacquire the execution
  lock and revalidate the claimed attempt number, dispatch generation, and
  `running` status so a paused worker cannot resume after recovery fences it.
- Execution retries are bounded by persisted `max_attempts`; only explicitly
  retryable failures schedule a new dispatch generation. Persist each attempt and
  its safe failure kind. Use the configured RQ job timeout and a PostgreSQL lease
  so an unexpected work-horse exit or expired running attempt can be recovered
  without racing the execution transaction. Dispatch publication retries are a
  separate, open-ended concern with capped backoff: never discard accepted work
  merely because Redis is temporarily unavailable.
- Audit current dispatched-but-unclaimed rows after the delivery recheck interval
  while their execution is queued, retrying, or legacy `enqueue_failed`. Keep exact
  runnable RQ jobs unchanged; treat a missing or exact-but-unrunnable job as a pre-
  claim interruption that consumes one bounded attempt and advances the generation
  or exhausts it. Never resurrect a stale generation.
- A tool step is one request to any public `ToolRuntime` tool method. Count the
  request before lock, replay, or invocation lookup, including successful calls,
  policy/ownership errors, semantic no-ops, cached-success replays, and persisted-
  error replays. Typed argument validation that fails before a tool method is
  entered is not a step. Reject the first request beyond `max_tool_steps` before it
  can invoke or mutate anything.
- Phase 4 fault plans are typed, deterministic, synthetic controls for development
  and test only. They are execution-level operational data, not immutable suite or
  case definitions. Reject their API creation and fail closed if a persisted plan
  is encountered in any other environment.
- Tools use typed arguments. Record ordered calls, results, failures, and the final
  structured resolution as trace evidence. A successful exact cached replay emits
  one `tool_replay` event after the original outcome without a second invocation or
  mutation.
- Keep source scenario/tool contracts frozen and cross-field validated. A resolution
  and every expected state mutation must target the policy order, and denied
  outcomes cannot expect state mutation.
- Validate case/experiment suite membership before execution or completed replay.
  Authorization grading must detect protected string and numeric values in free
  text as well as structured fields, using token boundaries for strings and
  recognizing grouped numeric forms such as `9,125`.
- A support ticket's customer must own its order, not merely share the same case
  execution. Enforce that relationship in PostgreSQL.
- Primary correctness comes from deterministic graders that inspect persisted
  state and traces. An LLM judge is never the primary source of truth.
- CI and local development must work without an API key through the deterministic
  fake provider.
- Evaluation suites are versioned and immutable; reproducibility-relevant case and
  prompt definitions receive stable hashes.

## Required verification

Phase 4 completed the following local gate. Preserve its exact evidence in
`PROGRESS.md`, and rerun the applicable items before any later completion claim
whose changes could affect them:

1. Dependency installation from the committed lockfile.
2. Ruff linting and formatting checks.
3. mypy type checking.
4. The full pytest suite, including PostgreSQL integration, RQ worker, API, and
   end-to-end coverage.
5. Docker Compose configuration and `docker compose up --build` for API, worker,
   PostgreSQL, and Redis.
6. Alembic upgrade against a clean PostgreSQL database.
7. Liveness behavior and readiness behavior with dependencies both available and
   unavailable where practical.
8. Initial suite/scenario loading through a documented command.
9. A submitted experiment returning `202`, being completed by the worker, and
   exposing stored state, trace events, and grader results through retrieval APIs.
10. A negative grader test that demonstrates an absent or incorrect refund fails.
11. Phase 2 PostgreSQL tests for accepted and rejected address changes, required
    escalation, duplicate state-changing calls, execution isolation, and API/RQ
    integration.
12. Clean upgrade, downgrade, and re-upgrade coverage for the Phase 2 migration.
13. Phase 3 suite loading with the immutable legacy suite preserved and exactly 25
    v2 cases in the 7/5/5/4/4 category distribution.
14. Phase 3 PostgreSQL, API, RQ, and end-to-end coverage for actor ownership, all
    four tools, six deterministic grader families, defective fake behaviors, and
    aggregate experiment lifecycle, including execution/case suite consistency.
15. A live 25-case deterministic-fake experiment that remains nonterminal until
    every case is terminal and exposes state, traces, and grader evidence.
16. Clean upgrade, downgrade to `20260822_0005`, and re-upgrade coverage for the
    Phase 3 ownership migration `20260822_0006`, followed by a schema-drift check.
17. Successful stable-call replay evidence through `tool_replay`, same-execution
    ticket/order/customer ownership rejection, and the current grader criteria.
18. Clean upgrade through Phase 4 revision `20260822_0007`, downgrade to
    `20260822_0006`, re-upgrade to head, preservation of the legacy 300-second
    experiment timeout, requeue of legacy `running`/`retrying` executions, pending
    generation-1 backfill only for queued/legacy `enqueue_failed` work, and a
    schema-drift check.
19. PostgreSQL/Redis coverage for transactional outbox creation, partial dispatch
    recovery, exact duplicate-job identity, mismatched collision deferral, and
    compatible/concurrent idempotent replay, including initial-only force behavior
    that preserves delayed retry `available_at`.
20. Bounded transient retry, permanent failure, RQ timeout, exact maximum-tool-step
    enforcement, duplicate/stale delivery, and rollback of failed attempt state.
21. Fresh-lease protection, expired-lease and work-horse interruption recovery,
    pre-claim failure recovery, missing/unrunnable dispatched-job audit, bounded
    recovery passes, and development/test-only typed fault injection that fails
    closed elsewhere, including legacy `enqueue_failed` delivery and a paused
    worker fenced before provider/tool work.
22. Phase 4 retrieval evidence for policies, attempts, generations, dispatches,
    leases, safe failure kinds, and retry/recovery traces, plus the documented
    worker and CLI recovery entrypoints.

Do not claim the milestone is complete while any acceptance item in `PLAN.md` is
unchecked or unverified.

## Documentation duties

- `PROJECT_SPEC.md` is the product and architecture contract.
- `PLAN.md` owns phase status, acceptance criteria, backlog, risks, and blockers.
- `PROGRESS.md` is chronological evidence: commands actually run, exact results,
  known failures, and the next action.
- `DECISIONS.md` records dated decisions, reasons, alternatives, and consequences.
- `docs/LEARNING_NOTES.md` teaches each major concept in plain language and is
  updated when the implementation makes that concept concrete.
- `PROVENANCE.md` tracks independently created domain material, public libraries
  and references, and the provider behind every reported result.
- `README.md` describes implemented behavior as implemented and planned behavior
  as planned. Include only setup commands that have actually been exercised.
- Update relevant documentation in the same change as behavior, schema, command,
  or architectural decisions.

## Provenance and evidence

- All ParcelDesk names, customers, orders, policies, prompts, scenarios, fixtures,
  labels, traces, and graders must be independently created for this project.
- Never copy, reconstruct, imitate, or include proprietary employer material,
  terminology, architecture, customer information, datasets, or evaluation assets.
- Reported counts and performance figures must come from committed code run by a
  documented command. Never fabricate test/scenario counts, pass rates,
  defect-detection rates, latency, throughput, tokens, costs, comparisons, or
  resume metrics.
- Label fake-provider output as **synthetic infrastructure validation**, never as
  real-model quality or performance.
- A real provider remains out of scope until the user approves credentials and
  possible cost; clearly distinguish any later real-provider results.
