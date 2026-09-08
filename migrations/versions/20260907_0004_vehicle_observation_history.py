"""Record immutable realtime vehicle observations for future replay.

Revision ID: 0004_vehicle_observation_history
Revises: 0003_realtime_current_state
"""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry


revision = "0004_vehicle_observation_history"
down_revision = "0003_realtime_current_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vehicle_observations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("observation_key", sa.String(length=64), nullable=False),
        sa.Column("static_feed_id", sa.Uuid(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("vehicle_id", sa.String(length=255), nullable=False),
        sa.Column("trip_gtfs_id", sa.String(length=255), nullable=True),
        sa.Column("route_gtfs_id", sa.String(length=255), nullable=True),
        sa.Column("position", Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True),
        sa.Column("bearing", sa.Float(), nullable=True),
        sa.Column("speed", sa.Float(), nullable=True),
        sa.Column("current_stop_sequence", sa.Integer(), nullable=True),
        sa.Column("current_status", sa.String(length=64), nullable=True),
        sa.Column("schedule_relationship", sa.String(length=64), nullable=True),
        sa.Column("delay_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["static_feed_id"], ["gtfs_feeds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("observation_key", name="uq_vehicle_observations_key"),
    )
    op.create_index("ix_vehicle_observation_vehicle_time", "vehicle_observations", ["vehicle_id", "observed_at"])
    op.create_index("ix_vehicle_observation_route_time", "vehicle_observations", ["route_gtfs_id", "observed_at"])
    op.create_index("ix_vehicle_observation_feed_time", "vehicle_observations", ["static_feed_id", "observed_at"])
    op.create_index("ix_vehicle_observation_position", "vehicle_observations", ["position"], postgresql_using="gist")


def downgrade() -> None:
    op.drop_table("vehicle_observations")
