"""Response models for recorded route reliability analytics."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class DelayDistributionResponse(BaseModel):
    source: Literal["direct_recorded_observations"]
    sample_count: int
    median_seconds: int | None
    percentile_10_seconds: int | None
    percentile_90_seconds: int | None
    minimum_seconds: int | None
    maximum_seconds: int | None
    sufficient: bool


class DelayBandsResponse(BaseModel):
    source: Literal["direct_recorded_observations"]
    sample_count: int
    early_count: int
    on_time_count: int
    late_under_5_count: int
    late_5_to_10_count: int
    late_over_10_count: int


class HeadwayResponse(BaseModel):
    source: Literal["direct_recorded_stop_sequence_entries", "static_gtfs_schedule"]
    stop_id: str | None
    stop_name: str | None
    sample_count: int
    median_seconds: int | None
    baseline_seconds: int | None
    bunching_event_count: int
    service_gap_event_count: int
    variability_seconds: int | None
    sufficient: bool


class ReliabilityTimeBucketResponse(BaseModel):
    source: Literal["direct_recorded_observations"]
    start: datetime
    end: datetime
    observation_count: int
    delay_observation_count: int
    median_delay_seconds: int | None
    late_observation_count: int


class ReliabilityStopProperties(BaseModel):
    stop_id: str
    name: str
    source: Literal["direct_recorded_stop_sequence_entries"]
    observation_count: int
    delay_observation_count: int
    median_delay_seconds: int | None
    late_observation_count: int
    reliability_status: Literal["good", "moderate", "poor", "unknown"]


class ReliabilityStopFeature(BaseModel):
    type: Literal["Feature"] = "Feature"
    geometry: dict[str, Any]
    properties: ReliabilityStopProperties


class ReliabilityStopFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    source: Literal["direct_recorded_stop_sequence_entries"]
    features: list[ReliabilityStopFeature]


class RouteReliabilityResponse(BaseModel):
    route_id: str
    static_feed_id: UUID
    start: datetime
    end: datetime
    observation_count: int
    observed_vehicle_count: int
    coverage_duration_seconds: int
    sufficient_history: bool
    sufficiency_reason: str
    data_notes: list[str]
    delay_distribution: DelayDistributionResponse
    delay_bands: DelayBandsResponse
    observed_headways: HeadwayResponse
    scheduled_headways: HeadwayResponse
    median_headway_deviation_seconds: int | None
    best_period_start: datetime | None
    worst_period_start: datetime | None
    through_time: list[ReliabilityTimeBucketResponse]
    spatial_reliability: ReliabilityStopFeatureCollection


class ComparisonPeriodResponse(BaseModel):
    label: Literal["A", "B"]
    start: datetime
    end: datetime
    observation_count: int
    observed_vehicle_count: int
    coverage_duration_seconds: int
    sufficient_history: bool
    sufficiency_reason: str
    median_delay_seconds: int | None
    percentile_90_delay_seconds: int | None
    observed_headway_seconds: int | None
    scheduled_headway_seconds: int | None
    headway_deviation_seconds: int | None
    headway_variability_seconds: int | None
    bunching_event_count: int | None
    service_gap_event_count: int | None


class ComparisonDeltasResponse(BaseModel):
    """Period B minus period A; positive delay/variability/event deltas are worse."""

    median_delay_seconds: int | None
    percentile_90_delay_seconds: int | None
    observed_headway_seconds: int | None
    headway_variability_seconds: int | None
    bunching_event_count: int | None
    service_gap_event_count: int | None
    observation_count: int
    coverage_duration_seconds: int


class RouteComparisonResponse(BaseModel):
    route_id: str
    static_feed_id: UUID
    period_a: ComparisonPeriodResponse
    period_b: ComparisonPeriodResponse
    deltas_b_minus_a: ComparisonDeltasResponse
    coverage_ratio: float
    comparable: bool
    better_period: Literal["A", "B", "tie"] | None
    verdict: str
    deciding_signals: list[str]
