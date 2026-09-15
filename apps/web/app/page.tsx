"use client";

import "maplibre-gl/dist/maplibre-gl.css";

import {
  AttributionControl,
  LngLatBounds,
  Map,
  Popup,
  type GeoJSONSource,
  setWorkerUrl,
} from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Feature, FeatureCollection, LineString, Point } from "geojson";

import { canDisplayReliabilityEstimates } from "./analytics";
import { API_NOT_RESPONDING, fetchJson } from "./format";
import { wholeVehicles } from "./scenarios/ScenarioPanels";
import { MapControls, type MapLayerToggles } from "./MapControls";
import { transitPulseBasemapStyle } from "./mapStyle";
import {
  ROUTE_COLORS,
  VEHICLE_COLORS,
  networkLineOpacity,
  networkLineWidth,
  selectedRouteCasingWidth,
  selectedRouteWidth,
  stopCasingRadius,
  stopRadius,
  vehicleColorExpression,
  vehicleIcon,
  vehicleIconSize,
} from "./mapSymbols";
import { ScenarioControls } from "./scenarios/ScenarioControls";
import { CurrentPlanPanel, EstimatePanel, ModelCardPanel } from "./scenarios/ScenarioPanels";
import { useScenarios } from "./scenarios/useScenarios";
import {
  type ReplayObservation,
  advanceReplayTimestamp,
  clampReplayTimestamp,
  replayCoverageBuckets,
  replayFrameAt,
  replayTimestampAtFraction,
  vehicleDataForMode,
} from "./replay";

type AppMode = "live" | "replay" | "analytics" | "scenarios";

/**
 * Minimum adjacent-headway intervals before derived bunching/gap/variability
 * counts are shown.  Below this there is insufficient evidence for a reliable
 * finding.
 */
const MIN_HEADWAY_EVENT_SAMPLES = 8;

/** The primary product modes, each stated as the question it answers. */
const MODES: ReadonlyArray<{ id: AppMode; label: string; question: string }> = [
  { id: "live", label: "Live", question: "What is happening now?" },
  { id: "replay", label: "Replay", question: "What happened earlier?" },
  { id: "analytics", label: "Analytics", question: "How reliable was service?" },
  { id: "scenarios", label: "Scenarios", question: "What if we changed the design?" },
];

type ServiceStatus = "loading" | "online" | "offline";
type NetworkState = "loading" | "ready" | "empty" | "error";

interface RouteSummary {
  route_id: string;
  agency_id: string;
  agency_name: string;
  short_name: string | null;
  long_name: string | null;
  route_type: number;
  color: string | null;
  text_color: string | null;
}

interface RouteDetail extends RouteSummary {
  description: string | null;
  url: string | null;
  sort_order: number | null;
  feed_id: string;
}

interface GeoJsonFeatureCollection {
  type: "FeatureCollection";
  features: Feature[];
  feed_id: string;
}

interface RealtimeStatus {
  active_vehicles: number;
  stale: boolean;
  feeds: Array<{
    feed_kind: string;
    last_success_at: string | null;
    source_timestamp: string | null;
    entity_count: number;
    error_message: string | null;
    stale: boolean;
  }>;
}

interface VehicleProperties {
  vehicle_id: string;
  trip_id: string | null;
  route_id: string | null;
  bearing: number | null;
  speed: number | null;
  timestamp: string | null;
  current_stop_sequence: number | null;
  current_status: string | null;
  schedule_relationship: string | null;
  delay_seconds: number | null;
}

interface RealtimeVehicleCollection extends GeoJsonFeatureCollection {
  features: Array<Feature<Point, VehicleProperties>>;
  source_timestamp: string | null;
  stale: boolean;
}

interface HistoricalVehicleProperties {
  observation_id: number;
  static_feed_id: string;
  vehicle_id: string;
  trip_id: string | null;
  route_id: string | null;
  observed_at: string | null;
  source_timestamp: string | null;
  recorded_at: string;
  bearing: number | null;
  speed: number | null;
  current_stop_sequence: number | null;
  current_status: string | null;
  schedule_relationship: string | null;
  delay_seconds: number | null;
}

interface HistoryVehicleObservationCollection {
  type: "FeatureCollection";
  static_feed_id: string;
  features: Array<Feature<Point, HistoricalVehicleProperties>>;
  start: string;
  end: string;
  limit: number;
}

interface HistoryAvailability {
  static_feed_id: string;
  first_observed_at: string | null;
  last_observed_at: string | null;
  observation_count: number;
}

interface RouteOperations {
  route_id: string;
  active_vehicles: number;
  service_status: string;
  status_reason: string;
  average_delay_seconds: number | null;
  delayed_vehicle_count: number;
  alert_count: number;
  prediction_stop_id: string | null;
  predicted_headways_seconds: number[];
  headway_baseline_seconds: number | null;
  bunching: boolean;
  service_gap: boolean;
  excluded_arrivals_beyond_horizon: number;
  prediction_horizon_seconds: number | null;
}

interface RealtimeAlert {
  id: string;
  header: string | null;
  description: string | null;
  cause: string | null;
  effect: string | null;
  active_periods: Array<{ start: string | null; end: string | null }>;
  affected_routes: string[];
}

interface NetworkRouteStatus {
  route_id: string;
  short_name: string | null;
  long_name: string | null;
  service_status: string;
  status_reason: string;
  active_vehicles: number;
  average_delay_seconds: number | null;
  delayed_vehicle_count: number;
  alert_count: number;
  prediction_stop_id: string | null;
  predicted_headways_seconds: number[];
  headway_baseline_seconds: number | null;
  bunching: boolean;
  service_gap: boolean;
  attention_score: number;
}

interface NetworkHealth {
  static_feed_id: string;
  generated_at: string;
  stale: boolean;
  active_vehicles: number;
  routes_with_live_service: number;
  routes_delayed: number;
  routes_with_bunching: number;
  routes_with_service_gaps: number;
  active_alerts: number;
  median_network_delay_seconds: number | null;
  routes: NetworkRouteStatus[];
  issues: NetworkRouteStatus[];
}

interface ErrorMetrics {
  n: number;
  mae: number;
  median_ae: number;
  p90_ae: number;
  rmse: number;
}

interface ReliabilityStopProperties {
  stop_id: string;
  name: string;
  source: "direct_recorded_stop_sequence_entries";
  observation_count: number;
  delay_observation_count: number;
  median_delay_seconds: number | null;
  late_observation_count: number;
  reliability_status: "good" | "moderate" | "poor" | "unknown";
}

interface ReliabilityStopCollection {
  type: "FeatureCollection";
  source: "direct_recorded_stop_sequence_entries";
  features: Array<Feature<Point, ReliabilityStopProperties>>;
}

interface RouteReliability {
  route_id: string;
  static_feed_id: string;
  start: string;
  end: string;
  observation_count: number;
  observed_vehicle_count: number;
  coverage_duration_seconds: number;
  sufficient_history: boolean;
  sufficiency_reason: string;
  data_notes: string[];
  delay_distribution: {
    source: "direct_recorded_observations";
    sample_count: number;
    median_seconds: number | null;
    percentile_10_seconds: number | null;
    percentile_90_seconds: number | null;
    minimum_seconds: number | null;
    maximum_seconds: number | null;
    sufficient: boolean;
  };
  delay_bands: {
    source: "direct_recorded_observations";
    sample_count: number;
    early_count: number;
    on_time_count: number;
    late_under_5_count: number;
    late_5_to_10_count: number;
    late_over_10_count: number;
  };
  observed_headways: {
    direction_id: number | null;
    source: "direct_recorded_stop_sequence_entries";
    stop_id: string | null;
    stop_name: string | null;
    sample_count: number;
    median_seconds: number | null;
    baseline_seconds: number | null;
    bunching_event_count: number;
    service_gap_event_count: number;
    variability_seconds: number | null;
    sufficient: boolean;
  };
  scheduled_headways: {
    source: "static_gtfs_schedule";
    stop_id: string | null;
    stop_name: string | null;
    sample_count: number;
    median_seconds: number | null;
    baseline_seconds: number | null;
    bunching_event_count: number;
    service_gap_event_count: number;
    variability_seconds: number | null;
    sufficient: boolean;
  };
  median_headway_deviation_seconds: number | null;
  best_period_start: string | null;
  worst_period_start: string | null;
  through_time: Array<{
    source: "direct_recorded_observations";
    start: string;
    end: string;
    observation_count: number;
    delay_observation_count: number;
    median_delay_seconds: number | null;
    late_observation_count: number;
  }>;
  spatial_reliability: ReliabilityStopCollection;
}

interface ComparisonPeriod {
  label: "A" | "B";
  start: string;
  end: string;
  observation_count: number;
  observed_vehicle_count: number;
  coverage_duration_seconds: number;
  sufficient_history: boolean;
  sufficiency_reason: string;
  median_delay_seconds: number | null;
  percentile_90_delay_seconds: number | null;
  observed_headway_seconds: number | null;
  scheduled_headway_seconds: number | null;
  headway_deviation_seconds: number | null;
  headway_variability_seconds: number | null;
  bunching_event_count: number | null;
  service_gap_event_count: number | null;
}

interface RouteComparison {
  route_id: string;
  static_feed_id: string;
  period_a: ComparisonPeriod;
  period_b: ComparisonPeriod;
  deltas_b_minus_a: {
    median_delay_seconds: number | null;
    percentile_90_delay_seconds: number | null;
    observed_headway_seconds: number | null;
    headway_variability_seconds: number | null;
    bunching_event_count: number | null;
    service_gap_event_count: number | null;
    observation_count: number;
    coverage_duration_seconds: number;
  };
  coverage_ratio: number;
  comparable: boolean;
  better_period: "A" | "B" | "tie" | null;
  verdict: string;
  deciding_signals: string[];
}

const EMPTY_COLLECTION: FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

const EDMONTON_CENTER: [number, number] = [-113.4938, 53.5461];
/** Served from `public/`; see `scripts/copy-maplibre-worker.mjs`. */
const MAPLIBRE_WORKER_URL = "/maplibre/maplibre-gl-worker.mjs";

const FALLBACK_BOUNDS = { west: -113.95, east: -113.14, south: 53.32, north: 53.75 };

function fallbackPoint(coordinates: number[]): [number, number] {
  const [longitude, latitude] = coordinates;
  return [
    ((longitude - FALLBACK_BOUNDS.west) / (FALLBACK_BOUNDS.east - FALLBACK_BOUNDS.west)) * 1000,
    ((FALLBACK_BOUNDS.north - latitude) / (FALLBACK_BOUNDS.north - FALLBACK_BOUNDS.south)) * 1000,
  ];
}

function fallbackPath(feature: Feature, stride: number): string | null {
  if (!feature.geometry || feature.geometry.type !== "LineString") return null;
  const coordinates = (feature as Feature<LineString>).geometry.coordinates;
  return coordinates
    .filter((_, index) => index === 0 || index === coordinates.length - 1 || index % stride === 0)
    .map(([longitude, latitude], index) => {
      const [x, y] = fallbackPoint([longitude, latitude]);
      return `${index === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
}

function cssRouteColor(value: unknown, fallback = "#f97316"): string {
  return typeof value === "string" && /^[0-9a-f]{6}$/i.test(value) ? `#${value}` : fallback;
}

function TransitMapFallback({
  networkShapes,
  selectedShapes,
  selectedStops,
  reliabilityStops,
  vehicles,
}: {
  networkShapes: GeoJsonFeatureCollection | null;
  selectedShapes: GeoJsonFeatureCollection | null;
  selectedStops: GeoJsonFeatureCollection | null;
  reliabilityStops: ReliabilityStopCollection | null;
  vehicles: RealtimeVehicleCollection | null;
}) {
  return (
    <svg
      aria-label="Edmonton geographic transit network"
      className="transit-map-fallback"
      preserveAspectRatio="none"
      role="img"
      viewBox="0 0 1000 1000"
    >
      {/*
        No decorative basemap geography here.  A stylised graticule and river
        curve are not Edmonton, and drawing invented ground under real route
        geometry misrepresents where service runs.  Only measured data is drawn.
      */}
      <rect fill="#e8edf2" height="1000" width="1000" />
      <g
        className="fallback-network"
        aria-label={`${networkShapes?.features.length ?? 0} static route shapes`}
        opacity={selectedShapes?.features.length ? 0.26 : 1}
      >
        {networkShapes?.features.map((feature, index) => {
          const path = fallbackPath(feature, 8);
          return path ? <path d={path} key={`network-${index}`} /> : null;
        })}
      </g>
      <g className="fallback-selected-route" aria-label="Selected route geometry">
        {selectedShapes?.features.map((feature, index) => {
          const path = fallbackPath(feature, 1);
          if (!path) return null;
          return (
            <g key={`selected-${index}`}>
              <path className="fallback-selected-route-casing" d={path} />
              <path d={path} stroke={cssRouteColor(feature.properties?.color)} />
            </g>
          );
        })}
      </g>
      <g className="fallback-stops" aria-label={`${selectedStops?.features.length ?? 0} selected route stops`}>
        {selectedStops?.features.map((feature, index) => {
          if (!feature.geometry || feature.geometry.type !== "Point") return null;
          const [x, y] = fallbackPoint(feature.geometry.coordinates);
          return <circle cx={x} cy={y} key={`stop-${index}`} r="4.2" />;
        })}
      </g>
      <g className="fallback-reliability" aria-label={`${reliabilityStops?.features.length ?? 0} reliability stop markers`}>
        {reliabilityStops?.features.map((feature) => {
          const [x, y] = fallbackPoint(feature.geometry.coordinates);
          return <circle className={feature.properties.reliability_status} cx={x} cy={y} key={feature.properties.stop_id} r="7.2" />;
        })}
      </g>
      <g className="fallback-vehicles" aria-label={`${vehicles?.features.length ?? 0} live vehicles`}>
        {vehicles?.features.map((feature) => {
          const [x, y] = fallbackPoint(feature.geometry.coordinates);
          return <circle cx={x} cy={y} key={feature.properties?.vehicle_id ?? `${x}-${y}`} r="4.6" />;
        })}
      </g>
    </svg>
  );
}

/** Feed-supplied text (stop names, vehicle labels) is inserted into popup HTML. */
function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => `&#${character.charCodeAt(0)};`);
}

