"""FastAPI surface for versioned static Edmonton transit network data."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from transitpulse_api.api_models import (
    AgencyResponse,
    HistoryAvailabilityResponse,
    HistoryVehicleObservationCollection,
    HistoryVehicleObservationFeature,
    HistoryVehicleObservationProperties,
    RealtimeAlertResponse,
    RealtimeFeedHealthResponse,
    RealtimeStatusResponse,
    RealtimeVehicleFeature,
    RealtimeVehicleFeatureCollection,
    RealtimeVehicleProperties,
    NetworkHealthResponse,
    NetworkRouteStatusResponse,
    RouteOperationsResponse,
    RouteDetailResponse,
    RouteSummaryResponse,
    ShapeFeature,
    ShapeFeatureCollection,
    ShapeProperties,
    StopFeature,
    StopFeatureCollection,
    StopProperties,
)
from transitpulse_api.analytics import (
    RecordedPoint,
    delay_buckets,
    delay_bands,
    delay_distribution,
    headway_metrics,
    observed_stop_sequence_events,
)
from transitpulse_api.analytics_models import (
    ComparisonDeltasResponse,
    ComparisonPeriodResponse,
    DelayDistributionResponse,
    DelayBandsResponse,
    HeadwayResponse,
    ReliabilityStopFeature,
    ReliabilityStopFeatureCollection,
    ReliabilityStopProperties,
    ReliabilityTimeBucketResponse,
    RouteReliabilityResponse,
    RouteComparisonResponse,
)
from transitpulse_api.config import settings
from transitpulse_api.database import get_engine, get_session
from transitpulse_api.models import (
    Agency,
    CalendarDate,
    GtfsFeed,
    RealtimeAlert,
    RealtimeFeedStatus,
    RealtimeTripState,
    RealtimeVehicleState,
    Route,
    ServiceCalendar,
    Shape,
    Stop,
    StopTime,
    Trip,
    VehicleObservation,
)
from transitpulse_api.operations import OperationsThresholds, calculate_headways, classify_service

app = FastAPI(title="TransitPulse API", version="0.4.0")
SessionDependency = Annotated[Session, Depends(get_session)]
MAX_RELIABILITY_OBSERVATIONS = 10_000


@app.get("/health", tags=["health"])
@app.get("/api/health", tags=["health"], include_in_schema=False)
def health() -> dict[str, str]:
    """Report that the API process is accepting requests."""

    return {"status": "ok"}


@app.get("/health/db", tags=["health"])
@app.get("/api/health/db", tags=["health"], include_in_schema=False)
def database_health() -> dict[str, str]:
    """Execute a real PostGIS query and report the connected extension version."""

    try:
        with get_engine().connect() as connection:
            postgis_version = connection.execute(text("SELECT PostGIS_Version()")).scalar_one()
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error

    return {"status": "ok", "postgis_version": str(postgis_version)}


def selected_feed(
    session: SessionDependency,
    feed_id: UUID | None = Query(default=None, description="A historical imported GTFS feed ID"),
) -> GtfsFeed:
    statement = select(GtfsFeed).where(GtfsFeed.import_status == "succeeded")
    if feed_id is not None:
        statement = statement.where(GtfsFeed.id == feed_id)
    else:
        statement = statement.order_by(GtfsFeed.imported_at.desc()).limit(1)
    try:
        feed = session.scalar(statement)
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error
    if feed is None:
        detail = "GTFS feed not found" if feed_id is not None else "No imported GTFS feed is available"
        raise HTTPException(status_code=404, detail=detail)
    return feed


FeedDependency = Annotated[GtfsFeed, Depends(selected_feed)]


def realtime_is_stale(last_success_at: datetime | None, source_timestamp: datetime | None) -> bool:
    """A delayed publisher timestamp is stale even if we just fetched it."""

    freshness = source_timestamp or last_success_at
    if freshness is None:
        return True
    return (datetime.now(timezone.utc) - freshness).total_seconds() > settings.realtime_stale_after_seconds


def realtime_feed_health(status: RealtimeFeedStatus) -> RealtimeFeedHealthResponse:
    return RealtimeFeedHealthResponse(
        feed_kind=status.feed_kind,
        source_url=status.source_url,
        last_attempt_at=status.last_attempt_at,
        last_success_at=status.last_success_at,
        source_timestamp=status.source_timestamp,
        entity_count=status.entity_count,
        error_message=status.error_message,
        stale=realtime_is_stale(status.last_success_at, status.source_timestamp),
    )


@app.get("/api/realtime/status", response_model=RealtimeStatusResponse, tags=["realtime"])
def realtime_status(session: SessionDependency) -> RealtimeStatusResponse:
    """Expose source health separately from the state consumers render on the map."""

    try:
        feed = session.scalar(
            select(GtfsFeed)
            .where(GtfsFeed.import_status == "succeeded")
            .order_by(GtfsFeed.imported_at.desc())
            .limit(1)
        )
        statuses = session.scalars(select(RealtimeFeedStatus).order_by(RealtimeFeedStatus.feed_kind)).all()
        active_vehicles = 0
        if feed is not None:
            active_vehicles = session.scalar(
                select(func.count()).select_from(RealtimeVehicleState).where(
                    RealtimeVehicleState.static_feed_id == feed.id
                )
            ) or 0
    except SQLAlchemyError as error:
        raise HTTPException(status_code=503, detail="database unavailable") from error
    health = [realtime_feed_health(status) for status in statuses]
    return RealtimeStatusResponse(
        static_feed_id=feed.id if feed else None,
        active_vehicles=active_vehicles,
        feeds=health,
        stale=not health or any(item.stale for item in health if item.feed_kind == "vehicle_positions"),
    )


def realtime_vehicle_collection(
    session: Session, feed: GtfsFeed, *, route_id: str | None = None
) -> RealtimeVehicleFeatureCollection:
    statement = (
        select(
            RealtimeVehicleState,
            func.ST_X(RealtimeVehicleState.position).label("longitude"),
            func.ST_Y(RealtimeVehicleState.position).label("latitude"),
        )
        .where(
            RealtimeVehicleState.static_feed_id == feed.id,
            RealtimeVehicleState.position.is_not(None),
        )
        .order_by(RealtimeVehicleState.vehicle_id)
    )
    if route_id is not None:
        statement = statement.where(RealtimeVehicleState.route_gtfs_id == route_id)
    rows = session.execute(statement).all()
    source_status = session.get(RealtimeFeedStatus, "vehicle_positions")
    source_timestamp = source_status.source_timestamp if source_status else None
    return RealtimeVehicleFeatureCollection(
        feed_id=feed.id,
        source_timestamp=source_timestamp,
        stale=realtime_is_stale(
            source_status.last_success_at if source_status else None,
            source_timestamp,
        ),
        features=[
            RealtimeVehicleFeature(
                geometry={"type": "Point", "coordinates": [float(longitude), float(latitude)]},
                properties=RealtimeVehicleProperties(
                    vehicle_id=state.vehicle_id,
                    trip_id=state.trip_gtfs_id,
                    route_id=state.route_gtfs_id,
                    bearing=state.bearing,
                    speed=state.speed,
                    timestamp=state.observed_at or state.source_timestamp,
                    current_stop_sequence=state.current_stop_sequence,
                    current_status=state.current_status,
                    schedule_relationship=state.schedule_relationship,
                    delay_seconds=state.delay_seconds,
                ),
            )
            for state, longitude, latitude in rows
        ],
    )


@app.get("/api/realtime/vehicles", response_model=RealtimeVehicleFeatureCollection, tags=["realtime"])
def realtime_vehicles(feed: FeedDependency, session: SessionDependency) -> RealtimeVehicleFeatureCollection:
    return realtime_vehicle_collection(session, feed)


@app.get("/api/realtime/vehicles/{vehicle_id}", response_model=RealtimeVehicleFeature, tags=["realtime"])
def realtime_vehicle_detail(
    vehicle_id: str, feed: FeedDependency, session: SessionDependency
) -> RealtimeVehicleFeature:
    collection = realtime_vehicle_collection(session, feed)
    for feature in collection.features:
        if feature.properties.vehicle_id == vehicle_id:
            return feature
    raise HTTPException(status_code=404, detail=f"Vehicle {vehicle_id!r} was not found in current realtime state")


@app.get(
    "/api/realtime/routes/{route_id}/vehicles",
    response_model=RealtimeVehicleFeatureCollection,
    tags=["realtime"],
)
def realtime_route_vehicles(
    route_id: str, feed: FeedDependency, session: SessionDependency
) -> RealtimeVehicleFeatureCollection:
    route_for_feed(session, feed, route_id)
    return realtime_vehicle_collection(session, feed, route_id=route_id)


@app.get("/api/realtime/alerts", response_model=list[RealtimeAlertResponse], tags=["realtime"])
def realtime_alerts(feed: FeedDependency, session: SessionDependency) -> list[RealtimeAlertResponse]:
    rows = session.scalars(
        select(RealtimeAlert)
        .where(RealtimeAlert.static_feed_id == feed.id)
        .order_by(RealtimeAlert.updated_at.desc(), RealtimeAlert.entity_id)
    ).all()
    return [
        RealtimeAlertResponse(
            id=row.entity_id,
            header=row.header,
            description=row.description,
            url=row.url,
            cause=row.cause,
            effect=row.effect,
            active_periods=row.active_periods,
            affected_routes=row.affected_routes,
            affected_stops=row.affected_stops,
            updated_at=row.updated_at,
        )
        for row in rows
    ]


def operations_thresholds() -> OperationsThresholds:
    return OperationsThresholds(
        on_time_seconds=settings.operations_on_time_seconds,
        major_delay_seconds=settings.operations_major_delay_seconds,
        bunching_ratio=settings.operations_bunching_ratio,
        gap_ratio=settings.operations_gap_ratio,
    )


@app.get("/api/operations/routes/{route_id}", response_model=RouteOperationsResponse, tags=["operations"])
def route_operations(
    route_id: str, feed: FeedDependency, session: SessionDependency
) -> RouteOperationsResponse:
    """Return only defensible, current route signals from static + realtime state."""

    route_for_feed(session, feed, route_id)
    vehicles = session.scalars(
        select(RealtimeVehicleState).where(
            RealtimeVehicleState.static_feed_id == feed.id,
            RealtimeVehicleState.route_gtfs_id == route_id,
        )
    ).all()
    trips = session.scalars(
        select(RealtimeTripState).where(
            RealtimeTripState.static_feed_id == feed.id,
            RealtimeTripState.route_gtfs_id == route_id,
        )
    ).all()
    delays = [item.delay_seconds for item in trips if item.delay_seconds is not None]
    if not delays:
        delays = [item.delay_seconds for item in vehicles if item.delay_seconds is not None]
    arrivals_by_stop: dict[str, list[datetime]] = defaultdict(list)
    for item in trips:
        if item.next_stop_id is not None and item.next_arrival_at is not None:
            arrivals_by_stop[item.next_stop_id].append(item.next_arrival_at)
    prediction_stop_id, arrivals = max(
        arrivals_by_stop.items(), key=lambda entry: len(entry[1]), default=(None, [])
    )
    headway = calculate_headways(arrivals, operations_thresholds())
    vehicle_feed = session.get(RealtimeFeedStatus, "vehicle_positions")
    has_fresh_live_data = vehicle_feed is not None and not realtime_is_stale(
        vehicle_feed.last_success_at, vehicle_feed.source_timestamp
    )
    service_status = classify_service(
        has_fresh_live_data=has_fresh_live_data,
        delays_seconds=delays,
        headway=headway,
        thresholds=operations_thresholds(),
    )
    alerts = session.scalars(
        select(RealtimeAlert).where(RealtimeAlert.static_feed_id == feed.id)
    ).all()
    alert_count = sum(route_id in alert.affected_routes for alert in alerts)
    reasons = {
        "NO_LIVE_DATA": "Vehicle Positions has not produced a fresh snapshot.",
        "BUNCHING": "Comparable predicted arrivals are unusually close at one stop.",
        "SERVICE_GAP": "Comparable predicted arrivals show an unusually large gap at one stop.",
        "MAJOR_DELAY": "At least one current trip update exceeds the configured major-delay threshold.",
        "MINOR_DELAY": "Current trip updates exceed the on-time tolerance.",
        "ON_TIME": "Fresh live data has no current delay or comparable spacing exception.",
    }
    return RouteOperationsResponse(
        route_id=route_id,
        active_vehicles=len(vehicles),
        service_status=service_status,
        status_reason=reasons[service_status],
        average_delay_seconds=round(sum(delays) / len(delays)) if delays else None,
        delayed_vehicle_count=sum(abs(delay) > settings.operations_on_time_seconds for delay in delays),
        alert_count=alert_count,
        prediction_stop_id=prediction_stop_id,
        predicted_headways_seconds=list(headway.headways_seconds),
        headway_baseline_seconds=round(headway.baseline_seconds) if headway.baseline_seconds else None,
        bunching=headway.bunching,
        service_gap=headway.service_gap,
    )


def _route_status_reason(service_status: str) -> str:
    return {
        "NO_LIVE_DATA": "No fresh vehicle snapshot is available for this route.",
        "BUNCHING": "Predicted arrivals at one stop are unusually close together.",
        "SERVICE_GAP": "Predicted arrivals at one stop show an unusually large gap.",
        "EARLY": "Current trips are running ahead of the configured on-time tolerance.",
        "MAJOR_DELAY": "A current trip exceeds the configured major-delay threshold.",
        "MINOR_DELAY": "Current trips exceed the configured on-time tolerance.",
        "ON_TIME": "Fresh live data shows no current delay or spacing exception.",
    }[service_status]


def _attention_score(
    service_status: str,
    *,
    delayed_vehicle_count: int,
    average_delay_seconds: int | None,
    alert_count: int,
) -> int:
    if service_status == "NO_LIVE_DATA":
        return 0
    severity = {
        "SERVICE_GAP": 500,
        "BUNCHING": 400,
        "MAJOR_DELAY": 300,
        "MINOR_DELAY": 200,
        "EARLY": 100,
        "ON_TIME": 0,
        "NO_LIVE_DATA": 0,
    }[service_status]
    return severity + min(delayed_vehicle_count, 99) * 10 + min(abs(average_delay_seconds or 0) // 60, 99) + min(alert_count, 9)


@app.get("/api/operations/network", response_model=NetworkHealthResponse, tags=["operations"])
def network_operations(feed: FeedDependency, session: SessionDependency) -> NetworkHealthResponse:
    """Summarize one current realtime snapshot without per-route API fan-out."""

    vehicles = session.scalars(
        select(RealtimeVehicleState).where(RealtimeVehicleState.static_feed_id == feed.id)
    ).all()
    trips = session.scalars(
        select(RealtimeTripState).where(RealtimeTripState.static_feed_id == feed.id)
    ).all()
    alerts = session.scalars(
        select(RealtimeAlert).where(RealtimeAlert.static_feed_id == feed.id)
    ).all()
    route_ids = {
        item.route_gtfs_id
        for item in [*vehicles, *trips]
        if item.route_gtfs_id is not None
    }
    route_rows = session.scalars(
        select(Route).where(Route.feed_id == feed.id, Route.gtfs_route_id.in_(route_ids))
    ).all() if route_ids else []
    routes_by_gtfs_id = {route.gtfs_route_id: route for route in route_rows}
    vehicles_by_route: dict[str, list[RealtimeVehicleState]] = defaultdict(list)
    trips_by_route: dict[str, list[RealtimeTripState]] = defaultdict(list)
    alerts_by_route: dict[str, int] = defaultdict(int)
    for vehicle in vehicles:
        if vehicle.route_gtfs_id:
            vehicles_by_route[vehicle.route_gtfs_id].append(vehicle)
    for trip in trips:
        if trip.route_gtfs_id:
            trips_by_route[trip.route_gtfs_id].append(trip)
    for alert in alerts:
        for route_id in set(alert.affected_routes):
            alerts_by_route[route_id] += 1

    vehicle_feed = session.get(RealtimeFeedStatus, "vehicle_positions")
    stale = vehicle_feed is None or realtime_is_stale(
        vehicle_feed.last_success_at if vehicle_feed else None,
        vehicle_feed.source_timestamp if vehicle_feed else None,
    )
    thresholds = operations_thresholds()
    route_statuses: list[NetworkRouteStatusResponse] = []
    all_delays: list[int] = []
    for route_id in sorted(route_ids):
        route_vehicles = vehicles_by_route.get(route_id, [])
        route_trips = trips_by_route.get(route_id, [])
        delays = [item.delay_seconds for item in route_trips if item.delay_seconds is not None] if not stale else []
        if not delays and not stale:
            delays = [item.delay_seconds for item in route_vehicles if item.delay_seconds is not None]
        all_delays.extend(delays)
        arrivals_by_stop: dict[str, list[datetime]] = defaultdict(list)
        for trip in route_trips if not stale else []:
            if trip.next_stop_id is not None and trip.next_arrival_at is not None:
                arrivals_by_stop[trip.next_stop_id].append(trip.next_arrival_at)
        prediction_stop_id, arrivals = max(
            arrivals_by_stop.items(), key=lambda entry: len(entry[1]), default=(None, [])
        )
        headway = calculate_headways(arrivals, thresholds)
        service_status = classify_service(
            has_fresh_live_data=not stale and bool(route_vehicles),
            delays_seconds=delays,
            headway=headway,
            thresholds=thresholds,
        )
        average_delay = round(sum(delays) / len(delays)) if delays else None
        delayed_vehicle_count = sum(delay > settings.operations_on_time_seconds for delay in delays)
        alert_count = alerts_by_route.get(route_id, 0)
        route = routes_by_gtfs_id.get(route_id)
        route_statuses.append(
            NetworkRouteStatusResponse(
                route_id=route_id,
                short_name=route.short_name if route else None,
                long_name=route.long_name if route else None,
                service_status=service_status,
                status_reason=_route_status_reason(service_status),
                active_vehicles=len(route_vehicles) if not stale else 0,
                average_delay_seconds=average_delay,
                delayed_vehicle_count=delayed_vehicle_count,
                alert_count=alert_count,
                prediction_stop_id=prediction_stop_id,
                predicted_headways_seconds=list(headway.headways_seconds),
                headway_baseline_seconds=round(headway.baseline_seconds) if headway.baseline_seconds else None,
                bunching=headway.bunching,
                service_gap=headway.service_gap,
                attention_score=_attention_score(
                    service_status,
                    delayed_vehicle_count=delayed_vehicle_count,
                    average_delay_seconds=average_delay,
                    alert_count=alert_count,
                ),
            )
        )
    issues = sorted(
        (status for status in route_statuses if status.attention_score > 0),
        key=lambda status: (-status.attention_score, status.short_name or status.route_id),
    )
    ordered_routes = sorted(
        route_statuses,
        key=lambda status: (-(status.attention_score), status.short_name or status.route_id),
    )
    return NetworkHealthResponse(
        static_feed_id=feed.id,
        generated_at=datetime.now(timezone.utc),
        stale=stale,
        active_vehicles=len(vehicles) if not stale else 0,
        routes_with_live_service=(sum(bool(items) for items in vehicles_by_route.values()) if not stale else 0),
        routes_delayed=sum(status.delayed_vehicle_count > 0 for status in route_statuses),
        routes_with_bunching=sum(status.bunching for status in route_statuses),
        routes_with_service_gaps=sum(status.service_gap for status in route_statuses),
        active_alerts=len(alerts),
        median_network_delay_seconds=round(median(all_delays)) if all_delays else None,
        routes=ordered_routes,
        issues=issues[:12],
    )


def history_window(
    start: datetime | None, end: datetime | None
) -> tuple[datetime, datetime]:
    """Keep the first history API deliberately bounded until replay exists."""

    window_end = end or datetime.now(timezone.utc)
    window_start = start or window_end - timedelta(hours=1)
    if window_start.tzinfo is None or window_end.tzinfo is None:
        raise HTTPException(status_code=422, detail="History timestamps must include a timezone")
    if window_start >= window_end:
        raise HTTPException(status_code=422, detail="History start must be before end")
    if window_end - window_start > timedelta(hours=24):
        raise HTTPException(status_code=422, detail="History range is limited to 24 hours")
    return window_start, window_end


def recorded_time_range(start: datetime, end: datetime):
    """Use publisher time when present, otherwise the recorder's UTC time."""

    return or_(
        and_(
            VehicleObservation.observed_at.is_not(None),
            VehicleObservation.observed_at >= start,
            VehicleObservation.observed_at < end,
        ),
        and_(
            VehicleObservation.observed_at.is_(None),
            VehicleObservation.recorded_at >= start,
            VehicleObservation.recorded_at < end,
        ),
    )


