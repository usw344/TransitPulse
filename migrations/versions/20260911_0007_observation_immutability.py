"""Enforce append-only protection for immutable realtime histories.

Revision ID: 0007_observation_immutability
Revises: 0006_trip_observation_history
Create Date: 2026-09-11 20:55:00
"""

from alembic import op


revision = "0007_observation_immutability"
down_revision = "0006_trip_observation_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_realtime_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'realtime observation history is append-only: %', TG_TABLE_NAME;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_vehicle_observation_mutation
        BEFORE UPDATE OR DELETE ON vehicle_observations
        FOR EACH ROW EXECUTE FUNCTION reject_realtime_history_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER prevent_trip_observation_mutation
        BEFORE UPDATE OR DELETE ON realtime_trip_observations
        FOR EACH ROW EXECUTE FUNCTION reject_realtime_history_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER prevent_trip_observation_mutation ON realtime_trip_observations")
    op.execute("DROP TRIGGER prevent_vehicle_observation_mutation ON vehicle_observations")
    op.execute("DROP FUNCTION reject_realtime_history_mutation")
