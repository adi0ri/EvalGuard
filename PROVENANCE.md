# EvalGuard Provenance and Integrity Record

## Independent origin statement

All Phase 0/1 ParcelDesk domain material was independently created for this
portfolio project. ParcelDesk is a wholly fictional business. Its prompt, scenario,
customer/order fixture, tool behavior, grader criteria, traces, labels, and result
wording are synthetic and authored specifically for EvalGuard.

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
| GitHub Actions | Workflow present; not run remotely | Planned CI execution of locked checks, tests, migration, and image build |

Jinja2/static HTML reporting is planned for Phase 5 but is not an installed or
implemented Phase 0/1 feature. No real-model SDK is installed.

## Official reference ledger

Only first-party project documentation is listed here. These references informed
dependency setup, framework behavior, database/transaction choices, queue wiring,
checks, and CI configuration; they are not domain-content sources.

| Date | Official documentation | Purpose |
| --- | --- | --- |
| 2026-08-18 | [Python 3.12 documentation](https://docs.python.org/3.12/) | Runtime and standard-library behavior |
| 2026-08-18 | [uv documentation](https://docs.astral.sh/uv/) | Python management, lockfile, frozen sync, and Windows commands |
| 2026-08-18 | [FastAPI documentation](https://fastapi.tiangolo.com/) | Application, dependency, status-code, and testing patterns |
| 2026-08-18 | [Uvicorn documentation](https://www.uvicorn.org/) | ASGI process command |
| 2026-08-18 | [Pydantic documentation](https://docs.pydantic.dev/latest/) and [settings documentation](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | Typed validation, serialization, and environment settings |
| 2026-08-18 | [SQLAlchemy 2.0 documentation](https://docs.sqlalchemy.org/en/20/) | Declarative models, sessions, transactions, constraints, and PostgreSQL types |
| 2026-08-18 | [Psycopg 3 documentation](https://www.psycopg.org/psycopg3/docs/) | PostgreSQL connection driver behavior |
| 2026-08-18 | [Alembic documentation](https://alembic.sqlalchemy.org/en/latest/) | Migration environment, revisions, and schema upgrade commands |
| 2026-08-18 | [PostgreSQL 16 documentation](https://www.postgresql.org/docs/16/) | Foreign keys, uniqueness, JSONB, transactions, indexes, and triggers |
| 2026-08-18 | [Redis documentation](https://redis.io/docs/latest/) and [RQ documentation](https://python-rq.org/docs/) | Redis connectivity, queues, jobs, and worker lifecycle |
| 2026-08-18 | [Docker Compose documentation](https://docs.docker.com/compose/) | Service dependencies, health checks, build, and startup workflow |
| 2026-08-18 | [pytest documentation](https://docs.pytest.org/en/stable/) | Fixtures, parametrization, markers, and test invocation |
| 2026-08-18 | [Ruff documentation](https://docs.astral.sh/ruff/) and [mypy documentation](https://mypy.readthedocs.io/en/stable/) | Lint/format and strict type-check configuration |
| 2026-08-18 | [GitHub Actions documentation](https://docs.github.com/en/actions) | Ubuntu CI jobs and service containers |

## Provider and result labeling

### Deterministic fake provider

Phase 1, local development, and CI use the in-repository deterministic fake
provider. Every output from that path is **synthetic infrastructure validation**.
It can show that API submission, Redis/RQ dispatch, typed tool execution,
transactions, tracing, deterministic grading, and retrieval work together. It
cannot support claims about real-model quality, reliability, latency, token
consumption, or cost.

For each reported fake-provider experiment, retain the suite/case version and
definition hashes, provider identity, trial identity, raw trace, persisted grader
evidence, exact command, and code revision when available. Phase 1 executions link
to immutable suite/case rows that carry the version and hashes; those hashes are not
duplicated on each execution row.

The 2026-08-18 live smoke result recorded in `PROGRESS.md` used only this fake
provider and is labeled synthetic infrastructure validation. Commit `03aaa00`
records the source, fixtures, deterministic provider, graders, and documentation
used for the completed Phase 0/1 milestone.

### Future real provider

A single optional real-provider adapter and experiment are Phase 6 only. They may
be added and run only after explicit approval to use credentials and potentially
incur cost. Any later real result must identify the provider/model, relevant
configuration, suite/case versions and hashes, run date, trial count, raw result
location, exact reproducible command, and measured token/cost/latency source. It
must be reported separately from fake-provider validation.

No real provider or paid API was invoked for Phase 0/1.

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

Planned fixed quantities, such as the V1 contract of exactly 25 scenarios and the
planned 25-case × two-prompt × three-trial comparison of 150 executions, must be
described as targets until generated and verified.

## Final milestone integrity check (complete)

- [x] Confirm all ParcelDesk domain assets were independently authored.
- [x] Search repository files for secrets, credentials, real customer data, and
  employer terminology.
- [x] Confirm no unapproved paid provider was invoked.
- [x] Reconcile scenario/result counts with repository artifacts and commands.
- [x] Ensure fake-provider output is labeled synthetic infrastructure validation.
- [x] Ensure `README.md`, `PROGRESS.md`, and result reports make no unverified
  claims.
- [x] Recheck the public dependency versions and official references above.
