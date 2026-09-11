"""Relational GTFS domain models, all scoped to an immutable imported feed."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID, uuid4

from geoalchemy2 import Geometry
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class GtfsFeed(Base):
    __tablename__ = "gtfs_feeds"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(255))
    source_url: Mapped[str] = mapped_column(Text)
    checksum_sha256: Mapped[str] = mapped_column(String(64), unique=True)
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    feed_start_date: Mapped[date | None] = mapped_column(Date)
    feed_end_date: Mapped[date | None] = mapped_column(Date)
    import_status: Mapped[str] = mapped_column(String(32), default="succeeded")
    import_counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    agencies: Mapped[list[Agency]] = relationship(
        back_populates="feed", cascade="all, delete-orphan"
    )


class Agency(Base):
    __tablename__ = "agencies"
    __table_args__ = (
        UniqueConstraint("feed_id", "gtfs_agency_id", name="uq_agencies_feed_gtfs_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="CASCADE"), index=True
    )
    gtfs_agency_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    url: Mapped[str] = mapped_column(Text)
    timezone: Mapped[str] = mapped_column(String(64))
    lang: Mapped[str | None] = mapped_column(String(32))
    phone: Mapped[str | None] = mapped_column(String(64))
    fare_url: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(String(255))

    feed: Mapped[GtfsFeed] = relationship(back_populates="agencies")
    routes: Mapped[list[Route]] = relationship(back_populates="agency")


class Route(Base):
    __tablename__ = "routes"
    __table_args__ = (
        UniqueConstraint("feed_id", "gtfs_route_id", name="uq_routes_feed_gtfs_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="CASCADE"), index=True
    )
    agency_id: Mapped[int] = mapped_column(ForeignKey("agencies.id"), index=True)
    gtfs_route_id: Mapped[str] = mapped_column(String(255))
    short_name: Mapped[str | None] = mapped_column(String(128))
    long_name: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    route_type: Mapped[int] = mapped_column(SmallInteger)
    url: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(6))
    text_color: Mapped[str | None] = mapped_column(String(6))
    sort_order: Mapped[int | None] = mapped_column(Integer)

    agency: Mapped[Agency] = relationship(back_populates="routes")
    trips: Mapped[list[Trip]] = relationship(back_populates="route")


class Stop(Base):
    __tablename__ = "stops"
    __table_args__ = (
        UniqueConstraint("feed_id", "gtfs_stop_id", name="uq_stops_feed_gtfs_id"),
        Index("ix_stops_location", "location", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="CASCADE"), index=True
    )
    gtfs_stop_id: Mapped[str] = mapped_column(String(255))
    code: Mapped[str | None] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    location_type: Mapped[int] = mapped_column(SmallInteger, default=0)
    parent_station_gtfs_id: Mapped[str | None] = mapped_column(String(255))
    timezone: Mapped[str | None] = mapped_column(String(64))
    wheelchair_boarding: Mapped[int | None] = mapped_column(SmallInteger)
    location: Mapped[str | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )

    stop_times: Mapped[list[StopTime]] = relationship(back_populates="stop")


class ServiceCalendar(Base):
    __tablename__ = "service_calendars"
    __table_args__ = (
        UniqueConstraint("feed_id", "gtfs_service_id", name="uq_calendars_feed_gtfs_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="CASCADE"), index=True
    )
    gtfs_service_id: Mapped[str] = mapped_column(String(255))
    monday: Mapped[bool] = mapped_column(Boolean, default=False)
    tuesday: Mapped[bool] = mapped_column(Boolean, default=False)
    wednesday: Mapped[bool] = mapped_column(Boolean, default=False)
    thursday: Mapped[bool] = mapped_column(Boolean, default=False)
    friday: Mapped[bool] = mapped_column(Boolean, default=False)
    saturday: Mapped[bool] = mapped_column(Boolean, default=False)
    sunday: Mapped[bool] = mapped_column(Boolean, default=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)

    trips: Mapped[list[Trip]] = relationship(back_populates="service")
    dates: Mapped[list[CalendarDate]] = relationship(
        back_populates="service", cascade="all, delete-orphan"
    )


class CalendarDate(Base):
    __tablename__ = "calendar_dates"
    __table_args__ = (
        UniqueConstraint("service_id", "date", name="uq_calendar_dates_service_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    service_id: Mapped[int] = mapped_column(
        ForeignKey("service_calendars.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date] = mapped_column(Date)
    exception_type: Mapped[int] = mapped_column(SmallInteger)

    service: Mapped[ServiceCalendar] = relationship(back_populates="dates")


class Shape(Base):
    __tablename__ = "shapes"
    __table_args__ = (
        UniqueConstraint("feed_id", "gtfs_shape_id", name="uq_shapes_feed_gtfs_id"),
        Index("ix_shapes_geometry", "geometry", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="CASCADE"), index=True
    )
    gtfs_shape_id: Mapped[str] = mapped_column(String(255))
    geometry: Mapped[str] = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=False), nullable=False
    )

    trips: Mapped[list[Trip]] = relationship(back_populates="shape")


class Trip(Base):
    __tablename__ = "trips"
    __table_args__ = (
        UniqueConstraint("feed_id", "gtfs_trip_id", name="uq_trips_feed_gtfs_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="CASCADE"), index=True
    )
    route_id: Mapped[int] = mapped_column(ForeignKey("routes.id"), index=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("service_calendars.id"), index=True)
    shape_id: Mapped[int | None] = mapped_column(ForeignKey("shapes.id"), index=True)
    gtfs_trip_id: Mapped[str] = mapped_column(String(255))
    headsign: Mapped[str | None] = mapped_column(String(255))
    short_name: Mapped[str | None] = mapped_column(String(255))
    direction_id: Mapped[int | None] = mapped_column(SmallInteger)
    block_id: Mapped[str | None] = mapped_column(String(255))
    wheelchair_accessible: Mapped[int | None] = mapped_column(SmallInteger)
    bikes_allowed: Mapped[int | None] = mapped_column(SmallInteger)

    route: Mapped[Route] = relationship(back_populates="trips")
    service: Mapped[ServiceCalendar] = relationship(back_populates="trips")
    shape: Mapped[Shape | None] = relationship(back_populates="trips")
    stop_times: Mapped[list[StopTime]] = relationship(
        back_populates="trip", cascade="all, delete-orphan"
    )


class StopTime(Base):
    __tablename__ = "stop_times"
    __table_args__ = (
        UniqueConstraint("trip_id", "stop_sequence", name="uq_stop_times_trip_sequence"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    trip_id: Mapped[int] = mapped_column(
        ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    stop_id: Mapped[int] = mapped_column(ForeignKey("stops.id"), index=True)
    arrival_time: Mapped[str | None] = mapped_column(String(16))
    departure_time: Mapped[str | None] = mapped_column(String(16))
    arrival_seconds: Mapped[int | None] = mapped_column(Integer)
    departure_seconds: Mapped[int | None] = mapped_column(Integer)
    stop_sequence: Mapped[int] = mapped_column(Integer)
    headsign: Mapped[str | None] = mapped_column(String(255))
    pickup_type: Mapped[int | None] = mapped_column(SmallInteger)
    drop_off_type: Mapped[int | None] = mapped_column(SmallInteger)
    timepoint: Mapped[int | None] = mapped_column(SmallInteger)

    trip: Mapped[Trip] = relationship(back_populates="stop_times")
    stop: Mapped[Stop] = relationship(back_populates="stop_times")


class RealtimeFeedStatus(Base):
    """Latest fetch health for one official GTFS-Realtime feed."""

    __tablename__ = "realtime_feed_statuses"

    feed_kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    static_feed_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="SET NULL"), index=True
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    entity_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class RealtimeVehicleState(Base):
    """One latest-known vehicle state per static feed and vehicle identifier."""

    __tablename__ = "realtime_vehicle_states"
    __table_args__ = (
        UniqueConstraint("static_feed_id", "vehicle_id", name="uq_rt_vehicle_state_feed_vehicle"),
        Index("ix_rt_vehicle_state_route", "static_feed_id", "route_gtfs_id"),
        Index("ix_rt_vehicle_state_position", "position", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    static_feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    vehicle_id: Mapped[str] = mapped_column(String(255), nullable=False)
    trip_gtfs_id: Mapped[str | None] = mapped_column(String(255))
    route_gtfs_id: Mapped[str | None] = mapped_column(String(255))
    matched_trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id"), index=True)
    matched_route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id"), index=True)
    position: Mapped[str | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    bearing: Mapped[float | None] = mapped_column()
    speed: Mapped[float | None] = mapped_column()
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_stop_sequence: Mapped[int | None] = mapped_column(Integer)
    current_status: Mapped[str | None] = mapped_column(String(64))
    schedule_relationship: Mapped[str | None] = mapped_column(String(64))
    delay_seconds: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RealtimeTripState(Base):
    """Latest useful trip-update values, preserved separately from vehicle positions."""

    __tablename__ = "realtime_trip_states"
    __table_args__ = (
        UniqueConstraint("static_feed_id", "trip_gtfs_id", name="uq_rt_trip_state_feed_trip"),
        Index("ix_rt_trip_state_route", "static_feed_id", "route_gtfs_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    static_feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    trip_gtfs_id: Mapped[str] = mapped_column(String(255), nullable=False)
    route_gtfs_id: Mapped[str | None] = mapped_column(String(255))
    matched_trip_id: Mapped[int | None] = mapped_column(ForeignKey("trips.id"), index=True)
    matched_route_id: Mapped[int | None] = mapped_column(ForeignKey("routes.id"), index=True)
    schedule_relationship: Mapped[str | None] = mapped_column(String(64))
    delay_seconds: Mapped[int | None] = mapped_column(Integer)
    next_stop_id: Mapped[str | None] = mapped_column(String(255))
    next_stop_sequence: Mapped[int | None] = mapped_column(Integer)
    next_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RealtimeAlert(Base):
    """Latest active alert as published by the official feed."""

    __tablename__ = "realtime_alerts"
    __table_args__ = (Index("ix_rt_alert_feed", "static_feed_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    static_feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)
    header: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    cause: Mapped[str | None] = mapped_column(String(64))
    effect: Mapped[str | None] = mapped_column(String(64))
    active_periods: Mapped[list[dict[str, str | None]]] = mapped_column(JSON, default=list)
    affected_routes: Mapped[list[str]] = mapped_column(JSON, default=list)
    affected_stops: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VehicleObservation(Base):
    """Immutable recorded vehicle state, always tied to its static GTFS version."""

    __tablename__ = "vehicle_observations"
    __table_args__ = (
        UniqueConstraint("observation_key", name="uq_vehicle_observations_key"),
        Index("ix_vehicle_observation_vehicle_time", "vehicle_id", "observed_at"),
        Index("ix_vehicle_observation_route_time", "route_gtfs_id", "observed_at"),
        Index("ix_vehicle_observation_feed_time", "static_feed_id", "observed_at"),
        Index("ix_vehicle_observation_position", "position", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    observation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    static_feed_id: Mapped[UUID] = mapped_column(
        ForeignKey("gtfs_feeds.id", ondelete="RESTRICT"), nullable=False
    )
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    vehicle_id: Mapped[str] = mapped_column(String(255), nullable=False)
    trip_gtfs_id: Mapped[str | None] = mapped_column(String(255))
    route_gtfs_id: Mapped[str | None] = mapped_column(String(255))
    position: Mapped[str | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    bearing: Mapped[float | None] = mapped_column()
    speed: Mapped[float | None] = mapped_column()
    current_stop_sequence: Mapped[int | None] = mapped_column(Integer)
    current_status: Mapped[str | None] = mapped_column(String(64))
    schedule_relationship: Mapped[str | None] = mapped_column(String(64))
    delay_seconds: Mapped[int | None] = mapped_column(Integer)
