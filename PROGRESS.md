# EvalGuard Progress Log

This is a chronological evidence log. A command is recorded as passing only when
it was actually run. Phase 0/1 entries below are from 2026-08-18 on
Windows/PowerShell; the Phase 2 and Phase 3 continuations are explicitly dated
2026-08-22.

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
those graders detect regressions. No Phase 3 implementation had started at that
Phase 2 checkpoint.

Git author identity remained configured, so local commit `e0f9715`
(`feat: complete EvalGuard Phase 2`) recorded the verified 21-file implementation,
migration, tests, and durable documentation. The Phase 2 change had not yet been
pushed at that checkpoint, and remote Phase 2 CI had not been run.

## Phase 3 implementation and review hardening — 2026-08-22

The user explicitly advanced the repository to Phase 3 and authorized a later
push. The immutable `parceldesk-refunds@1.0.0` suite and hashes remain unchanged.
The separately published `parceldesk-refunds@2.0.0` suite contains 25 independently
authored cases in the required 7 normal / 5 ineligible / 5 boundary / 4
authorization-privacy / 4 duplicate-recovery distribution. Fixtures carry a fixed
policy timestamp, an independent policy oracle, typed fake plans, and complete
definition hashes. The deterministic fake remains the only provider.

The worker now materializes multiple execution-owned customers/orders, supplies the
requester customer as trusted tool context, executes typed plans through all four
production tools, persists six deterministic grader families, and reconciles the
experiment from all child executions under a parent-row lock. Eleven stable fake
behavior IDs provide one reference behavior and ten deliberate defects.

Final review then tightened boundaries before repository closure:

- every tool uses execution-row then order-row lock order;
- successful exact cached replay rechecks actor ownership and adds one ordered
  `tool_replay` event after the original tool outcome without another invocation or
  mutation;
- the executor validates case/experiment suite membership before starting work or
  returning a completed replay, with the cross-suite rejection test exercising
  both queued and completed execution states;
- frozen typed contracts bind the resolution and every expected state mutation to
  the policy order, reject expected mutation for denied outcomes, and validate
  other cross-field invariants;
- privacy grading detects protected numeric as well as string values embedded in
  free text, uses token boundaries so `US` does not match inside `CUSTOMER`, and
  recognizes grouped numeric forms such as `9,125`;
- `trace_v1.invocation_outcomes_match` and
  `escalation_v1.ticket_owner_matches_order` add independent evidence, while
  `state_v1.tickets_exact` includes the ticket customer; and
- Alembic revision `20260822_0006` adds an order/customer/execution parent key and
  matching support-ticket foreign key so a same-execution ticket customer must own
  the referenced order.

All ten deliberate fake defects now run through targeted PostgreSQL execution and
fail their intended criterion across the six grader families. No SQLite or real
provider was substituted.

## Phase 3 final locked and automated verification — 2026-08-22

The final current-tree commands produced these results:

| Command | Exact observed result |
| --- | --- |
| `uv sync --frozen --all-groups` | Succeeded; checked 49 locked packages. |
| `uv run ruff check .` | Succeeded: `All checks passed!` |
| `uv run ruff format .` | Succeeded; formatted 2 files. |
| `uv run ruff format --check .` | Succeeded after the formatter had corrected 2 files. |
| `uv run mypy` | Succeeded: no issues found in 29 source files. |
| `uv run pytest tests/unit -q` | 84 passed with 1 warning in 0.38 seconds. |
| `uv run pytest tests/integration/test_phase3_scenarios_postgres.py -q` | 38 passed with 1 warning in 24.87 seconds. |
| `uv run pytest` | Collected 158 tests; 158 passed with 1 warning in 52.51 seconds. |

The warning remains the dependency-originated `StarletteDeprecationWarning` from
FastAPI's `TestClient` compatibility layer. It was not suppressed and did not fail
a test. Earlier intermediate counts were superseded by this final-tree run.

## Phase 3 clean migration and immutable seed verification — 2026-08-22

A separately created blank disposable PostgreSQL database was used for the complete
six-revision chain. The observed sequence was:

1. upgrade the blank database through `20260822_0006 (head)`;
2. run `alembic check`, which reported no model/migration drift;
3. downgrade `20260822_0006` to `20260822_0005`;
4. re-upgrade `20260822_0005` to `20260822_0006`;
5. run `alembic check` again with no drift;
6. run the suite seed twice and observe stable IDs, membership, and hashes; and
7. drop the disposable database.

The verified suite hashes were:

- v2: `28289cfdcd98be852b00e451b2f52ae8a9af65a454a6642bb96acb4991a19782`;
- legacy v1: `aaedf653770166eb7e967352b9242509a94dd9ba3e4ee833576617c403ec6abd`.

## Phase 3 Compose and final live smoke — 2026-08-22

| Command | Exact observed result |
| --- | --- |
| `docker compose config --quiet` | Exited 0 with no configuration error. |
| `docker compose up --build -d --wait` | Exited 0; API, PostgreSQL, and Redis were healthy, the worker was running, and one-shot `migrate` and `test-db` exited 0. |
| Container `alembic current` | Reported `20260822_0006 (head)`. |
| Container `alembic check` | Reported no model/migration drift. |
| Container seed, run twice | Returned stable v1/v2 identities, hashes, and 25-case v2 membership. |

The final rebuilt API used idempotency key
`phase3-final-b2adf6d0-6cfd-485f-8b35-f14cf4d41da7`. Initial submission and replay
both returned `202` with the same experiment and execution IDs. Experiment
`f661c252-631f-4a22-9ded-4118d7048feb` completed all 25 of 25 executions through
the deterministic fake. Retrieval showed:

- 507 of 507 passing grader results across six families;
- 44 tool invocations;
- 164 trace events, including exactly one `tool_replay`;
- 7 refunds and 5 support tickets;
- a sampled 4,599-cent refund;
- a sampled final address of `82 Harbor Fern Road`;
- a sampled `manual_review` ticket; and
- a sampled authorization tool invocation with failed status and no foreign state
  disclosure or mutation.

This is **synthetic infrastructure validation**, not real-model quality,
performance, latency, token, or cost evidence. No real or paid provider was called.

