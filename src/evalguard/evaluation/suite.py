"""The one original, versioned ParcelDesk suite implemented in Phase 1."""

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from evalguard.db.models import EvaluationCase, EvaluationSuite

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
