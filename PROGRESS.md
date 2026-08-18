# EvalGuard Progress Log

This is a chronological evidence log. A command is recorded as passing only when
it was actually run. All entries below are from 2026-08-18 on Windows/PowerShell
unless otherwise noted.

## Repository foundation

1. The initial workspace was inspected and found to be empty and not a Git
   repository. A new Git repository was initialized; no existing history or user
   files existed to preserve.
2. The Phase 0/1 package, initial Alembic revision, Docker Compose topology, test
   suite, GitHub Actions workflow, and durable documentation were created.
3. `uv.lock` was generated for Python 3.12. The final
   documentation/provenance/secret audit completed without a release blocker.
   Commit `03aaa00` records the verified Phase 0/1 implementation and lockfile.
4. Final source audit hardening added durable worker start/failure records, guarded
   published-suite membership with serialized database row locks, and a safe
   `prepare-test-db` path for fresh and reused Compose volumes. Four migrations now
   form the schema history: `20260818_0001` through `20260818_0004`.

## Intermediate failures diagnosed and repaired

- Docker commands initially could not reach the Docker daemon. Docker Desktop was
  started, after which Compose configuration, builds, service health, migrations,
  and live smoke checks could run. Static configuration was not treated as runtime
  proof.
- The first test wiring reused development PostgreSQL/Redis cleanup targets. That
  safety defect was replaced with `evalguard_test`, Redis database 15, the
  `evalguard-tests` queue, refusal guards in pytest, and the idempotent
  `prepare-test-db` command. The final suite ran only against those isolated
  targets.
- An audit found that sequential suite-sealing triggers did not serialize
  publication against a concurrent late case insert. Migration `20260818_0004`
  added suite-row locking, and a threaded PostgreSQL regression test now proves the
  late insert is rejected.
- The first final `docker compose up --build -d --wait` returned nonzero even though
  the standalone `test-db` one-shot exited 0. API/worker dependency wiring was
  updated to require successful `test-db` completion; the repeated command exited
  0 with the expected healthy/running/completed service states.
- Ordinary Ruff formatting findings were repaired with the formatter. The final
  lint and 50-file formatting checks both passed.

## Locked environment and static verification

| Command | Exact observed result |
| --- | --- |
| `uv sync --frozen --all-groups` | Succeeded; checked 49 packages from the repository lockfile. |
| `uv run ruff check .` | Succeeded: `All checks passed!` |
| `uv run ruff format --check .` | Succeeded: 50 files already formatted. |
| `uv run mypy` | Succeeded: no issues found in 27 source files. |
| `uv run pytest` | Collected 32 tests; 32 passed in 9.73 seconds. |

The pytest run produced one dependency-originated warning and no test failure:

```text
.venv\Lib\site-packages\fastapi\testclient.py:1: StarletteDeprecationWarning:
Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
```

The warning comes from the installed FastAPI/Starlette test-client compatibility
layer. It is recorded rather than suppressed; `httpx2` was not installed.

The full suite used PostgreSQL and Redis. It includes pure grader and validation
tests plus PostgreSQL transaction/isolation coverage, live readiness checks, an RQ
worker test, API persistence/`202` coverage, idempotent suite seeding, and the
end-to-end submit/work/retrieve flow. The final additions verify published-suite
append/move/concurrent-publication rejection, durable terminal worker-failure
evidence, and test-database bootstrap safety. SQLite was not substituted.

## Compose build and service startup

| Command | Exact observed result |
| --- | --- |
| `docker compose config --quiet` | Exited successfully with no configuration error. |
| `docker compose up --build -d --wait` | Exited 0 after building the local image; API, PostgreSQL, and Redis reached healthy state; the worker was running; one-shot `migrate` and `test-db` services exited with code 0. |

This exercised the API, worker, PostgreSQL, Redis, and migration services together.
The tested tool versions were Docker 29.6.1 and Docker Compose 5.2.0.

## Independent clean-database migration check

The migration was then verified against a separately created empty database, not
only the normal Compose database:

```powershell
docker compose exec -T postgres dropdb -U evalguard --if-exists evalguard_migration_check
docker compose exec -T postgres createdb -U evalguard evalguard_migration_check
$env:EVALGUARD_DATABASE_URL = 'postgresql+psycopg://evalguard:evalguard@localhost:5432/evalguard_migration_check'
uv run alembic upgrade head
uv run alembic current
uv run alembic check
Remove-Item Env:EVALGUARD_DATABASE_URL
docker compose exec -T postgres dropdb -U evalguard evalguard_migration_check
```

`alembic upgrade head` applied `20260818_0001` -> `20260818_0002` ->
`20260818_0003` -> `20260818_0004`. `alembic current` reported
`20260818_0004 (head)`, and `alembic check` reported `No new upgrade operations
detected.` The temporary `evalguard_migration_check` database was then removed.

