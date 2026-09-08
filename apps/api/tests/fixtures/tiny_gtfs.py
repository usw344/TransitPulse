"""A complete two-stop GTFS feed, built as a ZIP for importer tests."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Mapping


TABLES = {
    "agency.txt": """agency_id,agency_name,agency_url,agency_timezone\nETS,Example Transit,https://example.test,America/Edmonton\n""",
    "routes.txt": """route_id,agency_id,route_short_name,route_long_name,route_type,route_color,route_text_color\n100,ETS,100,Test Route,3,005EB8,FFFFFF\n""",
    "stops.txt": """stop_id,stop_code,stop_name,stop_lat,stop_lon,wheelchair_boarding\nSTOP_A,1001,Alpha Stop,53.5461,-113.4938,1\nSTOP_B,1002,Beta Stop,53.5500,-113.4900,2\n""",
    "calendar.txt": """service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\nWEEKDAY,1,1,1,1,1,0,0,20260901,20261130\n""",
    "calendar_dates.txt": """service_id,date,exception_type\nWEEKDAY,20260907,2\n""",
    "shapes.txt": """shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\nSHAPE_100,53.5461,-113.4938,1\nSHAPE_100,53.5500,-113.4900,2\n""",
    "trips.txt": """route_id,service_id,trip_id,trip_headsign,direction_id,shape_id\n100,WEEKDAY,TRIP_100,Downtown,0,SHAPE_100\n""",
    "stop_times.txt": """trip_id,arrival_time,departure_time,stop_id,stop_sequence\nTRIP_100,25:00:00,25:00:30,STOP_A,1\nTRIP_100,25:05:00,25:05:30,STOP_B,2\n""",
}


def make_gtfs_archive(
    *,
    replacements: Mapping[str, str] | None = None,
    omit: Iterable[str] = (),
) -> bytes:
    """Return a ZIP fixture, optionally malformed in a focused way."""

    contents = {**TABLES, **(replacements or {})}
    omitted = set(omit)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
        for name, contents in contents.items():
            if name not in omitted:
                entry = zipfile.ZipInfo(name, date_time=(2026, 9, 8, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                zipped.writestr(entry, contents)
    return buffer.getvalue()
