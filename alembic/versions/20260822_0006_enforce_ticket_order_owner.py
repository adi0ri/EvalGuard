"""Enforce support-ticket customer ownership through its order.

Revision ID: 20260822_0006
Revises: 20260822_0005
Create Date: 2026-08-22
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260822_0006"
down_revision: str | None = "20260822_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_orders_id_customer_execution",
        "orders",
        ["id", "customer_id", "case_execution_id"],
    )
    op.create_foreign_key(
        "fk_support_tickets_order_customer_execution",
        "support_tickets",
        "orders",
        ["order_id", "customer_id", "case_execution_id"],
        ["id", "customer_id", "case_execution_id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_support_tickets_order_customer_execution",
        "support_tickets",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_orders_id_customer_execution",
        "orders",
        type_="unique",
    )