## Phase 3 dependency-failure and log check — 2026-08-22

During a deliberate Redis outage, liveness remained `200` and readiness returned
`503` with Redis unavailable. After Redis and the worker were restored, readiness
returned `200`. Four worker Redis connection `ERROR` lines were expected during the
outage; a post-restoration scan found zero suspicious lines.

## Phase 3 known limit, Git status, and precise next action — 2026-08-22

Creating all case executions is transactional, but sending 25 jobs to Redis is not
one atomic operation. A process crash after only some enqueue calls succeed can
leave partial dispatch requiring reconciliation. Outbox-style recovery for that
crash window remains Phase 4 work; it is not claimed by Phase 3 idempotency or
aggregate lifecycle handling.

The Phase 2 commits were pushed successfully; local `main` and its `origin/main`
tracking ref were both at `26c3e86` before the Phase 3 commit. The final
high-confidence credential scan returned zero matching files, Git author name and
email remained configured, and commit `083808c` records the verified Phase 3
implementation. Phase 3 is not pushed and no Phase 3 remote-CI result is claimed.
Keep it local until the user explicitly asks to publish these Phase 3 commits. Do
not begin Phase 4 until the user explicitly advances it.

## Phase 3 publication and Phase 4 authorization — 2026-08-22

After the historical checkpoint above, the user explicitly requested publication
of Phase 3 and authorized the start of Phase 4. Phase 3 implementation commit
`083808c` and documentation follow-up `6655e79` were pushed successfully; local
`main` and `origin/main` were both at `6655e79`. No Phase 3 remote-CI result was
observed or claimed.

## Phase 4 implementation and final local acceptance — 2026-08-22

The user explicitly advanced the repository to Phase 4. The implementation adds a
PostgreSQL transactional outbox, generation-fenced RQ delivery, durable execution
attempts and leases, bounded classified retries, explicit job timeouts, exact tool-
step accounting, worker/CLI recovery, and typed development/test-only fault plans.
It does not change either immutable suite, any case definition, fake plan, grader
family, or suite/case hash. The deterministic fake remains the only provider.

## Phase 4 final dependency, static, and automated verification — 2026-08-22

The final current-tree commands produced these exact results:

| Command | Exact observed result |
| --- | --- |
| `uv lock --offline` | Succeeded: `Resolved 50 packages in 2ms`. |
| `uv sync --frozen --all-groups` | Succeeded: `Checked 49 packages in 8ms`. |
| `git diff --check` | Clean except for the expected LF→CRLF working-copy warnings. |
| `uv run ruff check .` | Succeeded: `All checks passed!` |
| `uv run ruff format --check .` | Succeeded: `67 files already formatted`. |
| `uv run mypy` | Succeeded: `Success: no issues found in 31 source files`. |
| `uv run pytest` | Collected 189 tests; `189 passed, 1 warning in 135.79s (0:02:15)`. |

The one pytest warning is the known dependency-originated Starlette
`TestClient`/httpx deprecation warning. It was not suppressed and did not fail the
run. The 189-test suite covers the PostgreSQL/Redis/RQ/API/end-to-end contracts for
outbox recovery and collisions, initial-only replay, attempt exhaustion,
permanent/timeout/step failures, rollback, isolation, stale/duplicate delivery,
fresh and expired leases, pre-claim recovery, dispatched-job loss, fault-plan
environment restrictions, and retrieval evidence.

The final concurrency audit added three PostgreSQL cases. Two parametrized cases
prove that a migrated legacy
`enqueue_failed` execution whose exact current-generation RQ delivery is terminal
or missing enters pre-claim abandonment recovery, records one interrupted attempt,
advances to generation 2, and leaves its next dispatch pending. One case pauses a
worker after claim, recovers that attempt, then proves the old worker revalidates
expected attempt number, generation, and `running` status under the execution lock
and returns stale before creating any customer, order, refund, invocation, grader,
or provider/tool trace evidence.

## Phase 4 disposable migration and backfill verification — 2026-08-22

The explicit target was
`evalguard_phase4_final_migration_20260822`. It was absent before the check, was
created solely for this sequence, and the normal development/test data was not
modified. The observed sequence was:

1. upgrade the blank database through `20260822_0006`;
2. seed the unchanged v1 and v2 suites and confirm their hashes:
   - v1: `aaedf653770166eb7e967352b9242509a94dd9ba3e4ee833576617c403ec6abd`;
   - v2: `28289cfdcd98be852b00e451b2f52ae8a9af65a454a6642bb96acb4991a19782`;
3. insert representative legacy executions in `queued`, `enqueue_failed`,
   `running`, `retrying`, and `completed` states;
4. upgrade to `20260822_0007 (head)` and run `alembic check`, which reported no
   model/migration drift;
5. verify existing experiment timeouts were backfilled to 300 seconds; `running`
   and `retrying` rows were normalized to `queued`; queued, legacy
   `enqueue_failed`, and normalized active rows received pending generation-1
   dispatches; and the completed row remained untouched without a dispatch;
6. downgrade `0007` to `0006`, manually restore representative `running` and
   `retrying` rows, re-upgrade to `0007`, and observe the same normalization and
   dispatch backfill again;
7. confirm `alembic current` again reported `20260822_0007 (head)` and a second
   `alembic check` reported no new upgrade operations; and
8. drop the exact disposable database and verify that it was absent afterward.

## Phase 4 Compose, schema, seed, and idle recovery verification — 2026-08-22

| Command or check | Exact observed result |
| --- | --- |
| `docker compose config --quiet` | Exited 0 with no configuration error. |
| `docker compose up --build -d --wait` | Exited 0 after the final image rebuild and wait. |
| Container migration state | Reported `20260822_0007 (head)`. |
| Container `alembic check` | Reported no model/migration drift. |
| Container seed, run twice | Returned the same suite/case IDs and hashes both times, with 25 v2 cases. |
| `evalguard reconcile`, run twice | Both bounded passes reported no work and all summary counters at zero. |

After bring-up, PostgreSQL, Redis, and the API were healthy; the worker was
running; and the one-shot `migrate` and `test-db` services had exited 0.

## Phase 4 final live baseline and idempotent replay — 2026-08-22

