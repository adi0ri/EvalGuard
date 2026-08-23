"""Immutable ParcelDesk suite loading for the legacy and Phase 3 inventories."""

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalguard.db.models import EvaluationCase, EvaluationSuite
from evalguard.evaluation.scenarios import PHASE3_SCENARIOS, ScenarioDefinition

SUITE_NAME = "parceldesk-refunds"
SUITE_VERSION = "1.0.0"
CASE_KEY = "eligible-refund"

CASE_PROMPT = (
    "I am customer CUST-1001. Please refund my delivered order PD-1001 because "
    "the item arrived damaged."
)

CASE_FIXTURE: dict[str, Any] = {
    "customer": {
        "external_id": "CUST-1001",
        "name": "Maya Chen",
        "email": "maya.chen@example.test",
    },
    "order": {
        "external_id": "PD-1001",
        "total_cents": 4599,
        "currency": "USD",
        "status": "delivered",
        "placed_at": "2026-01-10T12:00:00+00:00",
        "delivered_at": "2026-01-14T16:30:00+00:00",
    },
}

CASE_EXPECTED: dict[str, Any] = {
    "order_id": "PD-1001",
    "refund_amount_cents": 4599,
    "required_tools": ["lookup_order", "issue_refund"],
    "resolution_status": "resolved",
}


def definition_hash(value: dict[str, Any]) -> str:
    """Hash canonical JSON so a version always identifies the same definition."""

    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


CASE_DEFINITION_HASH = definition_hash(
    {"prompt": CASE_PROMPT, "fixture": CASE_FIXTURE, "expected": CASE_EXPECTED}
)
SUITE_DEFINITION_HASH = definition_hash(
    {
        "name": SUITE_NAME,
        "version": SUITE_VERSION,
        "case_hashes": [CASE_DEFINITION_HASH],
    }
)


class SuiteDefinitionConflictError(RuntimeError):
    """Raised when persisted versioned content differs from source-controlled content."""


def seed_initial_suite(session: Session) -> tuple[EvaluationSuite, EvaluationCase]:
    """Insert the Phase 1 suite once, or verify that the existing version is identical."""

    suite = session.scalar(
        select(EvaluationSuite)
        .where(
            EvaluationSuite.name == SUITE_NAME,
            EvaluationSuite.version == SUITE_VERSION,
        )
        .with_for_update()
    )
    if suite is None:
        suite = EvaluationSuite(
            name=SUITE_NAME,
            version=SUITE_VERSION,
            description="One synthetic eligible-refund evaluation for ParcelDesk.",
            definition_hash=SUITE_DEFINITION_HASH,
            is_published=False,
        )
        session.add(suite)
        session.flush()
    elif suite.definition_hash != SUITE_DEFINITION_HASH:
        raise SuiteDefinitionConflictError(
            f"Stored suite {SUITE_NAME} {SUITE_VERSION} has a different definition hash"
        )

    case = session.scalar(
        select(EvaluationCase).where(
            EvaluationCase.suite_id == suite.id,
            EvaluationCase.key == CASE_KEY,
        )
    )
    if case is None:
        case = EvaluationCase(
            suite_id=suite.id,
            key=CASE_KEY,
            title="Refund an eligible delivered order",
            prompt=CASE_PROMPT,
            definition_hash=CASE_DEFINITION_HASH,
            fixture=CASE_FIXTURE,
            expected=CASE_EXPECTED,
        )
        session.add(case)
        session.flush()
    elif case.definition_hash != CASE_DEFINITION_HASH:
        raise SuiteDefinitionConflictError(
            f"Stored case {CASE_KEY} has a different definition hash"
        )

    persisted_cases = session.scalars(
        select(EvaluationCase)
        .where(EvaluationCase.suite_id == suite.id)
        .order_by(EvaluationCase.key)
    ).all()
    if len(persisted_cases) != 1 or persisted_cases[0].definition_hash != CASE_DEFINITION_HASH:
        raise SuiteDefinitionConflictError(
            f"Stored suite {SUITE_NAME} {SUITE_VERSION} does not have its exact case membership"
        )

    if not suite.is_published:
        suite.is_published = True
        session.flush()

    return suite, case


PHASE3_SUITE_NAME = SUITE_NAME
PHASE3_SUITE_VERSION = "2.0.0"
PHASE3_SUITE_DESCRIPTION = (
    "Twenty-five synthetic ParcelDesk support scenarios across state, policy, "
    "authorization, escalation, duplicate, and recovery behavior."
)