def history_collection(
    session: Session,
    *,
    start: datetime,
    end: datetime,
    limit: int,
    vehicle_id: str | None = None,
    route_id: str | None = None,
    static_feed_id: UUID,
) -> HistoryVehicleObservationCollection:
    observed_time = func.coalesce(VehicleObservation.observed_at, VehicleObservation.recorded_at)
    statement = (
        select(
            VehicleObservation,
            func.ST_X(VehicleObservation.position).label("longitude"),
            func.ST_Y(VehicleObservation.position).label("latitude"),
        )
        .where(
            VehicleObservation.position.is_not(None),
            VehicleObservation.static_feed_id == static_feed_id,
            recorded_time_range(start, end),
        )
        .order_by(observed_time.asc(), VehicleObservation.id.asc())
        .limit(limit)
    )
    if vehicle_id is not None:
        statement = statement.where(VehicleObservation.vehicle_id == vehicle_id)
    if route_id is not None:
        statement = statement.where(VehicleObservation.route_gtfs_id == route_id)
    rows = session.execute(statement).all()
    return HistoryVehicleObservationCollection(
        static_feed_id=static_feed_id,
        start=start,
        end=end,
        limit=limit,
        features=[
            HistoryVehicleObservationFeature(
                geometry={"type": "Point", "coordinates": [float(longitude), float(latitude)]},
                properties=HistoryVehicleObservationProperties(
                    observation_id=item.id,
                    static_feed_id=item.static_feed_id,
                    vehicle_id=item.vehicle_id,
                    trip_id=item.trip_gtfs_id,
                    route_id=item.route_gtfs_id,
                    observed_at=item.observed_at,
                    source_timestamp=item.source_timestamp,
                    recorded_at=item.recorded_at,
                    bearing=item.bearing,
                    speed=item.speed,
                    current_stop_sequence=item.current_stop_sequence,
                    current_status=item.current_status,
                    schedule_relationship=item.schedule_relationship,
                    delay_seconds=item.delay_seconds,
                ),
            )
            for item, longitude, latitude in rows
        ],
    )