## Isolated test-database bootstrap

The exact `evalguard_test` database was force-dropped to prove the setup did not
depend on an existing Compose volume, then this documented command was run:

```powershell
docker compose run --build --rm test-db
```

It exited successfully, reported `created: true`, and applied all four migrations
through `20260818_0004`. Development and test database checks both reported
`20260818_0004 (head)`, and Alembic found no model/migration drift. pytest then ran
against PostgreSQL `evalguard_test`, Redis database 15, and queue
`evalguard-tests`; its safety guards refuse destructive cleanup outside those
isolated targets.

## Seed and final live queued-evaluation smoke

The initial suite loader was run twice:

```powershell
docker compose exec -T api python -m evalguard.cli seed
docker compose exec -T api python -m evalguard.cli seed
```

Both JSON responses returned the same suite ID, case ID, and case definition hash
for `parceldesk-refunds@1.0.0` / `eligible-refund`. This verified the documented
loader's idempotent path without creating duplicate suite/case rows.

A PowerShell smoke script used `Invoke-RestMethod`/`Invoke-WebRequest` against the
final rebuilt Compose API:

1. `GET /health/live` returned `200` and `{"status":"alive"}`.
2. `GET /health/ready` returned `200`; PostgreSQL and Redis were both `ready`.
3. `POST /api/v1/experiments` used the deterministic fake provider and an
   `Idempotency-Key`; it returned `202 Accepted` with durable experiment and
   execution IDs.
4. Polling `GET /api/v1/experiments/{experiment_id}` observed worker completion.
5. `GET /api/v1/executions/{execution_id}` returned one completed refund, seven
   ordered trace events, and four passing deterministic criteria. The final
   structured resolution contained `synthetic: true`.

This is **synthetic infrastructure validation**, not real-model quality or
performance evidence. No paid API, credential, token measurement, or model cost was
involved.

## Dependency failure behavior

Redis unavailability was exercised with the API still running:

```powershell
docker compose stop redis
```

PowerShell HTTP requests observed liveness remain `200` while readiness changed to
`503` and reported Redis unavailable. Redis and the worker were restored with:

```powershell
docker compose up -d --wait redis
docker compose restart worker
```

Readiness then returned `200` again. This demonstrates that liveness is independent
of backing services while readiness gates on both required dependencies.

## CI status

`.github/workflows/ci.yml` contains three Ubuntu jobs: locked dependency sync plus
Ruff/mypy, PostgreSQL/Redis migration and schema-drift check plus pytest, and
Compose configuration/image build verification. Its local commands match the
successful commands above.

The workflow has **not** been run remotely on GitHub, so no remote CI result is
claimed.

## Final repository audit

The checks above were repeated against the exact pre-commit tree. `uv lock --check`
resolved the 50 lockfile entries without drift. Repository-wide filename and
content scans found no private-key headers, provider tokens, real `.env` file,
internal host, personal filesystem path, non-example customer email, or proprietary
domain term. Credential-pattern matches were limited to the explicitly documented
local-only `evalguard` placeholders. Git's ignored-file view confirmed that the
virtual environment, caches, and bytecode are excluded from the commit candidates.

The public dependency versions, action SHA/version mappings, scenario/test counts,
fake-provider labeling, README commands, durable documents, and current source were
reconciled by two independent read-only audits. No Phase 0/1 implementation or
release-hygiene blocker remains.

## Local milestone commit

Git author identity was already configured and was not changed. The root commit
was created locally without pushing or publishing:

```text
03aaa00 feat: complete EvalGuard Phase 0 and Phase 1
```

The commit recorded 62 files and the generated `uv.lock`. This documentation-only
closure update follows it as a separate small local commit.

## Known warnings, limits, and unresolved work

- One upstream `StarletteDeprecationWarning` is present in the otherwise passing
  pytest run, as recorded above.
- The deterministic worker currently holds one transaction across the full fake
  execution. This is acceptable for the credential-free Phase 1 slice, but a later
  network provider needs shorter durable steps and explicit retry/recovery state.
  If that transaction fails, its partial tool events roll back; a separate
  transaction persists failed lifecycle state and one terminal `execution_failed`
  trace. Richer partial-step failure evidence is later hardening.
- PostgreSQL commit and Redis enqueue are separate operations. The API records an
  `enqueue_failed` terminal state and returns `503` if dispatch fails; automated
  recovery belongs to a later phase.
- Remote GitHub Actions execution remains unverified; every required local check is
  complete.

No failed Phase 0/1 implementation or local runtime check is currently known.

## Precise next action

The precise next unchecked phase is Phase 2: complete the two remaining typed
domain tools, add support-ticket/escalation state, and strengthen the planned
correctness boundaries. Do not begin it until the user explicitly advances the
milestone.
