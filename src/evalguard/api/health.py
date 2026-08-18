"""Liveness and dependency-aware readiness probes."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from evalguard.core.config import get_settings
from evalguard.db.session import get_db

router = APIRouter(tags=["health"])


def check_postgres(session: Session) -> bool:
    """Exercise a real connection instead of merely checking configuration."""

    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return False
    return True


def check_redis(redis_url: str) -> bool:
    """Ping Redis and always release the short-lived readiness connection."""

    client: Redis = Redis.from_url(
        redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        return bool(client.ping())
    except RedisError:
        return False
    finally:
        client.close()


@router.get("/health/live")
def liveness() -> dict[str, str]:
    """Report whether the API process can serve requests."""

    return {"status": "alive"}


@router.get("/health/ready", response_model=None)
def readiness(session: Annotated[Session, Depends(get_db)]) -> dict[str, Any] | JSONResponse:
    """Report readiness only when both required backing services answer."""

    settings = get_settings()
    dependencies = {
        "postgres": "ready" if check_postgres(session) else "unavailable",
        "redis": "ready" if check_redis(settings.redis_url) else "unavailable",
    }
    ready = all(state == "ready" for state in dependencies.values())
    payload: dict[str, Any] = {
        "status": "ready" if ready else "not_ready",
        "dependencies": dependencies,
    }
    if not ready:
        return JSONResponse(status_code=503, content=payload)
    return payload