The final rebuilt image returned `200` from both liveness and readiness. A baseline
v2 experiment submission returned `202`. The worker completed all 25 executions;
retrieval showed 25 total execution attempts, 25 dispatches, 507/507 passing
deterministic grader results, 44 tool calls, 7 refunds, and 5 support tickets.

Replaying the same idempotency key returned `202` with the same experiment ID. All
25 executions remained completed and the aggregate execution-attempt count remained
25, demonstrating that compatible replay did not repeat provider/tool work.

## Phase 4 final live retry, timeout, and worker-interruption recovery — 2026-08-22

Three separate legacy-v1 executions exercised distinct recovery paths:

- A typed transient failure produced attempt statuses `retry_scheduled` then
  `succeeded`, persisted failure kind `injected_transient`, and dispatch generations
  1 and 2. The final execution had one refund and 4/4 passing graders.
- A real RQ-enforced one-second timeout produced attempt statuses
  `retry_scheduled` then `succeeded`, persisted retryable failure kind `timeout`,
  and dispatch generations 1 and 2. The final execution had one refund and 4/4
  passing graders.
- An actual worker process received SIGKILL while its attempt was `running` with a
  live lease. Recovery persisted an `interrupted` attempt with failure kind
  `worker_interruption`, advanced from generation 1 to 2, and then recorded a
  `succeeded` attempt. The final execution had one refund and 4/4 passing graders.

The latter two checks exercise real RQ timeout and process-loss boundaries rather
than inferring production-hook behavior from direct function tests.

## Phase 4 Redis-outage acceptance and automatic outbox recovery — 2026-08-22

Redis was deliberately stopped while the API remained available. Liveness stayed
`200`, readiness changed to `503`, and a new v1 submission still returned `202`.
The accepted execution remained `queued` with zero execution attempts; its durable
dispatch remained `pending` after publication attempt 1 recorded `ConnectionError`.

After Redis was restored, worker reconciliation automatically published and
completed that same execution on its first provider/tool attempt. The dispatch
attempt count became 2, one refund existed, 4/4 graders passed, and readiness
returned to `200`. The outage therefore did not terminalize accepted work or spend
an execution attempt.

The final log review found only the expected injected-timeout and Redis-outage
warnings/errors. At the end of verification, PostgreSQL, Redis, and the API were
healthy; the worker was running; and the one-shot migration/test-database services
retained their successful exit state. All live results above are **synthetic
infrastructure validation**, not real-model quality, reliability, latency, token,
or cost evidence. No real or paid provider was called.

## Phase 4 completion, Git status, and precise next action — 2026-08-22

Phase 4 implementation and local acceptance are complete. All Phase 4 acceptance
items in `PLAN.md` are verified from the evidence above. No Phase 4 commit hash,
push, or remote-CI result is claimed: the local commit is pending, and Phase 4 must
remain unpushed until the user explicitly requests publication.

Phase 5 comparison/reporting remains out of scope and has not started. The precise
next action is to record the pending local Phase 4 commit when it occurs, then wait
for explicit user direction before any push or Phase 5 work.

## Phase 4 local implementation commit checkpoint — 2026-08-22

After the pending-commit checkpoint above, Git author identity remained configured
and local commit `e276ba5` (`feat: complete EvalGuard Phase 4`) recorded the verified
Phase 4 implementation. Local `main` is one commit ahead of `origin/main`, which
remains at Phase 3 documentation commit `6655e79`. Phase 4 is unpushed and no
remote-CI result is claimed.

Phase 5 has not started. The next action is to wait for explicit user direction
before either pushing Phase 4 or beginning Phase 5. No subsequent documentation
commit hash is claimed here.

## Phase 4 publication and Phase 5 authorization — 2026-08-22

The user explicitly requested that Phase 4 be pushed and Phase 5 begin. Before
publication, local `main` contained Phase 4 implementation commit `e276ba5` and
documentation commit `ac7d39e`, while `origin/main` was `6655e79`. The exact push
reported:

```text
6655e79..ac7d39e  main -> main
```

`git rev-parse HEAD` and `git rev-parse origin/main` then both returned
`ac7d39e524488bfb61a8c9e6b36051c1bb43c13d`. This is publication evidence only;
no Phase 4 remote-CI result was observed or claimed. The required repository
documents and original attached product contract were then read completely before
Phase 5 code changed.

The key Phase 5 modeling decision was to keep each immutable case's synthetic
customer request unchanged and introduce separate immutable agent/system prompts.
Prompt v1 and prompt v2 run as two experiments because an experiment has one
reproducible prompt identity. Each arm contains 25 cases × three trials = 75
executions; the comparison pairs those arms for 150 raw executions. The
deterministic fake records both prompt identities but deliberately follows the same
typed source-controlled plan, so the result validates infrastructure rather than
prompt quality.

## Phase 5 implementation — 2026-08-22

- Alembic revision `20260822_0008` and the SQLAlchemy model add published
  `agent_prompts`, nullable prompt identity for honest legacy experiments, persisted
  experiment trial count, and PostgreSQL checks limiting experiment/trial values to
  one through three. The prompt trigger permits only initial publication and then
  rejects update/delete.
- Prompt hashes are
  `08724bf556610d9563f27189eb5b298e62a1ce57952885f481c5f8cf93f5ab69`
  for `parceldesk-support-agent@1.0.0` and
  `f93d1378329f1156219063cebabfde0cd65e44d08c382541ed600c05140038ad`
  for `@2.0.0`. The preserved suite hashes remain
  `aaedf653770166eb7e967352b9242509a94dd9ba3e4ee833576617c403ec6abd`
  for v1 and
  `28289cfdcd98be852b00e451b2f52ae8a9af65a454a6642bb96acb4991a19782`
  for v2.
- Experiment creation now makes the complete case/trial grid and includes prompt,
  trial count, and exact fault trial in idempotent intent. A Phase 4 fault plan
  targets one `(case_key, trial_number)` rather than all trials for that case.
- Shared PostgreSQL analysis loads changing evidence together plus immutable suite
  membership, then implements non-vacuous execution pass semantics,
  explicit rates, criterion-vector stability, grader/category breakdowns,
  attempts/retries, and min/mean/median/nearest-rank-p95/max infrastructure timing.
  Token/cost availability is false rather than a fabricated zero.
