"""Pydantic response models for the static transit API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


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
    geometry: dict[str, Any] | None
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
    active_periods: list[dict[str, str | None]]
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
    #: Predicted arrivals excluded from the headway as non-comparable service.
    excluded_arrivals_beyond_horizon: int = 0
    prediction_horizon_seconds: int | None = None


class NetworkRouteStatusResponse(BaseModel):
    route_id: str
    short_name: str | None
    long_name: str | None
    service_status: str
    status_reason: str
    active_vehicles: int
    average_delay_seconds: int | None
    delayed_vehicle_count: int
    alert_count: int
    prediction_stop_id: str | None
    predicted_headways_seconds: list[int]
    headway_baseline_seconds: int | None
    bunching: bool
    service_gap: bool
    #: Predicted arrivals excluded from the headway as non-comparable service.
    excluded_arrivals_beyond_horizon: int = 0
    prediction_horizon_seconds: int | None = None
    attention_score: int


class NetworkHealthResponse(BaseModel):
    static_feed_id: UUID
    generated_at: datetime
    stale: bool
    active_vehicles: int
    routes_with_live_service: int
    routes_delayed: int
    routes_with_bunching: int
    routes_with_service_gaps: int
    active_alerts: int
    median_network_delay_seconds: int | None
    routes: list[NetworkRouteStatusResponse]
    issues: list[NetworkRouteStatusResponse]


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
    static_feed_id: UUID
    features: list[HistoryVehicleObservationFeature]
    start: datetime
    end: datetime
    limit: int


class HistoryAvailabilityResponse(BaseModel):
    """The bounded recorded range available for replay on one static feed."""

    static_feed_id: UUID
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    observation_count: int


class ScenarioEstimateRequest(BaseModel):
    """A proposed change to one route direction.

    Every field is optional and omitting one means "leave it as scheduled".
    That keeps the contract additive: a planner sends only what they changed,
    and an empty request is a valid question whose answer is today's schedule.
    """

    key: str = Field(description="Route key from /api/scenarios/routes")
    one_way_length_km: float | None = Field(default=None, gt=0, le=200)
    stop_count: int | None = Field(default=None, ge=2, le=400)
    day_type: Literal["weekday", "saturday", "sunday"] | None = None
    peak_headway_minutes: float | None = Field(default=None, gt=0, le=240)
    offpeak_headway_minutes: float | None = Field(default=None, gt=0, le=240)
    median_headway_minutes: float | None = Field(default=None, gt=0, le=240)
    service_span_hours: float | None = Field(default=None, gt=0, le=24.5)
    recovery_fraction: float | None = Field(default=None, ge=0, le=1)

    def changes(self) -> dict[str, object]:
        """Only the fields the caller actually set."""

        return {
            name: value
            for name, value in self.model_dump(exclude={"key"}).items()
            if value is not None
        }
