"""Safety checks for destructive test-database preparation."""

import pytest

from evalguard.cli import prepare_test_database


def test_prepare_test_database_rejects_non_postgresql_urls() -> None:
    with pytest.raises(ValueError, match="must use PostgreSQL"):
        prepare_test_database("sqlite:///evalguard_test")


def test_prepare_test_database_rejects_development_database_names() -> None:
    with pytest.raises(ValueError, match="does not end in _test"):
        prepare_test_database("postgresql+psycopg://evalguard:evalguard@localhost:5432/evalguard")
