"""Redis Queue connection and identity-safe enqueue helpers."""

from uuid import UUID

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.exceptions import DuplicateJobError, NoSuchJobError
from rq.job import Job, JobStatus

from evalguard.core.config import get_settings

DEFAULT_JOB_TIMEOUT_SECONDS = 30
LEGACY_JOB_TIMEOUT_SECONDS = 300
RESULT_TTL_SECONDS = 86_400
FAILURE_TTL_SECONDS = 604_800
EXECUTION_JOB_FUNCTION = "evalguard.workers.jobs.run_case_execution"
RUNNABLE_JOB_STATUSES = frozenset(
    {
        JobStatus.QUEUED,
        JobStatus.STARTED,
        JobStatus.DEFERRED,
        JobStatus.SCHEDULED,
    }
)


class DispatchIdentityError(RuntimeError):
    """An existing RQ job does not represent the expected dispatch intent."""


class UnrunnableDispatchJobError(RuntimeError):
    """An exact existing RQ job can no longer deliver its dispatch intent."""

    def __init__(self, job_id: str, status: JobStatus | None) -> None:
        self.job_id = job_id
        self.status = status
        status_value = status.value if status is not None else "missing"
        super().__init__(f"RQ job {job_id!r} is not runnable (status={status_value})")


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


def job_id_for_execution(case_execution_id: UUID, generation: int) -> str:
    """Return the stable RQ identity for one execution dispatch generation."""

    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise ValueError("Dispatch generation must be a positive integer")
    base_id = f"case-execution-{case_execution_id}"
    return base_id if generation == 1 else f"{base_id}-g{generation}"


def _is_expected_job(
    job: Job,
    *,
    queue: Queue,
    case_execution_id: UUID,
    generation: int,
    job_timeout_seconds: int,
) -> bool:
    """Verify that a duplicate job is the exact intent this process owns."""

    expected_arguments = (str(case_execution_id), generation)
    legacy_generation_one_arguments = (str(case_execution_id),)
    uses_legacy_arguments = (
        generation == 1
        and job_timeout_seconds == LEGACY_JOB_TIMEOUT_SECONDS
        and job.args == legacy_generation_one_arguments
    )
    expected_timeout = LEGACY_JOB_TIMEOUT_SECONDS if uses_legacy_arguments else job_timeout_seconds
    return (
        job.origin == queue.name
        and job.func_name == EXECUTION_JOB_FUNCTION
        and (job.args == expected_arguments or uses_legacy_arguments)
        and job.kwargs == {}
        and job.timeout == expected_timeout
    )


def validate_execution_job(
    job: Job,
    *,
    queue: Queue,
    case_execution_id: UUID,
    generation: int,
    job_timeout_seconds: int,
    expected_job_id: str,
) -> None:
    """Reject an RQ job that is not the exact persisted dispatch intent."""

    stable_job_id = job_id_for_execution(case_execution_id, generation)
    if (
        expected_job_id != stable_job_id
        or job.id != expected_job_id
        or not _is_expected_job(
            job,
            queue=queue,
            case_execution_id=case_execution_id,
            generation=generation,
            job_timeout_seconds=job_timeout_seconds,
        )
    ):
        raise DispatchIdentityError(
            f"RQ job {job.id!r} does not match persisted dispatch {expected_job_id!r}"
        )


def existing_execution_job(
    case_execution_id: UUID,
    generation: int,
    job_timeout_seconds: int,
    connection: Redis,
) -> Job | None:
    """Fetch and identity-check an existing RQ job for one dispatch generation."""

    job_id = job_id_for_execution(case_execution_id, generation)
    queue = get_queue(connection)
    try:
        existing = Job.fetch(job_id, connection=queue.connection)
    except NoSuchJobError:
        return None
    except RedisError:
        raise
    except Exception as fetch_error:
        raise DispatchIdentityError(
            f"RQ job {job_id!r} could not be decoded for identity validation"
        ) from fetch_error
    try:
        validate_execution_job(
            existing,
            queue=queue,
            case_execution_id=case_execution_id,
            generation=generation,
            job_timeout_seconds=job_timeout_seconds,
            expected_job_id=job_id,
        )
    except Exception as validation_error:
        if isinstance(validation_error, DispatchIdentityError):
            raise
        raise DispatchIdentityError(
            f"RQ job {job_id!r} could not be validated as the expected dispatch"
        ) from validation_error
    return existing


def enqueue_execution(
    case_execution_id: UUID,
    generation: int = 1,
    job_timeout_seconds: int | None = None,
    connection: Redis | None = None,
) -> Job:
    """Idempotently enqueue one execution generation after validating collisions."""

    job_id = job_id_for_execution(case_execution_id, generation)
    timeout_seconds = (
        DEFAULT_JOB_TIMEOUT_SECONDS if job_timeout_seconds is None else job_timeout_seconds
    )
    if (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
    ):
        raise ValueError("Job timeout must be a positive integer")

    queue = get_queue(connection)
    try:
        return queue.enqueue(
            EXECUTION_JOB_FUNCTION,
            str(case_execution_id),
            generation,
            job_id=job_id,
            job_timeout=timeout_seconds,
            result_ttl=RESULT_TTL_SECONDS,
            failure_ttl=FAILURE_TTL_SECONDS,
            meta={
                "case_execution_id": str(case_execution_id),
                "dispatch_generation": generation,
            },
            unique=True,
        )
    except DuplicateJobError as exc:
        try:
            existing = existing_execution_job(
                case_execution_id,
                generation,
                timeout_seconds,
                queue.connection,
            )
        except DispatchIdentityError as identity_error:
            raise identity_error from exc
        if existing is None:
            raise DispatchIdentityError(
                f"Duplicate RQ job {job_id!r} disappeared before identity validation"
            ) from exc
        existing_status = existing.get_status(refresh=True)
        if existing_status not in RUNNABLE_JOB_STATUSES:
            raise UnrunnableDispatchJobError(job_id, existing_status) from exc
        return existing
