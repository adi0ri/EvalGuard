"""Deterministic provider used for tests and synthetic infrastructure validation."""

from typing import Any

from evalguard.domain.tool_schemas import IssueRefundArgs, LookupOrderArgs
from evalguard.domain.tools import ToolRuntime


class DeterministicFakeProvider:
    """Follow one fixed, typed tool path without calling an external model."""

    name = "deterministic-fake"

    def run(
        self,
        *,
        prompt: str,
        expected_order_id: str,
        expected_amount_cents: int,
        tools: ToolRuntime,
    ) -> dict[str, Any]:
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