- Compatible comparisons require distinct experiments with the same suite/hash,
  provider, resilience policy, trial count, and exact case/trial grid. Fault-
  injected arms are rejected. Quality deltas remain null until both arms complete
  with grader evidence for every execution.
- `GET /api/v1/prompts`, the experiment summary and comparison endpoints, and the
  Jinja2 comparison report are implemented. The report uses the same Python
  analysis function, autoescaping, inline CSS, a visible synthetic-validation
  limitation, prompt/suite hashes, and raw execution links.
- Jinja2 `3.1.6` is a locked direct runtime dependency. No model SDK, API key, real
  provider, or paid call was added.

## Phase 5 focused and full automated verification — 2026-08-22

| Command | Exact observed result |
| --- | --- |
| Focused existing unit/API/RQ/e2e tests | `106 passed, 1 warning in 46.70s`. |
| New prompt/trial/analysis API tests | `6 passed, 1 warning in 22.02s`. |
| New exact Phase 5 e2e test | `1 passed, 1 warning in 47.35s`. |
| `uv run ruff check .` | Succeeded: `All checks passed!`. |
| `uv run ruff format --check .` | Succeeded: `74 files already formatted`. |
| `uv run mypy` | Succeeded: no issues found in 34 source files. |
| `uv run pytest` | Collected 196 tests; `196 passed, 1 warning in 221.25s (0:03:41)`. |

The exact 150-execution automated case verified 150 attempts, 150 dispatches,
3,042 grader rows, 264 tool invocations, 984 trace events, 42 refunds, and 30
support tickets. Every execution had isolated customer and order state; all 150
provider-request traces carried the correct prompt identity (75 per version).
Both arms passed 75/75 executions and 1,521/1,521 criteria with 25 stable-pass
cases. These are deterministic synthetic infrastructure counts, not model quality.

The one known dependency-originated FastAPI/Starlette `TestClient` warning remains
unchanged and did not fail any run. No test was skipped, weakened, or substituted
with SQLite.

## Phase 5 packaging failure and correction — 2026-08-22

The first final Compose build failed. Hatch already included
`evalguard/api/templates/comparison.html` as package data, while an explicit
`force-include` rule attempted to add the same wheel path again. The build backend
reported:

```text
ValueError: A second file is being added to the wheel archive at the same path:
`evalguard/api/templates/comparison.html`.
```

The redundant force-include rule was removed; the template remained part of the
ordinary package. `uv lock` then resolved 51 packages in 2 ms, and the repeated
Compose build installed `evalguard==0.1.0` non-editably with Jinja2 and completed
successfully. The live container later rendered the report with `200`, proving the
template was present in the built wheel/image.

The final `uv sync --frozen --all-groups` also rebuilt and installed the local
project successfully (`Prepared 1 package in 2.41s`, installed in 144 ms).

## Phase 5 disposable migration verification — 2026-08-22

The exact disposable target was
`evalguard_phase5_final_migration_20260822`; the normal development and test
databases were not used for destructive cleanup. The verified sequence was:

1. create the blank disposable PostgreSQL database;
2. upgrade from base through `20260822_0008`;
3. run `alembic check`, which reported no new upgrade operations;
4. seed both unchanged suites and both published prompt hashes;
5. confirm `alembic_version` was `20260822_0008` and two prompt rows were present;
6. downgrade to `20260822_0007`;
7. re-upgrade to `20260822_0008` and repeat the no-drift check;
8. re-seed and confirm the same two prompt hashes/count; and
9. drop the exact disposable database with forced connection cleanup.

The blank database had no experiments during inspection, as expected. The
migration does not fabricate a prompt identity for pre-Phase-5 experiment rows;
their nullable reference remains an honest legacy state, while new API submissions
always select a published prompt.

## Phase 5 final Compose and live comparison — 2026-08-22

`docker compose config --quiet` passed. After the packaging correction,
`docker compose build migrate test-db api worker` succeeded and
`docker compose up -d --wait --wait-timeout 180` exited 0. PostgreSQL, Redis, and
the API were healthy; the worker was running; and `migrate` plus `test-db` exited
0. Repeated in-container `evalguard seed` calls returned the same suite, case,
prompt IDs, and hashes. Liveness and readiness both returned `200`.

The final live submissions returned `202`:

- baseline prompt v1 experiment
  `575126a2-c921-4afd-858a-146c2878fa61`, with 75 executions;
- candidate prompt v2 experiment
  `55ca7503-0048-4e4f-bf11-4f88d36c9601`, with 75 executions.

The Compose worker completed all 150 executions. Each arm reported 75/75 passing
executions, 1,521/1,521 passing criteria, 25 stable-pass cases, and 75 attempts.
The compatible comparison was ready and returned zero strict-pass, criterion-pass,
and stable-case deltas. The HTML report returned `200`, contained the synthetic
label, and contained exactly 150 raw execution links. Same-key replay of the
candidate returned `202` with the same experiment and execution IDs.

The direct raw-evidence query for those two experiment IDs returned:

| executions | attempts | dispatches | graders | tools | traces | refunds | tickets |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 150 | 150 | 150 | 3,042 | 264 | 984 | 42 | 30 |

Provider-request traces grouped exactly 75 under prompt version `1.0.0` and 75
under `2.0.0`. Equal deterministic results are expected because the fake does not
interpret prompt text; no prompt-quality improvement is claimed.

Redis was then stopped after all work was terminal. Liveness remained `200`,
readiness returned `503` with PostgreSQL ready and Redis unavailable, and readiness
returned to `200` after Redis restarted. Final Compose state again showed healthy
PostgreSQL, Redis, and API, a running worker, and successful one-shot services.

## Phase 5 completion and precise next action — 2026-08-22

Every Phase 5 acceptance item in `PLAN.md` is verified from the evidence above.
No real/paid provider was called, token/cost data was not invented, and Phase 6 was
not started. Phase 5 has no local commit at this checkpoint and remains unpushed;
`origin/main` remains published Phase 4 documentation commit `ac7d39e`. The next
action is to create local Phase 5 commits while Git identity remains configured,
then wait for explicit user direction before any Phase 5 push or Phase 6 work.

