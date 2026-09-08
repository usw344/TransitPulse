"""Create versioned static GTFS network tables.

Revision ID: 0002_gtfs_static_network
Revises: 0001_enable_postgis
Create Date: 2026-09-07 01:00:00
"""

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry


revision = "0002_gtfs_static_network"
down_revision = "0001_enable_postgis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gtfs_feeds",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("downloaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("feed_start_date", sa.Date(), nullable=True),
        sa.Column("feed_end_date", sa.Date(), nullable=True),
        sa.Column("import_status", sa.String(length=32), nullable=False),
        sa.Column("import_counts", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("checksum_sha256"),
    )
    op.create_table(
        "agencies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("gtfs_agency_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("lang", sa.String(length=32), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("fare_url", sa.Text(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["feed_id"], ["gtfs_feeds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "gtfs_agency_id", name="uq_agencies_feed_gtfs_id"),
    )
    op.create_index("ix_agencies_feed_id", "agencies", ["feed_id"], unique=False)
    op.create_table(
        "service_calendars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("gtfs_service_id", sa.String(length=255), nullable=False),
        sa.Column("monday", sa.Boolean(), nullable=False),
        sa.Column("tuesday", sa.Boolean(), nullable=False),
        sa.Column("wednesday", sa.Boolean(), nullable=False),
        sa.Column("thursday", sa.Boolean(), nullable=False),
        sa.Column("friday", sa.Boolean(), nullable=False),
        sa.Column("saturday", sa.Boolean(), nullable=False),
        sa.Column("sunday", sa.Boolean(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["feed_id"], ["gtfs_feeds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "gtfs_service_id", name="uq_calendars_feed_gtfs_id"),
    )
    op.create_index("ix_service_calendars_feed_id", "service_calendars", ["feed_id"], unique=False)
    op.create_table(
        "stops",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("gtfs_stop_id", sa.String(length=255), nullable=False),
        sa.Column("code", sa.String(length=128), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("location_type", sa.SmallInteger(), nullable=False),
        sa.Column("parent_station_gtfs_id", sa.String(length=255), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=True),
        sa.Column("wheelchair_boarding", sa.SmallInteger(), nullable=True),
        sa.Column(
            "location",
            Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["feed_id"], ["gtfs_feeds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "gtfs_stop_id", name="uq_stops_feed_gtfs_id"),
    )
    op.create_index("ix_stops_feed_id", "stops", ["feed_id"], unique=False)
    op.create_index("ix_stops_location", "stops", ["location"], unique=False, postgresql_using="gist")
    op.create_table(
        "shapes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("gtfs_shape_id", sa.String(length=255), nullable=False),
        sa.Column(
            "geometry",
            Geometry(geometry_type="LINESTRING", srid=4326, spatial_index=False),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["feed_id"], ["gtfs_feeds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "gtfs_shape_id", name="uq_shapes_feed_gtfs_id"),
    )
    op.create_index("ix_shapes_feed_id", "shapes", ["feed_id"], unique=False)
    op.create_index("ix_shapes_geometry", "shapes", ["geometry"], unique=False, postgresql_using="gist")
    op.create_table(
        "routes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("agency_id", sa.Integer(), nullable=False),
        sa.Column("gtfs_route_id", sa.String(length=255), nullable=False),
        sa.Column("short_name", sa.String(length=128), nullable=True),
        sa.Column("long_name", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("route_type", sa.SmallInteger(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=6), nullable=True),
        sa.Column("text_color", sa.String(length=6), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["agency_id"], ["agencies.id"]),
        sa.ForeignKeyConstraint(["feed_id"], ["gtfs_feeds.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "gtfs_route_id", name="uq_routes_feed_gtfs_id"),
    )
    op.create_index("ix_routes_agency_id", "routes", ["agency_id"], unique=False)
    op.create_index("ix_routes_feed_id", "routes", ["feed_id"], unique=False)
    op.create_table(
        "calendar_dates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("exception_type", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(["service_id"], ["service_calendars.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("service_id", "date", name="uq_calendar_dates_service_date"),
    )
    op.create_index("ix_calendar_dates_service_id", "calendar_dates", ["service_id"], unique=False)
    op.create_table(
        "trips",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("feed_id", sa.Uuid(), nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("service_id", sa.Integer(), nullable=False),
        sa.Column("shape_id", sa.Integer(), nullable=True),
        sa.Column("gtfs_trip_id", sa.String(length=255), nullable=False),
        sa.Column("headsign", sa.String(length=255), nullable=True),
        sa.Column("short_name", sa.String(length=255), nullable=True),
        sa.Column("direction_id", sa.SmallInteger(), nullable=True),
        sa.Column("block_id", sa.String(length=255), nullable=True),
        sa.Column("wheelchair_accessible", sa.SmallInteger(), nullable=True),
        sa.Column("bikes_allowed", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(["feed_id"], ["gtfs_feeds.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["route_id"], ["routes.id"]),
        sa.ForeignKeyConstraint(["service_id"], ["service_calendars.id"]),
        sa.ForeignKeyConstraint(["shape_id"], ["shapes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("feed_id", "gtfs_trip_id", name="uq_trips_feed_gtfs_id"),
    )
    op.create_index("ix_trips_feed_id", "trips", ["feed_id"], unique=False)
    op.create_index("ix_trips_route_id", "trips", ["route_id"], unique=False)
    op.create_index("ix_trips_service_id", "trips", ["service_id"], unique=False)
    op.create_index("ix_trips_shape_id", "trips", ["shape_id"], unique=False)
    op.create_table(
        "stop_times",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("trip_id", sa.Integer(), nullable=False),
        sa.Column("stop_id", sa.Integer(), nullable=False),
        sa.Column("arrival_time", sa.String(length=16), nullable=True),
        sa.Column("departure_time", sa.String(length=16), nullable=True),
        sa.Column("arrival_seconds", sa.Integer(), nullable=True),
        sa.Column("departure_seconds", sa.Integer(), nullable=True),
        sa.Column("stop_sequence", sa.Integer(), nullable=False),
        sa.Column("headsign", sa.String(length=255), nullable=True),
        sa.Column("pickup_type", sa.SmallInteger(), nullable=True),
        sa.Column("drop_off_type", sa.SmallInteger(), nullable=True),
        sa.Column("timepoint", sa.SmallInteger(), nullable=True),
        sa.ForeignKeyConstraint(["stop_id"], ["stops.id"]),
        sa.ForeignKeyConstraint(["trip_id"], ["trips.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trip_id", "stop_sequence", name="uq_stop_times_trip_sequence"),
    )
    op.create_index("ix_stop_times_stop_id", "stop_times", ["stop_id"], unique=False)
    op.create_index("ix_stop_times_trip_id", "stop_times", ["trip_id"], unique=False)


def downgrade() -> None:
    op.drop_table("stop_times")
    op.drop_table("trips")
    op.drop_table("calendar_dates")
    op.drop_table("routes")
    op.drop_table("shapes")
    op.drop_table("stops")
    op.drop_table("service_calendars")
    op.drop_table("agencies")
    op.drop_table("gtfs_feeds")