def _phase3_case_payload(scenario: ScenarioDefinition) -> dict[str, Any]:
    """Return every reproducibility-relevant field stored for one Phase 3 case."""

    return scenario.model_dump(mode="json")


PHASE3_CASE_DEFINITION_HASHES: dict[str, str] = {
    scenario.key: definition_hash(_phase3_case_payload(scenario)) for scenario in PHASE3_SCENARIOS
}
PHASE3_SUITE_DEFINITION_HASH = definition_hash(
    {
        "name": PHASE3_SUITE_NAME,
        "version": PHASE3_SUITE_VERSION,
        "cases": [
            {
                "key": key,
                "definition_hash": PHASE3_CASE_DEFINITION_HASHES[key],
            }
            for key in sorted(PHASE3_CASE_DEFINITION_HASHES)
        ],
    }
)


def seed_phase3_suite(
    session: Session,
) -> tuple[EvaluationSuite, tuple[EvaluationCase, ...]]:
    """Insert or exactly verify the immutable 25-case Phase 3 suite."""

    for scenario in PHASE3_SCENARIOS:
        current_hash = definition_hash(_phase3_case_payload(scenario))
        expected_hash = PHASE3_CASE_DEFINITION_HASHES[scenario.key]
        if current_hash != expected_hash:
            raise SuiteDefinitionConflictError(
                f"Source-controlled case {scenario.key} changed after hash calculation"
            )

    suite = session.scalar(
        select(EvaluationSuite)
        .where(
            EvaluationSuite.name == PHASE3_SUITE_NAME,
            EvaluationSuite.version == PHASE3_SUITE_VERSION,
        )
        .with_for_update()
    )
    if suite is None:
        suite = EvaluationSuite(
            name=PHASE3_SUITE_NAME,
            version=PHASE3_SUITE_VERSION,
            description=PHASE3_SUITE_DESCRIPTION,
            definition_hash=PHASE3_SUITE_DEFINITION_HASH,
            is_published=False,
        )
        session.add(suite)
        session.flush()
    elif suite.definition_hash != PHASE3_SUITE_DEFINITION_HASH:
        raise SuiteDefinitionConflictError(
            f"Stored suite {PHASE3_SUITE_NAME} {PHASE3_SUITE_VERSION} "
            "has a different definition hash"
        )

    persisted_by_key = {
        case.key: case
        for case in session.scalars(
            select(EvaluationCase)
            .where(EvaluationCase.suite_id == suite.id)
            .order_by(EvaluationCase.key)
        ).all()
    }
    expected_keys = {scenario.key for scenario in PHASE3_SCENARIOS}
    if set(persisted_by_key).difference(expected_keys):
        raise SuiteDefinitionConflictError(
            f"Stored suite {PHASE3_SUITE_NAME} {PHASE3_SUITE_VERSION} "
            "has unexpected case membership"
        )

    for scenario in PHASE3_SCENARIOS:
        fixture_payload = scenario.fixture.model_dump(mode="json")
        expected_payload = scenario.expected.model_dump(mode="json")
        expected_hash = PHASE3_CASE_DEFINITION_HASHES[scenario.key]
        case = persisted_by_key.get(scenario.key)
        if case is None:
            if suite.is_published:
                raise SuiteDefinitionConflictError(
                    f"Stored published suite {PHASE3_SUITE_NAME} "
                    f"{PHASE3_SUITE_VERSION} is missing case {scenario.key}"
                )
            case = EvaluationCase(
                suite_id=suite.id,
                key=scenario.key,
                title=scenario.title,
                prompt=scenario.prompt,
                definition_hash=expected_hash,
                fixture=fixture_payload,
                expected=expected_payload,
            )
            session.add(case)
            session.flush()
            persisted_by_key[scenario.key] = case
            continue

        if (
            case.definition_hash != expected_hash
            or case.title != scenario.title
            or case.prompt != scenario.prompt
            or case.fixture != fixture_payload
            or case.expected != expected_payload
        ):
            raise SuiteDefinitionConflictError(
                f"Stored case {scenario.key} has a different Phase 3 definition"
            )

    persisted_cases = tuple(persisted_by_key[key] for key in sorted(expected_keys))
    if len(persisted_cases) != len(PHASE3_SCENARIOS):
        raise SuiteDefinitionConflictError(
            f"Stored suite {PHASE3_SUITE_NAME} {PHASE3_SUITE_VERSION} "
            "does not have its exact case membership"
        )

    if not suite.is_published:
        suite.is_published = True
        session.flush()

    return suite, persisted_cases