## Phase 5 release review and integrity corrections — 2026-08-22

Three independent release reviews found defects that the first 196-test checkpoint
did not cover. All were fixed before milestone closure:

1. An omitted-prompt replay of a pre-Phase-5 idempotency key conflicted because the
   legacy experiment honestly had `agent_prompt_id = NULL` while request defaults
   resolved to v2. Omitted prompt fields now match only an otherwise identical
   one-trial legacy null-prompt experiment. Either explicitly supplied prompt field
   conflicts, and every fresh omitted-prompt submission still binds v2.
2. Aggregate denominators were initially derived from observed execution rows, and
   direct writes could relabel an experiment's prompt/trial identity or attach a
   child trial beyond the parent count. Expected coordinates now come from immutable
   suite membership × persisted trials and expose `grid_integrity`. Revision `0008`
   freezes experiment prompt/trial identity and adds parent-aware child-trial
   triggers. Invalid grids cannot become terminal/fully graded or comparable;
   timing and attempt deltas wait for complete equal samples. Prompt comparison
   additionally requires published, same-family, distinct prompt identities.
3. A completed execution with any nonempty subset of grader rows initially counted
   as graded, so deletion of a failing criterion could improve strict pass. Analysis
   now loads a scoped one-row-per-execution aggregate of `grader_completed` trace
   evidence. Exactly one structurally valid marker must exactly match the complete
   canonical persisted grader vector, sorted grader names, and aggregate pass bit.
   Missing, duplicate, malformed, or mismatched evidence is ungraded. A full-grid
   regression deletes one failing criterion while retaining a passing row and
   proves strict pass cannot improve or enable a comparison delta.

Focused post-review evidence included the legacy replay regression; trigger/grid/
prompt-identity checks; three Phase 5 analysis cases; and the exact 150-execution
end-to-end case. The final independent audit reported no remaining release blocker
and no Phase 6, credential, paid-provider, or real-provider scope.

## Phase 5 final closure gates — 2026-08-22

| Command | Exact observed result |
| --- | --- |
| `uv sync --frozen --all-groups` | Succeeded: `Checked 50 packages in 8ms`. |
| `uv run ruff check .` | Succeeded: `All checks passed!`. |
| `uv run ruff format --check .` | Succeeded: `74 files already formatted`. |
| `uv run mypy` | Succeeded: no issues found in 34 source files. |
| `uv run pytest --collect-only -q` | Collected 199 tests in 0.91 seconds; emitted the one known upstream warning. |
| `uv run pytest` | `199 passed, 1 warning in 415.56s (0:06:55)`. |
| `git diff --check` | Exit 0; no whitespace error. Git printed only expected LF-to-CRLF notices. |
| `docker compose config --quiet` | Exit 0 with no output. |
| `uv run alembic current` | `20260822_0008 (head)`. |
| `uv run alembic check` | `No new upgrade operations detected.` |

The warning remains FastAPI's installed `TestClient` importing the upstream
Starlette/httpx compatibility path. No test was skipped, weakened, or replaced by
SQLite.

The final disposable migration target was
`evalguard_phase5_closure_migration_20260822`. From blank state it upgraded through
all eight revisions, reached `20260822_0008 (head)`, reported no drift, seeded both
unchanged suite hashes and both prompt hashes, and showed one prompt-immutability
trigger, one experiment-identity trigger, and two child-trial triggers. It then
downgraded to `0007`, re-upgraded to `0008`, repeated no-drift and seeding, showed
all three prompt/trial identity triggers after the round trip, and was dropped by
its exact name. The normal development and test databases were not destructively
cleaned.

`docker compose build migrate test-db api worker` rebuilt the non-editable package
successfully. A clean isolated Compose project named `evalguard-phase5-closure`
created new PostgreSQL/Redis volumes and started on host ports 55432, 56379, and
58000. `up --no-build -d --wait --wait-timeout 180` exited 0: PostgreSQL, Redis,
and API were healthy; the worker ran; and `migrate`/`test-db` exited 0. The clean
database was at revision `0008` with two prompts and all four triggers. Two seed
runs returned identical suite, case, prompt IDs, and hashes. The built wheel
contained `evalguard/api/templates/comparison.html`. Liveness and readiness both
returned `200`.

## Phase 5 final-tree live comparison — 2026-08-22

Fresh submissions against the clean packaged image returned `202`:

- baseline prompt v1: `322e5e82-c779-4acd-9ebe-fbd84c0bc849`, 75 executions;
- candidate prompt v2: `3ae3afb5-d669-4ee8-bce5-29742abeef38`, 75 executions.

Both completed. Each summary reported 75 expected, 75 graded, 75 passing,
1,521/1,521 criteria, 25 stable-pass cases, a valid complete grid, and comparison
readiness. JSON comparison returned `200` and zero strict-pass, criterion-pass, and
stable-case deltas. The HTML report returned `200`, `text/html`, its prominent
synthetic label, and exactly 150 raw-execution links. Compatible candidate replay
returned `202` with the same experiment and all 75 execution IDs.

Scoped PostgreSQL evidence returned:

| executions | attempts | dispatches | graders | tools | traces | refunds | tickets |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 150 | 150 | 150 | 3,042 | 264 | 984 | 42 | 30 |

All 150 executions had customer and order state. Prompt versions split 75/75 in
provider-request traces. Exactly 150 `grader_completed` events declared 3,042
criteria, matching all 3,042 grader rows used by the completeness guard.

The first combined Redis stop/probe/restart shell request was rejected by command
safety before execution, so it changed no state. Split explicit calls then stopped
only the isolated closure Redis service: liveness stayed `200`, readiness returned
`503` with PostgreSQL ready and Redis unavailable, Redis restarted, and readiness
returned to `200`. Final summary/report retrieval still returned `200`. The final
stack is running and healthy; RQ reports one idle worker, 150 finished jobs, and 0
failed. The only error-level log entries are the expected Redis reconnect messages
during that deliberate outage.

All final results remain **synthetic infrastructure validation**. The deterministic
fake intentionally ignores prompt semantics, no token/cost data exists, no real or
paid provider was invoked, and Phase 6 was not started. The next action is to record
local Phase 5 commits if Git identity remains configured. Do not push Phase 5 or
start Phase 6 without new user direction and separate cost/credential approval.