@app.get("/api/history/availability", response_model=HistoryAvailabilityResponse, tags=["history"])
def history_availability(
    feed: FeedDependency,
    session: SessionDependency,
    route_id: str | None = Query(default=None),
    vehicle_id: str | None = Query(default=None),
) -> HistoryAvailabilityResponse:
    """Expose only the real recorded range a replay control can select."""

    if route_id is not None:
        route_for_feed(session, feed, route_id)
    observed_time = func.coalesce(VehicleObservation.observed_at, VehicleObservation.recorded_at)
    statement = select(
        func.min(observed_time),
        func.max(observed_time),
        func.count(),
    ).where(VehicleObservation.static_feed_id == feed.id)
    if route_id is not None:
        statement = statement.where(VehicleObservation.route_gtfs_id == route_id)
    if vehicle_id is not None:
        statement = statement.where(VehicleObservation.vehicle_id == vehicle_id)
    first_observed_at, last_observed_at, observation_count = session.execute(statement).one()
    return HistoryAvailabilityResponse(
        static_feed_id=feed.id,
        first_observed_at=first_observed_at,
        last_observed_at=last_observed_at,
        observation_count=observation_count,
    )


@app.get(
    "/api/history/observations",
    response_model=HistoryVehicleObservationCollection,
    tags=["history"],
)
def history_observations(
    feed: FeedDependency,
    session: SessionDependency,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=5000, ge=1, le=5000),
    route_id: str | None = Query(default=None),
    vehicle_id: str | None = Query(default=None),
) -> HistoryVehicleObservationCollection:
    """Return a bounded, feed-consistent observation stream for browser replay."""

    if route_id is not None:
        route_for_feed(session, feed, route_id)
    window_start, window_end = history_window(start, end)
    return history_collection(
        session,
        start=window_start,
        end=window_end,
        limit=limit,
        route_id=route_id,
        vehicle_id=vehicle_id,
        static_feed_id=feed.id,
    )


