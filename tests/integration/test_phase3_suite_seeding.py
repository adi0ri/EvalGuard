"""PostgreSQL coverage for the immutable 25-case Phase 3 suite."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evalguard.db.models import EvaluationCase, EvaluationSuite
from evalguard.evaluation.scenarios import PHASE3_SCENARIOS
from evalguard.evaluation.suite import (
    CASE_DEFINITION_HASH,
    PHASE3_CASE_DEFINITION_HASHES,
    PHASE3_SUITE_DEFINITION_HASH,
    PHASE3_SUITE_NAME,
    PHASE3_SUITE_VERSION,
    SUITE_DEFINITION_HASH,
    seed_initial_suite,
    seed_phase3_suite,
)

pytestmark = pytest.mark.integration


def test_phase3_seed_is_idempotent_and_preserves_legacy_suite(
    db_session: Session,
) -> None:
    legacy_suite, legacy_case = seed_initial_suite(db_session)
    first_suite, first_cases = seed_phase3_suite(db_session)
    db_session.commit()

    second_suite, second_cases = seed_phase3_suite(db_session)
    db_session.commit()

    assert second_suite.id == first_suite.id
    assert tuple(case.id for case in second_cases) == tuple(case.id for case in first_cases)
    assert first_suite.name == PHASE3_SUITE_NAME
    assert first_suite.version == PHASE3_SUITE_VERSION
    assert first_suite.definition_hash == PHASE3_SUITE_DEFINITION_HASH
    assert first_suite.is_published is True
    assert len(first_cases) == 25
    assert {case.key for case in first_cases} == {scenario.key for scenario in PHASE3_SCENARIOS}
    assert {case.key: case.definition_hash for case in first_cases} == PHASE3_CASE_DEFINITION_HASHES

    assert legacy_suite.version == "1.0.0"
    assert legacy_suite.definition_hash == SUITE_DEFINITION_HASH
    assert legacy_case.definition_hash == CASE_DEFINITION_HASH
    assert db_session.scalar(select(func.count()).select_from(EvaluationSuite)) == 2
    assert db_session.scalar(select(func.count()).select_from(EvaluationCase)) == 26


def test_phase3_persisted_payloads_match_source_definitions(db_session: Session) -> None:
    suite, cases = seed_phase3_suite(db_session)
    db_session.commit()
    source_by_key = {scenario.key: scenario for scenario in PHASE3_SCENARIOS}

    assert suite.is_published is True
    for case in cases:
        scenario = source_by_key[case.key]
        assert case.title == scenario.title
        assert case.prompt == scenario.prompt
        assert case.fixture == scenario.fixture.model_dump(mode="json")
        assert case.expected == scenario.expected.model_dump(mode="json")


def test_phase3_seed_rejects_a_conflicting_existing_version(db_session: Session) -> None:
    conflicting = EvaluationSuite(
        name=PHASE3_SUITE_NAME,
        version=PHASE3_SUITE_VERSION,
        description="Synthetic conflict used only by this test.",
        definition_hash="0" * 64,
        is_published=False,
    )
    db_session.add(conflicting)
    db_session.commit()

    with pytest.raises(RuntimeError, match="different definition hash"):
        seed_phase3_suite(db_session)
