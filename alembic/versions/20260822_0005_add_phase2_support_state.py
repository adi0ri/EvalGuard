"""Add Phase 2 shipping address and support ticket state.

Revision ID: 20260822_0005
Revises: 20260818_0004
Create Date: 2026-08-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260822_0005"
down_revision: str | None = "20260818_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("shipping_address_line1", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("shipping_address_line2", sa.String(length=200), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("shipping_city", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("shipping_region", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("shipping_postal_code", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("shipping_country", sa.String(length=2), nullable=True),
    )

    op.create_table(
        "support_tickets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_execution_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason_code", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("tool_call_id", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["case_execution_id"],
            ["case_executions.id"],
            name="fk_support_tickets_execution",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id", "case_execution_id"],
            ["customers.id", "customers.case_execution_id"],
            name="fk_support_tickets_customer_execution",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["order_id", "case_execution_id"],
            ["orders.id", "orders.case_execution_id"],
            name="fk_support_tickets_order_execution",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["case_execution_id", "tool_call_id"],
            ["tool_invocations.case_execution_id", "tool_invocations.tool_call_id"],
            name="fk_support_tickets_tool_invocation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_support_tickets"),
        sa.UniqueConstraint(
            "case_execution_id",
            "order_id",
            "reason_code",
            name="uq_support_tickets_execution_order_reason",
        ),
        sa.UniqueConstraint(
            "case_execution_id",
            "tool_call_id",
            name="uq_support_tickets_execution_tool_call",
        ),
    )
    op.create_index(
        "ix_support_tickets_execution_status",
        "support_tickets",
        ["case_execution_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_support_tickets_execution_status", table_name="support_tickets")
    op.drop_table("support_tickets")
    op.drop_column("orders", "shipping_country")
    op.drop_column("orders", "shipping_postal_code")
    op.drop_column("orders", "shipping_region")
    op.drop_column("orders", "shipping_city")
    op.drop_column("orders", "shipping_address_line2")
    op.drop_column("orders", "shipping_address_line1")
