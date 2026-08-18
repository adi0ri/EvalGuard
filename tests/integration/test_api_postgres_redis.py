"""API persistence and live dependency readiness integration coverage."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from redis import Redis
from rq import Queue
from sqlalchemy import select
from sqlalchemy.orm import Session

import evalguard.api.routes as experiment_routes
from evalguard.db.models import CaseExecution, Experiment

pytestmark = pytest.mark.integration


def test_create_experiment_returns_202_after_records_are_persisted_and_queued(
    client: TestClient,
    seeded_suite: object,
    db_session: Session,
    rq_queue: Queue,
) -> None:
    del seeded_suite
    response = client.post(
        "/api/v1/experiments",
        json={
            "suite_name": "parceldesk-refunds",
            "suite_version": "1.0.0",
            "provider": "deterministic-fake",
            "trials": 1,
        },
        headers={"Idempotency-Key": "integration-create-202"},
    )

    assert response.status_code == 202
    payload = response.json()
    experiment_id = UUID(payload["id"])
    execution_id = UUID(payload["execution_ids"][0])
    assert payload["status"] == "queued"
    assert response.headers["location"] == f"/api/v1/experiments/{experiment_id}"

    db_session.expire_all()
    experiment = db_session.get(Experiment, experiment_id)
    execution = db_session.get(CaseExecution, execution_id)
    assert experiment is not None
    assert execution is not None
    assert experiment.status == "queued"
    assert execution.experiment_id == experiment.id
    assert execution.status == "queued"
    assert (
        db_session.scalars(
            select(CaseExecution).where(CaseExecution.experiment_id == experiment.id)
        )
        .one()
        .id
        == execution_id
    )
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
    assert rq_queue.job_ids == [f"case-execution-{retry.json()['execution_ids'][0]}"]
