"""Allow GTFS-optional coordinates for station-internal locations.

The downgrade is intentionally guarded: GTFS location types 3 and 4 may
legitimately have no geographic point.  An operator must remediate or remove
those rows before restoring the old NOT NULL contract; silently inventing a
point or deleting a stop would corrupt the imported feed.

Revision ID: 0005_optional_stop_coordinates
Revises: 0004_vehicle_observation_history
Create Date: 2026-09-07 20:00:00
"""

from alembic import op
from geoalchemy2 import Geometry
import sqlalchemy as sa


revision = "0005_optional_stop_coordinates"
down_revision = "0004_vehicle_observation_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "stops",
        "location",
        existing_type=Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    missing_locations = bind.scalar(sa.text("SELECT count(*) FROM stops WHERE location IS NULL"))
    if missing_locations:
        raise RuntimeError(
            "Cannot downgrade 0005 while stops.location contains NULL values. "
            "Remediate coordinate-less stop rows or restore a compatible backup before downgrading."
        )
    op.alter_column(
        "stops",
        "location",
        existing_type=Geometry(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )
