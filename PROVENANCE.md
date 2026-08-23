# EvalGuard Provenance and Integrity Record

## Independent origin statement

All Phase 0–5 ParcelDesk domain material was independently created for this
portfolio project. ParcelDesk is a wholly fictional business. Its prompts,
scenario, customer/order/address fixtures, support tickets, escalation reasons,
tool behavior, grader criteria, policies, traces, labels, and result wording are
synthetic and authored specifically for EvalGuard.

Phase 2 added original fictional shipping addresses in Port Aurora, original
support-ticket reason codes and wording, and project-specific policies for
pre-shipment address changes and high-value refund escalation. These names,
addresses, ticket fields, thresholds, and rules were written for EvalGuard; they
were not adapted from an employer, vendor, support platform, policy corpus, or
real customer interaction.

Phase 3 added the original 25-case v2 inventory: 7 normal successes, 5 ineligible
requests, 5 policy boundaries, 4 authorization/privacy cases, and 4
duplicate-or-recovery cases. It includes new fictional customers, orders,
addresses, prompts, fixed policy timestamps, expected policy contracts, typed fake
plans, grader wording, and deliberate defect modes. The inventory was authored for
EvalGuard and did not use an external scenario suite, policy corpus, employer
material, or real support interaction. The original one-case v1 suite remains
unchanged for reproducibility.

Phase 4 adds no customer-support scenario, prompt, policy, fake plan, grader, or
business dataset. Its new material is operational and independently authored for
EvalGuard: retry/failure-kind identifiers, deterministic fault modes, attempt and
dispatch evidence shapes, recovery trace wording, and development/test fault-plan
controls. These are not adapted from an outage report, employer system, queue
architecture, benchmark, or external chaos dataset. The published v1/v2 suite
rows, case payloads, and hashes remain unchanged.

Phase 5 adds two original agent/system prompt variants, their descriptions and
version identifiers, comparison labels, aggregate/report wording, and trial
semantics. These were independently authored for EvalGuard and were not copied or
adapted from a prompt library, benchmark, vendor example, employer asset, or real
support workflow. Agent prompts are versioned separately from the already
published fictional customer-request cases, whose content and hashes remain
unchanged.

No proprietary material from any employer or other private source was used. This
project must not copy, reconstruct, imitate, or include employer code, prompts,
policies, task wording, scenarios, graders, traces, labels, datasets, architecture,
internal terminology, or customer information. If the origin of proposed material
cannot be established safely, omit it and write a simpler original ParcelDesk
example.

No external dataset, scenario collection, evaluation suite, or support-policy
corpus is an input to the ParcelDesk domain.

## Public software inventory

These public projects provide implementation infrastructure only; none supplies
ParcelDesk domain material. Exact Python package versions come from `uv.lock`.
Transitive packages remain recorded in that lockfile rather than duplicated here.

| Public project | Version used | EvalGuard use |
| --- | --- | --- |
| Python | 3.12 | Application runtime |
| FastAPI | 0.141.1 | HTTP routing, dependency injection, request/response validation |
| Uvicorn | 0.52.3 | ASGI server |
| Pydantic | 2.13.4 | API, fixture, result, and tool models |
| pydantic-settings | 2.15.0 | Environment-driven settings |
| SQLAlchemy | 2.0.52 | Relational mapping, queries, sessions, and transactions |
| Psycopg | 3.3.4 | PostgreSQL driver and binary distribution |
| Alembic | 1.19.1 | Versioned PostgreSQL migrations |
| redis-py | 6.4.0 | Redis connections and readiness checks |
| RQ | 2.11.0 | Redis-backed job queue and worker |
| Jinja2 | 3.1.6 | Autoescaped server-rendered Phase 5 comparison report |
| HTTPX | 0.28.1 | Test HTTP client dependency |
| pytest / pytest-cov | 8.4.2 / 7.1.0 | Automated test runner and coverage tooling |
| Ruff | 0.16.3 | Lint and formatting checks |
| mypy | 1.20.2 | Strict static type checking |
| Hatchling | `>=1.27` build requirement | Python package build backend |
| uv | 0.11.28 used for verification | Python installation, locking, sync, and command runner |
| PostgreSQL container | `postgres:16-alpine` | Durable evaluation and ParcelDesk state |
| Redis container | `redis:7-alpine` | RQ queue transport |
| Docker / Docker Compose | 29.6.1 / 5.2.0 used for verification | Local image build and multi-process topology |
| `actions/checkout` / `astral-sh/setup-uv` | 7.0.1 / 10.0.1, pinned by commit SHA | CI checkout and uv installation |
| GitHub Actions | Run `32195390241` passed remotely | Historical locked checks, tests, clean migration, schema-drift check, and image build |