/**
 * Padding for framing geometry on the map.
 *
 * Whether the analysis band sits over the canvas depends on the mode (LIVE and
 * REPLAY shorten the canvas to stop above it; SCENARIOS draws underneath it),
 * so the overlap is measured rather than assumed.  Asking MapLibre for more
 * padding than the canvas has makes `fitBounds` silently do nothing.
 */
function mapFitPadding(container: HTMLElement | null) {
  const band = container?.parentElement?.querySelector<HTMLElement>(".network-intelligence, .analytics-workspace");
  const canvasBottom = container?.getBoundingClientRect().bottom ?? 0;
  const overlap = band ? Math.max(0, canvasBottom - band.getBoundingClientRect().top) : 0;
  return { top: 72, left: 56, right: 56, bottom: overlap + 56 };
}

function asFeatureCollection(data: GeoJsonFeatureCollection | null): FeatureCollection {
  return data ? { type: "FeatureCollection", features: data.features } : EMPTY_COLLECTION;
}

function routeLabel(route: RouteSummary): string {
  const name = route.long_name ?? route.short_name ?? route.route_id;
  return route.short_name && route.long_name ? `${route.short_name} · ${route.long_name}` : name;
}

function routeKind(routeType: number): "bus" | "rail" | "other" {
  return routeType === 0 || routeType === 1 || routeType === 2 ? "rail" : routeType === 3 ? "bus" : "other";
}


function feedStatus(status: RealtimeStatus | null, kind: string) {
  return status?.feeds.find((feed) => feed.feed_kind === kind) ?? null;
}

function formatRealtimeTime(value: string | null | undefined): string {
  if (!value) return "Awaiting feed";
  return new Intl.DateTimeFormat("en-CA", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "America/Edmonton" }).format(new Date(value));
}

function formatDelay(value: number | null): string {
  if (value === null) return "No delay data";
  const sign = value > 0 ? "+" : "";
  return `${sign}${Math.round(value / 60)} min`;
}

function formatSeconds(value: number | null): string {
  if (value === null) return "—";
  return `${Math.round(value / 60)} min`;
}

function formatPreciseSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(1)} s`;
}

function formatSignedSeconds(value: number | null): string {
  if (value === null) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  if (value !== 0 && Math.abs(value) < 60) {
    return `${sign}${Math.abs(Math.round(value))} sec`;
  }
  return `${sign}${Math.round(Math.abs(value) / 60)} min`;
}

function formatReplayTime(value: string | null): string {
  if (!value) return "No recorded history";
  return new Intl.DateTimeFormat("en-CA", {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: "America/Edmonton",
  }).format(new Date(value));
}

function formatCoverage(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(seconds % 3600 === 0 ? 0 : 1)} hr`;
}

function formatAlertTime(value: string): string {
  return new Intl.DateTimeFormat("en-CA", { dateStyle: "medium", timeStyle: "short", timeZone: "America/Edmonton" }).format(new Date(value));
}

function formatAlertPeriod(alert: RealtimeAlert): string {
  const period = alert.active_periods[0];
  // Publishers encode "no end date" as a far-future sentinel (ETS uses the year
  // 3000); printing it verbatim looks like a rendering bug.
  const end = period?.end && new Date(period.end).getUTCFullYear() < 2100 ? period.end : null;
  if (!period?.start && !end) return "Active period not supplied";
  if (period.start && end) return `${formatAlertTime(period.start)} – ${formatAlertTime(end)}`;
  return period.start ? `From ${formatAlertTime(period.start)} · until further notice` : `Until ${formatAlertTime(end as string)}`;
}

function formatCountDelta(value: number | null): string {
  if (value === null) return "—";
  if (value === 0) return "No change";
  return `${value > 0 ? "+" : "−"}${Math.abs(value)}`;
}