@app.get(
    "/api/history/vehicles/{vehicle_id}",
    response_model=HistoryVehicleObservationCollection,
    tags=["history"],
)
def vehicle_history(
    vehicle_id: str,
    feed: FeedDependency,
    session: SessionDependency,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> HistoryVehicleObservationCollection:
    window_start, window_end = history_window(start, end)
    return history_collection(
        session,
        start=window_start,
        end=window_end,
        limit=limit,
        vehicle_id=vehicle_id,
        static_feed_id=feed.id,
    )


@app.get(
    "/api/history/routes/{route_id}",
    response_model=HistoryVehicleObservationCollection,
    tags=["history"],
)
def route_history(
    route_id: str,
    feed: FeedDependency,
    session: SessionDependency,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
) -> HistoryVehicleObservationCollection:
    route_for_feed(session, feed, route_id)
    window_start, window_end = history_window(start, end)
    return history_collection(
        session,
        start=window_start,
        end=window_end,
        limit=limit,
        route_id=route_id,
        static_feed_id=feed.id,
    )


def service_is_active(
    calendar: ServiceCalendar,
    service_date: date,
    exceptions: dict[tuple[int, date], int],
) -> bool:
    """Evaluate a GTFS calendar and its explicit date override."""

    override = exceptions.get((calendar.id, service_date))
    if override is not None:
        return override == 1
    if (calendar.start_date and service_date < calendar.start_date) or (
        calendar.end_date and service_date > calendar.end_date
    ):
        return False
    weekdays = (
        calendar.monday,
        calendar.tuesday,
        calendar.wednesday,
        calendar.thursday,
        calendar.friday,
        calendar.saturday,
        calendar.sunday,
    )
    return weekdays[service_date.weekday()]


def scheduled_stop_times(
    session: Session,
    *,
    route: Route,
    stop: Stop,
    start: datetime,
    end: datetime,
    agency_timezone: str,
) -> list[datetime]:
    """Return static scheduled arrivals at one physical stop inside this range."""

    rows = session.execute(
        select(
            Trip.service_id,
            func.coalesce(StopTime.arrival_seconds, StopTime.departure_seconds),
        )
        .join(Trip, StopTime.trip_id == Trip.id)
        .where(Trip.route_id == route.id, StopTime.stop_id == stop.id)
    ).all()
    rows = [(service_id, seconds) for service_id, seconds in rows if seconds is not None]
    if not rows:
        return []
    calendars = session.scalars(
        select(ServiceCalendar)
        .join(Trip, Trip.service_id == ServiceCalendar.id)
        .where(Trip.route_id == route.id)
        .distinct()
    ).all()
    calendars_by_id = {calendar.id: calendar for calendar in calendars}
    exceptions = (
        {
            (service_id, exception_date): exception_type
            for service_id, exception_date, exception_type in session.execute(
                select(CalendarDate.service_id, CalendarDate.date, CalendarDate.exception_type).where(
                    CalendarDate.service_id.in_(calendars_by_id)
                )
            )
        }
        if calendars_by_id
        else {}
    )
    try:
        agency_zone = ZoneInfo(agency_timezone)
    except ZoneInfoNotFoundError:
        agency_zone = timezone.utc
    latest_service_offset = max(seconds for _, seconds in rows) // 86_400
    service_date = start.astimezone(agency_zone).date() - timedelta(days=latest_service_offset)
    final_date = end.astimezone(agency_zone).date()
    arrivals: list[datetime] = []
    while service_date <= final_date:
        active_service_ids = {
            service_id
            for service_id, calendar in calendars_by_id.items()
            if service_is_active(calendar, service_date, exceptions)
        }
        for service_id, seconds in rows:
            if service_id not in active_service_ids:
                continue
            scheduled = datetime.combine(service_date, time.min, tzinfo=agency_zone) + timedelta(seconds=seconds)
            if start <= scheduled < end:
                arrivals.append(scheduled)
        service_date += timedelta(days=1)
    return sorted(set(arrivals))


@app.get(
    "/api/analytics/routes/{route_id}",
    response_model=RouteReliabilityResponse,
    tags=["analytics"],
)
def route_reliability(
    route_id: str,
    feed: FeedDependency,
    session: SessionDependency,
    start: datetime = Query(..., description="Inclusive UTC analysis start"),
    end: datetime = Query(..., description="Exclusive UTC analysis end"),
) -> RouteReliabilityResponse:
    """Compare direct recorded signals with the static timetable at one route stop."""

    window_start, window_end = history_window(start, end)
    route, agency = route_for_feed(session, feed, route_id)
    observed_time = func.coalesce(VehicleObservation.observed_at, VehicleObservation.recorded_at)
    observations = session.scalars(
        select(VehicleObservation)
        .where(
            VehicleObservation.static_feed_id == feed.id,
            VehicleObservation.route_gtfs_id == route_id,
            recorded_time_range(window_start, window_end),
        )
        .order_by(observed_time.asc(), VehicleObservation.id.asc())
        .limit(MAX_RELIABILITY_OBSERVATIONS + 1)
    ).all()
    if len(observations) > MAX_RELIABILITY_OBSERVATIONS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Analytics range contains more than {MAX_RELIABILITY_OBSERVATIONS:,} observations; "
                "choose a shorter range"
            ),
        )

    points = [
        RecordedPoint(
            at=observation.observed_at or observation.recorded_at,
            vehicle_id=observation.vehicle_id,
            trip_id=observation.trip_gtfs_id,
            current_stop_sequence=observation.current_stop_sequence,
            delay_seconds=observation.delay_seconds,
        )
        for observation in observations
    ]
    events = observed_stop_sequence_events(points)
    trip_ids = {event.trip_id for event in events if event.trip_id is not None}
    sequences = {event.stop_sequence for event in events}
    stop_for_sequence: dict[tuple[str, int], tuple[Stop, float | None, float | None]] = {}
    if trip_ids and sequences:
        matching_stops = session.execute(
            select(
                Trip.gtfs_trip_id,
                StopTime.stop_sequence,
                Stop,
                func.ST_X(Stop.location).label("longitude"),
                func.ST_Y(Stop.location).label("latitude"),
            )
            .join(StopTime, StopTime.trip_id == Trip.id)
            .join(Stop, Stop.id == StopTime.stop_id)
            .where(
                Trip.route_id == route.id,
                Trip.gtfs_trip_id.in_(trip_ids),
                StopTime.stop_sequence.in_(sequences),
            )
        ).all()
        stop_for_sequence = {
            (trip_id, stop_sequence): (stop, longitude, latitude)
            for trip_id, stop_sequence, stop, longitude, latitude in matching_stops
        }

    events_by_stop: dict[int, list] = defaultdict(list)
    stop_records: dict[int, tuple[Stop, float | None, float | None]] = {}
    for event in events:
        if event.trip_id is None:
            continue
        matched_stop = stop_for_sequence.get((event.trip_id, event.stop_sequence))
        if matched_stop is None:
            continue
        stop, longitude, latitude = matched_stop
        stop_records[stop.id] = matched_stop
        events_by_stop[stop.id].append(event)

    selected_stop_id = max(
        events_by_stop,
        key=lambda stop_id: (len(events_by_stop[stop_id]), -stop_id),
        default=None,
    )
    selected_stop = stop_records.get(selected_stop_id) if selected_stop_id is not None else None
    selected_events = events_by_stop.get(selected_stop_id, []) if selected_stop_id is not None else []
    scheduled_arrivals = (
        scheduled_stop_times(
            session,
            route=route,
            stop=selected_stop[0],
            start=window_start,
            end=window_end,
            agency_timezone=agency.timezone,
        )
        if selected_stop is not None
        else []
    )
    thresholds = operations_thresholds()
    scheduled = headway_metrics(scheduled_arrivals, thresholds)
    observed = headway_metrics(
        [event.at for event in selected_events],
        thresholds,
        scheduled_baseline_seconds=scheduled.median_seconds,
    )
    delay = delay_distribution(point.delay_seconds for point in points)
    bands = delay_bands(
        (point.delay_seconds for point in points),
        on_time_seconds=settings.operations_on_time_seconds,
        major_delay_seconds=settings.operations_major_delay_seconds,
        severe_delay_seconds=settings.operations_severe_delay_seconds,
    )
    coverage_duration_seconds = (
        round((max(point.at for point in points) - min(point.at for point in points)).total_seconds())
        if len(points) >= 2
        else 0
    )
    sufficient_history = (
        len(points) >= settings.analytics_min_observations
        and delay.sample_count >= settings.analytics_min_delay_samples
        and coverage_duration_seconds >= settings.analytics_min_coverage_seconds
    )
    if not points:
        sufficiency_reason = "Insufficient recorded history: no observations in this window."
    elif len(points) < settings.analytics_min_observations:
        sufficiency_reason = (
            f"Insufficient recorded history: {len(points)} observations; "
            f"at least {settings.analytics_min_observations} are required."
        )
    elif coverage_duration_seconds < settings.analytics_min_coverage_seconds:
        sufficiency_reason = (
            "Insufficient recorded history: recorded coverage is shorter than "
            f"{settings.analytics_min_coverage_seconds // 60} minutes."
        )
    elif delay.sample_count < settings.analytics_min_delay_samples:
        sufficiency_reason = (
            f"Insufficient recorded history: {delay.sample_count} delay samples; "
            f"at least {settings.analytics_min_delay_samples} are required."
        )
    else:
        sufficiency_reason = "Recorded history meets the configured route-analysis minimums."
    timeline = delay_buckets(
        points,
        start=window_start,
        end=window_end,
        on_time_seconds=settings.operations_on_time_seconds,
    )
    comparable_periods = [bucket for bucket in timeline if bucket.median_delay_seconds is not None]
    best_period = min(comparable_periods, key=lambda bucket: abs(bucket.median_delay_seconds or 0), default=None)
    worst_period = max(comparable_periods, key=lambda bucket: abs(bucket.median_delay_seconds or 0), default=None)
    spatial_features: list[ReliabilityStopFeature] = []
    for stop_id, stop_events in sorted(
        events_by_stop.items(), key=lambda item: stop_records[item[0]][0].gtfs_stop_id
    ):
        stop, longitude, latitude = stop_records[stop_id]
        if longitude is None or latitude is None:
            continue
        stop_delay = delay_distribution(event.delay_seconds for event in stop_events)
        if stop_delay.median_seconds is None:
            reliability_status = "unknown"
        elif abs(stop_delay.median_seconds) <= settings.operations_on_time_seconds:
            reliability_status = "good"
        elif abs(stop_delay.median_seconds) < settings.operations_major_delay_seconds:
            reliability_status = "moderate"
        else:
            reliability_status = "poor"
        spatial_features.append(
            ReliabilityStopFeature(
                geometry={"type": "Point", "coordinates": [float(longitude), float(latitude)]},
                properties=ReliabilityStopProperties(
                    stop_id=stop.gtfs_stop_id,
                    name=stop.name,
                    source="direct_recorded_stop_sequence_entries",
                    observation_count=len(stop_events),
                    delay_observation_count=stop_delay.sample_count,
                    median_delay_seconds=stop_delay.median_seconds,
                    late_observation_count=sum(
                        event.delay_seconds > settings.operations_on_time_seconds
                        for event in stop_events
                        if event.delay_seconds is not None
                    ),
                    reliability_status=reliability_status,
                ),
            )
        )
    return RouteReliabilityResponse(
        route_id=route_id,
        static_feed_id=feed.id,
        start=window_start,
        end=window_end,
        observation_count=len(points),
        observed_vehicle_count=len({point.vehicle_id for point in points}),
        coverage_duration_seconds=coverage_duration_seconds,
        sufficient_history=sufficient_history,
        sufficiency_reason=sufficiency_reason,
        data_notes=[
            "Delay and timeline values are direct recorded Vehicle Positions observations; no delay means the publisher supplied none.",
            "Observed headways are recorded vehicle entries into the same GTFS stop sequence, not map-position interpolation or a certified arrival feed.",
            "Scheduled headways are static GTFS stop times active in the agency timezone for that same physical stop.",
            "Map markers show only stops with direct recorded stop-sequence evidence in the selected range.",
        ],
        delay_distribution=DelayDistributionResponse(
            source="direct_recorded_observations",
            **delay.__dict__,
            sufficient=delay.sample_count >= settings.analytics_min_delay_samples,
        ),
        delay_bands=DelayBandsResponse(source="direct_recorded_observations", **bands.__dict__),
        observed_headways=HeadwayResponse(
            source="direct_recorded_stop_sequence_entries",
            stop_id=selected_stop[0].gtfs_stop_id if selected_stop else None,
            stop_name=selected_stop[0].name if selected_stop else None,
            **observed.__dict__,
            sufficient=observed.sample_count >= settings.analytics_min_headway_samples,
        ),
        scheduled_headways=HeadwayResponse(
            source="static_gtfs_schedule",
            stop_id=selected_stop[0].gtfs_stop_id if selected_stop else None,
            stop_name=selected_stop[0].name if selected_stop else None,
            **scheduled.__dict__,
            sufficient=scheduled.sample_count >= settings.analytics_min_headway_samples,
        ),
        median_headway_deviation_seconds=(
            observed.median_seconds - scheduled.median_seconds
            if observed.median_seconds is not None and scheduled.median_seconds is not None
            else None
        ),
        best_period_start=best_period.start if best_period else None,
        worst_period_start=worst_period.start if worst_period else None,
        through_time=[
            ReliabilityTimeBucketResponse(source="direct_recorded_observations", **bucket.__dict__)
            for bucket in timeline
        ],
        spatial_reliability=ReliabilityStopFeatureCollection(
            source="direct_recorded_stop_sequence_entries",
            features=spatial_features,
        ),
    )