The final pre-commit audit confirmed configured Git author name/email without
printing either value. High-confidence secret patterns returned zero matches, and
the named proprietary-source scan returned zero matches outside integrity-policy
documents. `git diff --check` remained free of whitespace errors. No unrelated
user-owned file was removed or overwritten.

## Phase 5 local implementation commit checkpoint — 2026-08-22

Git identity remained configured. Local commit `c481ed4`
(`feat: complete EvalGuard Phase 5`) records the verified implementation, migration,
tests, final 199-test evidence, clean Compose/live comparison, and durable records.
`origin/main` remains at published Phase 4 documentation commit `ac7d39e`; Phase 5
is intentionally unpushed and no Phase 5 remote-CI result is claimed. This
follow-up durable-document update records that exact checkpoint. The next action is
to wait for explicit user direction before any Phase 5 push or Phase 6 work; a real
provider still requires separate credentials/cost approval.

## Phase 5 publication and Phase 6 authorization — 2026-08-22

The user explicitly requested Phase 5 publication and the start of Phase 6. The
push completed successfully. A post-push read-only check returned the same full
commit for local `HEAD` and `origin/main`:

```text
270bbe453245e2ac52c93e29e62d2d041c03af50
```

The published tip is documentation commit `270bbe4`, immediately after Phase 5
implementation commit `c481ed4`. This verifies publication only; no Phase 5 remote-
CI result was observed or claimed.

The original product contract requires a second explicit approval before adding or
invoking one real provider, using a credential, or possibly spending money. The
user advanced the milestone but did not give that separate approval. Phase 6
therefore began with its cost-free portfolio track only. No provider/model, SDK,
API-key setting, request budget, price, or paid call was added or used.

## Phase 6 cost-free portfolio implementation — 2026-08-22

- `docs/PORTFOLIO_GUIDE.md` now provides the 60-second project story, architecture
  diagram, experiment methodology, exact Phase 5 evidence, reproducible demo, code
  tour, limitations, interview design stories, and the separate real-provider
  approval/result-record contract.
- `scripts/demo.ps1` is a PowerShell-native deterministic-fake walkthrough. It
  verifies readiness and seeded v2 inputs, submits prompt v1 and v2 with three
  trials, waits for both 75-execution arms, retrieves both summaries and the
  comparison, checks the HTML report, and prints its synthetic limitation. Its
  provider field is fixed to `deterministic-fake`.
- `DECISIONS.md` records that a real network adapter cannot safely be inserted into
  the current long deterministic transaction. A later approved implementation
  needs durable provider turns, network I/O outside database transactions, claim
  revalidation, persisted responses before tool effects, and explicit resume
  semantics.
- Every durable document was updated to distinguish the locally verified cost-free
  track from the still-unapproved real-provider track.

## Phase 6 demo and final gates — 2026-08-22

The PowerShell parser reported `PowerShell parser: passed`. The exact live command
was:

```powershell
.\scripts\demo.ps1 -BaseUrl 'http://localhost:58000' `
  -RunId 'phase6docs20260822' -PollIntervalSeconds 1 -TimeoutSeconds 900
```

It exited 0 against the clean isolated `evalguard-phase5-closure` packaged stack
and produced:

- baseline experiment `a3675de7-2ee0-47ec-b17d-c354c7d87d02`;
- candidate experiment `8f6cfffd-9532-46a8-98dc-23ed30b22ba6`;
- 75 expected and 75 passing executions in each arm;
- 1,521/1,521 passing criteria in each arm;
- valid grids, a ready comparison, report status `200`, and exactly 150 raw links;
- token usage unavailable and cost unavailable; and
- scoped PostgreSQL counts of 150 executions, 150 attempts, 150 dispatches, 3,042
  grader rows, 264 tool invocations, 984 trace events, 42 refunds, and 30 tickets.

After suppressing PowerShell's progress UI for a clean demo, the final script was
parsed again and replayed with the same `RunId`. It returned the same two experiment
IDs and the scoped execution/attempt/dispatch counts remained 150/150/150, proving
the demo's same-key replay did not repeat provider/tool work.

This is **synthetic infrastructure validation**, not prompt/model quality, model
latency, token, or cost evidence.

The final current-tree commands produced these exact results:

| Command | Exact observed result |
| --- | --- |
| `uv sync --frozen --all-groups` | Succeeded: `Checked 50 packages in 10ms`. |
| PowerShell parser over `scripts/demo.ps1` | Passed with zero parse errors. |
| `uv run ruff check .` | Succeeded: `All checks passed!`. |
| `uv run ruff format --check .` | Succeeded: `75 files already formatted`. |
| `uv run mypy` | Succeeded: no issues found in 34 source files. |
| `uv run pytest` | `199 passed, 1 warning in 187.64s (0:03:07)`. |
| `docker compose config --quiet` | Exited 0 with no output. |
| `uv run alembic current` | Reported `20260822_0008 (head)`. |
| `uv run alembic check` | Reported `No new upgrade operations detected.` |
| `docker compose build migrate test-db api worker` | Exited 0; the non-editable `evalguard:local` image built successfully. |
| `git diff --check` | Exited 0; only expected LF-to-CRLF working-copy warnings were printed. |

The pytest warning is the unchanged dependency-originated FastAPI/Starlette
`TestClient` warning. No test was skipped, weakened, or replaced with SQLite. A
container inspection confirmed the rebuilt image contains the portfolio guide,
PowerShell demo, and installed Jinja2 comparison template:

```powershell
docker run --rm --entrypoint python evalguard:local -c `
  "from pathlib import Path; paths=['/app/docs/PORTFOLIO_GUIDE.md','/app/scripts/demo.ps1','/app/.venv/lib/python3.12/site-packages/evalguard/api/templates/comparison.html']; print({path: Path(path).is_file() for path in paths})"
```

All three paths returned `True`. The final artifact hash check was:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath `
  'docs/PORTFOLIO_GUIDE.md','scripts/demo.ps1'
docker run --rm --entrypoint sha256sum evalguard:local `
  /app/docs/PORTFOLIO_GUIDE.md /app/scripts/demo.ps1 `
  /app/.venv/lib/python3.12/site-packages/evalguard/api/templates/comparison.html
```

