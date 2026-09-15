from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest
from sqlalchemy import text

from transitpulse_ml.block_attribution import (
    AMBIGUOUS,
    ATTRIBUTED,
    CROSS_FEED,
    MISSING_BLOCK,
    UNMATCHED,
    attribution_records,
    records_sha256,
    summarize_attributions,
    write_new_report,
)
from transitpulse_api.database import get_engine
from transitpulse_api.models import (
    Agency,
    GtfsFeed,
    RealtimeTripObservation,
    Route,
    ServiceCalendar,
    Trip,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from audit_static_trip_block_attribution import read_attribution_rows  # noqa: E402


def source_row(
    *,
    source_id: int,
    exact_matches: int = 1,
    other_feed_matches: int = 0,
    block_id: str | None = "block-a",
    observed_route: str | None = "004",
    static_route: str | None = "004",
) -> dict[str, object]:
    return {
        "source_observation_id": source_id,
        "observation_key": f"observation-{source_id}",
        "static_feed_id": "feed-a",
        "observed_at": f"2026-09-11T08:00:0{source_id}-06:00",
        "recorded_at": f"2026-09-11T08:00:1{source_id}-06:00",
        "source_timestamp": None,
        "vehicle_id": "vehicle-a",
        "trip_gtfs_id": f"trip-{source_id}",
        "observed_route_gtfs_id": observed_route,
        "static_route_gtfs_id": static_route,
        "static_block_id": block_id,
        "exact_static_trip_match_count": exact_matches,
        "other_feed_trip_match_count": other_feed_matches,
    }


def test_exact_feed_trip_attribution_counts_missing_cross_feed_and_ambiguity() -> None:
    records = attribution_records(
        [
            source_row(source_id=4, exact_matches=2),
            source_row(source_id=3, exact_matches=0, other_feed_matches=1),
            source_row(source_id=2, block_id=None),
            source_row(source_id=1),
            source_row(source_id=5, exact_matches=0, other_feed_matches=0),
        ]
    )

    assert [record["attribution_status"] for record in records] == [
        ATTRIBUTED,
        MISSING_BLOCK,
        CROSS_FEED,
        AMBIGUOUS,
        UNMATCHED,
    ]
    assert records[0]["static_block_id"] == "block-a"
    assert records[0]["recorded_at"] == "2026-09-11T08:00:11-06:00"
    assert records[1]["static_block_id"] is None
    assert records[2]["static_block_id"] is None
    assert records[3]["static_block_id"] is None
    summary = summarize_attributions(records)
    assert summary["unattributed_row_count"] == 4
    assert summary["per_static_route_observed_calendar_date"][0]["observed_calendar_date"] == "2026-09-11"


def test_block_attribution_output_checksum_is_deterministic_and_route_mismatch_is_visible() -> None:
    unordered = [
        source_row(source_id=2, observed_route="999"),
        source_row(source_id=1),
    ]
    first = attribution_records(unordered)
    second = attribution_records(reversed(unordered))

    assert first == second
    assert records_sha256(first) == records_sha256(second)
    assert first[1]["route_id_relation"] == "mismatches_static_trip"


def test_new_report_refuses_existing_output(tmp_path: Path) -> None:
    output = tmp_path / "audit.json"
    write_new_report(output, {"schema_version": 1})
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_new_report(output, {"schema_version": 1})


def _source_snapshot() -> tuple[int, str]:
    with get_engine().connect() as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                text(
                    """
                    SELECT id, observation_key, static_feed_id::text, vehicle_id,
                           trip_gtfs_id, observed_at, next_arrival_at, next_departure_at
                    FROM realtime_trip_observations
                    ORDER BY id
                    """
                )
            ).mappings()
        ]
    encoded = json.dumps(rows, sort_keys=True, default=str, separators=(",", ":")).encode()
    return len(rows), sha256(encoded).hexdigest()


def test_read_only_block_attribution_query_preserves_disposable_source_history(db_session) -> None:
    """The live recorder database is never eligible for this integration test."""

    observed_at = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
    feed = GtfsFeed(
        id=uuid4(),
        provider="test-provider",
        source_url="https://example.invalid/gtfs.zip",
        checksum_sha256="a" * 64,
        downloaded_at=observed_at,
    )
    db_session.add(feed)
    db_session.flush()
    agency = Agency(
        feed_id=feed.id,
        gtfs_agency_id="agency",
        name="Test Transit",
        url="https://example.invalid",
        timezone="America/Regina",
    )
    calendar = ServiceCalendar(feed_id=feed.id, gtfs_service_id="weekday")
    db_session.add_all([agency, calendar])
    db_session.flush()
    route = Route(
        feed_id=feed.id,
        agency_id=agency.id,
        gtfs_route_id="004",
        route_type=3,
    )
    db_session.add(route)
    db_session.flush()
    trip = Trip(
        feed_id=feed.id,
        route_id=route.id,
        service_id=calendar.id,
        gtfs_trip_id="trip-004",
        block_id="block-004",
    )
    db_session.add(trip)
    db_session.add(
        RealtimeTripObservation(
            observation_key="read-only-proof",
            static_feed_id=feed.id,
            source_timestamp=observed_at,
            observed_at=observed_at,
            vehicle_id="vehicle-004",
            entity_id="entity-004",
            trip_gtfs_id="trip-004",
            route_gtfs_id="004",
            next_stop_id="stop-1",
            next_stop_sequence=1,
            next_arrival_at=observed_at,
        )
    )
    db_session.commit()
    before = _source_snapshot()

    static_feed, rows = read_attribution_rows(
        get_engine(),
        static_feed_id=str(feed.id),
        start_at=observed_at,
        end_at=datetime(2026, 9, 11, 20, 1, tzinfo=timezone.utc),
        route_ids=("004",),
        max_source_rows=10,
    )

    assert static_feed["checksum_sha256"] == "a" * 64
    assert attribution_records(rows)[0]["attribution_status"] == ATTRIBUTED
    assert _source_snapshot() == before
