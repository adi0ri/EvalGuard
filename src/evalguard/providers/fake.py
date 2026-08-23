"""Deterministic providers used for synthetic infrastructure validation."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from evalguard.domain.tool_schemas import (
    EscalateTicketArgs,
    EscalateTicketResult,
    IssueRefundArgs,
    IssueRefundResult,
    LookupOrderArgs,
    LookupOrderResult,
    ShippingAddress,
    UpdateShippingAddressArgs,
    UpdateShippingAddressResult,
)
from evalguard.domain.tools import ToolExecutionError, ToolRuntime
from evalguard.evaluation.fixture_models import (
    EscalatePlanStep,
    FakePlanStep,
    IssueRefundPlanStep,
    LookupPlanStep,
    UpdateAddressPlanStep,
)

if TYPE_CHECKING:
    from evalguard.evaluation.scenarios import ScenarioDefinition


class FakeBehavior(StrEnum):
    """Stable test-only behavior identifiers for evaluator regression checks."""

    REFERENCE = "reference"
    CLAIM_SUCCESS_WITHOUT_ACTION = "defect.claim_success_without_action"
    WRONG_TOOL_ORDER = "defect.wrong_tool_order"
    WRONG_REFUND_AMOUNT = "defect.wrong_refund_amount"
    WRONG_SHIPPING_ADDRESS = "defect.wrong_shipping_address"
    WRONG_ESCALATION_METADATA = "defect.wrong_escalation_metadata"
    ATTEMPT_FORBIDDEN_ACTION = "defect.attempt_forbidden_action"
    DISCLOSE_PROTECTED_FIELD = "defect.disclose_protected_field"
    OMIT_REQUIRED_ESCALATION = "defect.omit_required_escalation"
    REPEAT_STATE_CHANGE_NEW_CALL_ID = "defect.repeat_state_change_new_call_id"
    MISMATCHED_RESOLUTION_REFERENCE = "defect.mismatched_resolution_reference"


class DeterministicPlanError(RuntimeError):
    """A source-controlled fake plan is internally inconsistent."""


STATE_CHANGING_TOOLS = frozenset({"issue_refund", "update_shipping_address", "escalate_ticket"})


def _different_call_id(call_id: str) -> str:
    suffix = "-defect-repeat"
    return f"{call_id[: 100 - len(suffix)]}{suffix}"


def _different_escalation_reason(reason_code: str) -> str:
    alternatives = (
        "manual_review",
        "missing_information",
        "address_change_blocked",
        "high_value_refund",
    )
    return next(candidate for candidate in alternatives if candidate != reason_code)


class DeterministicFakeProvider:
    """Execute a reproducible typed plan without calling an external model."""

    name = "deterministic-fake"

    def __init__(self, *, behavior_id: str | FakeBehavior = FakeBehavior.REFERENCE) -> None:
        self.behavior = FakeBehavior(behavior_id)

    def run(
        self,
        *,
        prompt: str,
        expected_order_id: str,
        expected_amount_cents: int,
        tools: ToolRuntime,
    ) -> dict[str, Any]:
        """Preserve the Phase 1 eligible-refund provider contract exactly."""

        tools.trace(
            "provider_request",
            {"provider": self.name, "prompt": prompt, "synthetic": True},
        )
        lookup = tools.lookup_order(
            LookupOrderArgs(order_id=expected_order_id),
            tool_call_id="fake-lookup-1",
        )
        refund = tools.issue_refund(
            IssueRefundArgs(
                order_id=lookup.order_id,
                amount_cents=expected_amount_cents,
                reason="Synthetic eligible-refund evaluation fixture",
            ),
            tool_call_id="fake-refund-1",
        )
        resolution: dict[str, Any] = {
            "status": "resolved",
            "action": "refund_issued",
            "order_id": refund.order_id,
            "refund_id": refund.refund_id,
            "amount_cents": refund.amount_cents,
            "currency": refund.currency,
            "message": "The eligible ParcelDesk order was refunded.",
            "synthetic": True,
        }
        tools.trace(
            "provider_response",
            {"provider": self.name, "resolution": resolution, "synthetic": True},
        )
        return resolution

    def run_scenario(
        self,
        *,
        definition: ScenarioDefinition,
        tools: ToolRuntime,
    ) -> dict[str, Any]:
        """Execute a Phase 3 plan, optionally applying one deterministic defect."""

        tools.trace(
            "provider_request",
            {
                "provider": self.name,
                "behavior_id": self.behavior.value,
                "scenario_key": definition.key,
                "prompt": definition.prompt,
                "synthetic": True,
            },
        )
        steps = self._behavior_steps(definition)
        results: dict[str, BaseModel] = {}
        errors: dict[str, str] = {}
        for step in steps:
            try:
                result = self._call_tool(step, tools)
            except ToolExecutionError as exc:
                errors[step.tool_call_id] = str(exc)
                if self.behavior is FakeBehavior.REFERENCE and not step.expect_error:
                    raise
            else:
                results[step.tool_call_id] = result
                if self.behavior is FakeBehavior.REFERENCE and step.expect_error:
                    raise DeterministicPlanError(
                        f"Plan step {step.tool_call_id} was expected to fail"
                    )

        resolution = self._build_resolution(definition, results, errors)
        tools.trace(
            "provider_response",
            {
                "provider": self.name,
                "behavior_id": self.behavior.value,
                "scenario_key": definition.key,
                "resolution": resolution,
                "synthetic": True,
            },
        )
        return resolution

    def _behavior_steps(self, definition: ScenarioDefinition) -> list[FakePlanStep]:
        steps = list(definition.expected.fake_plan.steps)
        behavior = self.behavior
        if behavior is FakeBehavior.REFERENCE:
            return steps
        if behavior is FakeBehavior.CLAIM_SUCCESS_WITHOUT_ACTION:
            return [step for step in steps if step.tool_name not in STATE_CHANGING_TOOLS]
        if behavior is FakeBehavior.WRONG_TOOL_ORDER:
            lookup_index = next(
                (index for index, step in enumerate(steps) if step.tool_name == "lookup_order"),
                None,
            )
            action_index = next(
                (
                    index
                    for index, step in enumerate(steps)
                    if step.tool_name in STATE_CHANGING_TOOLS
                ),
                None,
            )
            if lookup_index is None or action_index is None:
                raise DeterministicPlanError("wrong_tool_order requires lookup and action steps")
            steps[lookup_index], steps[action_index] = steps[action_index], steps[lookup_index]
            return steps
        if behavior is FakeBehavior.WRONG_REFUND_AMOUNT:
            for index, step in enumerate(steps):
                if isinstance(step, IssueRefundPlanStep):
                    wrong_amount = step.arguments.amount_cents + 1
                    steps[index] = step.model_copy(
                        update={
                            "arguments": step.arguments.model_copy(
                                update={"amount_cents": wrong_amount}
                            )
                        }
                    )
                    return steps
            raise DeterministicPlanError("wrong_refund_amount requires an issue_refund step")
        if behavior is FakeBehavior.WRONG_SHIPPING_ADDRESS:
            for index, step in enumerate(steps):
                if isinstance(step, UpdateAddressPlanStep):
                    address = step.arguments.address
                    wrong_line = f"{address.line1[:185]} Defect"
                    wrong_address = address.model_copy(update={"line1": wrong_line})
                    steps[index] = step.model_copy(
                        update={
                            "arguments": step.arguments.model_copy(
                                update={"address": wrong_address}
                            )
                        }
                    )
                    return steps
            raise DeterministicPlanError(
                "wrong_shipping_address requires an update_shipping_address step"
            )
        if behavior is FakeBehavior.WRONG_ESCALATION_METADATA:
            for index, step in enumerate(steps):
                if isinstance(step, EscalatePlanStep):
                    wrong_arguments = step.arguments.model_copy(
                        update={
                            "reason_code": _different_escalation_reason(step.arguments.reason_code),
                            "reason": "Synthetic intentionally incorrect escalation metadata",
                            "priority": "normal" if step.arguments.priority != "normal" else "high",
                        }
                    )
                    steps[index] = step.model_copy(update={"arguments": wrong_arguments})
                    return steps
            raise DeterministicPlanError(
                "wrong_escalation_metadata requires an escalate_ticket step"
            )
        if behavior is FakeBehavior.ATTEMPT_FORBIDDEN_ACTION:
            return [*steps, self._forbidden_step(definition)]
        if behavior is FakeBehavior.OMIT_REQUIRED_ESCALATION:
            filtered: list[FakePlanStep] = [
                step for step in steps if step.tool_name != "escalate_ticket"
            ]
            if len(filtered) == len(steps):
                raise DeterministicPlanError(
                    "omit_required_escalation requires an escalate_ticket step"
                )
            return filtered
        if behavior is FakeBehavior.REPEAT_STATE_CHANGE_NEW_CALL_ID:
            action = next(
                (step for step in reversed(steps) if step.tool_name in STATE_CHANGING_TOOLS),
                None,
            )
            if action is None:
                raise DeterministicPlanError(
                    "repeat_state_change_new_call_id requires a state-changing step"
                )
            return [
                *steps,
                action.model_copy(update={"tool_call_id": _different_call_id(action.tool_call_id)}),
            ]
        if behavior in {
            FakeBehavior.DISCLOSE_PROTECTED_FIELD,
            FakeBehavior.MISMATCHED_RESOLUTION_REFERENCE,
        }:
            return steps
        raise AssertionError(f"Unhandled fake behavior: {behavior}")

    def _forbidden_step(self, definition: ScenarioDefinition) -> FakePlanStep:
        policy = definition.expected.policy
        if not policy.forbidden_tools:
            raise DeterministicPlanError(
                "attempt_forbidden_action requires at least one forbidden tool"
            )
        tool_name = policy.forbidden_tools[0]
        order = next(
            (row for row in definition.fixture.orders if row.external_id == policy.order_id),
            None,
        )
        if order is None:
            raise DeterministicPlanError("The expected target order is absent from the fixture")
        call_id = "defect-forbidden-action-1"
        if tool_name == "lookup_order":
            return LookupPlanStep(
                tool_call_id=call_id,
                arguments=LookupOrderArgs(order_id=order.external_id),
            )
        if tool_name == "issue_refund":
            return IssueRefundPlanStep(
                tool_call_id=call_id,
                arguments=IssueRefundArgs(
                    order_id=order.external_id,
                    amount_cents=order.total_cents,
                    reason="Synthetic intentionally forbidden refund attempt",
                ),
            )
        if tool_name == "update_shipping_address":
            address = order.shipping_address or ShippingAddress(
                line1="404 Synthetic Defect Way",
                city="Port Aurora",
                region="CA",
                postal_code="94109",
                country="US",
            )
            return UpdateAddressPlanStep(
                tool_call_id=call_id,
                arguments=UpdateShippingAddressArgs(
                    order_id=order.external_id,
                    address=address,
                ),
            )
        return EscalatePlanStep(
            tool_call_id=call_id,
            arguments=EscalateTicketArgs(
                order_id=order.external_id,
                reason_code="manual_review",
                reason="Synthetic intentionally forbidden escalation attempt",
                priority="normal",
            ),
        )

    @staticmethod
    def _call_tool(step: FakePlanStep, tools: ToolRuntime) -> BaseModel:
        if isinstance(step, LookupPlanStep):
            return tools.lookup_order(step.arguments, tool_call_id=step.tool_call_id)
        if isinstance(step, IssueRefundPlanStep):
            return tools.issue_refund(step.arguments, tool_call_id=step.tool_call_id)
        if isinstance(step, UpdateAddressPlanStep):
            return tools.update_shipping_address(step.arguments, tool_call_id=step.tool_call_id)
        return tools.escalate_ticket(step.arguments, tool_call_id=step.tool_call_id)

    def _build_resolution(
        self,
        definition: ScenarioDefinition,
        results: dict[str, BaseModel],
        errors: dict[str, str],
    ) -> dict[str, Any]:
        plan = definition.expected.fake_plan.resolution
        resolution: dict[str, Any] = {
            "status": plan.status,
            "action": plan.action,
            "message": plan.message,
            "synthetic": True,
        }
        if plan.order_id is not None:
            resolution["order_id"] = plan.order_id

        source = results.get(plan.source_tool_call_id or "")
        if isinstance(source, LookupOrderResult):
            resolution.update(
                {
                    "order_id": source.order_id,
                    "order_status": source.status,
                    "total_cents": source.total_cents,
                    "currency": source.currency,
                }
            )
        elif isinstance(source, IssueRefundResult):
            resolution.update(
                {
                    "order_id": source.order_id,
                    "refund_id": source.refund_id,
                    "amount_cents": source.amount_cents,
                    "currency": source.currency,
                }
            )
        elif isinstance(source, UpdateShippingAddressResult):
            resolution.update(
                {
                    "order_id": source.order_id,
                    "address": source.address.model_dump(mode="json"),
                }
            )
        elif isinstance(source, EscalateTicketResult):
            resolution.update(
                {
                    "order_id": source.order_id,
                    "ticket_id": source.ticket_id,
                    "reason_code": source.reason_code,
                    "priority": source.priority,
                }
            )
        elif plan.source_tool_call_id in errors:
            resolution["error"] = errors[plan.source_tool_call_id]

        if self.behavior is FakeBehavior.DISCLOSE_PROTECTED_FIELD:
            protected = definition.expected.policy.resolution.protected_fields
            field = protected[0] if protected else "customer_email"
            resolution[field] = "synthetic-private-value@example.test"
        elif self.behavior is FakeBehavior.MISMATCHED_RESOLUTION_REFERENCE:
            if "refund_id" in resolution:
                resolution["refund_id"] = "00000000-0000-0000-0000-000000000000"
            elif "ticket_id" in resolution:
                resolution["ticket_id"] = "00000000-0000-0000-0000-000000000000"
            else:
                resolution["order_id"] = "PD-NOT-PERSISTED"
        return resolution
