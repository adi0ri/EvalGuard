"""SQLAlchemy 2 models for evaluation evidence and isolated ParcelDesk state."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(
        naming_convention={
            "ix": "ix_%(column_0_label)s",
            "uq": "uq_%(table_name)s_%(column_0_name)s",
            "ck": "ck_%(table_name)s_%(constraint_name)s",
            "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
            "pk": "pk_%(table_name)s",
        }
    )


class EvaluationSuite(Base):
    __tablename__ = "evaluation_suites"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_evaluation_suites_name_version"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class EvaluationCase(Base):
    __tablename__ = "evaluation_cases"
    __table_args__ = (
        UniqueConstraint("suite_id", "key", name="uq_evaluation_cases_suite_key"),
        Index("ix_evaluation_cases_suite_id", "suite_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    suite_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evaluation_suites.id", ondelete="RESTRICT"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fixture: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    expected: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_experiments_idempotency_key"),
        Index("ix_experiments_suite_status", "suite_id", "status"),
        CheckConstraint(
            "max_attempts BETWEEN 1 AND 5",
            name="max_attempts_range",
        ),
        CheckConstraint(
            "job_timeout_seconds BETWEEN 1 AND 300",
            name="job_timeout_seconds_range",
        ),
        CheckConstraint(
            "max_tool_steps BETWEEN 1 AND 100",
            name="max_tool_steps_range",
        ),
        CheckConstraint(
            "retry_backoff_seconds BETWEEN 0 AND 300",
            name="retry_backoff_seconds_range",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    suite_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evaluation_suites.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    job_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    max_tool_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    retry_backoff_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaseExecution(Base):
    __tablename__ = "case_executions"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "case_id",
            "trial_number",
            name="uq_case_executions_experiment_case_trial",
        ),
        Index("ix_case_executions_experiment_status", "experiment_id", "status"),
        Index("ix_case_executions_case_id", "case_id"),
        CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_nonnegative",
        ),
        CheckConstraint(
            "dispatch_generation >= 1",
            name="dispatch_generation_positive",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    experiment_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
    )
    case_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evaluation_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    trial_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    dispatch_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_failure_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_failure_retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    final_resolution: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempts"
    __table_args__ = (
        UniqueConstraint(
            "case_execution_id",
            "attempt_number",
            name="uq_execution_attempts_execution_number",
        ),
        Index(
            "ix_execution_attempts_execution_status",
            "case_execution_id",
            "status",
        ),
        Index(
            "ix_execution_attempts_status_lease",
            "status",
            "lease_expires_at",
        ),
        CheckConstraint(
            "attempt_number >= 1",
            name="attempt_number_positive",
        ),
        CheckConstraint(
            "dispatch_generation >= 1",
            name="dispatch_generation_positive",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'retry_scheduled', 'failed', 'interrupted')",
            name="status_allowed",
        ),
        CheckConstraint(
            "step_count >= 0",
            name="step_count_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("case_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    dispatch_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    retryable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    failure_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    lease_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExecutionDispatch(Base):
    __tablename__ = "execution_dispatches"
    __table_args__ = (
        UniqueConstraint(
            "case_execution_id",
            "generation",
            name="uq_execution_dispatches_execution_generation",
        ),
        UniqueConstraint("job_id", name="uq_execution_dispatches_job_id"),
        Index(
            "ix_execution_dispatches_due",
            "status",
            "available_at",
        ),
        CheckConstraint(
            "generation >= 1",
            name="generation_positive",
        ),
        CheckConstraint(
            "status IN ('pending', 'dispatched')",
            name="status_allowed",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="attempt_count_nonnegative",
        ),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("case_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    job_id: Mapped[str] = mapped_column(String(150), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class ExecutionFaultPlan(Base):
    __tablename__ = "execution_fault_plans"
    __table_args__ = (
        CheckConstraint(
            "mode IN ("
            "'transient_before_provider', "
            "'transient_after_tools', "
            "'timeout', "
            "'permanent_before_provider', "
            "'worker_interruption'"
            ")",
            name="mode_allowed",
        ),
        CheckConstraint(
            "fail_attempts >= 1",
            name="fail_attempts_positive",
        ),
    )

    case_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("case_executions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    fail_attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class TraceEvent(Base):
    __tablename__ = "trace_events"
    __table_args__ = (
        UniqueConstraint(
            "case_execution_id",
            "sequence_number",
            name="uq_trace_events_execution_sequence",
        ),
        Index("ix_trace_events_execution_type", "case_execution_id", "event_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    case_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("case_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class ToolInvocation(Base):
    __tablename__ = "tool_invocations"
    __table_args__ = (
        UniqueConstraint(
            "case_execution_id",
            "tool_call_id",
            name="uq_tool_invocations_execution_call",
        ),
        Index("ix_tool_invocations_execution_tool", "case_execution_id", "tool_name"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("case_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_call_id: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class GraderResult(Base):
    __tablename__ = "grader_results"
    __table_args__ = (
        UniqueConstraint(
            "case_execution_id",
            "grader_name",
            "criterion",
            name="uq_grader_results_execution_grader_criterion",
        ),
        Index("ix_grader_results_execution_passed", "case_execution_id", "passed"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("case_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    grader_name: Mapped[str] = mapped_column(String(100), nullable=False)
    criterion: Mapped[str] = mapped_column(String(100), nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint(
            "case_execution_id", "external_id", name="uq_customers_execution_external"
        ),
        UniqueConstraint("id", "case_execution_id", name="uq_customers_id_execution"),
        Index("ix_customers_case_execution_id", "case_execution_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("case_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        ForeignKeyConstraint(
            ["customer_id", "case_execution_id"],
            ["customers.id", "customers.case_execution_id"],
            name="fk_orders_customer_execution",
            ondelete="CASCADE",
        ),
        UniqueConstraint("case_execution_id", "external_id", name="uq_orders_execution_external"),
        UniqueConstraint("id", "case_execution_id", name="uq_orders_id_execution"),
        UniqueConstraint(
            "id",
            "customer_id",
            "case_execution_id",
            name="uq_orders_id_customer_execution",
        ),
        Index("ix_orders_case_execution_id", "case_execution_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("case_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    external_id: Mapped[str] = mapped_column(String(64), nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    shipping_address_line1: Mapped[str | None] = mapped_column(String(200), nullable=True)
    shipping_address_line2: Mapped[str | None] = mapped_column(String(200), nullable=True)
    shipping_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    shipping_region: Mapped[str | None] = mapped_column(String(100), nullable=True)
    shipping_postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shipping_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        ForeignKeyConstraint(
            ["order_id", "case_execution_id"],
            ["orders.id", "orders.case_execution_id"],
            name="fk_refunds_order_execution",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["case_execution_id", "tool_call_id"],
            ["tool_invocations.case_execution_id", "tool_invocations.tool_call_id"],
            name="fk_refunds_tool_invocation",
            ondelete="CASCADE",
        ),
        UniqueConstraint("case_execution_id", "order_id", name="uq_refunds_execution_order"),
        UniqueConstraint(
            "case_execution_id", "tool_call_id", name="uq_refunds_execution_tool_call"
        ),
        Index("ix_refunds_case_execution_id", "case_execution_id"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("case_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["customer_id", "case_execution_id"],
            ["customers.id", "customers.case_execution_id"],
            name="fk_support_tickets_customer_execution",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["order_id", "case_execution_id"],
            ["orders.id", "orders.case_execution_id"],
            name="fk_support_tickets_order_execution",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["order_id", "customer_id", "case_execution_id"],
            ["orders.id", "orders.customer_id", "orders.case_execution_id"],
            name="fk_support_tickets_order_customer_execution",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["case_execution_id", "tool_call_id"],
            ["tool_invocations.case_execution_id", "tool_invocations.tool_call_id"],
            name="fk_support_tickets_tool_invocation",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "case_execution_id",
            "order_id",
            "reason_code",
            name="uq_support_tickets_execution_order_reason",
        ),
        UniqueConstraint(
            "case_execution_id",
            "tool_call_id",
            name="uq_support_tickets_execution_tool_call",
        ),
        Index("ix_support_tickets_execution_status", "case_execution_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), primary_key=True, default=uuid4)
    case_execution_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("case_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    customer_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    order_id: Mapped[UUID] = mapped_column(PostgreSQLUUID(as_uuid=True), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(50), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    tool_call_id: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
