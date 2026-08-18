"""Prevent moving cases out of published suites.

Revision ID: 20260818_0003
Revises: 20260818_0002
Create Date: 2026-08-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260818_0003"
down_revision: str | None = "20260818_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_evaluation_case_mutation()
        RETURNS trigger AS $$
        DECLARE
            old_suite_published boolean := FALSE;
            new_suite_published boolean := FALSE;
        BEGIN
            IF TG_OP IN ('UPDATE', 'DELETE') THEN
                SELECT is_published INTO old_suite_published
                FROM evaluation_suites
                WHERE id = OLD.suite_id;
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                SELECT is_published INTO new_suite_published
                FROM evaluation_suites
                WHERE id = NEW.suite_id;
            END IF;

            IF old_suite_published = FALSE AND new_suite_published = FALSE THEN
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


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION guard_evaluation_case_mutation()
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