No real-model SDK is installed.

## Official reference ledger

Only first-party project documentation is listed here. These references informed
dependency setup, framework behavior, database/transaction choices, queue wiring,
checks, and CI configuration; they are not domain-content sources.

| Date | Official documentation | Purpose |
| --- | --- | --- |
| 2026-08-18 | [Python 3.12 documentation](https://docs.python.org/3.12/) | Runtime and standard-library behavior |
| 2026-08-18 | [uv documentation](https://docs.astral.sh/uv/) | Python management, lockfile, frozen sync, and Windows commands |
| 2026-08-18 | [FastAPI documentation](https://fastapi.tiangolo.com/) | Application, dependency, status-code, and testing patterns |
| 2026-08-22 | [FastAPI templates documentation](https://fastapi.tiangolo.com/advanced/templates/) | Jinja2 template integration and response rendering |
| 2026-08-18 | [Uvicorn documentation](https://www.uvicorn.org/) | ASGI process command |
| 2026-08-18 | [Pydantic documentation](https://docs.pydantic.dev/latest/) and [settings documentation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | Typed validation, serialization, and environment settings |
| 2026-08-18 | [SQLAlchemy 2.0 documentation](https://docs.sqlalchemy.org/en/20/) | Declarative models, sessions, transactions, constraints, and PostgreSQL types |
| 2026-08-18 | [Psycopg 3 documentation](https://www.psycopg.org/psycopg3/docs/) | PostgreSQL connection driver behavior |
| 2026-08-18 | [Alembic documentation](https://alembic.sqlalchemy.org/en/latest/) | Migration environment, revisions, and schema upgrade commands |
| 2026-08-18 | [PostgreSQL 16 documentation](https://www.postgresql.org/docs/16/) | Foreign keys, uniqueness, JSONB, transactions, indexes, and triggers |
| 2026-08-18 | [Redis documentation](https://redis.io/docs/latest/) and [RQ documentation](https://python-rq.org/docs/) | Redis connectivity, queues, jobs, and worker lifecycle |
| 2026-08-22 | [RQ jobs](https://python-rq.org/docs/jobs/), [exceptions and work-horse termination](https://python-rq.org/docs/exceptions/), and [workers](https://python-rq.org/docs/workers/) | Explicit `job_timeout`, duplicate/stale delivery context, work-horse kill hook, and worker maintenance behavior used by Phase 4 |
| 2026-08-18 | [Docker Compose documentation](https://docs.docker.com/compose/) | Service dependencies, health checks, build, and startup workflow |
| 2026-08-18 | [pytest documentation](https://docs.pytest.org/en/stable/) | Fixtures, parametrization, markers, and test invocation |
| 2026-08-18 | [Ruff documentation](https://docs.astral.sh/ruff/) and [mypy documentation](https://mypy.readthedocs.io/en/stable/) | Lint/format and strict type-check configuration |
| 2026-08-18 | [GitHub Actions documentation](https://docs.github.com/en/actions) | Ubuntu CI jobs and service containers |
| 2026-08-22 | [Hatch build configuration](https://hatch.pypa.io/1.0/config/build/) | Wheel package-data behavior checked after a duplicate template inclusion failed the first image build |

Phase 2 reused the first-party Pydantic, SQLAlchemy, PostgreSQL, Alembic, FastAPI,
Redis/RQ, pytest, and Docker documentation already listed above. No third-party
domain policy, scenario, address, ticket, or dataset became an input.

Phase 3 used the same public infrastructure and first-party references and added no
dependency. Typed definitions use the existing JSONB case/evidence columns and
materialize into relational business tables. Final review added Alembic revision
`20260822_0006`, which strengthens the existing support-ticket model by requiring a
ticket's customer to own its referenced order.

Phase 4 uses the same public dependencies and no external domain source. The
declared RQ floor is raised to `>=2.8,<3` while the lockfile remains on RQ 2.11.0;
the first-party RQ pages above informed explicit job timeout, unexpected work-horse
termination handling, and worker maintenance integration. Alembic revision
`20260822_0007` stores retry policy, attempts, generations, leases, dispatch
intents, and development/test fault plans. Its compatibility data migration
preserves the former 300-second RQ timeout for existing experiments, while new
experiments use the 30-second application default; it requeues legacy
`running`/`retrying` work and backfills dispatch only for queued/legacy
`enqueue_failed` executions. PostgreSQL remains authoritative; Redis/RQ remains
transport.

Phase 5 adds Jinja2 only for the small server-rendered report. Revision
`20260822_0008` adds immutable, versioned agent prompts plus experiment prompt and
trial identity without changing either published suite. Aggregate analysis and
comparison are repository-authored reductions over persisted relational evidence;
no external benchmark formula, dataset, model output, or report template is used.

## Provider and result labeling

### Deterministic fake provider

Phases 1 through 5, local development, and CI use the in-repository deterministic fake
provider. Every output from that path is **synthetic infrastructure validation**.
It can show that API submission, Redis/RQ dispatch, typed tool execution,
transactions, tracing, deterministic grading, and retrieval work together. It
cannot support claims about real-model quality, reliability, model latency, token
consumption, or cost.

For each reported fake-provider experiment, retain the suite/case version and
definition hashes, provider identity, trial identity, raw trace, persisted grader
evidence, exact command, and code revision when available. Phase 1 through Phase 5
executions link to immutable suite/case rows that carry the version and hashes;
those hashes are not duplicated on each execution row.

The 2026-08-18 live smoke result recorded in `PROGRESS.md` used only this fake
provider and is labeled synthetic infrastructure validation. Commit `03aaa00`
records the source, fixtures, deterministic provider, graders, and documentation
used for the completed Phase 0/1 milestone.

The 2026-08-22 Phase 2 verification recorded in `PROGRESS.md` also used only the
deterministic fake provider and synthetic fixtures. The final automated suite
collected 49 tests and passed all 49; the live Compose smoke completed the one
existing immutable refund execution with its persisted state, two tool
invocations, seven trace events, and four passing deterministic grader results.
Those counts are repository/runtime evidence, not a model-quality benchmark.
Commit `e0f9715` records the verified Phase 2 implementation, migration, tests,
and documentation. The Phase 2 closure was pushed successfully; at that checkpoint,
local `main` and its `origin/main` tracking ref were at `26c3e86`. No later remote-
CI result is claimed without a separately observed run.

Phase 3 preserves that legacy queued path and adds the immutable
`parceldesk-refunds@2.0.0` suite. Its deterministic typed plans exercise all four
tools across exactly 25 cases, and its independent expected policy contracts feed
six deterministic grader families. The provider exposes eleven stable behavior
IDs: one reference and ten intentional defects, each covered by a targeted
PostgreSQL execution. The final automated run passed 158 tests in 52.51 seconds
across unit, PostgreSQL, API, RQ, and end-to-end coverage; the focused unit run
passed 84 tests in 0.38 seconds, and focused Phase 3 integration passed 38 tests in
24.87 seconds. Each emitted only the recorded upstream warning.
Locked installation checked 49 packages; Ruff lint and the post-format check
passed; and mypy passed 29 source files. Clean migration reached
`20260822_0006 (head)`, downgraded to `0005`, re-upgraded to `0006`, and reported no
drift. The stable suite hashes were
`28289cfdcd98be852b00e451b2f52ae8a9af65a454a6642bb96acb4991a19782`
for v2 and
`aaedf653770166eb7e967352b9242509a94dd9ba3e4ee833576617c403ec6abd`
for v1. Compose startup passed, and the live v2 smoke completed all 25 synthetic
executions with 507/507 passing grader results across six families, 44 tool
invocations, 164 trace events including one `tool_replay`, 7 refunds, and 5 support
tickets. The Redis-unavailable probe kept liveness at `200`, changed readiness to
`503`, and returned readiness to `200` after restoration. These are synthetic
infrastructure results, not real-model quality measurements. Phase 3 commits
`083808c` and `6655e79` are pushed; `origin/main` remains at `6655e79`. No Phase 3
remote-CI result is claimed.

Phase 4 fault injection remains inside that deterministic fake path. One optional
execution-scoped plan may inject a typed transient pre-provider failure, transient
post-tool failure, timeout, permanent pre-provider failure, or worker interruption
for a configured number of attempts. The API permits those controls only in
development/test, and the executor independently refuses a persisted plan in any
other environment. Attempt, dispatch, lease, pre-claim delivery-audit, and recovery
evidence can validate infrastructure behavior; it cannot establish how often a real
provider times out, recovers, follows policy, or uses tools. The final local suite
passed all 189 tests in 135.79 seconds with one known dependency warning. The
synthetic live v2 baseline completed 25/25 executions with 507/507 passing grader
criteria, 44 tool calls, 7 refunds, and 5 tickets. Separate synthetic v1 checks
proved typed transient recovery, a real RQ-enforced timeout, actual worker-SIGKILL
recovery, and accepted-work recovery across a Redis outage. The revision `0007`
migration round trip and Compose topology also passed. These are operational
infrastructure results, not real-provider reliability. The runtime evidence was
recorded before repository closure. Local implementation commit
`e276ba5` now records verified Phase 4; local `main` remains ahead of `origin/main`
at `6655e79`. Phase 4 remains unpushed, no remote CI is claimed, and
publication requires explicit user approval.

The final count increased by three independently authored PostgreSQL regressions:
two parametrized cases prove missing/terminal exact deliveries for migrated legacy
`enqueue_failed` executions enter pre-claim recovery, and one proves a recovered
paused worker is fenced before provider/tool effects. They add no domain case,
provider output, grader family, or external source.

Phase 4 was subsequently published as requested: `origin/main` advanced to
`ac7d39e`. No remote-CI result for that push is claimed.

Phase 5 compares two separately versioned prompt records over the unchanged v2
suite. The prompt hashes are
`08724bf556610d9563f27189eb5b298e62a1ce57952885f481c5f8cf93f5ab69`
for v1 and
`f93d1378329f1156219063cebabfde0cd65e44d08c382541ed600c05140038ad`
for v2. The deterministic fake intentionally ignores semantic prompt differences
and executes the case's typed plan, so a zero observed delta validates analysis and
report plumbing only.

The final pre-closure automated Phase 5 run passed 196 tests in 221.25 seconds with
the one known dependency warning. The live comparison created two 25-case × three-
trial experiments and completed all 150 executions. Each arm passed 75/75
executions and 1,521/1,521 deterministic criteria with 25 stable cases. The shared
comparison reported zero strict-pass, criterion-pass, and stability deltas. Raw
live evidence contained 150 attempts, 150 dispatches, 3,042 grader rows, 264 tool
invocations, 984 trace events, 42 refunds, and 30 support tickets; provider-request
traces split 75/75 across the two prompt versions. These are **synthetic
infrastructure validation**, not prompt- or model-quality results. Exact commands,
experiment identifiers, migration checks, and runtime probes are recorded in
`PROGRESS.md`; the final closure rerun is recorded separately after release review.

The post-review closure run passed all 199 tests in 415.56 seconds. The three added
regressions cover legacy null-prompt idempotent replay, PostgreSQL/grid identity,
and exact reconciliation of complete grader rows with the atomic
`grader_completed` trace vector. A fresh clean-image live run again produced 150
executions and the same scoped 150/150/3,042/264/984/42/30 evidence counts. Its 150
completion markers declared exactly 3,042 criteria, matching the 3,042 grader rows.
This remains synthetic infrastructure validation, not a quality measurement.

### Future real provider

A single optional real-provider adapter and experiment are Phase 6 only. They may
be added and run only after explicit approval to use credentials and potentially
incur cost. Any later real result must identify the provider/model, relevant
configuration, suite/case versions and hashes, run date, trial count, raw result
location, exact reproducible command, and measured token/cost/latency source. It
must be reported separately from fake-provider validation.

No real provider or paid API was invoked for Phase 0–5.

## Metrics integrity

Never fabricate or estimate a measured result. This includes test counts, scenario
counts, pass rates, defect-detection rates, latency, throughput, token usage, costs,
benchmark comparisons, and resume metrics.

A number may be reported as a result only when:

1. code in this repository produces it;
2. the exact command is documented;
3. raw evidence is retained or its durable location is recorded;
4. the provider and dataset/suite version are identified; and
5. the label distinguishes synthetic infrastructure validation from real-provider
   evaluation.

The v2 inventory count of 25 and its 7/5/5/4/4 distribution are verified repository
facts. The 25-case × two-prompt × three-trial comparison produced exactly 150
executions locally. Its zero delta is a verified deterministic-fake infrastructure
result, not evidence that the prompts are equally effective for a real model.

## Phase 0–5 integrity check

- [x] Confirm all ParcelDesk domain assets were independently authored.
- [x] Preserve the previously verified secret, credential, real-data, and employer-
  terminology scans through the Phase 2 revision.
- [x] Confirm no unapproved paid provider was invoked.
- [x] Reconcile the recorded Phase 3 scenario/result counts with repository
  artifacts and commands: 25 cases, 158 passing tests, and 507 passing live grader
  criteria.
- [x] Ensure fake-provider output is labeled synthetic infrastructure validation.
- [x] Reconcile `README.md`, `PROGRESS.md`, and result reports with the recorded
  Phase 3 final-tree local reruns.
- [x] Recheck the public dependency versions and official references above.
- [x] Confirm Phase 2 address, ticket, escalation, and policy material is original
  synthetic ParcelDesk content.
- [x] Preserve the Phase 2 record distinguishing direct PostgreSQL/API tool
  verification from its then-unchanged queued fake-provider refund scenario.
- [x] Confirm the v2 inventory contains exactly 25 independently authored cases in
  the required 7/5/5/4/4 distribution and preserves the immutable v1 suite.
- [x] Confirm Phase 3 uses a fixed policy clock, actor-scoped production tools, six
  deterministic grader families, and only the in-repository deterministic fake.
- [x] Reconcile the implemented Phase 3 automated count with the observed full run:
  158 tests passed in 52.51 seconds.
- [x] Record the Phase 3 locked installation, clean migration, Compose
  rebuild/startup, and live 25-case smoke as synthetic infrastructure validation.
- [x] Record the final high-confidence secret scan (zero matching files) and Phase
  3 code revision `083808c`.
- [x] Confirm Phase 4 introduces operational retry/fault/evidence definitions only,
  keeps both published suite versions and hashes unchanged, and uses only the
  in-repository deterministic fake provider.
- [x] Record the first-party RQ timeout, work-horse termination, and worker
  maintenance references used by the Phase 4 implementation.
- [x] Reconcile Phase 4 test/migration/Compose/live-recovery counts and results
  against the final commands recorded in `PROGRESS.md`.
- [x] Complete the final Phase 4 dependency, provenance, secret, credential,
  proprietary-material, and result-label audit.
- [x] Record the successful Phase 3 push: commits `083808c` and `6655e79`; both
  refs were at `6655e79` at that checkpoint.
- [x] Record verified Phase 4 implementation commit `e276ba5`; local `main` ahead
  of `origin/main` at `6655e79` before publication.
- [x] Record successful Phase 4 publication: `origin/main` advanced to `ac7d39e`.
- [x] Confirm both Phase 5 prompt definitions and report language were independently
  authored, hash-stable, and kept separate from immutable customer-request cases.
- [x] Reconcile the Phase 5 196-test run, clean `0008` migration round trip,
  Compose build/startup, 150-execution live comparison, raw evidence counts, and
  Redis readiness probe with `PROGRESS.md`.
- [x] Confirm Jinja2 is used only for an autoescaped server-rendered report and no
  real-model SDK, external dataset, or paid provider was added or invoked.
- [x] Label all Phase 5 timing and comparison results synthetic infrastructure
  validation, with token and cost data explicitly unavailable.
- [x] Record the final 199-test closure, clean-image 150-execution rerun, exact
  grader-marker reconciliation, and zero-match high-confidence secret/proprietary-
  name scans.
- [x] Record verified Phase 5 implementation commit `c481ed4`; keep it local and
  distinguish that checkpoint from published `origin/main` at `ac7d39e`.
- [ ] Record any observed Phase 3 remote-CI result if it occurs; none is claimed.
