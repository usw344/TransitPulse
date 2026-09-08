"""Add current GTFS-Realtime ingestion state.

Revision ID: 0003_realtime_current_state
Revises: 0002_gtfs_static_network
"""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry


revision = "0003_realtime_current_state"
down_revision = "0002_gtfs_static_network"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "realtime_feed_statuses",
        sa.Column("feed_kind", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("static_feed_id", sa.Uuid(), nullable=True),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entity_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["static_feed_id"], ["gtfs_feeds.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("feed_kind"),
    )
    op.create_index("ix_realtime_feed_statuses_static_feed_id", "realtime_feed_statuses", ["static_feed_id"])
    op.create_table(
        "realtime_vehicle_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("static_feed_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("vehicle_id", sa.String(length=255), nullable=False),
        sa.Column("trip_gtfs_id", sa.String(length=255), nullable=True),
        sa.Column("route_gtfs_id", sa.String(length=255), nullable=True),
        sa.Column("matched_trip_id", sa.Integer(), nullable=True),
        sa.Column("matched_route_id", sa.Integer(), nullable=True),
        sa.Column("position", Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True),
        sa.Column("bearing", sa.Float(), nullable=True),
        sa.Column("speed", sa.Float(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_stop_sequence", sa.Integer(), nullable=True),
        sa.Column("current_status", sa.String(length=64), nullable=True),
        sa.Column("schedule_relationship", sa.String(length=64), nullable=True),
        sa.Column("delay_seconds", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["static_feed_id"], ["gtfs_feeds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_trip_id"], ["trips.id"]),
        sa.ForeignKeyConstraint(["matched_route_id"], ["routes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("static_feed_id", "vehicle_id", name="uq_rt_vehicle_state_feed_vehicle"),
    )
    op.create_index("ix_realtime_vehicle_states_static_feed_id", "realtime_vehicle_states", ["static_feed_id"])
    op.create_index("ix_realtime_vehicle_states_matched_trip_id", "realtime_vehicle_states", ["matched_trip_id"])
    op.create_index("ix_realtime_vehicle_states_matched_route_id", "realtime_vehicle_states", ["matched_route_id"])
    op.create_index("ix_rt_vehicle_state_route", "realtime_vehicle_states", ["static_feed_id", "route_gtfs_id"])
    op.create_index("ix_rt_vehicle_state_position", "realtime_vehicle_states", ["position"], postgresql_using="gist")
    op.create_table(
        "realtime_trip_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("static_feed_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("trip_gtfs_id", sa.String(length=255), nullable=False),
        sa.Column("route_gtfs_id", sa.String(length=255), nullable=True),
        sa.Column("matched_trip_id", sa.Integer(), nullable=True),
        sa.Column("matched_route_id", sa.Integer(), nullable=True),
        sa.Column("schedule_relationship", sa.String(length=64), nullable=True),
        sa.Column("delay_seconds", sa.Integer(), nullable=True),
        sa.Column("next_stop_id", sa.String(length=255), nullable=True),
        sa.Column("next_stop_sequence", sa.Integer(), nullable=True),
        sa.Column("next_arrival_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["static_feed_id"], ["gtfs_feeds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_trip_id"], ["trips.id"]),
        sa.ForeignKeyConstraint(["matched_route_id"], ["routes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("static_feed_id", "trip_gtfs_id", name="uq_rt_trip_state_feed_trip"),
    )
    op.create_index("ix_realtime_trip_states_static_feed_id", "realtime_trip_states", ["static_feed_id"])
    op.create_index("ix_realtime_trip_states_matched_trip_id", "realtime_trip_states", ["matched_trip_id"])
    op.create_index("ix_realtime_trip_states_matched_route_id", "realtime_trip_states", ["matched_route_id"])
    op.create_index("ix_rt_trip_state_route", "realtime_trip_states", ["static_feed_id", "route_gtfs_id"])
    op.create_table(
        "realtime_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("static_feed_id", sa.Uuid(), nullable=False),
        sa.Column("entity_id", sa.String(length=255), nullable=False),
        sa.Column("header", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("cause", sa.String(length=64), nullable=True),
        sa.Column("effect", sa.String(length=64), nullable=True),
        sa.Column("active_periods", sa.JSON(), nullable=False),
        sa.Column("affected_routes", sa.JSON(), nullable=False),
        sa.Column("affected_stops", sa.JSON(), nullable=False),
        sa.Column("source_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.ForeignKeyConstraint(["static_feed_id"], ["gtfs_feeds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("static_feed_id", "entity_id", name="uq_rt_alert_feed_entity"),
    )
    op.create_index("ix_realtime_alerts_static_feed_id", "realtime_alerts", ["static_feed_id"])
    op.create_index("ix_rt_alert_feed", "realtime_alerts", ["static_feed_id"])


def downgrade() -> None:
    op.drop_table("realtime_alerts")
    op.drop_table("realtime_trip_states")
    op.drop_table("realtime_vehicle_states")
    op.drop_table("realtime_feed_statuses")
