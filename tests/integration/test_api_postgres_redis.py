"""API persistence, idempotency, and live dependency integration coverage."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from rq import Queue
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

import evalguard.api.routes as experiment_routes
from evalguard.api.app import create_app
from evalguard.db.models import CaseExecution, Experiment
from evalguard.domain.tool_schemas import (
    EscalateTicketArgs,
    ShippingAddress,
    UpdateShippingAddressArgs,
)
from evalguard.domain.tools import ToolRuntime
from evalguard.evaluation.suite import SUITE_NAME, SUITE_VERSION

pytestmark = pytest.mark.integration
PHASE3_CASE_COUNT = 25


def test_create_experiment_returns_202_after_records_are_persisted_and_queued(
    client: TestClient,
    seeded_suite: object,
    db_session: Session,
    rq_queue: Queue,
) -> None:
    del seeded_suite
    response = client.post(
        "/api/v1/experiments",
        json={"provider": "deterministic-fake", "trials": 1},
        headers={"Idempotency-Key": "integration-create-202"},
    )

    assert response.status_code == 202
    payload = response.json()
    experiment_id = UUID(payload["id"])
    execution_ids = {UUID(value) for value in payload["execution_ids"]}
    assert len(execution_ids) == PHASE3_CASE_COUNT
    assert payload["status"] == "queued"
    assert response.headers["location"] == f"/api/v1/experiments/{experiment_id}"

    db_session.expire_all()
    experiment = db_session.get(Experiment, experiment_id)
    assert experiment is not None
    assert experiment.status == "queued"
    executions = db_session.scalars(
        select(CaseExecution).where(CaseExecution.experiment_id == experiment.id)
    ).all()
    assert len(executions) == PHASE3_CASE_COUNT
    assert {execution.id for execution in executions} == execution_ids
    assert all(execution.status == "queued" for execution in executions)
    assert set(rq_queue.job_ids) == {
        f"case-execution-{execution_id}" for execution_id in execution_ids
    }


def test_explicit_phase1_suite_submission_remains_available(
    client: TestClient,
    seeded_legacy_suite: object,
    db_session: Session,
    rq_queue: Queue,
) -> None:
    del seeded_legacy_suite
    response = client.post(
        "/api/v1/experiments",
        json={
            "suite_name": SUITE_NAME,
            "suite_version": SUITE_VERSION,
            "provider": "deterministic-fake",
            "trials": 1,
        },
        headers={"Idempotency-Key": "integration-explicit-phase1"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert len(payload["execution_ids"]) == 1
    execution_id = UUID(payload["execution_ids"][0])
    execution = db_session.get(CaseExecution, execution_id)
    assert execution is not None
    assert execution.status == "queued"
    assert rq_queue.job_ids == [f"case-execution-{execution_id}"]


def test_readiness_exercises_live_postgres_and_redis(
    client: TestClient,
    redis_connection: Redis,
) -> None:
    assert redis_connection.ping() is True

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"postgres": "ready", "redis": "ready"},
    }


def test_idempotent_retry_requeues_an_enqueue_failure(
    client: TestClient,
    seeded_suite: object,
    db_session: Session,
    rq_queue: Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del seeded_suite
    real_enqueue = experiment_routes.enqueue_execution

    def fail_enqueue(_: UUID) -> None:
        raise OSError("synthetic Redis dispatch failure")

    monkeypatch.setattr(experiment_routes, "enqueue_execution", fail_enqueue)
    first = client.post(
        "/api/v1/experiments",
        json={"provider": "deterministic-fake", "trials": 1},
        headers={"Idempotency-Key": "retry-enqueue-failure"},
    )
    assert first.status_code == 503

    experiment = db_session.scalar(
        select(Experiment).where(Experiment.idempotency_key == "retry-enqueue-failure")
    )
    assert experiment is not None
    db_session.refresh(experiment)
    original_id = experiment.id
    assert experiment.status == "enqueue_failed"
    failed_executions = db_session.scalars(
        select(CaseExecution).where(CaseExecution.experiment_id == experiment.id)
    ).all()
    assert len(failed_executions) == PHASE3_CASE_COUNT
    assert all(execution.status == "enqueue_failed" for execution in failed_executions)

    monkeypatch.setattr(experiment_routes, "enqueue_execution", real_enqueue)
    retry = client.post(
        "/api/v1/experiments",
        json={"provider": "deterministic-fake", "trials": 1},
        headers={"Idempotency-Key": "retry-enqueue-failure"},
    )
    replay = client.post(
        "/api/v1/experiments",
        json={"provider": "deterministic-fake", "trials": 1},
        headers={"Idempotency-Key": "retry-enqueue-failure"},
    )

    assert retry.status_code == 202
    assert replay.status_code == 202
    assert UUID(retry.json()["id"]) == original_id
    assert UUID(replay.json()["id"]) == original_id
    assert retry.json()["execution_ids"] == replay.json()["execution_ids"]
    retried_execution_ids = retry.json()["execution_ids"]
    assert len(retried_execution_ids) == PHASE3_CASE_COUNT
    assert set(rq_queue.job_ids) == {
        f"case-execution-{execution_id}" for execution_id in retried_execution_ids
    }


def test_concurrent_idempotency_key_submissions_create_one_experiment(
    seeded_suite: object,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del seeded_suite
    start = Barrier(2)
    enqueue_attempts: list[UUID] = []
    enqueue_attempts_lock = Lock()

    def record_enqueue(execution_id: UUID) -> None:
        with enqueue_attempts_lock:
            enqueue_attempts.append(execution_id)

    monkeypatch.setattr(experiment_routes, "enqueue_execution", record_enqueue)

    def submit() -> tuple[int, dict[str, object]]:
        start.wait(timeout=5)
        with TestClient(create_app()) as concurrent_client:
            response = concurrent_client.post(
                "/api/v1/experiments",
                json={"provider": "deterministic-fake", "trials": 1},
                headers={"Idempotency-Key": "concurrent-phase3-submission"},
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: submit(), range(2)))

    assert [status_code for status_code, _ in responses] == [202, 202]
    assert len({str(payload["id"]) for _, payload in responses}) == 1
    assert len({str(payload["execution_ids"]) for _, payload in responses}) == 1
    assert all(len(payload["execution_ids"]) == PHASE3_CASE_COUNT for _, payload in responses)
    assert len(enqueue_attempts) == PHASE3_CASE_COUNT
    assert len(set(enqueue_attempts)) == PHASE3_CASE_COUNT
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Experiment)) == 1
        assert session.scalar(select(func.count()).select_from(CaseExecution)) == PHASE3_CASE_COUNT


def test_concurrent_idempotent_retry_waits_for_dispatch_outcome(
    seeded_suite: object,
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del seeded_suite
    start = Barrier(2)
    first_dispatch_started = Event()
    release_first_dispatch = Event()
    enqueue_attempts: list[UUID] = []
    enqueue_attempts_lock = Lock()

    def controlled_enqueue(execution_id: UUID) -> None:
        with enqueue_attempts_lock:
            enqueue_attempts.append(execution_id)
            attempt_number = len(enqueue_attempts)
        if attempt_number == 1:
            first_dispatch_started.set()
            if not release_first_dispatch.wait(timeout=5):
                raise TimeoutError("Test did not release the first dispatch attempt")
            raise OSError("synthetic concurrent dispatch failure")

    monkeypatch.setattr(experiment_routes, "enqueue_execution", controlled_enqueue)

    def submit() -> tuple[int, dict[str, object]]:
        start.wait(timeout=5)
        with TestClient(create_app()) as concurrent_client:
            response = concurrent_client.post(
                "/api/v1/experiments",
                json={"provider": "deterministic-fake", "trials": 1},
                headers={"Idempotency-Key": "concurrent-dispatch-retry-phase3"},
            )
            return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit) for _ in range(2)]
        try:
            assert first_dispatch_started.wait(timeout=5)
            assert all(not future.done() for future in futures)
        finally:
            release_first_dispatch.set()
        responses = [future.result(timeout=10) for future in futures]

    assert sorted(status_code for status_code, _ in responses) == [202, 503]
    assert len(enqueue_attempts) == PHASE3_CASE_COUNT + 1
    assert enqueue_attempts[0] in enqueue_attempts[1:]
    assert len(set(enqueue_attempts)) == PHASE3_CASE_COUNT
    with session_factory() as session:
        experiment = session.scalar(
            select(Experiment).where(
                Experiment.idempotency_key == "concurrent-dispatch-retry-phase3"
            )
        )
        assert experiment is not None
        assert experiment.status == "queued"
        assert session.scalar(select(func.count()).select_from(Experiment)) == 1
        executions = session.scalars(
            select(CaseExecution).where(CaseExecution.experiment_id == experiment.id)
        ).all()
        assert len(executions) == PHASE3_CASE_COUNT
        assert all(execution.status == "queued" for execution in executions)


def test_execution_retrieval_includes_shipping_address_and_escalation(
    client: TestClient,
    session_factory: sessionmaker[Session],
    execution_factory: Callable[..., UUID],
) -> None:
    execution_id = execution_factory(order_status="processing")
    address = ShippingAddress(
        line1="14 Cedar Lane",
        line2="Suite 2",
        city="Port Aurora",
        region="CA",
        postal_code="94107",
        country="US",
    )
    with session_factory.begin() as session:
        runtime = ToolRuntime(session, execution_id)
        runtime.update_shipping_address(
            UpdateShippingAddressArgs(order_id="PD-1001", address=address),
            tool_call_id="api-address-update-1",
        )
        ticket = runtime.escalate_ticket(
            EscalateTicketArgs(
                order_id="PD-1001",
                reason_code="manual_review",
                reason="Synthetic API retrieval evidence.",
                priority="normal",
            ),
            tool_call_id="api-escalation-1",
        )

    response = client.get(f"/api/v1/executions/{execution_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["orders"][0]["shipping_address"] == address.model_dump(mode="json")
    assert payload["support_tickets"] == [
        {
            "id": ticket.ticket_id,
            "case_execution_id": str(execution_id),
            "customer_id": payload["orders"][0]["customer_id"],
            "order_id": payload["support_tickets"][0]["order_id"],
            "reason_code": "manual_review",
            "reason": "Synthetic API retrieval evidence.",
            "priority": "normal",
            "status": "escalated",
            "tool_call_id": "api-escalation-1",
            "created_at": payload["support_tickets"][0]["created_at"],
            "escalated_at": payload["support_tickets"][0]["escalated_at"],
        }
    ]