def _comparison_period(label: str, result: RouteReliabilityResponse) -> ComparisonPeriodResponse:
    headways_sufficient = result.observed_headways.sufficient
    return ComparisonPeriodResponse(
        label=label,
        start=result.start,
        end=result.end,
        observation_count=result.observation_count,
        observed_vehicle_count=result.observed_vehicle_count,
        coverage_duration_seconds=result.coverage_duration_seconds,
        sufficient_history=result.sufficient_history,
        sufficiency_reason=result.sufficiency_reason,
        median_delay_seconds=(
            result.delay_distribution.median_seconds
            if result.sufficient_history and result.delay_distribution.sufficient
            else None
        ),
        percentile_90_delay_seconds=(
            result.delay_distribution.percentile_90_seconds
            if result.sufficient_history and result.delay_distribution.sufficient
            else None
        ),
        observed_headway_seconds=(result.observed_headways.median_seconds if headways_sufficient else None),
        scheduled_headway_seconds=(result.scheduled_headways.median_seconds if headways_sufficient else None),
        headway_deviation_seconds=(result.median_headway_deviation_seconds if headways_sufficient else None),
        headway_variability_seconds=(result.observed_headways.variability_seconds if headways_sufficient else None),
        bunching_event_count=(result.observed_headways.bunching_event_count if headways_sufficient else None),
        service_gap_event_count=(result.observed_headways.service_gap_event_count if headways_sufficient else None),
    )


