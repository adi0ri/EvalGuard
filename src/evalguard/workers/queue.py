"""Redis Queue connection and enqueue helpers."""

from uuid import UUID

from redis import Redis
from rq import Queue
from rq.job import Job

from evalguard.core.config import get_settings


def get_redis_connection(*, for_worker: bool = False) -> Redis:
    settings = get_settings()
    options: dict[str, float] = {"socket_connect_timeout": 5}
    if not for_worker:
        options["socket_timeout"] = 5
    return Redis.from_url(settings.redis_url, **options)


def get_queue(connection: Redis | None = None) -> Queue:
    settings = get_settings()
    return Queue(
        name=settings.queue_name,
        connection=connection or get_redis_connection(),
        default_timeout=300,
    )


def enqueue_execution(case_execution_id: UUID) -> Job:
    """Enqueue one stable, unique job id for safe correlation and redelivery."""

    return get_queue().enqueue(
        "evalguard.workers.jobs.run_case_execution",
        str(case_execution_id),
        job_id=f"case-execution-{case_execution_id}",
        result_ttl=86_400,
        failure_ttl=604_800,
        unique=True,
    )
