"""RQ worker process entry point."""

from rq import Worker

from evalguard.core.config import get_settings
from evalguard.core.logging import configure_logging
from evalguard.workers.queue import get_queue, get_redis_connection


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    connection = get_redis_connection(for_worker=True)
    worker = Worker([get_queue(connection)], connection=connection)
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
