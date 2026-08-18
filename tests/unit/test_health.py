"""Service-free unit coverage for API health behavior."""

from collections.abc import Iterator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from evalguard.api import health


@pytest.fixture
def health_client() -> Iterator[tuple[TestClient, Session]]:
    application = FastAPI()
    application.include_router(health.router)
    session = cast(Session, object())
    application.dependency_overrides[health.get_db] = lambda: session

    with TestClient(application) as client:
        yield client, session


def test_liveness_reports_that_the_api_process_is_alive(
    health_client: tuple[TestClient, Session],
) -> None:
    client, _ = health_client

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_readiness_succeeds_when_postgres_and_redis_are_available(
    monkeypatch: pytest.MonkeyPatch,
    health_client: tuple[TestClient, Session],
) -> None:
    client, session = health_client
    checked: dict[str, object] = {}

    def postgres_is_ready(candidate: Session) -> bool:
        checked["session"] = candidate
        return True

    def redis_is_ready(redis_url: str) -> bool:
        checked["redis_url"] = redis_url
        return True

    monkeypatch.setattr(health, "check_postgres", postgres_is_ready)
    monkeypatch.setattr(health, "check_redis", redis_is_ready)

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"postgres": "ready", "redis": "ready"},
    }
    assert checked == {
        "session": session,
        "redis_url": health.get_settings().redis_url,
    }


@pytest.mark.parametrize(
    ("postgres_ready", "redis_ready", "expected_dependencies"),
    [
        (False, True, {"postgres": "unavailable", "redis": "ready"}),
        (True, False, {"postgres": "ready", "redis": "unavailable"}),
        (False, False, {"postgres": "unavailable", "redis": "unavailable"}),
    ],
    ids=["postgres-unavailable", "redis-unavailable", "all-unavailable"],
)
def test_readiness_returns_503_when_a_dependency_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    health_client: tuple[TestClient, Session],
    postgres_ready: bool,
    redis_ready: bool,
    expected_dependencies: dict[str, str],
) -> None:
    client, _ = health_client
    monkeypatch.setattr(health, "check_postgres", lambda _: postgres_ready)
    monkeypatch.setattr(health, "check_redis", lambda _: redis_ready)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "dependencies": expected_dependencies,
    }
