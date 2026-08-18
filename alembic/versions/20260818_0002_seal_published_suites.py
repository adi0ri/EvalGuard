"""Seal published evaluation suite contents.

Revision ID: 20260818_0002
Revises: 20260818_0001
Create Date: 2026-08-18
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260818_0002"
down_revision: str | None = "20260818_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows become unpublished and must be re-verified by the idempotent seed
    # command before the one permitted transition to published.
    op.add_column(
        "evaluation_suites",
        sa.Column("is_published", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.alter_column("evaluation_suites", "is_published", server_default=None)

    op.execute("DROP TRIGGER evaluation_cases_immutable ON evaluation_cases")
    op.execute("DROP TRIGGER evaluation_suites_immutable ON evaluation_suites")
    op.execute("DROP FUNCTION prevent_evaluation_definition_mutation()")

    op.execute(
        """
        CREATE FUNCTION guard_evaluation_suite_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.is_published = FALSE
               AND NEW.is_published = TRUE
               AND OLD.id IS NOT DISTINCT FROM NEW.id
               AND OLD.name IS NOT DISTINCT FROM NEW.name
               AND OLD.version IS NOT DISTINCT FROM NEW.version
               AND OLD.description IS NOT DISTINCT FROM NEW.description
               AND OLD.definition_hash IS NOT DISTINCT FROM NEW.definition_hash
               AND OLD.created_at IS NOT DISTINCT FROM NEW.created_at THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'published evaluation suites are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER evaluation_suites_immutable
        BEFORE UPDATE OR DELETE ON evaluation_suites
        FOR EACH ROW EXECUTE FUNCTION guard_evaluation_suite_mutation()
        """
    )
    op.execute(
        """
        CREATE FUNCTION guard_evaluation_case_mutation()
        RETURNS trigger AS $$
        DECLARE
            suite_published boolean;
            owning_suite_id uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                owning_suite_id := OLD.suite_id;
            ELSE
                owning_suite_id := NEW.suite_id;
            END IF;

            SELECT is_published INTO suite_published
            FROM evaluation_suites
            WHERE id = owning_suite_id;

            IF suite_published = FALSE THEN
                IF TG_OP = 'DELETE' THEN
                    RETURN OLD;
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'cases in published evaluation suites are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER evaluation_cases_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON evaluation_cases
        FOR EACH ROW EXECUTE FUNCTION guard_evaluation_case_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER evaluation_cases_immutable ON evaluation_cases")
    op.execute("DROP TRIGGER evaluation_suites_immutable ON evaluation_suites")
    op.execute("DROP FUNCTION guard_evaluation_case_mutation()")
    op.execute("DROP FUNCTION guard_evaluation_suite_mutation()")
    op.drop_column("evaluation_suites", "is_published")

    op.execute(
        """
        CREATE FUNCTION prevent_evaluation_definition_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'versioned evaluation definitions are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER evaluation_suites_immutable
        BEFORE UPDATE OR DELETE ON evaluation_suites
        FOR EACH ROW EXECUTE FUNCTION prevent_evaluation_definition_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER evaluation_cases_immutable
        BEFORE UPDATE OR DELETE ON evaluation_cases
        FOR EACH ROW EXECUTE FUNCTION prevent_evaluation_definition_mutation()
        """
    )