function toDateTimeLocalValue(value: string): string {
  const date = new Date(value);
  const pad = (number: number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/**
 * Axis ticks in seconds for the delay chart, so magnitudes are readable off the
 * plot instead of only on hover.
 */
function delayAxisCeiling(buckets: Array<{ median_delay_seconds: number | null; delay_observation_count: number }>): number {
  const peak = buckets.reduce(
    (largest, bucket) => (bucket.delay_observation_count > 0 ? Math.max(largest, Math.abs(bucket.median_delay_seconds ?? 0)) : largest),
    0,
  );
  return Math.max(120, Math.ceil(peak / 60) * 60);
}

function delayAxisTicks(buckets: Array<{ median_delay_seconds: number | null; delay_observation_count: number }>): number[] {
  const ceiling = delayAxisCeiling(buckets);
  // Only emit ticks whose rendered label is distinct, so the axis never prints
  // "0m, 1m, 1m, 2m" for a shallow range.
  const candidates = [0, ceiling / 2, ceiling];
  const seen = new Set<string>();
  return candidates.filter((tick) => {
    const label = formatAxisMinutes(tick);
    if (seen.has(label)) return false;
    seen.add(label);
    return true;
  });
}

/** Axis label for a delay tick, with sub-minute precision when the range is small. */
function formatAxisMinutes(seconds: number): string {
  if (seconds === 0) return "0";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = seconds / 60;
  return Number.isInteger(minutes) ? `${minutes}m` : `${minutes.toFixed(1)}m`;
}

/** Height reserved under each large-chart bar for its value label. */
const DELAY_LABEL_PX = 19;

function reliabilityBarHeight(delaySeconds: number | null): number {
  if (delaySeconds === null) return 7;
  return Math.max(9, Math.min(100, Math.round((Math.abs(delaySeconds) / 600) * 100)));
}

/**
 * Route states that count as "needing attention". Mirrors ATTENTION_STATES in
 * apps/api/transitpulse_api/operations.py: routes averaging 1-5 minutes late are
 * ordinary operations here, not exceptions.
 */
const ATTENTION_STATES = new Set(["SERVICE_GAP", "BUNCHING", "MAJOR_DELAY", "EARLY"]);

/** Pluralize a count so the UI never reads "1 gaps". */
function plural(count: number, singular: string, plural_ = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural_}`;
}

/**
 * The single source of truth for how a service state is named on screen.
 *
 * The route-list badge and the selected-route status panel previously derived
 * their own labels, so the same route could read DELAYED in one place and
 * MINOR DELAY in another at the same moment.
 */
const SERVICE_STATE_LABELS: Record<string, string> = {
  ON_TIME: "ON TIME",
  EARLY: "EARLY",
  MINOR_DELAY: "MINOR DELAY",
  MAJOR_DELAY: "MAJOR DELAY",
  BUNCHING: "BUNCHING",
  SERVICE_GAP: "SERVICE GAP",
  NO_LIVE_DATA: "NO DATA",
};

function statusLabel(value: string | undefined): string {
  if (!value) return SERVICE_STATE_LABELS.NO_LIVE_DATA;
  return SERVICE_STATE_LABELS[value] ?? value.replaceAll("_", " ");
}

function statusClass(value: string | undefined): string {
  return (value ?? "NO_LIVE_DATA").toLowerCase().replaceAll("_", "-");
}

function routeDisplayName(route: NetworkRouteStatus): string {
  return route.short_name ?? route.route_id;
}

/**
 * The one-line evidence beside a priority-queue badge.  It follows the badge's
 * own status, so a route flagged BUNCHING never shows a gap as its evidence
 * just because it also happens to have one.
 */
function issueEvidence(issue: NetworkRouteStatus): string {
  const headways = issue.predicted_headways_seconds;
  if (issue.service_status === "SERVICE_GAP" && headways.length) {
    return `Largest gap ${formatSeconds(Math.max(...headways))}`;
  }
  if (issue.service_status === "BUNCHING") {
    const baseline = issue.headway_baseline_seconds;
    const close = baseline ? headways.filter((value) => value < baseline * 0.5).length : 0;
    return close ? plural(close, "close pair") : "Arrivals closer than scheduled";
  }
  const late = plural(issue.delayed_vehicle_count, "late trip");
  return issue.average_delay_seconds === null ? late : `${late} · avg ${formatSignedSeconds(issue.average_delay_seconds)}`;
}

function routeHeadwayEvidence(operations: RouteOperations): string {
  const headways = operations.predicted_headways_seconds;
  if (!headways.length) return "No comparable predicted arrivals are available at one stop.";
  const minimum = Math.min(...headways);
  const maximum = Math.max(...headways);
  const baseline = operations.headway_baseline_seconds;
  if (operations.service_gap) return `Largest predicted gap at stop ${operations.prediction_stop_id ?? "—"}: ${formatSeconds(maximum)} versus ${formatSeconds(baseline)} baseline.`;
  if (operations.bunching) return `Closest predicted pair at stop ${operations.prediction_stop_id ?? "—"}: ${formatSeconds(minimum)} versus ${formatSeconds(baseline)} baseline.`;
  return `${headways.length} predicted intervals at stop ${operations.prediction_stop_id ?? "—"}; ${formatSeconds(minimum) === formatSeconds(maximum) ? `all ${formatSeconds(minimum)}` : `range ${formatSeconds(minimum)}–${formatSeconds(maximum)}`}, baseline ${formatSeconds(baseline)}.`;
}

/** Disclose that far-future predictions were filtered, rather than filtering silently. */
function horizonExclusionNote(operations: RouteOperations): string | null {
  if (!operations.excluded_arrivals_beyond_horizon) return null;
  const horizon = operations.prediction_horizon_seconds;
  const window = horizon ? `${Math.round(horizon / 60)} min` : "the comparable-service window";
  const count = operations.excluded_arrivals_beyond_horizon;
  return `${count} predicted arrival${count === 1 ? "" : "s"} beyond ${window} excluded as next-period service.`;
}

export default function Home() {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
  const popupRef = useRef<Popup | null>(null);
  const attributionRef = useRef<AttributionControl | null>(null);
  const basemapFailedRef = useRef(false);
  const [mapLoaded, setMapLoaded] = useState(false);
  const [apiStatus, setApiStatus] = useState<ServiceStatus>("loading");
  const [databaseStatus, setDatabaseStatus] = useState<ServiceStatus>("loading");
  const [networkState, setNetworkState] = useState<NetworkState>("loading");
  const [networkMessage, setNetworkMessage] = useState("Loading Edmonton network…");
  const [routes, setRoutes] = useState<RouteSummary[]>([]);
  const [networkShapes, setNetworkShapes] = useState<GeoJsonFeatureCollection | null>(null);
  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null);
  const [selectedRoute, setSelectedRoute] = useState<RouteDetail | null>(null);
  const [selectedShapes, setSelectedShapes] = useState<GeoJsonFeatureCollection | null>(null);
  const [selectedStops, setSelectedStops] = useState<GeoJsonFeatureCollection | null>(null);
  const [routeLoading, setRouteLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<"all" | "bus" | "rail">("all");
  const [realtimeStatus, setRealtimeStatus] = useState<RealtimeStatus | null>(null);
  const [vehicleState, setVehicleState] = useState<"loading" | "ready" | "error">("loading");
  const [vehicles, setVehicles] = useState<RealtimeVehicleCollection | null>(null);
  const [selectedVehicleId, setSelectedVehicleId] = useState<string | null>(null);
  /**
   * Whether MapLibre finished loading.  The SVG fallback must not draw over a
   * working basemap: it washes out the tiles and duplicates the same network
   * geometry the real layers already render.
   */
  const [mapReady, setMapReady] = useState(false);
  /** Why the basemap is absent, when we actually know. */
  const [basemapFailure, setBasemapFailure] = useState<string | null>(null);
  const [mapLayers, setMapLayers] = useState<MapLayerToggles>({
    routes: true,
    stops: true,
    vehicles: true,
  });
  const [operations, setOperations] = useState<RouteOperations | null>(null);
  const [routeAlerts, setRouteAlerts] = useState<RealtimeAlert[]>([]);
  const [mode, setMode] = useState<AppMode>("live");
  const [networkHealth, setNetworkHealth] = useState<NetworkHealth | null>(null);
  const [networkAlerts, setNetworkAlerts] = useState<RealtimeAlert[]>([]);
  const [replayAvailability, setReplayAvailability] = useState<HistoryAvailability | null>(null);
  const [replayHistory, setReplayHistory] = useState<HistoryVehicleObservationCollection | null>(null);
  const [replayStart, setReplayStart] = useState<string | null>(null);
  const [replayEnd, setReplayEnd] = useState<string | null>(null);
  const [replayTimestamp, setReplayTimestamp] = useState<string | null>(null);
  const [replaySpeed, setReplaySpeed] = useState(1);
  const [replayPlaying, setReplayPlaying] = useState(false);
  const [replayMessage, setReplayMessage] = useState("");
  const [analyticsAvailability, setAnalyticsAvailability] = useState<HistoryAvailability | null>(null);
  const [analyticsStart, setAnalyticsStart] = useState<string | null>(null);
  const [analyticsEnd, setAnalyticsEnd] = useState<string | null>(null);
  const [analytics, setAnalytics] = useState<RouteReliability | null>(null);
  const [analyticsMessage, setAnalyticsMessage] = useState("Select a route to inspect recorded reliability.");
  /**
   * True while Period A is the automatically chosen default window.  A busy
   * route records more observations in six hours than the API will analyse in
   * one request, so only a default window is shrunk to fit; a range the user
   * picked is never silently changed.
   */
  const analyticsAutoWindowRef = useRef(false);
  const [comparisonEnabled, setComparisonEnabled] = useState(false);
  const [comparisonStart, setComparisonStart] = useState<string | null>(null);
  const [comparisonEnd, setComparisonEnd] = useState<string | null>(null);
  const [comparison, setComparison] = useState<RouteComparison | null>(null);
  const [comparisonMessage, setComparisonMessage] = useState("Choose two periods to compare.");

  useEffect(() => {
    const abortController = new AbortController();
    void fetchJson<{ status: string }>("/api/health", abortController.signal)
      .then(() => setApiStatus("online"))
      .catch(() => setApiStatus("offline"));
    void fetchJson<{ status: string }>("/api/health/db", abortController.signal)
      .then(() => setDatabaseStatus("online"))
      .catch(() => setDatabaseStatus("offline"));

    Promise.all([
      fetchJson<RouteSummary[]>("/api/routes", abortController.signal),
      fetchJson<GeoJsonFeatureCollection>("/api/network/shapes", abortController.signal),
    ])
      .then(([loadedRoutes, loadedShapes]) => {
        setRoutes(loadedRoutes);
        setNetworkShapes(loadedShapes);
        setNetworkState("ready");
        setNetworkMessage(`${loadedRoutes.length} routes from the current imported feed`);
      })
      .catch((error: unknown) => {
        if (abortController.signal.aborted) return;
        const message = error instanceof Error ? error.message : "Network data could not be loaded";
        setNetworkState(message.includes("No imported GTFS feed") ? "empty" : "error");
        setNetworkMessage(message);
      });
    return () => abortController.abort();
  }, []);

  useEffect(() => {
    let alive = true;
    const refreshRealtime = () => {
      void fetchJson<RealtimeStatus>("/api/realtime/status")
        .then((status) => { if (alive) setRealtimeStatus(status); })
        .catch(() => { if (alive) setRealtimeStatus(null); });
    };
    refreshRealtime();
    const timer = window.setInterval(refreshRealtime, 15_000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    let alive = true;
    const refreshNetworkHealth = () => {
      void Promise.all([
        fetchJson<NetworkHealth>("/api/operations/network"),
        fetchJson<RealtimeAlert[]>("/api/realtime/alerts"),
      ]).then(([health, alerts]) => {
        if (!alive) return;
        setNetworkHealth(health);
        setNetworkAlerts(alerts);
      }).catch(() => {
        if (!alive) return;
        setNetworkHealth(null);
      });
    };
    refreshNetworkHealth();
    const timer = window.setInterval(refreshNetworkHealth, 15_000);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    if (mode !== "live") return;
    let alive = true;
    const endpoint = selectedRouteId
      ? `/api/realtime/routes/${encodeURIComponent(selectedRouteId)}/vehicles`
      : "/api/realtime/vehicles";
    const refreshVehicles = () => {
      void fetchJson<RealtimeVehicleCollection>(endpoint)
        .then((data) => {
          if (!alive) return;
          setVehicles(data);
          setVehicleState("ready");
        })
        .catch(() => {
          if (!alive) return;
          setVehicles(null);
          setVehicleState("error");
        });
    };
    refreshVehicles();
    const timer = window.setInterval(refreshVehicles, 15_000);
    return () => { alive = false; window.clearInterval(timer); };
  }, [mode, selectedRouteId]);

  useEffect(() => {
    if (mode !== "replay") return;
    const abortController = new AbortController();
    const params = new URLSearchParams();
    if (selectedRouteId) params.set("route_id", selectedRouteId);
    void fetchJson<HistoryAvailability>(`/api/history/availability?${params.toString()}`, abortController.signal)
      .then((availability) => {
        if (!availability.first_observed_at || !availability.last_observed_at) {
          setReplayAvailability(availability);
          setReplayHistory(null);
          setReplayMessage("No recorded vehicle positions are available for this selection.");
          return;
        }
        const first = Date.parse(availability.first_observed_at);
        const last = Date.parse(availability.last_observed_at);
        const start = new Date(Math.max(first, last - 5 * 60_000)).toISOString();
        const end = new Date(last).toISOString();
        setReplayAvailability(availability);
        setReplayStart(start);
        setReplayEnd(end);
        setReplayTimestamp(end);
        setReplayMessage(`${availability.observation_count.toLocaleString()} recorded positions are available for this static feed.`);
      })
      .catch((error: unknown) => {
        if (abortController.signal.aborted) return;
        setReplayAvailability(null);
        setReplayHistory(null);
        setReplayMessage(error instanceof Error ? error.message : "Recorded history could not be loaded.");
      });
    return () => abortController.abort();
  }, [mode, selectedRouteId]);

  useEffect(() => {
    if (mode !== "replay" || !replayStart || !replayEnd) return;
    const abortController = new AbortController();
    const params = new URLSearchParams({ start: replayStart, end: replayEnd, limit: "5000" });
    if (selectedRouteId) params.set("route_id", selectedRouteId);
    void fetchJson<HistoryVehicleObservationCollection>(`/api/history/observations?${params.toString()}`, abortController.signal)
      .then((history) => {
        if (abortController.signal.aborted) return;
        setReplayHistory(history);
        setReplayTimestamp((current) => {
          const start = Date.parse(history.start);
          const end = Date.parse(history.end);
          return new Date(clampReplayTimestamp(current ? Date.parse(current) : end, start, end)).toISOString();
        });
        setReplayMessage(history.features.length ? `${history.features.length.toLocaleString()} observations loaded for this window.` : "No positions were recorded in this window.");
      })
      .catch((error: unknown) => {
        if (!abortController.signal.aborted) {
          setReplayHistory(null);
          setReplayMessage(error instanceof Error ? error.message : "Replay window could not be loaded.");
        }
      });
    return () => abortController.abort();
  }, [mode, replayEnd, replayStart, selectedRouteId]);

  useEffect(() => {
    if (!replayPlaying || !replayTimestamp || !replayStart || !replayEnd) return;
    const start = Date.parse(replayStart);
    const end = Date.parse(replayEnd);
    const timer = window.setInterval(() => {
      setReplayTimestamp((current) => {
        if (!current) return current;
        const next = advanceReplayTimestamp(Date.parse(current), 250, replaySpeed, start, end);
        if (next >= end) setReplayPlaying(false);
        return new Date(next).toISOString();
      });
    }, 250);
    return () => window.clearInterval(timer);
  }, [replayEnd, replayPlaying, replaySpeed, replayStart, replayTimestamp]);

  const replayVehicles = useMemo<RealtimeVehicleCollection | null>(() => {
    if (!replayHistory || !replayTimestamp) return null;
    const frame = replayFrameAt(
      replayHistory.features,
      Date.parse(replayTimestamp),
      replayHistory.static_feed_id,
    );
    return {
      type: "FeatureCollection",
      feed_id: replayHistory.static_feed_id,
      source_timestamp: replayTimestamp,
      stale: false,
      features: frame.map((feature) => ({
        type: "Feature",
        geometry: feature.geometry,
        properties: {
          vehicle_id: feature.properties.vehicle_id,
          trip_id: feature.properties.trip_id,
          route_id: feature.properties.route_id,
          bearing: feature.properties.bearing,
          speed: feature.properties.speed,
          timestamp: feature.properties.observed_at ?? feature.properties.source_timestamp ?? feature.properties.recorded_at,
          current_stop_sequence: feature.properties.current_stop_sequence,
          current_status: feature.properties.current_status,
          schedule_relationship: feature.properties.schedule_relationship,
          delay_seconds: feature.properties.delay_seconds,
        },
      })),
    };
  }, [replayHistory, replayTimestamp]);
  // Analytics and Scenarios both answer questions about the schedule, not about
  // where buses are right now; plotting live vehicles on either would invite
  // reading a live position as evidence for a historical or modelled claim.
  const displayedVehicles = mode === "analytics" || mode === "scenarios"
    ? null
    : vehicleDataForMode(mode, vehicles, replayVehicles);

  /** Recording density across the loaded replay window. */
  const replayCoverage = useMemo(
    () => replayCoverageBuckets(
      (replayHistory?.features ?? []) as unknown as ReplayObservation[],
      replayStart,
      replayEnd,
    ),
    [replayHistory, replayStart, replayEnd],
  );

  const replayCoveragePeak = Math.max(1, ...replayCoverage.map((bucket) => bucket.count));

  /**
   * Mode emphasis and layer visibility.
   *
   * Each mode has a different subject, so the same four data layers are
   * re-weighted rather than redrawn: LIVE leads with vehicles, ANALYTICS with
   * reliability evidence, SCENARIOS with the route alignment alone. Keeping one
   * set of layers means a mode switch never re-parses geometry, and the map
   * cannot end up in a state where two modes' symbology are both visible.
   */
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const visible = (id: string, on: boolean) => {
      if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none");
    };
    const paint = (id: string, property: "line-opacity", value: unknown) => {
      if (map.getLayer(id)) map.setPaintProperty(id, property, value as never);
    };

    visible("network-routes-line", mapLayers.routes);
    for (const id of ["selected-route-casing", "selected-route-line"]) {
      visible(id, mapLayers.routes);
    }
    for (const id of ["selected-stops-casing", "selected-stops-dot"]) {
      visible(id, mapLayers.stops && mode !== "analytics");
    }
    for (const id of ["reliability-stops-halo", "reliability-stops-dot"]) {
      visible(id, mapLayers.stops && mode === "analytics");
    }
    const vehiclesOn = mapLayers.vehicles && (mode === "live" || mode === "replay");
    for (const id of ["live-vehicles-halo", "live-vehicles-dot", "live-vehicles-heading"]) {
      visible(id, vehiclesOn);
    }

    // In SCENARIOS the unselected network drops back hard, because the screen is
    // about one route's geometry and everything else is orientation.
    paint("network-routes-line", "line-opacity", networkLineOpacity(mode));
  }, [mapReady, mapLayers, mode]);

  const fitSelectedRoute = useCallback(() => {
    const map = mapRef.current;
    if (!map || !selectedShapes?.features.length) return;
    map.resize();
    const bounds = new LngLatBounds();
    for (const feature of selectedShapes.features) {
      const geometry = feature.geometry;
      if (geometry.type !== "LineString") continue;
      for (const coordinate of geometry.coordinates) {
        bounds.extend(coordinate as [number, number]);
      }
    }
    if (!bounds.isEmpty()) map.fitBounds(bounds, { padding: mapFitPadding(mapContainerRef.current), maxZoom: 15, duration: 550 });
  }, [selectedShapes]);

  const fitNetwork = useCallback(() => {
    mapRef.current?.fitBounds(
      [
        [FALLBACK_BOUNDS.west, FALLBACK_BOUNDS.south],
        [FALLBACK_BOUNDS.east, FALLBACK_BOUNDS.north],
      ],
      { padding: mapFitPadding(mapContainerRef.current), duration: 550 },
    );
  }, []);

  // SCENARIOS. The catalogue is fetched lazily the first time the mode opens,
  // because the estimator artifact is generated and a fresh checkout may not
  // have one; a 503 should not be requested on every app start.
  const scenarios = useScenarios("", mode === "scenarios");
  const scenarioBaseline = scenarios.result?.baseline ?? null;
  const scenarioUnchanged = scenarios.result?.estimate.scenario_unchanged ?? false;
  // Frequency/span-only edits: the fleet moves, the speed model is not used.
  const scenarioSpeedHeld = !scenarioUnchanged && (scenarios.result?.estimate.design_distance ?? 1) < 1e-9;

  // Draw the scenario's route on the shared map by reusing the existing
  // selection pathway, so SCENARIOS gets route geometry without a second map.
  const scenarioRouteId = scenarioBaseline?.original_route_id ?? null;
  useEffect(() => {
    if (mode !== "scenarios" || !scenarioRouteId) return;
    setSelectedRouteId((current) => (current === scenarioRouteId ? current : scenarioRouteId));
  }, [mode, scenarioRouteId]);

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;
    // MapLibre 6 resolves its tile-parsing worker from `import.meta.url`, which
    // after bundling points into `/_next/static/chunks/` where the worker file
    // does not exist.  The 404 returns Next's HTML shell, the browser rejects
    // the module worker on MIME type, and every vector tile then fails to parse
    // silently — no vector source ever loads and `load` never fires.  Point at
    // the copy `scripts/copy-maplibre-worker.mjs` places in `public/`.
    setWorkerUrl(MAPLIBRE_WORKER_URL);
    const map = new Map({
      container: mapContainerRef.current,
      // TransitPulse's own cartography, not an off-the-shelf basemap: see
      // `mapStyle.ts` for why the stock style competes with the transit data.
      style: transitPulseBasemapStyle(),
      center: EDMONTON_CENTER,
      zoom: 10.5,
      attributionControl: false,
      // The stock zoom/compass widget is replaced by `MapControls`, which
      // matches the rest of the console and offers fit-route and layer toggles.
      dragRotate: false,
    });
    map.touchZoomRotate.disableRotation();
    map.on("error", (event) => {
      const reason = event.error instanceof Error ? event.error.message : "MapLibre could not render the map";
      // An unreachable tile host (an offline demo, a CARTO outage) still lets
      // MapLibre fire `load`, because the transit layers are local data. That
      // must read as "no basemap", not replace the route list with an error,
      // and the CARTO/OpenStreetMap credit must not stay up over blank ground.
      const sourceId = (event as { sourceId?: string }).sourceId;
      if (sourceId === "carto" || /cartocdn|tiles\.json|\.pbf|Failed to fetch|AJAXError/i.test(reason)) {
        basemapFailedRef.current = true;
        setBasemapFailure("the tile server could not be reached");
        if (attributionRef.current) {
          map.removeControl(attributionRef.current);
          attributionRef.current = null;
        }
        return;
      }
      setNetworkState("error");
      setNetworkMessage(`Map error: ${reason}`);
    });
    // A lost GPU context stops tiles rendering with no error of its own, which
    // otherwise shows up as an unexplained blank basemap. Say what happened and
    // fall back to the measured-geometry view rather than leaving a dead canvas.
    map.on("webglcontextlost", () => {
      setMapReady(false);
      setBasemapFailure("the browser released the map's GPU context");
    });
    map.on("webglcontextrestored", () => setBasemapFailure(null));
    map.on("load", () => {
      setMapReady(true);
      // Credit the basemap only once it has actually rendered.  Showing this
      // while only the local fallback is drawn attributes geometry to CARTO
      // and OpenStreetMap that they did not supply.
      if (!basemapFailedRef.current) {
        attributionRef.current = new AttributionControl({ compact: true });
        // Top-right: the bottom-right corner belongs to the map controls.
        map.addControl(attributionRef.current, "top-right");
      }
      map.addSource("network-routes", { type: "geojson", data: EMPTY_COLLECTION });
      map.addLayer({
        id: "network-routes-line",
        type: "line",
        source: "network-routes",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ROUTE_COLORS.network,
          "line-width": networkLineWidth(),
          "line-opacity": 0.42,
        },
      });
      map.addSource("selected-route", { type: "geojson", data: EMPTY_COLLECTION });
      map.addLayer({
        id: "selected-route-casing",
        type: "line",
        source: "selected-route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ROUTE_COLORS.selectedCasing,
          "line-width": selectedRouteCasingWidth(),
          "line-opacity": 0.95,
        },
      });
      map.addLayer({
        id: "selected-route-line",
        type: "line",
        source: "selected-route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          // GTFS route_color is published without a leading "#", which MapLibre
          // cannot parse and silently renders as black.
          "line-color": [
            "case",
            ["has", "color"],
            ["concat", "#", ["get", "color"]],
            ROUTE_COLORS.selected,
          ],
          "line-width": selectedRouteWidth(),
          "line-opacity": 1,
        },
      });
      map.addSource("selected-stops", { type: "geojson", data: EMPTY_COLLECTION });
      map.addLayer({
        id: "selected-stops-casing",
        type: "circle",
        source: "selected-stops",
        paint: {
          "circle-radius": stopCasingRadius(),
          "circle-color": "#ffffff",
          "circle-stroke-width": 0,
          "circle-opacity": 0.95,
        },
      });
      map.addLayer({
        id: "selected-stops-dot",
        type: "circle",
        source: "selected-stops",
        paint: {
          "circle-radius": stopRadius(),
          "circle-color": "#1e3a4c",
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1,
        },
      });
      map.addSource("reliability-stops", { type: "geojson", data: EMPTY_COLLECTION });
      map.addLayer({
        id: "reliability-stops-halo",
        type: "circle",
        source: "reliability-stops",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "observation_count"], 1, 8, 8, 15],
          "circle-color": ["match", ["get", "reliability_status"], "poor", "#e11d48", "moderate", "#f59e0b", "good", "#0f766e", "#64748b"],
          "circle-opacity": 0.27,
        },
      });
      map.addLayer({
        id: "reliability-stops-dot",
        type: "circle",
        source: "reliability-stops",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["get", "observation_count"], 1, 4.5, 8, 7],
          "circle-color": ["match", ["get", "reliability_status"], "poor", "#e11d48", "moderate", "#f59e0b", "good", "#0f766e", "#64748b"],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1.4,
        },
      });
      map.addSource("live-vehicles", { type: "geojson", data: EMPTY_COLLECTION });
      map.addLayer({
        id: "live-vehicles-halo",
        type: "circle",
        source: "live-vehicles",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 6, 14, 13],
          "circle-color": vehicleColorExpression,
          "circle-opacity": 0.16,
        },
      });
      // The coloured disc under the chevron: the icon itself is white-and-dark
      // so it stays legible, and state is carried by this disc beneath it.
      map.addLayer({
        id: "live-vehicles-dot",
        type: "circle",
        source: "live-vehicles",
        paint: {
          "circle-radius": ["case", ["boolean", ["feature-state", "selected"], false], 8, 6],
          "circle-color": vehicleColorExpression,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1.6,
        },
      });
      const icon = vehicleIcon();
      if (icon && !map.hasImage("tp-vehicle")) {
        map.addImage("tp-vehicle", icon, { pixelRatio: 2 });
      }
      // Heading is drawn only where the feed actually reports one. Rotating a
      // marker by a default of 0 would point every unknown-bearing vehicle due
      // north, which reads as information and is not.
      map.addLayer({
        id: "live-vehicles-heading",
        type: "symbol",
        source: "live-vehicles",
        filter: ["has", "bearing"],
        layout: {
          "icon-image": "tp-vehicle",
          "icon-size": vehicleIconSize("live"),
          "icon-rotate": ["get", "bearing"],
          "icon-rotation-alignment": "map",
          "icon-allow-overlap": true,
          "icon-ignore-placement": true,
        },
        paint: { "icon-opacity": 0.96 },
      });
      // One click handler and one popup. Per-layer handlers each opened their own
      // popup, so a vehicle standing at a stop produced two stacked cards. The
      // most specific thing under the cursor wins: vehicle, then reliability
      // evidence, then the stop itself.
      const showPopup = (coordinates: number[], html: string) => {
        popupRef.current?.remove();
        popupRef.current = new Popup({ offset: 12 })
          .setLngLat(coordinates as [number, number])
          .setHTML(html)
          .addTo(map);
      };
      const clickableLayers = ["live-vehicles-dot", "reliability-stops-dot", "selected-stops-dot"];
      map.on("click", (event) => {
        for (const layer of clickableLayers) {
          if (!map.getLayer(layer)) continue;
          const [feature] = map.queryRenderedFeatures(event.point, { layers: [layer] });
          if (!feature || feature.geometry.type !== "Point") continue;
          const coordinates = feature.geometry.coordinates;
          if (layer === "live-vehicles-dot") {
            const properties = feature.properties as VehicleProperties;
            setSelectedVehicleId(properties.vehicle_id);
            showPopup(coordinates, `<strong>Vehicle ${escapeHtml(properties.vehicle_id)}</strong><span>Route ${escapeHtml(properties.route_id ?? "unmatched")} · ${escapeHtml(formatDelay(properties.delay_seconds ?? null))}</span>`);
          } else if (layer === "reliability-stops-dot") {
            const properties = feature.properties as ReliabilityStopProperties;
            showPopup(coordinates, `<strong>${escapeHtml(properties.name)}</strong><span>${properties.observation_count} recorded stop arrivals · median delay ${escapeHtml(formatSignedSeconds(properties.median_delay_seconds ?? null))}</span>`);
          } else {
            const name = feature.properties?.name as string | undefined;
            if (!name) continue;
            const stopId = feature.properties?.stop_id as string | undefined;
            showPopup(coordinates, `<strong>${escapeHtml(name)}</strong><span>Stop ${escapeHtml(stopId ?? "")}</span>`);
          }
          return;
        }
      });
      for (const layer of clickableLayers) {
        map.on("mouseenter", layer, () => { map.getCanvas().style.cursor = "pointer"; });
        map.on("mouseleave", layer, () => { map.getCanvas().style.cursor = ""; });
      }
      setMapLoaded(true);
    });
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!mapLoaded) return;
    const source = mapRef.current?.getSource("network-routes") as GeoJSONSource | undefined;
    source?.setData(asFeatureCollection(networkShapes));
  }, [mapLoaded, networkShapes]);

  useEffect(() => {
    if (!mapLoaded) return;
    const source = mapRef.current?.getSource("live-vehicles") as GeoJSONSource | undefined;
    source?.setData(asFeatureCollection(displayedVehicles));
  }, [displayedVehicles, mapLoaded]);

  useEffect(() => {
    if (!mapLoaded) return;
    const source = mapRef.current?.getSource("reliability-stops") as GeoJSONSource | undefined;
    source?.setData(mode === "analytics" ? analytics?.spatial_reliability ?? EMPTY_COLLECTION : EMPTY_COLLECTION);
  }, [analytics, mapLoaded, mode]);

  useEffect(() => {
    if (!selectedRouteId) {
      setSelectedRoute(null);
      setSelectedShapes(null);
      setSelectedStops(null);
      setOperations(null);
      setRouteAlerts([]);
      setAnalyticsAvailability(null);
      setAnalyticsStart(null);
      setAnalyticsEnd(null);
      setAnalytics(null);
      setAnalyticsMessage("Select a route to inspect recorded reliability.");
      setComparisonEnabled(false);
      setComparisonStart(null);
      setComparisonEnd(null);
      setComparison(null);
      setComparisonMessage("Choose two periods to compare.");
      return;
    }
    const abortController = new AbortController();
    setRouteLoading(true);
    Promise.all([
      fetchJson<RouteDetail>(`/api/routes/${encodeURIComponent(selectedRouteId)}`, abortController.signal),
      fetchJson<GeoJsonFeatureCollection>(`/api/routes/${encodeURIComponent(selectedRouteId)}/shape`, abortController.signal),
      fetchJson<GeoJsonFeatureCollection>(`/api/routes/${encodeURIComponent(selectedRouteId)}/stops`, abortController.signal),
      fetchJson<RouteOperations>(`/api/operations/routes/${encodeURIComponent(selectedRouteId)}`, abortController.signal).catch(() => null),
      fetchJson<RealtimeAlert[]>("/api/realtime/alerts", abortController.signal).catch(() => []),
    ])
      .then(([detail, shapes, stops, routeOperations, alerts]) => {
        setSelectedRoute(detail);
        setSelectedShapes(shapes);
        setSelectedStops(stops);
        setOperations(routeOperations);
        setRouteAlerts(alerts.filter((alert) => alert.affected_routes.includes(selectedRouteId)));
      })
      .catch((error: unknown) => {
        if (!abortController.signal.aborted) setNetworkMessage(error instanceof Error ? error.message : "Route could not be loaded");
      })
      .finally(() => {
        if (!abortController.signal.aborted) setRouteLoading(false);
      });
    return () => abortController.abort();
  }, [selectedRouteId]);

  useEffect(() => {
    if (mode !== "analytics" || !selectedRouteId) return;
    const abortController = new AbortController();
    setAnalytics(null);
    setAnalyticsAvailability(null);
    setAnalyticsStart(null);
    setAnalyticsEnd(null);
    setComparisonEnabled(false);
    setComparisonStart(null);
    setComparisonEnd(null);
    setComparison(null);
    setAnalyticsMessage("Loading the recorded range for this route…");
    void fetchJson<HistoryAvailability>(
      `/api/history/availability?route_id=${encodeURIComponent(selectedRouteId)}`,
      abortController.signal,
    )
      .then((availability) => {
        if (abortController.signal.aborted) return;
        setAnalyticsAvailability(availability);
        if (!availability.first_observed_at || !availability.last_observed_at) {
          setAnalyticsMessage("Insufficient recorded history");
          return;
        }
        const first = Date.parse(availability.first_observed_at);
        const last = Date.parse(availability.last_observed_at);
        const start = Math.max(first, last - 6 * 60 * 60_000);
        if (start >= last) {
          setAnalyticsMessage("Insufficient recorded history");
          return;
        }
        analyticsAutoWindowRef.current = true;
        setAnalyticsStart(new Date(start).toISOString());
        setAnalyticsEnd(new Date(last).toISOString());
        const duration = last - start;
        const comparisonEnd = start;
        const comparisonStart = Math.max(first, comparisonEnd - duration);
        if (comparisonStart < comparisonEnd) {
          setComparisonStart(new Date(comparisonStart).toISOString());
          setComparisonEnd(new Date(comparisonEnd).toISOString());
        }
        setAnalyticsMessage("Loading direct recorded reliability signals…");
      })
      .catch((error: unknown) => {
        if (!abortController.signal.aborted) {
          setAnalyticsMessage(error instanceof Error ? error.message : "Recorded reliability could not be loaded.");
        }
      });
    return () => abortController.abort();
  }, [mode, selectedRouteId]);

  useEffect(() => {
    if (!selectedRouteId || mode === "replay") return;
    let alive = true;
    const refreshRouteOperations = () => {
      void Promise.all([
        fetchJson<RouteOperations>(`/api/operations/routes/${encodeURIComponent(selectedRouteId)}`),
        fetchJson<RealtimeAlert[]>("/api/realtime/alerts"),
      ])
        .then(([routeOperations, alerts]) => {
          if (!alive) return;
          setOperations(routeOperations);
          setRouteAlerts(alerts.filter((alert) => alert.affected_routes.includes(selectedRouteId)));
        })
        .catch(() => {
          if (alive) setOperations(null);
        });
    };
    refreshRouteOperations();
    const timer = window.setInterval(refreshRouteOperations, 15_000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [mode, selectedRouteId]);

  useEffect(() => {
    if (mode !== "analytics" || !selectedRouteId || !analyticsStart || !analyticsEnd) return;
    if (Date.parse(analyticsStart) >= Date.parse(analyticsEnd)) {
      setAnalytics(null);
      setAnalyticsMessage("Choose an analysis end after its start.");
      return;
    }
    const abortController = new AbortController();
    setAnalyticsMessage("Loading direct recorded reliability signals…");
    const params = new URLSearchParams({ start: analyticsStart, end: analyticsEnd });
    void fetchJson<RouteReliability>(
      `/api/analytics/routes/${encodeURIComponent(selectedRouteId)}?${params.toString()}`,
      abortController.signal,
    )
      .then((result) => {
        if (abortController.signal.aborted) return;
        setAnalytics(result);
        setAnalyticsMessage(`${result.observation_count.toLocaleString()} recorded observations in this range.`);
      })
      .catch((error: unknown) => {
        if (abortController.signal.aborted) return;
        const start = Date.parse(analyticsStart);
        const end = Date.parse(analyticsEnd);
        const tooMany = error instanceof Error && error.message.includes("more than");
        if (tooMany && analyticsAutoWindowRef.current && end - start > 30 * 60_000) {
          // Halve the default window (keeping its end at the latest record) and
          // move Period B to sit directly before it, then let this effect rerun.
          const nextStart = end - (end - start) / 2;
          const first = analyticsAvailability?.first_observed_at ? Date.parse(analyticsAvailability.first_observed_at) : nextStart;
          const comparisonBegin = Math.max(first, nextStart - (end - nextStart));
          setAnalyticsStart(new Date(nextStart).toISOString());
          if (comparisonBegin < nextStart) {
            setComparisonStart(new Date(comparisonBegin).toISOString());
            setComparisonEnd(new Date(nextStart).toISOString());
          }
          return;
        }
        setAnalytics(null);
        setAnalyticsMessage(error instanceof Error ? error.message : "Reliability analytics could not be loaded.");
      });
    return () => abortController.abort();
  }, [analyticsAvailability, analyticsEnd, analyticsStart, mode, selectedRouteId]);

  useEffect(() => {
    if (!comparisonEnabled || mode !== "analytics" || !selectedRouteId || !analyticsStart || !analyticsEnd || !comparisonStart || !comparisonEnd) {
      setComparison(null);
      return;
    }
    if (Date.parse(comparisonStart) >= Date.parse(comparisonEnd)) {
      setComparison(null);
      setComparisonMessage("Choose a Period B end after its start.");
      return;
    }
    const abortController = new AbortController();
    setComparisonMessage("Comparing direct recorded evidence…");
    const params = new URLSearchParams({
      period_a_start: analyticsStart,
      period_a_end: analyticsEnd,
      period_b_start: comparisonStart,
      period_b_end: comparisonEnd,
    });
    void fetchJson<RouteComparison>(
      `/api/analytics/routes/${encodeURIComponent(selectedRouteId)}/compare?${params.toString()}`,
      abortController.signal,
    )
      .then((result) => {
        if (abortController.signal.aborted) return;
        setComparison(result);
        setComparisonMessage(result.verdict);
      })
      .catch((error: unknown) => {
        if (!abortController.signal.aborted) {
          setComparison(null);
          setComparisonMessage(error instanceof Error ? error.message : "The two periods could not be compared.");
        }
      });
    return () => abortController.abort();
  }, [analyticsEnd, analyticsStart, comparisonEnabled, comparisonEnd, comparisonStart, mode, selectedRouteId]);

  useEffect(() => {
    if (!mapLoaded) return;
    const map = mapRef.current;
    // Each mode gives the canvas a different height; MapLibre only notices on
    // its next resize observation, so fitting first would frame the route for
    // the previous mode's canvas and crop it.
    map?.resize();
    const shapes = asFeatureCollection(selectedShapes);
    const stops = asFeatureCollection(selectedStops);
    (map?.getSource("selected-route") as GeoJSONSource | undefined)?.setData(shapes);
    (map?.getSource("selected-stops") as GeoJSONSource | undefined)?.setData(stops);
    const coordinates: [number, number][] = shapes.features.flatMap((feature) => {
      const geometry = feature.geometry;
      if (!geometry || geometry.type !== "LineString") return [];
      return geometry.coordinates.map(([longitude, latitude]) => [longitude, latitude] as [number, number]);
    });
    if (map && coordinates.length > 1) {
      const bounds = coordinates.reduce(
        (result, coordinate) => result.extend(coordinate),
        new LngLatBounds(coordinates[0], coordinates[0]),
      );
      map.fitBounds(bounds, { padding: mapFitPadding(mapContainerRef.current), maxZoom: 14, duration: 500 });
    }
  }, [mapLoaded, mode, selectedShapes, selectedStops]);

  const filteredRoutes = useMemo(() => {
    const query = search.trim().toLowerCase();
    return routes.filter((route) => {
      const matchesFilter = filter === "all" || routeKind(route.route_type) === filter;
      const searchable = `${route.route_id} ${route.short_name ?? ""} ${route.long_name ?? ""}`.toLowerCase();
      return matchesFilter && (!query || searchable.includes(query));
    });
  }, [filter, routes, search]);
  const networkStatusByRoute = useMemo(
    () => new globalThis.Map(networkHealth?.routes.map((status) => [status.route_id, status]) ?? []),
    [networkHealth],
  );
  const selectedVehicle = useMemo(
    () => displayedVehicles?.features.find((feature) => feature.properties?.vehicle_id === selectedVehicleId)?.properties ?? null,
    [displayedVehicles, selectedVehicleId],
  );
  const vehicleFeed = feedStatus(realtimeStatus, "vehicle_positions");
  // ETS publishes one alert per affected route, so the same stop closure appears
  // as several identical cards. Cards merge on title, effect and period.
  const groupedAlerts = useMemo(() => {
    const groups = new globalThis.Map<string, RealtimeAlert>();
    for (const alert of networkAlerts) {
      const key = `${alert.header ?? ""}|${alert.effect ?? ""}|${alert.active_periods[0]?.start ?? ""}|${alert.active_periods[0]?.end ?? ""}`;
      const existing = groups.get(key);
      if (existing) {
        groups.set(key, { ...existing, affected_routes: [...new Set([...existing.affected_routes, ...alert.affected_routes])] });
      } else {
        groups.set(key, alert);
      }
    }
    return [...groups.values()];
  }, [networkAlerts]);
  // The API caps the issue list, so the count comes from every inspectable
  // route with an attention score rather than from the capped list's length.
  const flaggedRouteCount = networkHealth?.routes.filter((route) => ATTENTION_STATES.has(route.service_status) && (route.short_name || route.long_name)).length ?? 0;
  const replayProgress = useMemo(() => {
    if (!replayStart || !replayEnd || !replayTimestamp) return 0;
    const start = Date.parse(replayStart);
    const end = Date.parse(replayEnd);
    return end > start ? ((Date.parse(replayTimestamp) - start) / (end - start)) * 1000 : 0;
  }, [replayEnd, replayStart, replayTimestamp]);
  const updateMode = (nextMode: AppMode) => {
    setMode(nextMode);
    setReplayPlaying(false);
    setSelectedVehicleId(null);
    if (nextMode === "live") setReplayMessage("");
  };
  const delayBandRows = analytics ? [
    { label: "Early", count: analytics.delay_bands.early_count, tone: "early" },
    { label: "On time", count: analytics.delay_bands.on_time_count, tone: "on-time" },
    { label: "1–5 min late", count: analytics.delay_bands.late_under_5_count, tone: "minor" },
    { label: "5–10 min late", count: analytics.delay_bands.late_5_to_10_count, tone: "major" },
    { label: "10+ min late", count: analytics.delay_bands.late_over_10_count, tone: "severe" },
  ] : [];

  return (
    <main className="transit-shell">
      <header className="ops-header">
        <div className="ops-identity">
          <span className="ops-logo" aria-hidden="true">TP</span>
          <div><strong>TransitPulse</strong><span>Edmonton Operations</span></div>
        </div>
        <nav className="primary-modes" aria-label="Product mode">
          {MODES.map((option) => (
            <button
              className={mode === option.id ? "active" : ""}
              key={option.id}
              onClick={() => updateMode(option.id)}
              title={option.question}
              type="button"
            >
              <strong>{option.label}</strong>
              <small>{option.question}</small>
            </button>
          ))}
        </nav>
        <div className="ops-clock">
          <span className={`live-pill ${realtimeStatus?.stale ? "stale" : ""}`}><i />{realtimeStatus?.stale ? "STALE" : mode.toUpperCase()}</span>
          <time>{formatRealtimeTime(mode === "replay" ? replayTimestamp : networkHealth?.generated_at)}</time>
          <small>
            {mode === "replay" ? "Playhead · " : ""}America/Edmonton
            <a className="research-link" href="/research/model-lab">Research</a>
          </small>
        </div>
      </header>

      <section className={`network-summary mode-${mode}`} aria-label={`${mode} summary`}>
        {mode === "live" && <>
          <div className="summary-heading"><span>Network now</span><strong>{networkHealth?.stale ? "Feed delayed" : "Current operations"}</strong></div>
          <div className="kpi"><span>Active vehicles</span><strong>{networkHealth?.active_vehicles ?? "—"}</strong><small>direct live positions</small></div>
          <div className="kpi"><span>Routes operating</span><strong>{networkHealth?.routes_with_live_service ?? "—"}</strong><small>with live vehicles</small></div>
          <div className="kpi warning"><span>Routes with late trips</span><strong>{networkHealth?.routes_delayed ?? "—"}</strong><small>a trip over 1 min late</small></div>
          <div className="kpi critical"><span>Bunching</span><strong>{networkHealth?.routes_with_bunching ?? "—"}</strong><small>{networkHealth?.routes_with_bunching === 1 ? "route flagged" : "routes flagged"}</small></div>
          <div className="kpi critical"><span>Service gaps</span><strong>{networkHealth?.routes_with_service_gaps ?? "—"}</strong><small>{networkHealth?.routes_with_service_gaps === 1 ? "route flagged" : "routes flagged"}</small></div>
          <div className="kpi"><span>Active alerts</span><strong>{networkHealth?.active_alerts ?? "—"}</strong><small>publisher supplied</small></div>
        </>}
        {mode === "replay" && <>
          <div className="summary-heading"><span>Recorded service</span><strong>Historical replay</strong></div>
          <div className="kpi"><span>Replay vehicles</span><strong>{displayedVehicles?.features.length ?? 0}</strong><small>at playhead</small></div>
          <div className="kpi"><span>Loaded observations</span><strong>{replayHistory?.features.length.toLocaleString() ?? "—"}</strong><small>in the loaded window</small></div>
          <div className="kpi"><span>Recorded observations</span><strong>{replayAvailability?.observation_count.toLocaleString() ?? "—"}</strong><small>{selectedRoute ? `route ${selectedRoute.short_name ?? selectedRoute.route_id}` : "all recorded routes"}</small></div>
          <div className="kpi wide"><span>Playhead</span><strong>{formatReplayTime(replayTimestamp)}</strong><small>{selectedRoute ? `Route ${selectedRoute.short_name ?? selectedRoute.route_id}` : "All recorded routes"}</small></div>
        </>}
        {mode === "scenarios" && <>
          <div className="summary-heading"><span>Planning scenario</span><strong>{scenarioBaseline ? `Route ${scenarioBaseline.route_label}` : "Select a route"}</strong></div>
          <div className="kpi"><span>Scheduled runtime</span><strong>{scenarioBaseline?.scheduled_runtime_minutes != null ? `${scenarioBaseline.scheduled_runtime_minutes.toFixed(0)} min` : "—"}</strong><small>published, one way</small></div>
          <div className="kpi"><span>Estimated runtime</span><strong>{scenarios.result ? `${scenarios.result.estimate.runtime_minutes.point.toFixed(0)} min` : "—"}</strong><small>{!scenarios.result || scenarioUnchanged ? "no change yet" : scenarioSpeedHeld ? "held at schedule" : `range ${scenarios.result.estimate.runtime_minutes.low.toFixed(0)}–${scenarios.result.estimate.runtime_minutes.high.toFixed(0)}`}</small></div>
          <div className="kpi"><span>Commercial speed</span><strong>{scenarios.result ? `${scenarios.result.estimate.commercial_speed_kmh.point.toFixed(1)}` : "—"}</strong><small>{scenarioUnchanged || scenarioSpeedHeld ? "km/h, as scheduled" : "km/h, estimated"}</small></div>
          <div className="kpi"><span>Est. vehicles</span><strong>{scenarios.result?.estimate.fleet ? wholeVehicles(scenarios.result.estimate.fleet.vehicles.point) : "—"}</strong><small>{scenarios.result?.estimate.fleet ? `ratio ${scenarios.result.estimate.fleet.vehicles.point.toFixed(2)}, rounded up` : "estimated requirement"}</small></div>
          <div className={`kpi ${scenarioUnchanged || scenarioSpeedHeld ? "" : scenarios.result?.estimate.confidence === "low" ? "critical" : scenarios.result?.estimate.confidence === "moderate" ? "warning" : ""}`}><span>Confidence</span><strong>{!scenarios.result ? "—" : scenarioUnchanged || scenarioSpeedHeld ? "N/A" : scenarios.result.estimate.confidence.toUpperCase()}</strong><small>{scenarioUnchanged ? "nothing changed yet" : scenarioSpeedHeld ? "no model used" : "from held-out skill"}</small></div>
          <div className="kpi wide"><span>Basis</span><strong>{!scenarios.result ? "—" : scenarioUnchanged ? "SCHEDULE" : scenarioSpeedHeld ? "ARITHMETIC" : "ESTIMATE"}</strong><small>{scenarioUnchanged ? "published GTFS timetable" : scenarioSpeedHeld ? "schedule + recovery rule" : "8-city planning model"}</small></div>
        </>}
        {mode === "analytics" && <>
          <div className="summary-heading"><span>Route history</span><strong>{selectedRoute ? routeLabel(selectedRoute) : "Select a route"}</strong></div>
          <div className="kpi"><span>Observations</span><strong>{analytics?.observation_count.toLocaleString() ?? "—"}</strong><small>all records in range</small></div>
          <div className="kpi"><span>Vehicles observed</span><strong>{analytics?.observed_vehicle_count ?? "—"}</strong><small>distinct identifiers</small></div>
          <div className="kpi"><span>Coverage</span><strong>{formatCoverage(analytics?.coverage_duration_seconds)}</strong><small>first to last record</small></div>
          <div className="kpi"><span>Median delay</span><strong>{analytics?.sufficient_history ? formatSignedSeconds(analytics.delay_distribution.median_seconds) : "—"}</strong><small>observed</small></div>
          <div className="kpi"><span>P90 delay</span><strong>{analytics?.sufficient_history ? formatSignedSeconds(analytics.delay_distribution.percentile_90_seconds) : "—"}</strong><small>observed</small></div>
          <div className="kpi"><span>Observed headway</span><strong>{analytics?.observed_headways.sufficient ? formatSeconds(analytics.observed_headways.median_seconds) : "—"}</strong><small>{analytics?.observed_headways.sufficient ? `1 stop · ${plural(analytics.observed_headways.sample_count, "interval")}` : "one sampled stop"}</small></div>
        </>}
      </section>

      <section className={`map-pane mode-${mode}`} aria-label="Edmonton transit operations workspace">
        <div className="map" ref={mapContainerRef} />
        {(!mapReady || basemapFailure) && <p className="map-basemap-notice" role="status">
          {basemapFailure
            ? `No basemap — ${basemapFailure}. Transit data is still shown.`
            : "No basemap — showing measured route geometry only"}
        </p>}
        {!mapReady && <TransitMapFallback
          networkShapes={networkShapes}
          selectedShapes={selectedShapes}
          selectedStops={selectedStops}
          reliabilityStops={analytics?.spatial_reliability ?? null}
          vehicles={displayedVehicles}
        />}
        <div className={`map-context mode-${mode}`}>
          <span className="pulse-mark" aria-hidden="true">●</span>
          <div>
            <strong>{mode === "replay" ? "Recorded history" : mode === "analytics" ? "Recorded analytics" : mode === "scenarios" ? "Planning scenario" : "Live network"}</strong>
            <span>{mode === "replay" ? formatReplayTime(replayTimestamp) : mode === "analytics" ? (selectedRoute ? routeLabel(selectedRoute) : "Select a route") : mode === "scenarios" ? (scenarioBaseline ? `Route ${scenarioBaseline.route_label} · current alignment` : "Select a route") : formatRealtimeTime(networkHealth?.generated_at)}</span>
          </div>
        </div>
        <MapControls
          canFitRoute={Boolean(selectedShapes?.features.length)}
          layers={mapLayers}
          onFitNetwork={fitNetwork}
          onFitRoute={fitSelectedRoute}
          onToggle={(layer) => setMapLayers((previous) => ({ ...previous, [layer]: !previous[layer] }))}
          onZoom={(delta) => mapRef.current?.easeTo({ zoom: (mapRef.current?.getZoom() ?? 10.5) + delta, duration: 260 })}
          showVehicleToggle={mode === "live" || mode === "replay"}
        />
        {mode === "scenarios" && <div className="scenario-map-note" role="note">
          <strong>Current alignment only</strong>
          <span>
            A scenario changes <em>length</em> and <em>stop count</em>, not the path. No proposed
            alignment is drawn: the model has no road network to place it on.
          </span>
        </div>}
        <div className="map-legend">
          <span className="legend-line" /> Static network
          {mode === "analytics" ? <>
            <span className="legend-dot" style={{ background: "#0f766e" }} /> Good
            <span className="legend-dot" style={{ background: "#f59e0b" }} /> Moderate
            <span className="legend-dot" style={{ background: "#e11d48" }} /> Poor
            <span className="legend-dot" style={{ background: "#64748b" }} /> No delay data
          </> : <>
            <span className="legend-group">{mode === "replay" ? "Recorded vehicles" : "Vehicles"}</span>
            <span className="legend-dot" style={{ background: VEHICLE_COLORS.normal }} /> On time
            <span className="legend-dot" style={{ background: VEHICLE_COLORS.delayed }} /> 1–5 min late
            <span className="legend-dot" style={{ background: VEHICLE_COLORS.severe }} /> 5+ min late
            <span className="legend-dot" style={{ background: VEHICLE_COLORS.unknown }} /> No delay data
          </>}
        </div>
        {mode === "scenarios" && <section className="network-intelligence scenario-band" aria-label="Current plan versus proposed scenario">
          {scenarios.unavailableReason ? (
            <div className="intelligence-column">
              <div className="panel-heading"><div><p className="eyebrow">SCENARIOS</p><h2>Estimator unavailable</h2></div></div>
              <p className="panel-empty">{scenarios.unavailableReason}{scenarios.unavailableReason === API_NOT_RESPONDING ? null : <><br />Build it with <code>scripts/build_cross_city_dataset.py</code> then <code>scripts/build_scenario_estimator.py</code>.</>}</p>
            </div>
          ) : (
            <>
              <CurrentPlanPanel baseline={scenarioBaseline} />
              <EstimatePanel pending={scenarios.pending} result={scenarios.result} />
              {/* The per-change-size skill describes the speed model; for an unchanged
                  plan or a frequency-only edit that model is not used, so the card
                  falls back to Edmonton's overall held-out figure. */}
              <ModelCardPanel designDistance={scenarioUnchanged || scenarioSpeedHeld ? null : scenarios.result?.estimate.design_distance ?? null} model={scenarios.model} />
            </>
          )}
          {scenarios.error && <p className="scenario-error" role="status">{scenarios.error}</p>}
        </section>}

        {mode === "live" && <section className="network-intelligence" aria-label="Network issues and service alerts">
          <div className="intelligence-column">
            <div className="panel-heading"><div><p className="eyebrow">PRIORITY QUEUE</p><h2>Routes needing attention</h2></div><span>{flaggedRouteCount > 5 ? `top 5 of ${flaggedRouteCount} flagged` : `${flaggedRouteCount} flagged`}</span></div>
            <div className="issue-list">
              {networkHealth?.issues.slice(0, 5).map((issue, index) => (
                <button key={issue.route_id} onClick={() => setSelectedRouteId(issue.route_id)} type="button">
                  <span className="issue-rank">{String(index + 1).padStart(2, "0")}</span>
                  <span className="issue-route">Route {routeDisplayName(issue)}<small>{issue.long_name ?? issue.route_id}</small></span>
                  <span className={`status-badge ${statusClass(issue.service_status)}`}>{statusLabel(issue.service_status)}</span>
                  <span className="issue-evidence">{issueEvidence(issue)}</span>
                </button>
              ))}
              {networkHealth && networkHealth.issues.length === 0 && <p className="panel-empty">{networkHealth.stale ? "Live vehicle snapshot is stale; current route issues are withheld." : "No current route-level exceptions detected."}</p>}
              {!networkHealth && <p className="panel-empty">{apiStatus === "offline" ? API_NOT_RESPONDING : "Loading live network health…"}</p>}
            </div>
          </div>
          <div className="intelligence-column alerts-column">
            <div className="panel-heading"><div><p className="eyebrow">SERVICE ALERTS</p><h2>Publisher notices</h2></div><span>{networkAlerts.length} active</span></div>
            <div className="alert-list">
              {groupedAlerts.slice(0, 4).map((alert) => (
                <button disabled={!alert.affected_routes.length} key={alert.id} onClick={() => alert.affected_routes[0] && setSelectedRouteId(alert.affected_routes[0])} type="button">
                  <span className="alert-effect">{alert.effect?.replaceAll("_", " ") ?? "NOTICE"}</span>
                  <strong>{alert.header ?? "Service advisory"}</strong>
                  <small>{alert.affected_routes.length ? `Routes ${alert.affected_routes.slice(0, 4).join(", ")}` : "Network or stop-specific notice"}</small>
                  <time>{formatAlertPeriod(alert)}</time>
                </button>
              ))}
              {networkAlerts.length === 0 && <p className="panel-empty">No publisher alerts are available.</p>}
            </div>
          </div>
        </section>}

        {mode === "replay" && <section className="network-intelligence" aria-label="Replay evidence at the playhead">
          <div className="intelligence-column">
            <div className="panel-heading"><div><p className="eyebrow">AT THE PLAYHEAD</p><h2>Recorded vehicles</h2></div><span>{plural(displayedVehicles?.features.length ?? 0, "vehicle")}</span></div>
            <div className="issue-list">
              {(displayedVehicles?.features ?? []).slice(0, 40).map((feature, index) => (
                <button
                  className={`issue-row ${selectedVehicleId === feature.properties.vehicle_id ? "selected" : ""}`}
                  key={`${feature.properties.vehicle_id}-${index}`}
                  onClick={() => setSelectedVehicleId(feature.properties.vehicle_id)}
                  type="button"
                >
                  <span className="issue-rank">{index + 1}</span>
                  <span className="issue-route">Vehicle {feature.properties.vehicle_id}<small>{feature.properties.route_id ? `Route ${feature.properties.route_id}` : "No route reported"}</small></span>
                  <span className={`status-badge ${feature.properties.delay_seconds === null ? "" : feature.properties.delay_seconds > 300 ? "major-delay" : feature.properties.delay_seconds > 60 ? "minor-delay" : feature.properties.delay_seconds < -60 ? "early" : "on-time"}`}>
                    {formatSignedSeconds(feature.properties.delay_seconds ?? null)}
                  </span>
                  <span className="issue-evidence">{formatRealtimeTime(feature.properties.timestamp)}</span>
                </button>
              ))}
              {!displayedVehicles?.features.length && <p className="panel-empty">No recorded vehicles at this playhead. Move the playhead or widen the window.</p>}
            </div>
          </div>
          <div className="intelligence-column">
            <div className="panel-heading"><div><p className="eyebrow">LOADED WINDOW</p><h2>Observation coverage</h2></div><span>{replayHistory?.features.length.toLocaleString() ?? "—"} loaded</span></div>
            {replayCoverage.length > 0 ? <>
              <div className="delay-plot">
                <div className="delay-axis" aria-hidden="true">
                  <span style={{ bottom: "100%" }}>{replayCoveragePeak}</span>
                  <span style={{ bottom: "0%" }}>0</span>
                </div>
                <div className="timeline-bars large">
                  {replayCoverage.map((bucket) => (
                    <div className="timeline-bar-slot" key={bucket.start} title={`${formatReplayTime(bucket.start)}: ${plural(bucket.count, "observation")}`}>
                      <i className={bucket.count ? "on-time" : "no-data"} style={{ height: `${bucket.count ? Math.max(6, Math.round((bucket.count / replayCoveragePeak) * 100)) : 100}%` }} />
                    </div>
                  ))}
                </div>
              </div>
              <div className="timeline-labels"><span>{formatReplayTime(replayStart)}</span><span>{formatReplayTime(replayEnd)}</span></div>
              <div className="chart-legend"><span><i className="swatch on-time" />Recorded observations</span><span><i className="swatch no-data" />No records in bucket</span></div>
              <p className="metric-basis">Recorded GTFS-Realtime vehicle positions in this window. Gaps are recording gaps, not proof that no service ran.</p>
            </> : <p className="panel-empty">Load a replay window to see recorded coverage.</p>}
          </div>
        </section>}

        {mode === "analytics" && <section className="analytics-workspace" aria-label="Route reliability analytics">
          <div className="analytics-heading">
            <div><p className="eyebrow">RECORDED RELIABILITY</p><h2>{selectedRoute ? routeLabel(selectedRoute) : "Choose a route to begin"}</h2></div>
            {analyticsAvailability?.first_observed_at && analyticsAvailability.last_observed_at && analyticsStart && analyticsEnd && <div className="analytics-heading-actions"><div className="analytics-date-grid period-a">
              <label>Period A from<input aria-label="Reliability analysis start" type="datetime-local" min={toDateTimeLocalValue(analyticsAvailability.first_observed_at)} max={toDateTimeLocalValue(analyticsAvailability.last_observed_at)} value={toDateTimeLocalValue(analyticsStart)} onChange={(event) => { const next = new Date(event.target.value); if (!Number.isNaN(next.valueOf())) { analyticsAutoWindowRef.current = false; setAnalyticsStart(next.toISOString()); }; }} /></label>
              <label>Period A to<input aria-label="Reliability analysis end" type="datetime-local" min={toDateTimeLocalValue(analyticsAvailability.first_observed_at)} max={toDateTimeLocalValue(analyticsAvailability.last_observed_at)} value={toDateTimeLocalValue(analyticsEnd)} onChange={(event) => { const next = new Date(event.target.value); if (!Number.isNaN(next.valueOf())) { analyticsAutoWindowRef.current = false; setAnalyticsEnd(next.toISOString()); }; }} /></label>
            </div><button className={`comparison-toggle ${comparisonEnabled ? "active" : ""}`} disabled={!comparisonStart || !comparisonEnd} onClick={() => setComparisonEnabled((enabled) => !enabled)} type="button">{comparisonEnabled ? "Close comparison" : "Compare periods"}</button></div>}
          </div>
          {comparisonEnabled && analyticsAvailability?.first_observed_at && analyticsAvailability.last_observed_at && comparisonStart && comparisonEnd && <section className="comparison-workspace" aria-label="Historical period comparison">
            <div className="comparison-controls"><div><p className="eyebrow">PERIOD B</p><strong>Choose the comparison window</strong></div><div className="analytics-date-grid">
              <label>From<input aria-label="Comparison period start" type="datetime-local" min={toDateTimeLocalValue(analyticsAvailability.first_observed_at)} max={toDateTimeLocalValue(analyticsAvailability.last_observed_at)} value={toDateTimeLocalValue(comparisonStart)} onChange={(event) => { const next = new Date(event.target.value); if (!Number.isNaN(next.valueOf())) setComparisonStart(next.toISOString()); }} /></label>
              <label>To<input aria-label="Comparison period end" type="datetime-local" min={toDateTimeLocalValue(analyticsAvailability.first_observed_at)} max={toDateTimeLocalValue(analyticsAvailability.last_observed_at)} value={toDateTimeLocalValue(comparisonEnd)} onChange={(event) => { const next = new Date(event.target.value); if (!Number.isNaN(next.valueOf())) setComparisonEnd(next.toISOString()); }} /></label>
            </div></div>
            {!comparison && <p className="comparison-message">{comparisonMessage}</p>}
            {comparison && <><div className={`comparison-verdict ${comparison.comparable ? "comparable" : "guarded"}`}><div><span>{comparison.comparable ? "COMPARABLE PERIODS" : "COMPARISON GUARDED"}</span><strong>{comparison.verdict}</strong></div><small>{Math.round(comparison.coverage_ratio * 100)}% coverage match</small></div>
              <div className="comparison-table" role="table" aria-label="Period A and Period B reliability metrics">
                <div className="comparison-row heading" role="row"><span>Metric</span><strong>Period A</strong><strong>Period B</strong><strong>B − A</strong></div>
                <div className="comparison-row" role="row"><span>Median delay</span><strong>{formatSignedSeconds(comparison.period_a.median_delay_seconds)}</strong><strong>{formatSignedSeconds(comparison.period_b.median_delay_seconds)}</strong><b>{formatSignedSeconds(comparison.deltas_b_minus_a.median_delay_seconds)}</b></div>
                <div className="comparison-row" role="row"><span>P90 delay</span><strong>{formatSignedSeconds(comparison.period_a.percentile_90_delay_seconds)}</strong><strong>{formatSignedSeconds(comparison.period_b.percentile_90_delay_seconds)}</strong><b>{formatSignedSeconds(comparison.deltas_b_minus_a.percentile_90_delay_seconds)}</b></div>
                <div className="comparison-row" role="row"><span>Observed headway</span><strong>{formatSeconds(comparison.period_a.observed_headway_seconds)}</strong><strong>{formatSeconds(comparison.period_b.observed_headway_seconds)}</strong><b>{formatSignedSeconds(comparison.deltas_b_minus_a.observed_headway_seconds)}</b></div>
                <div className="comparison-row" role="row"><span>Headway variability</span><strong>{formatSeconds(comparison.period_a.headway_variability_seconds)}</strong><strong>{formatSeconds(comparison.period_b.headway_variability_seconds)}</strong><b>{formatSignedSeconds(comparison.deltas_b_minus_a.headway_variability_seconds)}</b></div>
                <div className="comparison-row" role="row"><span>Bunching events</span><strong>{comparison.period_a.bunching_event_count ?? "—"}</strong><strong>{comparison.period_b.bunching_event_count ?? "—"}</strong><b>{formatCountDelta(comparison.deltas_b_minus_a.bunching_event_count)}</b></div>
                <div className="comparison-row" role="row"><span>Service gaps</span><strong>{comparison.period_a.service_gap_event_count ?? "—"}</strong><strong>{comparison.period_b.service_gap_event_count ?? "—"}</strong><b>{formatCountDelta(comparison.deltas_b_minus_a.service_gap_event_count)}</b></div>
                <div className="comparison-row" role="row"><span>Recorded coverage</span><strong>{formatCoverage(comparison.period_a.coverage_duration_seconds)}</strong><strong>{formatCoverage(comparison.period_b.coverage_duration_seconds)}</strong><b>{formatSignedSeconds(comparison.deltas_b_minus_a.coverage_duration_seconds)}</b></div>
              </div>
              {comparison.deciding_signals.length > 0 && <p className="comparison-reason">{comparison.deciding_signals.slice(0, 2).join(" ")}</p>}
            </>}
          </section>}
          {!analytics && <div className="analytics-empty"><strong>{analyticsMessage}</strong><span>TransitPulse reports insufficient history instead of estimating unsupported metrics.</span></div>}
          {analytics && !analytics.sufficient_history && <div className="analytics-empty insufficient"><strong>Insufficient recorded history</strong><span>{analytics.sufficiency_reason}</span><small>{analytics.observation_count.toLocaleString()} observations from {analytics.observed_vehicle_count} vehicles across {formatCoverage(analytics.coverage_duration_seconds)}.</small></div>}
          {analytics?.sufficient_history && <div className="analytics-grid">
            <article className="chart-card delay-chart"><div className="chart-title"><div><span>OBSERVED</span><h3>Median delay over time</h3></div><strong>{analytics.delay_distribution.sample_count.toLocaleString()} delay samples</strong></div>
              <div className="delay-plot">
                <div className="delay-axis" aria-hidden="true">
                  {/* Bars and ticks share one scale fitted to the data, measured
                      from the bars' baseline above the value labels. A fixed
                      10-minute scale left typical delays as slivers and piled
                      every tick label into the bottom fifth of the axis. */}
                  {delayAxisTicks(analytics.through_time).map((tick) => (
                    <span key={tick} style={{ bottom: `calc(${DELAY_LABEL_PX}px + (100% - ${DELAY_LABEL_PX}px) * ${tick / delayAxisCeiling(analytics.through_time)})` }}>{formatAxisMinutes(tick)}</span>
                  ))}
                </div>
                <div className="timeline-bars large">{analytics.through_time.map((bucket) => {
                  const hasData = bucket.delay_observation_count > 0;
                  const fraction = hasData ? Math.max(0.04, Math.abs(bucket.median_delay_seconds ?? 0) / delayAxisCeiling(analytics.through_time)) : 1;
                  return (
                    <div
                      className="timeline-bar-slot"
                      key={bucket.start}
                      title={hasData
                        ? `${formatReplayTime(bucket.start)}: ${formatSignedSeconds(bucket.median_delay_seconds)} (${plural(bucket.delay_observation_count, "sample")})`
                        : `${formatReplayTime(bucket.start)}: no observations recorded`}
                    >
                      <i
                        className={hasData ? ((bucket.median_delay_seconds ?? 0) > 300 ? "late" : (bucket.median_delay_seconds ?? 0) > 60 ? "minor" : "on-time") : "no-data"}
                        style={{ height: `calc((100% - ${DELAY_LABEL_PX}px) * ${fraction})` }}
                      />
                      <b className={hasData ? "" : "muted"}>{hasData ? formatSignedSeconds(bucket.median_delay_seconds) : "no data"}</b>
                    </div>
                  );
                })}</div>
              </div>
              <div className="timeline-labels"><span>{formatReplayTime(analytics.start)}</span><span>{formatReplayTime(analytics.end)}</span></div>
              <div className="chart-legend"><span><i className="swatch on-time" />Within 1 min</span><span><i className="swatch minor" />1–5 min late</span><span><i className="swatch late" />Over 5 min late</span><span><i className="swatch no-data" />No observations</span></div>
              <div className="period-extremes"><span>Best <strong>{analytics.best_period_start ? formatReplayTime(analytics.best_period_start) : "—"}</strong></span><span>Worst <strong>{analytics.worst_period_start ? formatReplayTime(analytics.worst_period_start) : "—"}</strong></span></div>
            </article>
            <article className="chart-card headway-card"><div className="chart-title"><div><span>OBSERVED vs SCHEDULED · ONE STOP</span><h3>Service regularity</h3></div></div>
              <p className="metric-basis">{analytics.observed_headways.stop_name ?? "No matched stop"}{analytics.observed_headways.direction_id !== null && analytics.observed_headways.direction_id !== undefined ? ` · direction ${analytics.observed_headways.direction_id}` : ""} · {plural(analytics.observed_headways.sample_count, "adjacent headway")}. Not a route-wide headway.</p>
              {analytics.observed_headways.sufficient && analytics.scheduled_headways.sufficient ? <><div className="headway-bars"><div><span>Observed</span><i style={{ width: `${Math.min(100, ((analytics.observed_headways.median_seconds ?? 0) / Math.max(analytics.observed_headways.median_seconds ?? 1, analytics.scheduled_headways.median_seconds ?? 1)) * 100)}%` }} /><strong>{formatSeconds(analytics.observed_headways.median_seconds)}</strong></div><div><span>Scheduled</span><i className="scheduled" style={{ width: `${Math.min(100, ((analytics.scheduled_headways.median_seconds ?? 0) / Math.max(analytics.observed_headways.median_seconds ?? 1, analytics.scheduled_headways.median_seconds ?? 1)) * 100)}%` }} /><strong>{formatSeconds(analytics.scheduled_headways.median_seconds)}</strong></div></div>
              {analytics.observed_headways.sample_count >= MIN_HEADWAY_EVENT_SAMPLES
                ? <div className="headway-events"><span><strong>{analytics.observed_headways.bunching_event_count}</strong> bunching</span><span><strong>{analytics.observed_headways.service_gap_event_count}</strong> {analytics.observed_headways.service_gap_event_count === 1 ? "gap" : "gaps"}</span><span><strong>{formatSeconds(analytics.observed_headways.variability_seconds)}</strong> variability</span></div>
                : <p className="metric-basis withheld">Bunching, gap and variability counts are withheld below {MIN_HEADWAY_EVENT_SAMPLES} intervals.</p>}</> : <div className="metric-insufficient"><strong>Insufficient observed headways</strong><span>{analytics.observed_headways.sample_count} comparable adjacent entries at {analytics.observed_headways.stop_name ?? "the sampled stop"}.</span></div>}
            </article>
            <article className="chart-card distribution-card"><div className="chart-title"><div><span>OBSERVED</span><h3>Delay distribution</h3></div><strong>{analytics.delay_bands.sample_count.toLocaleString()} samples</strong></div>
              <div className="band-list">{delayBandRows.map((band) => <div key={band.label}><span>{band.label}</span><i><b className={band.tone} style={{ width: `${analytics.delay_bands.sample_count ? Math.max(2, band.count / analytics.delay_bands.sample_count * 100) : 0}%` }} /></i><strong>{band.count.toLocaleString()}</strong></div>)}</div>
            </article>
            <article className="chart-card evidence-card"><div className="chart-title"><div><span>DATA QUALITY</span><h3>Coverage and evidence</h3></div></div>
              <dl><div><dt>Distinct vehicles</dt><dd>{analytics.observed_vehicle_count}</dd></div><div><dt>Coverage</dt><dd>{formatCoverage(analytics.coverage_duration_seconds)}</dd></div><div><dt>Stop evidence</dt><dd>{analytics.spatial_reliability.features.length} stops</dd></div><div><dt>Analysis window</dt><dd>{formatCoverage((Date.parse(analytics.end) - Date.parse(analytics.start)) / 1000)}</dd></div></dl>
              <p>Delays are recorded observations. Headways come from recorded stop arrivals; scheduled values come from the static GTFS timetable.</p>
            </article>
          </div>}
        </section>}
      </section>

      <aside className="route-sidebar">
        <header className="sidebar-header">
          <p className="eyebrow">{mode === "replay" ? "HISTORICAL REPLAY" : mode === "analytics" ? "ROUTE CONTEXT" : mode === "scenarios" ? "PLANNING SCENARIO" : "LIVE OPERATIONS"}</p>
          <h1>{mode === "analytics" ? "Route reliability" : mode === "replay" ? "Replay workspace" : mode === "scenarios" ? "Service design" : "Route inspector"}</h1>
          <p className={`network-status ${mode === "scenarios" ? "ready" : networkState}`}>
            {mode === "scenarios"
              ? scenarios.routes.length
                ? `${new Set(scenarios.routes.map((route) => route.route_label)).size} Edmonton-region routes · published schedules`
                : scenarios.unavailableReason ? "Scenario estimator unavailable" : "Loading scenario routes…"
              : networkMessage}
          </p>
          {mode === "scenarios" ? (
            // Live vehicle counts are irrelevant to a scheduled-design question,
            // and printing them here invited reading a live position as evidence
            // for a modelled claim — exactly what the map deliberately avoids.
            <p className="realtime-status">
              <i className="status-dot online" /> Scheduled service design
              <small> · no live data is used in this mode</small>
            </p>
          ) : mode === "replay" ? (
            <p className="realtime-status"><i className="status-dot online" /> {displayedVehicles?.features.length ?? 0} replay vehicles <small> · {formatReplayTime(replayTimestamp)}</small></p>
          ) : mode === "analytics" ? (
            <p className="realtime-status"><i className="status-dot online" /> Recorded history <small> · {analyticsAvailability?.observation_count ? `${analyticsAvailability.observation_count.toLocaleString()} observations for this route` : "no live data is used in this view"}</small></p>
          ) : (
            <p className={`realtime-status ${realtimeStatus?.stale ? "stale" : ""}`}>
              <i className={`status-dot ${realtimeStatus && !realtimeStatus.stale ? "online" : "offline"}`} />
              {realtimeStatus?.stale ? "Live feed stale" : `${realtimeStatus?.active_vehicles ?? 0} live vehicles`}
              <small> · updated {formatRealtimeTime(vehicleFeed?.source_timestamp ?? vehicleFeed?.last_success_at)}</small>
            </p>
          )}
        </header>

        <section className="replay-controls" aria-label="Historical playback controls">
          {mode === "replay" && <p className="eyebrow">PLAYBACK CONTROLS</p>}
          {mode === "replay" && <>
            <p className="replay-availability">
              {replayAvailability?.first_observed_at && replayAvailability.last_observed_at
                ? <>Available: {formatReplayTime(replayAvailability.first_observed_at)} – {formatReplayTime(replayAvailability.last_observed_at)}</>
                : "Loading available recorded history…"}
            </p>
            {replayAvailability?.first_observed_at && replayAvailability.last_observed_at && replayStart && replayEnd && <div className="replay-date-grid">
              <label>From<input aria-label="Replay start" type="datetime-local" min={toDateTimeLocalValue(replayAvailability.first_observed_at)} max={toDateTimeLocalValue(replayAvailability.last_observed_at)} value={toDateTimeLocalValue(replayStart)} onChange={(event) => {
                const next = new Date(event.target.value);
                if (Number.isNaN(next.valueOf())) return;
                setReplayPlaying(false);
                setReplayStart(next.toISOString());
                setReplayTimestamp((current) => current && Date.parse(current) < next.valueOf() ? next.toISOString() : current);
              }} /></label>
              <label>To<input aria-label="Replay end" type="datetime-local" min={toDateTimeLocalValue(replayAvailability.first_observed_at)} max={toDateTimeLocalValue(replayAvailability.last_observed_at)} value={toDateTimeLocalValue(replayEnd)} onChange={(event) => {
                const next = new Date(event.target.value);
                if (Number.isNaN(next.valueOf())) return;
                setReplayPlaying(false);
                setReplayEnd(next.toISOString());
                setReplayTimestamp((current) => current && Date.parse(current) > next.valueOf() ? next.toISOString() : current);
              }} /></label>
            </div>}
            <p className="replay-clock">Replay time <strong>{formatReplayTime(replayTimestamp)}</strong></p>
            <input aria-label="Replay timeline" className="replay-timeline" disabled={!replayStart || !replayEnd || !replayTimestamp} max="1000" min="0" onChange={(event) => {
              if (!replayStart || !replayEnd) return;
              setReplayPlaying(false);
              const next = replayTimestampAtFraction(Number(event.target.value) / 1000, Date.parse(replayStart), Date.parse(replayEnd));
              setReplayTimestamp(new Date(next).toISOString());
            }} type="range" value={Math.round(replayProgress)} />
            <div className="replay-actions">
              <button aria-label="Jump backward 30 seconds" disabled={!replayTimestamp || !replayStart || !replayEnd} onClick={() => {
                if (!replayTimestamp || !replayStart || !replayEnd) return;
                setReplayPlaying(false);
                setReplayTimestamp(new Date(clampReplayTimestamp(Date.parse(replayTimestamp) - 30_000, Date.parse(replayStart), Date.parse(replayEnd))).toISOString());
              }} type="button">−30s</button>
              <button className="primary" disabled={!replayHistory?.features.length} onClick={() => {
                if (replayTimestamp && replayStart && replayEnd && Date.parse(replayTimestamp) >= Date.parse(replayEnd)) setReplayTimestamp(replayStart);
                setReplayPlaying((playing) => !playing);
              }} type="button">{replayPlaying ? "Pause" : "Play"}</button>
              <button aria-label="Jump forward 30 seconds" disabled={!replayTimestamp || !replayStart || !replayEnd} onClick={() => {
                if (!replayTimestamp || !replayStart || !replayEnd) return;
                setReplayPlaying(false);
                setReplayTimestamp(new Date(clampReplayTimestamp(Date.parse(replayTimestamp) + 30_000, Date.parse(replayStart), Date.parse(replayEnd))).toISOString());
              }} type="button">+30s</button>
            </div>
            <div className="speed-row" aria-label="Replay speed">
              {[1, 5, 20, 60].map((speed) => <button className={replaySpeed === speed ? "active" : ""} key={speed} onClick={() => setReplaySpeed(speed)} type="button">{speed}x</button>)}
            </div>
            {replayMessage && <p className="replay-message">{replayMessage}</p>}
          </>}
        </section>

        {mode === "scenarios" ? (
          <ScenarioControls
            baseline={scenarioBaseline}
            edits={scenarios.edits}
            onEdit={scenarios.edit}
            onReset={scenarios.reset}
            onSearch={scenarios.setSearch}
            onSelect={scenarios.select}
            routes={scenarios.routes}
            search={scenarios.search}
            selectedKey={scenarios.selectedKey}
          />
        ) : (
        <>
        <div className="route-controls">
          <label className="search-label" htmlFor="route-search">Search routes</label>
          <input id="route-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Number or destination" />
          {selectedRouteId && <button className="clear-route" onClick={() => setSelectedRouteId(null)} type="button">Clear selection</button>}
          <div className="filter-row" aria-label="Route type filter">
            {(["all", "bus", "rail"] as const).map((option) => (
              <button key={option} className={filter === option ? "active" : ""} onClick={() => setFilter(option)} type="button">
                {option === "all" ? "All" : option === "bus" ? "Bus" : "Rail"}
              </button>
            ))}
          </div>
        </div>

        <div className="route-list" aria-live="polite">
          {networkState === "loading" && <p className="empty-message">Loading routes…</p>}
          {networkState === "empty" && <p className="empty-message">Import a GTFS feed, then refresh this viewer.</p>}
          {networkState === "error" && <p className="empty-message">{networkMessage}</p>}
          {networkState === "ready" && filteredRoutes.map((route) => (
            <button
              className={`route-row ${selectedRouteId === route.route_id ? "selected" : ""}`}
              title={routeLabel(route)}
              key={route.route_id}
              onClick={() => setSelectedRouteId(route.route_id)}
              type="button"
            >
              <span className="route-chip" style={{ backgroundColor: `#${route.color ?? "334155"}`, color: `#${route.text_color ?? "FFFFFF"}` }}>{route.short_name ?? route.route_id}</span>
              <span className="route-copy"><strong title={routeLabel(route)}>{routeLabel(route)}</strong><small>{route.agency_name} · {routeKind(route.route_type)}</small></span>
              {/* Live status badges belong to LIVE only: beside recorded history
                  they read as a finding about the period being analysed. */}
              {mode === "live" && <span className={`route-health ${statusClass(networkStatusByRoute.get(route.route_id)?.service_status)}`}>{statusLabel(networkStatusByRoute.get(route.route_id)?.service_status)}</span>}
            </button>
          ))}
          {networkState === "ready" && filteredRoutes.length === 0 && <p className="empty-message">No routes match that filter.</p>}
        </div>

        <section className="route-detail" aria-live="polite">
          {routeLoading && <p className="detail-muted">Loading route details…</p>}
          {!routeLoading && !selectedRoute && <p className="detail-muted">Choose a route to show its shape and stops.</p>}
          {!routeLoading && selectedRoute && <>
            <p className="eyebrow">SELECTED ROUTE</p>
            <h2>{routeLabel(selectedRoute)}</h2>
            <p>{selectedRoute.description ?? "Static schedule geometry and stops from the selected GTFS feed."}</p>
            <dl><div><dt>Agency</dt><dd>{selectedRoute.agency_name}</dd></div><div><dt>Timetable</dt><dd title={`Static GTFS feed ${selectedRoute.feed_id}`}>Published GTFS</dd></div></dl>
            {mode !== "live" ? <p className="replay-route-note">{mode === "replay" ? "Static route details remain selected. Current live operations are deliberately hidden while recorded positions are replaying." : "This view uses recorded history only. Current live status for this route is shown in LIVE."}</p> : <div className="operations-summary">
              <div className={`service-state ${statusClass(operations?.service_status)}`}>
                <span>Service status</span><strong>{statusLabel(operations?.service_status)}</strong>
              </div>
              <div className="metric-grid">
                <div><span>Active</span><strong>{vehicleState === "ready" ? operations?.active_vehicles ?? 0 : "—"}</strong></div>
                <div><span>Avg delay</span><strong>{formatSeconds(operations?.average_delay_seconds ?? null)}</strong></div>
                <div><span>Alerts</span><strong>{operations?.alert_count ?? 0}</strong></div>
              </div>
              <p className="operations-reason">{operations?.status_reason ?? "Loading current route operations…"}</p>
              {operations?.headway_baseline_seconds !== null && operations?.headway_baseline_seconds !== undefined && <p className="headway-note">{routeHeadwayEvidence(operations)}</p>}
              {operations && horizonExclusionNote(operations) && <p className="headway-note excluded">{horizonExclusionNote(operations)}</p>}
              {routeAlerts.length > 0 && <div className="route-alerts">{routeAlerts.slice(0, 2).map((alert) => <p key={alert.id}><strong>{alert.effect?.replaceAll("_", " ") ?? "Alert"}:</strong> {alert.header ?? alert.description ?? "Service disruption"}<span>{formatAlertPeriod(alert)}</span></p>)}</div>}
            </div>}
            <section className="reliability-summary" aria-label="Recorded route reliability analytics">
              <p className="eyebrow">RELIABILITY ANALYTICS</p>
              <p className="reliability-intro">Recorded-history analysis for this route. Direct observations and the static timetable remain separate.</p>
              {analyticsAvailability?.first_observed_at && analyticsAvailability.last_observed_at && analyticsStart && analyticsEnd && <div className="analytics-date-grid">
                <label>From<input aria-label="Reliability analysis start" type="datetime-local" min={toDateTimeLocalValue(analyticsAvailability.first_observed_at)} max={toDateTimeLocalValue(analyticsAvailability.last_observed_at)} value={toDateTimeLocalValue(analyticsStart)} onChange={(event) => {
                  const next = new Date(event.target.value);
                  if (Number.isNaN(next.valueOf())) return;
                  { analyticsAutoWindowRef.current = false; setAnalyticsStart(next.toISOString()); };
                }} /></label>
                <label>To<input aria-label="Reliability analysis end" type="datetime-local" min={toDateTimeLocalValue(analyticsAvailability.first_observed_at)} max={toDateTimeLocalValue(analyticsAvailability.last_observed_at)} value={toDateTimeLocalValue(analyticsEnd)} onChange={(event) => {
                  const next = new Date(event.target.value);
                  if (Number.isNaN(next.valueOf())) return;
                  { analyticsAutoWindowRef.current = false; setAnalyticsEnd(next.toISOString()); };
                }} /></label>
              </div>}
              <p className="analytics-message">{analyticsMessage}</p>
              {analytics && !canDisplayReliabilityEstimates(analytics.sufficient_history, analytics.delay_distribution.sufficient) && <div className="analytics-empty insufficient compact"><strong>Insufficient recorded history</strong><span>{analytics.sufficiency_reason}</span><small>{analytics.observation_count.toLocaleString()} observations · delay estimates are hidden until the configured evidence minimums are met.</small></div>}
              {analytics && canDisplayReliabilityEstimates(analytics.sufficient_history, analytics.delay_distribution.sufficient) && <>
                <p className="analytics-source">RECORDED OBSERVATIONS · delay samples {analytics.delay_distribution.sample_count.toLocaleString()}</p>
                <div className="metric-grid reliability-metrics">
                  <div><span>Median delay</span><strong>{formatSignedSeconds(analytics.delay_distribution.median_seconds)}</strong></div>
                  <div><span>P10 delay</span><strong>{formatSignedSeconds(analytics.delay_distribution.percentile_10_seconds)}</strong></div>
                  <div><span>P90 delay</span><strong>{formatSignedSeconds(analytics.delay_distribution.percentile_90_seconds)}</strong></div>
                  <div><span>Range samples</span><strong>{analytics.observation_count.toLocaleString()}</strong></div>
                </div>
                <div className="reliability-timeline" aria-label="Direct recorded median delay through time">
                  <div className="timeline-heading"><strong>Recorded delay through time</strong><span>bar height = median delay</span></div>
                  <div className="timeline-bars">
                    {analytics.through_time.map((bucket) => <div className="timeline-bar-slot" key={bucket.start} title={`${formatReplayTime(bucket.start)} – ${formatReplayTime(bucket.end)}: ${bucket.delay_observation_count} delay samples, median ${formatSignedSeconds(bucket.median_delay_seconds)}`}>
                      <i className={bucket.late_observation_count > 0 ? "late" : bucket.delay_observation_count ? "on-time" : "unknown"} style={{ height: `${reliabilityBarHeight(bucket.median_delay_seconds)}%` }} />
                    </div>)}
                  </div>
                  <div className="timeline-labels"><span>{formatReplayTime(analytics.start)}</span><span>{formatReplayTime(analytics.end)}</span></div>
                </div>
                <div className="headway-comparison">
                  <p className="analytics-source">HEADWAYS AT {analytics.observed_headways.stop_name ?? "NO MATCHED STOP"}</p>
                  <div><span>OBSERVED · RECORDED STOP ARRIVALS</span><strong>{formatSeconds(analytics.observed_headways.median_seconds)}</strong><small>{plural(analytics.observed_headways.sample_count, "adjacent headway")} · {analytics.observed_headways.bunching_event_count} bunching · {plural(analytics.observed_headways.service_gap_event_count, "gap")}</small></div>
                  <div><span>SCHEDULED · GTFS TIMETABLE</span><strong>{formatSeconds(analytics.scheduled_headways.median_seconds)}</strong><small>{analytics.scheduled_headways.sample_count} scheduled adjacent headways</small></div>
                  <p>Median deviation: <strong>{formatSignedSeconds(analytics.median_headway_deviation_seconds)}</strong> versus scheduled.</p>
                </div>
                <div className="spatial-reliability-note">
                  <p className="analytics-source">SPATIAL RELIABILITY VIEW</p>
                  <p><i className="reliability-marker late" /> Late-record marker <i className="reliability-marker on-time" /> Recorded delay within tolerance <i className="reliability-marker unknown" /> No recorded delay value</p>
                  <p>Markers on the map are sized by recorded stop arrivals and open a stop-level summary.</p>
                  {analytics.spatial_reliability.features.length > 0 && <ul>{analytics.spatial_reliability.features.slice(0, 3).map((feature) => <li key={feature.properties.stop_id}>{feature.properties.name}: {feature.properties.observation_count} entries · {formatSignedSeconds(feature.properties.median_delay_seconds)} median</li>)}</ul>}
                </div>
                <details className="analytics-notes"><summary>Method and source notes</summary>{analytics.data_notes.map((note) => <p key={note}>{note}</p>)}</details>
              </>}
            </section>
          </>}
        </section>

        {selectedVehicle && <section className="vehicle-detail" aria-live="polite">
          <p className="eyebrow">SELECTED VEHICLE</p>
          <h2>Vehicle {selectedVehicle.vehicle_id}</h2>
          <dl>
            <div><dt>Route</dt><dd>{selectedVehicle.route_id ?? "Unmatched"}</dd></div>
            <div><dt>Delay</dt><dd>{formatDelay(selectedVehicle.delay_seconds)}</dd></div>
            <div><dt>Status</dt><dd>{selectedVehicle.current_status?.replaceAll("_", " ") ?? "Unknown"}</dd></div>
          </dl>
        </section>}
        </>
        )}

        <footer className="service-footer">
          <span><i className={`status-dot ${apiStatus}`} /> API {apiStatus}</span>
          <span><i className={`status-dot ${databaseStatus}`} /> PostGIS {databaseStatus}</span>
        </footer>
      </aside>
    </main>
  );
}