def _optional_delta(period_a: int | None, period_b: int | None) -> int | None:
    return period_b - period_a if period_a is not None and period_b is not None else None


@app.get(
    "/api/analytics/routes/{route_id}/compare",
    response_model=RouteComparisonResponse,
    tags=["analytics"],
)
def compare_route_reliability(
    route_id: str,
    feed: FeedDependency,
    session: SessionDependency,
    period_a_start: datetime = Query(...),
    period_a_end: datetime = Query(...),
    period_b_start: datetime = Query(...),
    period_b_end: datetime = Query(...),
) -> RouteComparisonResponse:
    """Compare two bounded route-history periods with explicit coverage safeguards."""

    result_a = route_reliability(route_id, feed, session, period_a_start, period_a_end)
    result_b = route_reliability(route_id, feed, session, period_b_start, period_b_end)
    period_a = _comparison_period("A", result_a)
    period_b = _comparison_period("B", result_b)
    requested_durations = [
        (result_a.end - result_a.start).total_seconds(),
        (result_b.end - result_b.start).total_seconds(),
    ]
    recorded_coverages = [period_a.coverage_duration_seconds, period_b.coverage_duration_seconds]
    requested_ratio = min(requested_durations) / max(requested_durations)
    recorded_ratio = (
        min(recorded_coverages) / max(recorded_coverages)
        if max(recorded_coverages) > 0
        else 0.0
    )
    coverage_ratio = min(requested_ratio, recorded_ratio)
    deltas = ComparisonDeltasResponse(
        median_delay_seconds=_optional_delta(period_a.median_delay_seconds, period_b.median_delay_seconds),
        percentile_90_delay_seconds=_optional_delta(
            period_a.percentile_90_delay_seconds, period_b.percentile_90_delay_seconds
        ),
        observed_headway_seconds=_optional_delta(
            period_a.observed_headway_seconds, period_b.observed_headway_seconds
        ),
        headway_variability_seconds=_optional_delta(
            period_a.headway_variability_seconds, period_b.headway_variability_seconds
        ),
        bunching_event_count=_optional_delta(
            period_a.bunching_event_count, period_b.bunching_event_count
        ),
        service_gap_event_count=_optional_delta(
            period_a.service_gap_event_count, period_b.service_gap_event_count
        ),
        observation_count=period_b.observation_count - period_a.observation_count,
        coverage_duration_seconds=(
            period_b.coverage_duration_seconds - period_a.coverage_duration_seconds
        ),
    )
    comparable = (
        period_a.sufficient_history
        and period_b.sufficient_history
        and coverage_ratio >= settings.analytics_comparison_min_coverage_ratio
    )
    if not period_a.sufficient_history or not period_b.sufficient_history:
        verdict = "The periods cannot be compared because one or both lack sufficient recorded history."
        better_period = None
        deciding_signals: list[str] = []
    elif coverage_ratio < settings.analytics_comparison_min_coverage_ratio:
        verdict = (
            "No winner: recorded coverage differs too much for a meaningful comparison "
            f"({coverage_ratio:.0%} coverage match)."
        )
        better_period = None
        deciding_signals = []
    else:
        signals = [
            ("median delay", period_a.median_delay_seconds, period_b.median_delay_seconds, True),
            ("P90 delay", period_a.percentile_90_delay_seconds, period_b.percentile_90_delay_seconds, True),
            ("headway deviation", period_a.headway_deviation_seconds, period_b.headway_deviation_seconds, True),
            ("headway variability", period_a.headway_variability_seconds, period_b.headway_variability_seconds, False),
            ("bunching events", period_a.bunching_event_count, period_b.bunching_event_count, False),
            ("service gaps", period_a.service_gap_event_count, period_b.service_gap_event_count, False),
        ]
        wins = {"A": 0, "B": 0}
        deciding_signals = []
        for label, raw_a, raw_b, use_absolute in signals:
            if raw_a is None or raw_b is None:
                continue
            score_a = abs(raw_a) if use_absolute else raw_a
            score_b = abs(raw_b) if use_absolute else raw_b
            if score_a == score_b:
                continue
            winner = "A" if score_a < score_b else "B"
            wins[winner] += 1
            unit = "events" if label in {"bunching events", "service gaps"} else "seconds"
            deciding_signals.append(
                f"Period {winner} performed better on {label} ({raw_a} vs {raw_b} {unit})."
            )
        if wins["A"] == wins["B"]:
            better_period = "tie"
            verdict = "Neither period clearly performed better across the comparable reliability signals."
        else:
            better_period = "A" if wins["A"] > wins["B"] else "B"
            verdict = (
                f"Period {better_period} performed better on {wins[better_period]} of "
                f"{wins['A'] + wins['B']} differing reliability signals."
            )
    return RouteComparisonResponse(
        route_id=route_id,
        static_feed_id=feed.id,
        period_a=period_a,
        period_b=period_b,
        deltas_b_minus_a=deltas,
        coverage_ratio=round(coverage_ratio, 3),
        comparable=comparable,
        better_period=better_period,
        verdict=verdict,
        deciding_signals=deciding_signals,
    )


