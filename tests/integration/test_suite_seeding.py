"""PostgreSQL integration coverage for immutable suite loading."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from evalguard.db.models import EvaluationCase, EvaluationSuite
from evalguard.evaluation.suite import (
    CASE_DEFINITION_HASH,
    CASE_KEY,
    SUITE_DEFINITION_HASH,
    SUITE_NAME,
    SUITE_VERSION,
    seed_initial_suite,
)

pytestmark = pytest.mark.integration


def test_seed_initial_suite_is_idempotent_and_preserves_stable_hashes(
    db_session: Session,
) -> None:
    first_suite, first_case = seed_initial_suite(db_session)
    db_session.commit()
    second_suite, second_case = seed_initial_suite(db_session)
    db_session.commit()

    assert second_suite.id == first_suite.id
    assert second_case.id == first_case.id
    assert first_suite.name == SUITE_NAME
    assert first_suite.version == SUITE_VERSION
    assert first_suite.definition_hash == SUITE_DEFINITION_HASH
    assert first_case.key == CASE_KEY
    assert first_case.definition_hash == CASE_DEFINITION_HASH
    assert db_session.scalar(select(func.count()).select_from(EvaluationSuite)) == 1
    assert db_session.scalar(select(func.count()).select_from(EvaluationCase)) == 1


def test_published_suite_rejects_an_appended_case(db_session: Session) -> None:
    suite, _ = seed_initial_suite(db_session)
    db_session.commit()
    assert suite.is_published is True

    db_session.add(
        EvaluationCase(
            suite_id=suite.id,
            key="late-case",
            title="This case must not be appended",
            prompt="Synthetic prompt that should never persist.",
            definition_hash="0" * 64,
            fixture={},
            expected={},
        )
    )
    with pytest.raises(DBAPIError, match="cases in published evaluation suites are immutable"):
        db_session.commit()
    db_session.rollback()

    assert db_session.scalar(select(func.count()).select_from(EvaluationCase)) == 1


def test_case_cannot_be_moved_out_of_a_published_suite(db_session: Session) -> None:
    suite, case = seed_initial_suite(db_session)
    unpublished_suite = EvaluationSuite(
        name="unpublished-test-suite",
        version="1.0.0",
        description="Synthetic unpublished target used only by this test.",
        definition_hash="1" * 64,
        is_published=False,
    )
    db_session.add(unpublished_suite)
    db_session.commit()
    assert suite.is_published is True

    case.suite_id = unpublished_suite.id
    with pytest.raises(DBAPIError, match="cases in published evaluation suites are immutable"):
        db_session.commit()
    db_session.rollback()

    db_session.refresh(case)
    assert case.suite_id == suite.id


def test_case_insert_serializes_with_suite_publication(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as setup_session:
        suite = EvaluationSuite(
            name="concurrent-publication-test",
            version="1.0.0",
            description="Synthetic suite used to verify publication locking.",
            definition_hash="2" * 64,
            is_published=False,
        )
        setup_session.add(suite)
        setup_session.flush()
        suite_id = suite.id

    def insert_late_case() -> None:
        with session_factory.begin() as insert_session:
            insert_session.add(
                EvaluationCase(
                    suite_id=suite_id,
                    key="racing-case",
                    title="A concurrent case that must be rejected",
                    prompt="Synthetic concurrent prompt.",
                    definition_hash="3" * 64,
                    fixture={},
                    expected={},
                )
            )

    with session_factory() as publisher_session:
        locked_suite = publisher_session.scalar(
            select(EvaluationSuite).where(EvaluationSuite.id == suite_id).with_for_update()
        )
        assert locked_suite is not None
        locked_suite.is_published = True

        with ThreadPoolExecutor(max_workers=1) as executor:
            late_insert = executor.submit(insert_late_case)
            publisher_session.commit()
            with pytest.raises(
                DBAPIError, match="cases in published evaluation suites are immutable"
            ):
                late_insert.result(timeout=5)

    with session_factory() as verification_session:
        assert (
            verification_session.scalar(
                select(func.count())
                .select_from(EvaluationCase)
                .where(EvaluationCase.suite_id == suite_id)
            )
            == 0
        )
