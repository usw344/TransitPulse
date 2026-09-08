"""FastAPI surface for versioned static Edmonton transit network data."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from transitpulse_api.api_models import (
    AgencyResponse,
    HistoryVehicleObservationCollection,
    HistoryVehicleObservationFeature,
    HistoryVehicleObservationProperties,
    RealtimeAlertResponse,
    RealtimeFeedHealthResponse,
    RealtimeStatusResponse,
    RealtimeVehicleFeature,
    RealtimeVehicleFeatureCollection,
    RealtimeVehicleProperties,
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
from transitpulse_api.config import settings
from transitpulse_api.database import get_engine, get_session
from transitpulse_api.models import (
    Agency,
    GtfsFeed,
    RealtimeAlert,
    RealtimeFeedStatus,
    RealtimeTripState,
    RealtimeVehicleState,
    Route,
    Shape,
    Stop,
    StopTime,
    Trip,
    VehicleObservation,
)
from transitpulse_api.operations import OperationsThresholds, calculate_headways, classify_service

app = FastAPI(title="TransitPulse API", version="0.4.0")
SessionDependency = Annotated[Session, Depends(get_session)]


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


def history_collection(
    session: Session,
    *,
    start: datetime,
    end: datetime,
    limit: int,
    vehicle_id: str | None = None,
    route_id: str | None = None,
    feed_id: UUID | None = None,
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
            observed_time >= start,
            observed_time < end,
        )
        .order_by(observed_time.asc(), VehicleObservation.id.asc())
        .limit(limit)
    )
    if vehicle_id is not None:
        statement = statement.where(VehicleObservation.vehicle_id == vehicle_id)
    if route_id is not None:
        statement = statement.where(VehicleObservation.route_gtfs_id == route_id)
    if feed_id is not None:
        statement = statement.where(VehicleObservation.static_feed_id == feed_id)
    rows = session.execute(statement).all()
    return HistoryVehicleObservationCollection(
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


@app.get(
    "/api/history/vehicles/{vehicle_id}",
    response_model=HistoryVehicleObservationCollection,
    tags=["history"],
)
def vehicle_history(
    vehicle_id: str,
    session: SessionDependency,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    feed_id: UUID | None = Query(default=None),
) -> HistoryVehicleObservationCollection:
    window_start, window_end = history_window(start, end)
    return history_collection(
        session,
        start=window_start,
        end=window_end,
        limit=limit,
        vehicle_id=vehicle_id,
        feed_id=feed_id,
    )


@app.get(
    "/api/history/routes/{route_id}",
    response_model=HistoryVehicleObservationCollection,
    tags=["history"],
)
def route_history(
    route_id: str,
    session: SessionDependency,
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=5000),
    feed_id: UUID | None = Query(default=None),
) -> HistoryVehicleObservationCollection:
    window_start, window_end = history_window(start, end)
    return history_collection(
        session,
        start=window_start,
        end=window_end,
        limit=limit,
        route_id=route_id,
        feed_id=feed_id,
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
        geometry={"type": "Point", "coordinates": [float(longitude), float(latitude)]},
        properties=StopProperties(
            stop_id=stop.gtfs_stop_id,
            code=stop.code,
            name=stop.name,
            description=stop.description,
            wheelchair_boarding=stop.wheelchair_boarding,
        ),
    )
