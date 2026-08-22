"""PowerShell-friendly maintenance commands for local development and CI."""

import argparse
import json
import re
from collections.abc import Sequence
from pathlib import Path

from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from alembic import command
from evalguard.core.config import get_settings
from evalguard.core.logging import configure_logging
from evalguard.db.session import session_scope
from evalguard.evaluation.suite import seed_initial_suite


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evalguard")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed", help="Load or verify the immutable baseline suite and scenario")
    subparsers.add_parser(
        "prepare-test-db",
        help="Create and migrate the isolated PostgreSQL database used by pytest",
    )
    return parser


def prepare_test_database(database_url: str) -> dict[str, object]:
    """Idempotently create a safe *_test database and migrate it to Alembic head."""

    parsed_url = make_url(database_url)
    database_name = parsed_url.database or ""
    if not parsed_url.drivername.startswith("postgresql"):
        raise ValueError("The test database must use PostgreSQL")
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*_test", database_name) is None:
        raise ValueError("Refusing to prepare a database whose name does not end in _test")

    admin_url = parsed_url.set(database="postgres")
    admin_engine = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 5, "application_name": "evalguard-test-bootstrap"},
    )
    created = False
    try:
        with admin_engine.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": database_name},
            )
            if exists is None:
                # The strict identifier allowlist above makes quoting deterministic and safe.
                connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')
                created = True
    finally:
        admin_engine.dispose()

    alembic_path = Path.cwd() / "alembic.ini"
    if not alembic_path.is_file():
        raise FileNotFoundError("prepare-test-db must run from the repository root")
    alembic_config = AlembicConfig(str(alembic_path))
    alembic_config.attributes["database_url"] = database_url
    command.upgrade(alembic_config, "head")
    return {"database": database_name, "created": created, "migration": "head"}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    if args.command == "seed":
        with session_scope() as session:
            suite, case = seed_initial_suite(session)
            payload = {
                "suite_id": str(suite.id),
                "suite": f"{suite.name}@{suite.version}",
                "case_id": str(case.id),
                "case": case.key,
                "definition_hash": case.definition_hash,
            }
        print(json.dumps(payload, sort_keys=True))
        return 0
    if args.command == "prepare-test-db":
        print(json.dumps(prepare_test_database(settings.test_database_url), sort_keys=True))
        return 0
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
