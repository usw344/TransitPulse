"""Pydantic response models for the static transit API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class AgencyResponse(BaseModel):
    agency_id: str
    name: str
    url: str
    timezone: str


class RouteSummaryResponse(BaseModel):
    route_id: str
    agency_id: str
    agency_name: str
    short_name: str | None
    long_name: str | None
    route_type: int
    color: str | None
    text_color: str | None


class RouteDetailResponse(RouteSummaryResponse):
    description: str | None
    url: str | None
    sort_order: int | None
    feed_id: UUID


class StopProperties(BaseModel):
    stop_id: str
    code: str | None
    name: str
    description: str | None
    wheelchair_boarding: int | None


class StopFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: StopProperties


class StopFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[StopFeature]
    feed_id: UUID


class ShapeProperties(BaseModel):
    shape_id: str
    route_id: str
    color: str | None = None


class ShapeFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: ShapeProperties


class ShapeFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[ShapeFeature]
    feed_id: UUID


class RealtimeFeedHealthResponse(BaseModel):
    feed_kind: str
    source_url: str
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    source_timestamp: datetime | None
    entity_count: int
    error_message: str | None
    stale: bool


class RealtimeStatusResponse(BaseModel):
    static_feed_id: UUID | None
    active_vehicles: int
    feeds: list[RealtimeFeedHealthResponse]
    stale: bool


class RealtimeVehicleProperties(BaseModel):
    vehicle_id: str
    trip_id: str | None
    route_id: str | None
    bearing: float | None
    speed: float | None
    timestamp: datetime | None
    current_stop_sequence: int | None
    current_status: str | None
    schedule_relationship: str | None
    delay_seconds: int | None


class RealtimeVehicleFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: RealtimeVehicleProperties


class RealtimeVehicleFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    feed_id: UUID | None
    features: list[RealtimeVehicleFeature]
    source_timestamp: datetime | None
    stale: bool


class RealtimeAlertResponse(BaseModel):
    id: str
    header: str | None
    description: str | None
    url: str | None
    cause: str | None
    effect: str | None
    affected_routes: list[str]
    affected_stops: list[str]
    updated_at: datetime


class RouteOperationsResponse(BaseModel):
    route_id: str
    active_vehicles: int
    service_status: str
    status_reason: str
    average_delay_seconds: int | None
    delayed_vehicle_count: int
    alert_count: int
    prediction_stop_id: str | None
    predicted_headways_seconds: list[int]
    headway_baseline_seconds: int | None
    bunching: bool
    service_gap: bool


class HistoryVehicleObservationProperties(BaseModel):
    observation_id: int
    static_feed_id: UUID
    vehicle_id: str
    trip_id: str | None
    route_id: str | None
    observed_at: datetime | None
    source_timestamp: datetime | None
    recorded_at: datetime
    bearing: float | None
    speed: float | None
    current_stop_sequence: int | None
    current_status: str | None
    schedule_relationship: str | None
    delay_seconds: int | None


class HistoryVehicleObservationFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: HistoryVehicleObservationProperties


class HistoryVehicleObservationCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[HistoryVehicleObservationFeature]
    start: datetime
    end: datetime
    limit: int
