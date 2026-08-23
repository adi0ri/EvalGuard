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
  remains uncommitted and unpushed; no Phase 3 remote-CI result is claimed.
- Do not begin Phase 4 or later work unless the user explicitly advances the
  milestone. Record later ideas in `PLAN.md` instead.

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
- Redis and RQ provide background dispatch. Experiment submission persists work
  before enqueueing it and returns `202 Accepted`; it does not wait for evaluation.
- Each case execution receives isolated synthetic business state relevant to its
  case. Customer, order, refund, and support-ticket ownership is relational and
  execution scoped.
- Business entities stay relational. JSON is reserved for naturally structured
  fixtures, model/tool payloads, trace payloads, and similar evidence.
- State-changing tools run transactionally and must be safe against repeated job
  or tool delivery. Enforce important uniqueness rules in PostgreSQL as well as in
  application logic. Acquire the execution lock before the order lock consistently.
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

Before claiming Phase 0/1/2/3 complete, run and record the exact commands and results
for all of the following:

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