def route_for_feed(session: Session, feed: GtfsFeed, route_id: str) -> tuple[Route, Agency]:
    route = session.execute(
        select(Route, Agency)
        .join(Agency, Route.agency_id == Agency.id)
        .where(Route.feed_id == feed.id, Route.gtfs_route_id == route_id)
    ).one_or_none()
    if route is None:
        raise HTTPException(status_code=404, detail=f"Route {route_id!r} was not found in this feed")
    return route


def route_summary(route: Route, agency: Agency) -> RouteSummaryResponse:
    return RouteSummaryResponse(
        route_id=route.gtfs_route_id,
        agency_id=agency.gtfs_agency_id,
        agency_name=agency.name,
        short_name=route.short_name,
        long_name=route.long_name,
        route_type=route.route_type,
        color=route.color,
        text_color=route.text_color,
    )


@app.get("/api/agencies", response_model=list[AgencyResponse], tags=["network"])
def agencies(feed: FeedDependency, session: SessionDependency) -> list[AgencyResponse]:
    rows = session.scalars(
        select(Agency).where(Agency.feed_id == feed.id).order_by(Agency.name)
    ).all()
    return [
        AgencyResponse(
            agency_id=agency.gtfs_agency_id,
            name=agency.name,
            url=agency.url,
            timezone=agency.timezone,
        )
        for agency in rows
    ]


