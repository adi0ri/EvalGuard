"""Contract tests for the immutable Phase 3 scenario inventory."""

from collections import Counter

import pytest
from pydantic import ValidationError

from evalguard.evaluation.fixture_models import (
    ScenarioCaseExpected,
    ScenarioExpectation,
    ScenarioFixture,
)
from evalguard.evaluation.scenarios import PHASE3_CATEGORY_COUNTS, PHASE3_SCENARIOS
from evalguard.evaluation.suite import (
    CASE_DEFINITION_HASH,
    PHASE3_CASE_DEFINITION_HASHES,
    PHASE3_SUITE_DEFINITION_HASH,
    PHASE3_SUITE_NAME,
    PHASE3_SUITE_VERSION,
    SUITE_DEFINITION_HASH,
    definition_hash,
)

EXPECTED_KEYS = {
    "address-change-first-blocked-status",
    "address-change-last-allowed-status",
    "address-change-missing-postal-code",
    "address-change-pending",
    "address-change-pending-remove-unit",
    "authorization-address-cross-customer",
    "authorization-escalation-cross-customer",
    "authorization-refund-cross-customer",
    "duplicate-address-semantic-retry",
    "duplicate-escalation-semantic-retry",
    "duplicate-refund-stable-call-replay",
    "eligible-refund",
    "manual-review-request",
    "order-status-owned",
    "privacy-order-status-cross-customer",
    "recover-after-lookup-not-found",
    "refund-at-escalation-threshold",
    "refund-below-escalation-threshold",
    "refund-cancelled-order",
    "refund-expired-window",
    "refund-missing-parts-delivered",
    "refund-on-window-last-day",
    "refund-pending-order",
    "refund-shipped-order",
    "refund-wrong-item-delivered",
}
EXPECTED_CATEGORY_COUNTS = {
    "normal_success": 7,
    "ineligible_request": 5,
    "policy_boundary": 5,
    "authorization_privacy": 4,
    "duplicate_timeout_recovery": 4,
}


def test_phase3_inventory_has_exact_stable_keys_and_distribution() -> None:
    keys = [scenario.key for scenario in PHASE3_SCENARIOS]

    assert len(keys) == 25
    assert len(set(keys)) == 25
    assert set(keys) == EXPECTED_KEYS
    assert PHASE3_CATEGORY_COUNTS == EXPECTED_CATEGORY_COUNTS
    assert Counter(scenario.expected.category for scenario in PHASE3_SCENARIOS) == Counter(
        EXPECTED_CATEGORY_COUNTS
    )


def test_phase3_hashes_cover_complete_case_contracts_and_membership_keys() -> None:
    assert PHASE3_SUITE_NAME == "parceldesk-refunds"
    assert PHASE3_SUITE_VERSION == "2.0.0"
    assert PHASE3_SUITE_DEFINITION_HASH == (
        "28289cfdcd98be852b00e451b2f52ae8a9af65a454a6642bb96acb4991a19782"
    )
    assert set(PHASE3_CASE_DEFINITION_HASHES) == EXPECTED_KEYS

    for scenario in PHASE3_SCENARIOS:
        assert PHASE3_CASE_DEFINITION_HASHES[scenario.key] == definition_hash(
            scenario.model_dump(mode="json")
        )

    assert (
        definition_hash(
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
        == PHASE3_SUITE_DEFINITION_HASH
    )


def test_legacy_v1_definition_hashes_remain_exactly_unchanged() -> None:
    assert CASE_DEFINITION_HASH == (
        "aaedf653770166eb7e967352b9242509a94dd9ba3e4ee833576617c403ec6abd"
    )
    assert SUITE_DEFINITION_HASH == (
        "c291543f19b185f16f89311aa7669d0a6227d8fc7080cf3eefa4aa1c58a850dc"
    )


def test_every_phase3_payload_round_trips_through_closed_typed_contracts() -> None:
    for scenario in PHASE3_SCENARIOS:
        fixture_payload = scenario.fixture.model_dump(mode="json")
        expected_payload = scenario.expected.model_dump(mode="json")

        assert ScenarioFixture.model_validate(fixture_payload) == scenario.fixture
        assert ScenarioCaseExpected.model_validate(expected_payload) == scenario.expected
        assert len(scenario.expected.policy.tool_call_bounds) == 4


def test_authorization_cases_have_distinct_requester_and_target_owner() -> None:
    authorization_cases = [
        scenario
        for scenario in PHASE3_SCENARIOS
        if scenario.expected.category == "authorization_privacy"
    ]

    assert len(authorization_cases) == 4
    for scenario in authorization_cases:
        target = next(
            order
            for order in scenario.fixture.orders
            if order.external_id == scenario.expected.policy.order_id
        )
        assert target.customer_external_id != scenario.fixture.requester_customer_id
        assert scenario.expected.policy.resolution.protected_fields


def test_source_scenario_models_and_nested_tool_arguments_are_frozen() -> None:
    scenario = PHASE3_SCENARIOS[0]

    with pytest.raises(ValidationError, match="frozen"):
        scenario.title = "A stale title"  # type: ignore[misc]
    with pytest.raises(ValidationError, match="frozen"):
        scenario.expected.fake_plan.steps[0].arguments.order_id = (  # type: ignore[misc]
            "PD-STALE"
        )


def test_policy_outcome_is_bound_to_resolution_and_expected_state() -> None:
    payload = PHASE3_SCENARIOS[0].expected.policy.model_dump(mode="json")
    payload["outcome"] = "access_denied"

    with pytest.raises(ValidationError, match="outcome and resolution action"):
        ScenarioExpectation.model_validate(payload)


def test_policy_resolution_is_bound_to_the_target_order() -> None:
    payload = PHASE3_SCENARIOS[0].expected.policy.model_dump(mode="json")
    payload["resolution"]["order_id"] = "PD-NOT-THE-TARGET"

    with pytest.raises(ValidationError, match="resolution order must match"):
        ScenarioExpectation.model_validate(payload)


def test_denied_outcome_cannot_expect_an_address_mutation() -> None:
    scenario = next(row for row in PHASE3_SCENARIOS if row.key == "address-change-pending")
    payload = scenario.expected.policy.model_dump(mode="json")
    payload["outcome"] = "access_denied"
    payload["resolution"]["action"] = "access_denied"

    with pytest.raises(ValidationError, match="denied outcome cannot expect state mutations"):
        ScenarioExpectation.model_validate(payload)


def test_expected_state_mutations_are_bound_to_the_policy_order() -> None:
    scenario = next(row for row in PHASE3_SCENARIOS if row.key == "eligible-refund")
    payload = scenario.expected.policy.model_dump(mode="json")
    payload["refunds"][0]["order_id"] = "PD-OTHER-FIXTURE-ORDER"

    with pytest.raises(ValidationError, match="state mutations must target the policy order"):
        ScenarioExpectation.model_validate(payload)
