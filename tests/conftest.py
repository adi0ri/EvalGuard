"""Shared fixtures for tests that cross PostgreSQL and Redis boundaries."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import UUID, uuid4

# Tests intentionally override application defaults before importing EvalGuard. Cleanup
# fixtures are destructive within these dedicated targets and must never touch dev data.
os.environ["EVALGUARD_ENVIRONMENT"] = "test"
os.environ["EVALGUARD_DATABASE_URL"] = os.environ.get(
    "EVALGUARD_TEST_DATABASE_URL",
    "postgresql+psycopg://evalguard:evalguard@localhost:5432/evalguard_test",
)
os.environ["EVALGUARD_REDIS_URL"] = os.environ.get(
    "EVALGUARD_TEST_REDIS_URL", "redis://localhost:6379/15"
)
os.environ["EVALGUARD_QUEUE_NAME"] = os.environ.get("EVALGUARD_TEST_QUEUE_NAME", "evalguard-tests")

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job
from rq.registry import (
    CanceledJobRegistry,
    DeferredJobRegistry,
    FailedJobRegistry,
    FinishedJobRegistry,
    ScheduledJobRegistry,
    StartedJobRegistry,
)
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from evalguard.core.config import get_settings
from evalguard.db.models import Base, CaseExecution, Customer, Experiment, Order
from evalguard.evaluation.suite import seed_initial_suite
from evalguard.workers.queue import get_queue

SessionFactory = sessionmaker[Session]
ExecutionFactory = Callable[..., UUID]


@dataclass(frozen=True)
class SeededDefinition:
    """Identifiers are safer to share than session-bound ORM instances."""

    suite_id: UUID
    case_id: UUID


def _truncate_application_tables(engine: Engine) -> None:
    tables = list(Base.metadata.sorted_tables)
    if not tables:
        pytest.fail("SQLAlchemy metadata contains no application tables", pytrace=False)
    preparer = engine.dialect.identifier_preparer
    names = ", ".join(preparer.format_table(table) for table in tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"))


def _queue_registries(
    queue: Queue,
) -> tuple[
    StartedJobRegistry,
    FinishedJobRegistry,
    FailedJobRegistry,
    DeferredJobRegistry,
    ScheduledJobRegistry,
    CanceledJobRegistry,
]:
    options = {"queue": queue, "connection": queue.connection}
    return (
        StartedJobRegistry(**options),
        FinishedJobRegistry(**options),
        FailedJobRegistry(**options),
        DeferredJobRegistry(**options),
        ScheduledJobRegistry(**options),
        CanceledJobRegistry(**options),
    )


def _delete_queue_jobs(queue: Queue) -> None:
    """Delete only this project's queue state, never the whole Redis database."""

    registries = _queue_registries(queue)
    job_ids = set(queue.job_ids)
    for registry in registries:
        job_ids.update(registry.get_job_ids(cleanup=False))

    for job_id in job_ids:
        try:
            Job.fetch(job_id, connection=queue.connection).delete()
        except NoSuchJobError:
            continue

    queue.empty()
    if registries:
        queue.connection.delete(*(registry.key for registry in registries))


@pytest.fixture(scope="session")
def postgres_engine() -> Iterator[Engine]:
    """Require the configured PostgreSQL schema; integration tests never use SQLite."""

    settings = get_settings()
    database_url = settings.database_url
    database_name = make_url(database_url).database or ""
    if settings.environment != "test" or not database_name.endswith("_test"):
        pytest.fail(
            "Refusing destructive test cleanup outside a *_test database in test mode.",
            pytrace=False,
        )
    engine = create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"connect_timeout": 3},
    )
    if engine.dialect.name != "postgresql":
        engine.dispose()
        pytest.fail(
            f"Integration tests require PostgreSQL, got {engine.dialect.name!r}",
            pytrace=False,
        )

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        engine.dispose()
        safe_url = engine.url.render_as_string(hide_password=True)
        pytest.fail(
            f"PostgreSQL is required at {safe_url}: {type(exc).__name__}: {exc}",
            pytrace=False,
        )

    expected_tables = {table.name for table in Base.metadata.sorted_tables}
    actual_tables = set(inspect(engine).get_table_names())
    missing_tables = sorted(expected_tables - actual_tables)
    if missing_tables:
        engine.dispose()
        pytest.fail(
            "PostgreSQL schema is not migrated; missing tables: "
            f"{', '.join(missing_tables)}. Run `uv run alembic upgrade head`.",
            pytrace=False,
        )

    yield engine
    engine.dispose()


