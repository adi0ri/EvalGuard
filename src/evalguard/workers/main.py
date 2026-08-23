"""RQ worker process with PostgreSQL-authoritative recovery maintenance."""

import logging
from typing import Any
from uuid import UUID

from rq import Worker
from rq.job import Job

from evalguard.core.config import get_settings
from evalguard.core.logging import configure_logging
from evalguard.db.session import get_engine
from evalguard.workers.dispatch import reconcile_pending_dispatches
from evalguard.workers.jobs import (
    recover_interrupted_execution,
    recover_stale_executions,
    validate_delivery_identity,
)
from evalguard.workers.queue import get_queue, get_redis_connection

logger = logging.getLogger(__name__)
MAINTENANCE_INTERVAL_SECONDS = 2
WORKER_TTL_SECONDS = 20


def run_recovery_cycle() -> dict[str, object]:
    """Recover abandoned attempts, then publish any due durable dispatches."""

    recovery = recover_stale_executions()
    dispatch = reconcile_pending_dispatches()
    return {"recovery": recovery.as_dict(), "dispatch": dispatch.as_dict()}


def _work_horse_killed_handler(
    job: Job,
    _retpid: int,
    _ret_val: int,
    _rusage: Any,
) -> None:
    """Fast-path recovery when RQ observes an abnormal child-process exit."""

    try:
        execution_id = UUID(str(job.args[0]))
        dispatch_generation = int(job.args[1]) if len(job.args) > 1 else 1
        validate_delivery_identity(execution_id, dispatch_generation, job)
        outcome = recover_interrupted_execution(
            execution_id,
            dispatch_generation,
            job.id,
        )
        logger.warning(
            "Recovered execution after RQ work-horse exit",
            extra={
                "event": "work_horse_execution_recovered",
                "execution_id": str(execution_id),
                "outcome": outcome,
            },
        )
    except Exception:
        logger.exception(
            "Failed to recover execution after RQ work-horse exit",
            extra={"event": "work_horse_recovery_failed", "job_id": job.id},
        )


class RecoveryWorker(Worker):
    """Run EvalGuard recovery alongside RQ's own registry maintenance."""

    def run_maintenance_tasks(self) -> None:
        super().run_maintenance_tasks()  # type: ignore[no-untyped-call]
        try:
            summary = run_recovery_cycle()
            logger.debug(
                "Worker recovery maintenance completed",
                extra={"event": "worker_recovery_cycle", "summary": summary},
            )
        except Exception:
            logger.exception(
                "Worker recovery maintenance deferred",
                extra={"event": "worker_recovery_cycle_failed"},
            )

    def prepare_job_execution(
        self,
        job: Job,
        remove_from_intermediate_queue: bool = False,
    ) -> None:
        """Replace the parent's inherited SQLAlchemy pool inside every forked horse."""

        get_engine().dispose(close=False)
        super().prepare_job_execution(job, remove_from_intermediate_queue)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    connection = get_redis_connection(for_worker=True)
    try:
        run_recovery_cycle()
    except Exception:
        logger.exception(
            "Initial worker recovery cycle deferred",
            extra={"event": "worker_initial_recovery_failed"},
        )
    worker = RecoveryWorker(
        [get_queue(connection)],
        connection=connection,
        maintenance_interval=MAINTENANCE_INTERVAL_SECONDS,
        worker_ttl=WORKER_TTL_SECONDS,
        work_horse_killed_handler=_work_horse_killed_handler,
    )
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
