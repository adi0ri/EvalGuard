# EvalGuard Progress Log

This is a chronological evidence log. A command is recorded as passing only when
it was actually run. Phase 0/1 entries below are from 2026-08-18 on
Windows/PowerShell; the Phase 2 continuation is explicitly dated 2026-08-22.

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

The repository was published publicly at
`https://github.com/adi0ri/EvalGuard`. The first push of `main` at commit
`7b0bd2e1d280d1ed897c13811f22feb72cb4ff33` triggered GitHub Actions run
[`32195390241`](https://github.com/adi0ri/EvalGuard/actions/runs/32195390241),
which completed successfully. Its three jobs reported:

- lint and type-check: passed in 17 seconds;
- Docker Compose configuration and image build: passed in 25 seconds; and
- clean PostgreSQL migration, schema-drift check, and pytest: passed in 30 seconds.

This is the first verified remote CI result. The public repository's `main` branch
was created directly from the already-verified local history; no history was
rewritten and no pull request was needed for the initial publication.

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

No failed Phase 0/1 implementation or local runtime check is currently known.

## Precise next action

The precise next unchecked phase is Phase 2: complete the two remaining typed
domain tools, add support-ticket/escalation state, and strengthen the planned
correctness boundaries. Do not begin it until the user explicitly advances the
milestone.

## Phase 2 implementation — 2026-08-22

The user explicitly advanced the repository to Phase 2. The implementation stayed
within that milestone:

- migration `20260822_0005` added relational shipping-address columns to each
  execution-owned order and an execution-owned `support_tickets` table with
  composite foreign keys and uniqueness constraints;
- the typed `update_shipping_address` and `escalate_ticket` tools were added to
  the production `ToolRuntime`;
- address changes are allowed only while an order is `pending` or `processing`,
  while refunds at or above 50,000 cents are rejected and require escalation;
- execution and order row locks serialize state-changing tool calls, exact call-ID
  replay requires the same tool and validated arguments, and semantic duplicate
  address/escalation requests converge without an additional business mutation;
- deterministic policy rejections now leave a failed `ToolInvocation` and an
  ordered `tool_error` event, while transaction rollback removes business state
  and its invocation/trace evidence together; and
- execution retrieval now includes a structured shipping address and relational
  support-ticket records. Published-suite triggers and stable hashes were left
  unchanged, and their existing immutability tests remain in the passing suite.

The deterministic queued provider intentionally still performs the immutable
Phase 1 `lookup_order`/`issue_refund` case. The two new tools are exercised through
the production runtime with PostgreSQL and API retrieval coverage. Adding queued
address/escalation scenarios or their broader graders would be Phase 3 work and
was not started.

## Phase 2 audit findings and repairs — 2026-08-22

- The audit required expected domain rejections to be durable evidence, not only
  raised exceptions. Failed calls now persist the validated arguments, failure
  message and type, completion time, and `tool_call`/`tool_error` sequence. Stable
  replay of that exact failed call raises the persisted failure without adding a
  second invocation.
- Concurrency coverage was expanded to prove that two exact concurrent address
  calls replay one invocation, two different call IDs with the same requested
  address produce one mutation plus one semantic duplicate, and concurrent
  escalations converge on one ticket. A forced-rollback test proves address,
  ticket, invocation, and trace writes share the caller's transaction boundary.
- The first submission-key serialization design did not guarantee that lock
  acquisition, persistence commits, dispatch outcome, and unlock all used one
  physical PostgreSQL connection. The final implementation uses a dedicated
  connection-bound session and a key-derived advisory lock across that entire
  sequence. Concurrent API tests prove one experiment/execution/enqueue for two
  simultaneous successful submissions and prove a retry waits for the first
  dispatch outcome before recovering the same durable experiment.
- Cross-execution tool access is rejected in the runtime, and PostgreSQL composite
  foreign keys independently reject a ticket that combines execution ownership
  from different cases.

## Phase 2 locked and automated verification — 2026-08-22

The following final commands were run against the completed Phase 2 tree:

| Command | Exact observed result |
| --- | --- |
| `uv sync --frozen --all-groups` | Succeeded: `Checked 49 packages in 4ms`. |
| `uv run ruff check .` | Succeeded: `All checks passed!` |
| `uv run ruff format --check .` | Succeeded: `52 files already formatted`. |
| `uv run mypy` | Succeeded: no issues found in 27 source files. |
| `uv run pytest` | Collected 49 tests; 49 passed with one warning in 15.85 seconds. |

The only warning was the already-recorded dependency-originated
`StarletteDeprecationWarning` from FastAPI's `TestClient` compatibility layer. It
was not suppressed, and it did not fail a test.

A final read-only integrity scan covered all 21 changed or new files and found no
credential-like values. A separate scan of source, tests, and migrations found no
obvious external or proprietary fixture markers.

The 49-test PostgreSQL/Redis suite includes the original Phase 0/1 checks plus
successful and rejected address changes, required high-value escalation, exact and
semantic duplicate calls, conflicting call-ID reuse, concurrent calls, explicit
transaction rollback, cross-execution access and foreign-key rejection, API
retrieval of the new state, concurrent API submission idempotency, RQ worker
regression coverage, and the complete queued end-to-end refund flow. SQLite was not
substituted and no existing test was weakened.

## Phase 2 clean migration round trip — 2026-08-22

The complete five-revision chain was exercised only against the disposable empty
database `evalguard_phase2_migration_check`. The observed sequence was:

1. create the disposable database and point `EVALGUARD_DATABASE_URL` to it;
2. run `uv run alembic upgrade head`, which applied `20260818_0001` through
   `20260822_0005`;
3. run `uv run alembic current`, which reported `20260822_0005 (head)`, and
   `uv run alembic check`, which reported `No new upgrade operations detected.`;
4. run `uv run alembic downgrade 20260818_0004`, which removed only the Phase 2
   schema and reported `20260818_0004` as current;
5. run `uv run alembic upgrade head` again, returning to
   `20260822_0005 (head)` with no model/migration drift; and
6. remove the environment override and drop the disposable database.

Neither `evalguard` nor `evalguard_test` was used as a destructive migration-test
target. After cleanup, only those normal development and test databases remained.

## Phase 2 Compose and live smoke — 2026-08-22

| Command | Exact observed result |
| --- | --- |
| `docker compose config --quiet` | Exited 0 with no configuration error. |
| `docker compose up --build -d --wait` | Exited 0; API, PostgreSQL, and Redis were healthy, the worker was running, and one-shot `migrate` and `test-db` exited 0. |
| `uv run alembic current` | Reported `20260822_0005 (head)`. |
| `uv run alembic check` | Reported `No new upgrade operations detected.` |

The documented loader was run twice with
`docker compose exec -T api python -m evalguard.cli seed`. Both calls returned the
same suite ID, case ID, and definition hash for
`parceldesk-refunds@1.0.0` / `eligible-refund`, proving that the published suite's
idempotent seed path remained intact.

The final rebuilt live Compose API produced this exact smoke evidence:

1. `GET /health/live` returned `200`.
2. `GET /health/ready` returned `200` with PostgreSQL and Redis ready.
3. `POST /api/v1/experiments` used idempotency key
   `phase2-live-1787441765695` and returned `202` for experiment
   `35278b3d-23ab-482b-bb9a-2798a1b895e6` and execution
   `baee1588-b044-4c08-8a0b-e2a13729d93b`.
4. Polling observed the execution reach `completed`.
5. Retrieval returned one synthetic customer, one order, and one completed refund;
   the unchanged baseline case correctly had a null shipping address and no support
   tickets. It contained the two baseline tool invocations, seven ordered trace
   events, and four of four passing deterministic grader results.

After the final documented-tree image rebuild, liveness and readiness again
returned `200`. Replaying the same key returned `202` with the same experiment and
execution IDs, the experiment remained completed, and retrieval still showed one
refund, zero tickets, seven trace events, and four passing grader results.

This remains **synthetic infrastructure validation**, not real-model quality or
performance evidence. No real or paid provider was called.

An attempted Phase 2 dependency-down probe was blocked by the execution
environment's command policy before it ran. No Compose service was stopped by that
attempt, so no new unavailable-dependency result is claimed. The verified Phase 1
Redis-unavailable liveness/readiness evidence earlier in this log remains the
available dependency-failure proof.

## Phase 2 known limits and precise next action — 2026-08-22

- The one upstream `StarletteDeprecationWarning` remains.
- The short deterministic worker still uses one transaction for its local fake
  execution; unexpected worker failure still records a terminal summary after the
  partial execution transaction rolls back. Shorter durable network-provider steps
  and recovery remain later resilience work.
- PostgreSQL persistence and Redis dispatch remain separate systems. Submission
  serialization makes same-key outcomes deterministic, but automated outbox-style
  recovery remains later work.
- Phase 2 does not claim that the fake provider autonomously selects the two new
  tools in a queued evaluation. Its live path remains the verified refund baseline.

Phase 2 is locally verified. The precise next unchecked work, only after the user
explicitly advances the milestone, is Phase 3: expand the immutable original suite
to exactly 25 scenarios, add deterministic state/trace/policy/authorization/
escalation graders, and add deliberately defective fake-agent behaviors that prove
those graders detect regressions. No Phase 3 implementation was started here.