@pytest.fixture
def clean_database(postgres_engine: Engine) -> Iterator[None]:
    """Give each stateful test a clean, migration-created PostgreSQL schema."""

    _truncate_application_tables(postgres_engine)
    try:
        yield
    finally:
        _truncate_application_tables(postgres_engine)


@pytest.fixture
def session_factory(postgres_engine: Engine, clean_database: None) -> SessionFactory:
    del clean_database
    return sessionmaker(bind=postgres_engine, expire_on_commit=False)


@pytest.fixture
def db_session(session_factory: SessionFactory) -> Iterator[Session]:
    with session_factory() as session:
        yield session
        session.rollback()


@pytest.fixture
def seeded_suite(db_session: Session) -> SeededDefinition:
    suite, case = seed_initial_suite(db_session)
    db_session.commit()
    return SeededDefinition(suite_id=suite.id, case_id=case.id)


@pytest.fixture
def execution_factory(
    session_factory: SessionFactory,
    seeded_suite: SeededDefinition,
) -> ExecutionFactory:
    created = 0

    def create_execution(
        *,
        materialize_state: bool = True,
        order_external_id: str = "PD-1001",
        customer_external_id: str | None = None,
        total_cents: int = 4599,
        order_status: str = "delivered",
        shipping_address: dict[str, str | None] | None = None,
    ) -> UUID:
        nonlocal created
        created += 1
        customer_id = customer_external_id or f"CUST-TEST-{created}"
        now = datetime(2026, 1, 14, 16, 30, tzinfo=UTC)

        with session_factory.begin() as session:
            experiment = Experiment(
                suite_id=seeded_suite.suite_id,
                provider="deterministic-fake",
                status="queued",
                idempotency_key=f"fixture-{uuid4()}",
            )
            session.add(experiment)
            session.flush()
            execution = CaseExecution(
                experiment_id=experiment.id,
                case_id=seeded_suite.case_id,
                trial_number=1,
                status="queued",
            )
            session.add(execution)
            session.flush()

            if materialize_state:
                customer = Customer(
                    case_execution_id=execution.id,
                    external_id=customer_id,
                    name=f"Synthetic Customer {created}",
                    email=f"customer-{created}@example.test",
                )
                session.add(customer)
                session.flush()
                session.add(
                    Order(
                        case_execution_id=execution.id,
                        customer_id=customer.id,
                        external_id=order_external_id,
                        total_cents=total_cents,
                        currency="USD",
                        status=order_status,
                        placed_at=datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
                        delivered_at=now if order_status == "delivered" else None,
                        shipping_address_line1=(shipping_address or {}).get("line1"),
                        shipping_address_line2=(shipping_address or {}).get("line2"),
                        shipping_city=(shipping_address or {}).get("city"),
                        shipping_region=(shipping_address or {}).get("region"),
                        shipping_postal_code=(shipping_address or {}).get("postal_code"),
                        shipping_country=(shipping_address or {}).get("country"),
                    )
                )
            execution_id = execution.id
        return execution_id

    return create_execution


@pytest.fixture
def redis_connection() -> Iterator[Redis]:
    settings = get_settings()
    redis_url = settings.redis_url
    redis_path = urlsplit(redis_url).path
    if (
        settings.environment != "test"
        or settings.queue_name != "evalguard-tests"
        or redis_path in {"", "/", "/0"}
    ):
        pytest.fail(
            "Refusing destructive queue cleanup outside the isolated test queue/Redis DB.",
            pytrace=False,
        )
    connection = Redis.from_url(
        redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        connection.ping()
    except (RedisError, OSError) as exc:
        connection.close()
        parsed_url = urlsplit(redis_url)
        safe_redis_url = (
            f"{parsed_url.scheme}://{parsed_url.hostname or '<unknown>'}"
            f"{f':{parsed_url.port}' if parsed_url.port is not None else ''}"
            f"{parsed_url.path}"
        )
        pytest.fail(
            f"Redis is required at {safe_redis_url}: {type(exc).__name__}: {exc}",
            pytrace=False,
        )

    queue = get_queue(connection)
    _delete_queue_jobs(queue)
    try:
        yield connection
    finally:
        _delete_queue_jobs(queue)
        connection.close()


@pytest.fixture
def rq_queue(redis_connection: Redis) -> Queue:
    return get_queue(redis_connection)


@pytest.fixture
def client(clean_database: None) -> Iterator[TestClient]:
    del clean_database
    from evalguard.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client