The final host/image hashes matched at
`23e2f1d27aa1d19cb8dc55c22be91e1ef01b32bac83b79864a3cba9759b4a929`
for the guide and
`f46cc83be53d10da54770e2ed8b483653b283284df5fe960c5cf6548a073c71f`
for the demo; the installed comparison template was also present.

The exact high-confidence secret scan was:

```powershell
$matches = rg -l --hidden --glob '!.git/**' --glob '!uv.lock' `
  '(-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,})' .
```

It returned zero matching files.

An independent read-only final review then checked factual claims, the PowerShell
flow and idempotency-key length, stale status language, and the approval boundary.
After correcting the distinction between fixture materialization and conditional
refund/ticket effects, it reported no remaining blocker.

The cost-free Phase 6 portfolio track is locally complete. Phase 6 as a whole is
not complete because the real-provider adapter and bounded experiment are still
unchecked and unapproved. The precise next action is to ask for the separate
approval naming one provider/model, secret-delivery method, request scope, and
possible-spend ceiling. Do not add or call the provider before that approval.

## Phase 6 local portfolio commit checkpoint — 2026-08-22

Git author identity remained configured. Local commit `5a058e0`
(`feat: complete Phase 6 portfolio track`) records the verified guide, demo, exact
evidence, teaching notes, and durable-document updates. Local `main` is one commit
ahead of `origin/main`, which remains at the requested Phase 5 publication commit
`270bbe4`. The Phase 6 commit is intentionally unpushed, and no remote-CI result is
claimed. The separately approved real-provider track remains the next unchecked
work.

## Phase 6 bounded-provider authorization and design — 2026-08-23

The user directed EvalGuard to finish all phases, push the result, and not ask
again. Decision D027 treats that as authorization to complete the bounded keyless
implementation while retaining the separate safety boundary around a billable
request. Presence-only checks found neither `EVALGUARD_OPENAI_API_KEY` nor
`OPENAI_API_KEY` on the host. The rebuilt API and worker containers also reported
`False` without printing a value. Consequently zero paid calls were made and no
live-provider result exists.

First-party OpenAI Responses, GPT-5.6 Luna, model, and Structured Outputs
documentation was reviewed before implementation. The server-owned track pins
`openai-responses` with `gpt-5.6-luna`, reasoning `none`, 1,200 maximum output
tokens, six turns, a 45-second per-turn timeout, zero SDK retries, one network
attempt per turn, `store=false`, and `parallel_tool_calls=false`. It permits one
idempotency-keyed experiment globally, at most 25 cases, one trial, one logical
attempt, a 300-second job timeout, no deterministic fault injection, and a
conservative $5 estimated-cost ceiling. The immutable price snapshot dated
2026-08-23 is input $0.20/M, cached input $0.02/M, and output $1.20/M. Calculated
USD is labeled estimated rather than provider billing.

## Phase 6 implementation — 2026-08-23

- `pyproject.toml` and `uv.lock` add OpenAI SDK 2.54.0 while retaining the
  credential-free deterministic fake as the default and CI provider.
- Revision `20260823_0009` adds immutable experiment provider identity, a partial
  unique one-real-experiment fence, provider-turn request/response/tool/usage
  evidence, append-only database triggers, recovery counts, and attempt-versus-
  resume dispatch identity.
- The adapter locally validates exact strict tool/final schemas before crossing
  the send fence, disables SDK retries/storage/parallel tools, normalizes only
  bounded safe response fields, redacts transport errors, and rejects a returned
  model outside the approved identity.
- The real executor commits the request before network I/O, holds no database
  transaction during the scripted send, commits purchased response evidence
  before tools, and advances each durable tool output in a short transaction.
  Unknown send outcomes are terminal and never resent. Safe worker recovery
  resumes the same attempt through a new dispatch generation at most three times.
- API retrieval exposes immutable provider identity and ordered turns. Summary and
  comparison paths expose available provider usage, client-observed latency, and
  price-snapshot estimates without turning missing values into zero.
- Provider-neutral grading retains exact typed semantics, policy, authorization,
  state, trace, and structured-resolution checks while removing fake-only injected
  retry/duplicate choreography from real-provider scoring.

The final review found and corrected these material issues before acceptance:

1. JSONB object validation originally depended on key iteration order; it now
   checks the complete key set and has a reordered-object regression.
2. Usage relations and PostgreSQL integer bounds are validated before persistence;
   pricing comes from the immutable experiment snapshot.
3. Returned-model mismatch retains evidence and usage but blocks tool effects and
   cost attribution. Only the exact alias or its dated form is accepted.
4. Request validation and hashing complete before `request_started`; strict schema
   validation rejects unsupported `allOf`/`oneOf`, and identifiers match database
   length bounds.
5. PostgreSQL advisory locking plus the partial unique index enforces the single
   real experiment. Comparison requires identical nonempty returned-model sets.
6. Provider-neutral duplicate/recovery cases use semantic expectations rather than
   demanding fake-only replays or repeated state changes.
7. A fenced `request_started` turn that returns late now transitions from
   `indeterminate` to immutable `superseded` evidence. A PostgreSQL regression
   proves usage/cost evidence survives, a conflicting second persistence cannot
   overwrite it, and no refund, tool invocation, tool trace, or business effect is
   created.

## Phase 6 failures observed and corrected — 2026-08-23

No failed command was relabeled as a pass:

- The first combined Phase 6 run reported three failures and two fixture errors.
  The failures exposed the JSONB key-order assumption; the errors came from
  running PostgreSQL suites concurrently against the same resettable test
  database. The validator was fixed, and database tests were then run serially.
- An early full run reached 225 passes and one failure because an old RQ regression
  mutated an experiment's now-immutable provider identity. The test now injects
  the intended executor failure without weakening the production invariant.
- The first disposable migration downgrade found that an intermediate migration
  edit had not created the final index in that temporary schema. The still-new
  revision was corrected with the final index and a defensive downgrade; the
  subsequent clean round trip and drift checks passed.
- After the final image rebuild, the persistent development database—already at
  the earlier uncommitted form of `0009`—reported only the final partial unique
  index as drift. `CREATE UNIQUE INDEX IF NOT EXISTS` added that schema object
  without deleting or rewriting rows, after which `uv run alembic check` reported
  no new operations. Fresh databases already receive the index from `0009`.
- One focused format check identified a line wrap in the late-response regression;
  formatting corrected it and the configured checks passed.
- A combined Redis stop/probe/start shell request was rejected by command safety
  before execution. The explicit stop, probe, start, and recovery checks were run
  as separate commands and passed.

## Phase 6 final keyless verification — 2026-08-23

The exact final-tree results were:

| Command or gate | Exact observed result |
| --- | --- |
| `uv sync --frozen --all-groups` | `Checked 55 packages in 14ms`. |
| `uv run ruff check .` | `All checks passed!`. |
| `uv run ruff format --check .` | `83 files already formatted`. |
| `uv run mypy` | `Success: no issues found in 37 source files`. |
| Late-response regression | `1 passed, 1 warning in 4.67s`. |
| Phase 6 real-provider integration file | `11 passed, 1 warning in 17.96s`. |
| `uv run pytest` | `247 passed, 1 warning in 240.12s (0:04:00)`. |
| `docker compose config --quiet` | Exited 0 with no output. |
| `docker compose up --build --wait` | Exited 0; rebuilt `evalguard:local`, completed migration/test-database one-shots, started API/worker, and reached healthy PostgreSQL/Redis/API plus a running worker. |
| Container revision | PostgreSQL reported `20260823_0009`. |
| `uv run alembic check` | `No new upgrade operations detected.` after the persistent-development schema reconciliation described above. |
| High-confidence secret scan | `high_confidence_secret_match_files=0`. |
| `git diff --check` | No whitespace errors; only expected LF-to-CRLF working-copy notices. |

The disposable database `evalguard_phase6_final_20260823` upgraded from base
through `20260823_0009`, reported no drift, downgraded to `20260822_0008`,
re-upgraded to head, reported no drift again, and was dropped. Repeated seeding
remained stable at two suites, 26 total cases, and two prompts. The immutable suite
hashes remained
`aaedf653770166eb7e967352b9242509a94dd9ba3e4ee833576617c403ec6abd`
for v1 and
`28289cfdcd98be852b00e451b2f52ae8a9af65a454a6642bb96acb4991a19782`
for v2; prompt hashes remained
`08724bf556610d9563f27189eb5b298e62a1ce57952885f481c5f8cf93f5ab69`
and
`f93d1378329f1156219063cebabfde0cd65e44d08c382541ed600c05140038ad`.

The final rebuilt-image deterministic smoke returned liveness/readiness `200` and
submission `202`. Experiment `32ec7383-f6ed-478a-bfe0-7bd7a0727e64` completed
execution `b22329b2-906c-43b9-8902-21671683065a` in one attempt with one refund,
`lookup_order` then `issue_refund`, seven trace events, and 4/4 passing deterministic
criteria. Provider-turn count was correctly zero for the fake provider. Same-key
replay returned the same experiment and one execution. A separately submitted
`openai-responses` intent returned `403` with the gate disabled before network
work. With Redis stopped, liveness stayed `200` and readiness returned `503`;
after restart, Redis became healthy, readiness returned `200`, and the worker
reconnected without replacement.

Every runtime result in this section is either keyless implementation validation
or **synthetic infrastructure validation**. It is not OpenAI model-quality,
provider-latency, token, or billing evidence. The Phase 6 implementation/local
acceptance gate is complete. The credentialed experiment remains unchecked and
externally blocked because no worker-only credential is available; zero paid calls
were made. Git commits and requested publication are the next actions.

## Phase 6 keyless implementation commit checkpoint — 2026-08-23

Git author name and email remained configured without either value being printed.
Local commit `5479c9a` (`feat: complete bounded Phase 6 provider track`) records the
adapter, immutable provider configuration, revision `20260823_0009`, provider-turn
ledger, recovery executor, provider-neutral grading, API/analysis exposure, 247-test
gate, final rebuilt-image smoke, and reconciled durable documents. At this
checkpoint local `main` is one commit ahead of `origin/main`, which remains at the
published cost-free portfolio checkpoint `31a4192`. No remote-CI result is inferred.
The next action is the user-requested push.

## Phase 6 keyless implementation publication — 2026-08-23

`git push origin main` succeeded and advanced `origin/main` from `31a4192` through
implementation commit `5479c9a` to verification commit `73784c9`. Immediate
read-only resolution returned the same full SHA for local `HEAD` and
`origin/main`:

```text
73784c9e94df6c3dafb33ee39440facc79f40858
```

This records publication only. No remote-CI run was observed or inferred, no
credential appeared, and no real or paid provider request was made. This durable
publication follow-up is the final local documentation change; after it is pushed,
the only unchecked Phase 6 result remains the externally blocked credentialed
experiment.

## Phase 6 remote CI observation — 2026-08-30

The previously published documentation follow-up is commit `e301d25`
(`docs: record Phase 6 publication`). On 2026-08-30, a read-only GitHub CLI check
observed final-tip GitHub Actions run
[`32627986410`](https://github.com/adi0ri/EvalGuard/actions/runs/32627986410).
The run was created for exact SHA
`e301d25e37e8bf4de880fabf544da1c7a19a4dfd` at 2026-08-23 08:20:11 UTC and
completed successfully at 08:21:51 UTC. All three jobs passed:

- `Lint and type-check`: locked sync, Ruff lint, 83-file formatting check, and
  mypy over 37 source files;
- `Migrate and test`: fresh PostgreSQL upgrade, schema-drift check, and
  `247 passed, 1 warning in 57.17s`; and
- `Verify Docker and Compose`: Compose configuration validation and application-
  image build.

The immediately preceding run for verification commit `73784c9` is recorded by
GitHub as cancelled; it is not used as evidence. The later run at the final
publication tip is the claimed Phase 6 remote result. A same-day presence-only
host check still found no supported provider credential. This CI run used the
keyless workflow and does not represent a paid request, live-model result, or
provider-quality measurement. The only unchecked Phase 6 outcome remains the
separately gated credentialed experiment.
