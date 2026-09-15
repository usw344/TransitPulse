"""Retain bounded-rate immutable active-vehicle Trip Update facts.

Revision ID: 0006_trip_observation_history
Revises: 0005_optional_stop_coordinates
Create Date: 2026-09-11 20:45:00
"""

import sqlalchemy as sa
from alembic import op


revision = "0006_trip_observation_history"
down_revision = "0005_optional_stop_coordinates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "realtime_trip_observations",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("observation_key", sa.String(length=64), nullable=False),
        sa.Column("static_feed_id", sa.Uuid(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("vehicle_id", sa.String(length=255), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("trip_gtfs_id", sa.String(length=255), nullable=False),
        sa.Column("route_gtfs_id", sa.String(length=255), nullable=True),
        sa.Column("schedule_relationship", sa.String(length=64), nullable=True),
        sa.Column("delay_seconds", sa.Integer(), nullable=True),
        sa.Column("next_stop_id", sa.String(length=255), nullable=True),
        sa.Column("next_stop_sequence", sa.Integer(), nullable=True),
        sa.Column("next_arrival_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_departure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_arrival_delay_seconds", sa.Integer(), nullable=True),
        sa.Column("next_departure_delay_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["static_feed_id"], ["gtfs_feeds.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("observation_key", name="uq_realtime_trip_observations_key"),
    )
    op.create_index(
        "ix_realtime_trip_observation_vehicle_time",
        "realtime_trip_observations",
        ["vehicle_id", "observed_at"],
    )
    op.create_index(
        "ix_realtime_trip_observation_route_time",
        "realtime_trip_observations",
        ["route_gtfs_id", "observed_at"],
    )
    op.create_index(
        "ix_realtime_trip_observation_feed_time",
        "realtime_trip_observations",
        ["static_feed_id", "observed_at"],
    )
    op.create_index(
        "ix_realtime_trip_observation_trip_stop_time",
        "realtime_trip_observations",
        ["trip_gtfs_id", "next_stop_sequence", "observed_at"],
    )


def downgrade() -> None:
    op.drop_table("realtime_trip_observations")