@app.get("/api/routes", response_model=list[RouteSummaryResponse], tags=["network"])
def routes(feed: FeedDependency, session: SessionDependency) -> list[RouteSummaryResponse]:
    rows = session.execute(
        select(Route, Agency)
        .join(Agency, Route.agency_id == Agency.id)
        .where(Route.feed_id == feed.id)
        .order_by(Route.sort_order.nulls_last(), Route.short_name, Route.long_name, Route.gtfs_route_id)
    ).all()
    return [route_summary(route, agency) for route, agency in rows]


@app.get("/api/routes/{route_id}", response_model=RouteDetailResponse, tags=["network"])
def route_detail(route_id: str, feed: FeedDependency, session: SessionDependency) -> RouteDetailResponse:
    route, agency = route_for_feed(session, feed, route_id)
    return RouteDetailResponse(
        **route_summary(route, agency).model_dump(),
        description=route.description,
        url=route.url,
        sort_order=route.sort_order,
        feed_id=feed.id,
    )


@app.get(
    "/api/routes/{route_id}/stops",
    response_model=StopFeatureCollection,
    tags=["network"],
)
def route_stops(route_id: str, feed: FeedDependency, session: SessionDependency) -> StopFeatureCollection:
    route, _ = route_for_feed(session, feed, route_id)
    rows = session.execute(
        select(
            Stop,
            func.ST_X(Stop.location).label("longitude"),
            func.ST_Y(Stop.location).label("latitude"),
        )
        .join(StopTime, StopTime.stop_id == Stop.id)
        .join(Trip, Trip.id == StopTime.trip_id)
        .where(Trip.route_id == route.id)
        .group_by(Stop.id)
        .order_by(func.min(StopTime.stop_sequence), Stop.name)
    ).all()
    return StopFeatureCollection(
        feed_id=feed.id,
        features=[
            StopFeature(
                geometry={"type": "Point", "coordinates": [float(longitude), float(latitude)]},
                properties=StopProperties(
                    stop_id=stop.gtfs_stop_id,
                    code=stop.code,
                    name=stop.name,
                    description=stop.description,
                    wheelchair_boarding=stop.wheelchair_boarding,
                ),
            )
            for stop, longitude, latitude in rows
        ],
    )


@app.get(
    "/api/routes/{route_id}/shape",
    response_model=ShapeFeatureCollection,
    tags=["network"],
)
def route_shape(route_id: str, feed: FeedDependency, session: SessionDependency) -> ShapeFeatureCollection:
    route, _ = route_for_feed(session, feed, route_id)
    rows = session.execute(
        select(Shape.gtfs_shape_id, func.ST_AsGeoJSON(Shape.geometry))
        .join(Trip, Trip.shape_id == Shape.id)
        .where(Trip.route_id == route.id)
        .distinct()
        .order_by(Shape.gtfs_shape_id)
    ).all()
    return ShapeFeatureCollection(
        feed_id=feed.id,
        features=[
            ShapeFeature(
                geometry=json.loads(geometry),
                properties=ShapeProperties(
                    shape_id=shape_id,
                    route_id=route.gtfs_route_id,
                    color=route.color,
                ),
            )
            for shape_id, geometry in rows
        ],
    )


@app.get("/api/network/shapes", response_model=ShapeFeatureCollection, tags=["network"])
def network_shapes(feed: FeedDependency, session: SessionDependency) -> ShapeFeatureCollection:
    """Return the full feed's route geometry once for the map's background layer."""

    rows = session.execute(
        select(
            Shape.gtfs_shape_id,
            func.ST_AsGeoJSON(Shape.geometry),
            Route.gtfs_route_id,
            Route.color,
        )
        .join(Trip, Trip.shape_id == Shape.id)
        .join(Route, Trip.route_id == Route.id)
        .where(Route.feed_id == feed.id)
        .distinct()
        .order_by(Route.gtfs_route_id, Shape.gtfs_shape_id)
    ).all()
    return ShapeFeatureCollection(
        feed_id=feed.id,
        features=[
            ShapeFeature(
                geometry=json.loads(geometry),
                properties=ShapeProperties(
                    shape_id=shape_id, route_id=route_id, color=color
                ),
            )
            for shape_id, geometry, route_id, color in rows
        ],
    )


@app.get("/api/stops/{stop_id}", response_model=StopFeature, tags=["network"])
def stop_detail(stop_id: str, feed: FeedDependency, session: SessionDependency) -> StopFeature:
    row = session.execute(
        select(Stop, func.ST_X(Stop.location), func.ST_Y(Stop.location)).where(
            Stop.feed_id == feed.id, Stop.gtfs_stop_id == stop_id
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Stop {stop_id!r} was not found in this feed")
    stop, longitude, latitude = row
    return StopFeature(
        geometry=(
            {"type": "Point", "coordinates": [float(longitude), float(latitude)]}
            if longitude is not None and latitude is not None
            else None
        ),
        properties=StopProperties(
            stop_id=stop.gtfs_stop_id,
            code=stop.code,
            name=stop.name,
            description=stop.description,
            wheelchair_boarding=stop.wheelchair_boarding,
        ),
    )
