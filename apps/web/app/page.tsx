"use client";

import "maplibre-gl/dist/maplibre-gl.css";

import {
  AttributionControl,
  LngLatBounds,
  Map,
  NavigationControl,
  Popup,
  type GeoJSONSource,
} from "maplibre-gl";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Feature, FeatureCollection, LineString, Point } from "geojson";

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
}

interface RealtimeAlert {
  id: string;
  header: string | null;
  description: string | null;
  affected_routes: string[];
}

const EMPTY_COLLECTION: FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

const EDMONTON_CENTER: [number, number] = [-113.4938, 53.5461];
const MAP_STYLE = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

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
  vehicles,
}: {
  networkShapes: GeoJsonFeatureCollection | null;
  selectedShapes: GeoJsonFeatureCollection | null;
  selectedStops: GeoJsonFeatureCollection | null;
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
      <rect fill="#dbe7e8" fillOpacity="0.2" height="1000" width="1000" />
      <g className="fallback-grid" aria-hidden="true">
        {[150, 300, 450, 600, 750, 900].map((position) => (
          <path d={`M0 ${position} H1000 M${position} 0 V1000`} key={position} />
        ))}
      </g>
      <path
        aria-hidden="true"
        className="fallback-river"
        d="M-40 610 C110 555 165 655 270 600 S430 555 540 600 S705 670 815 585 S950 525 1040 570"
      />
      <text className="fallback-city-label" x="500" y="420">EDMONTON</text>
      <text className="fallback-city-subtitle" x="500" y="446">ALBERTA · LIVE TRANSIT NETWORK</text>
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
      <g className="fallback-vehicles" aria-label={`${vehicles?.features.length ?? 0} live vehicles`}>
        {vehicles?.features.map((feature) => {
          const [x, y] = fallbackPoint(feature.geometry.coordinates);
          return <circle cx={x} cy={y} key={feature.properties?.vehicle_id ?? `${x}-${y}`} r="4.6" />;
        })}
      </g>
    </svg>
  );
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

function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  return fetch(url, { cache: "no-store", signal }).then(async (response) => {
    if (!response.ok) {
      const detail = await response.json().catch(() => null) as { detail?: string } | null;
      throw new Error(detail?.detail ?? `Request failed (${response.status})`);
    }
    return response.json() as Promise<T>;
  });
}

function feedStatus(status: RealtimeStatus | null, kind: string) {
  return status?.feeds.find((feed) => feed.feed_kind === kind) ?? null;
}

function formatRealtimeTime(value: string | null | undefined): string {
  if (!value) return "Awaiting feed";
  return new Intl.DateTimeFormat("en-CA", { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
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

export default function Home() {
  const mapContainerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<Map | null>(null);
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
  const [operations, setOperations] = useState<RouteOperations | null>(null);
  const [routeAlerts, setRouteAlerts] = useState<RealtimeAlert[]>([]);

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
  }, [selectedRouteId]);

  useEffect(() => {
    if (!mapContainerRef.current || mapRef.current) return;
    const map = new Map({
      container: mapContainerRef.current,
      style: MAP_STYLE,
      center: EDMONTON_CENTER,
      zoom: 10.5,
      attributionControl: false,
    });
    map.on("error", (event) => {
      const reason = event.error instanceof Error ? event.error.message : "MapLibre could not render the map";
      setNetworkState("error");
      setNetworkMessage(`Map error: ${reason}`);
    });
    map.addControl(new NavigationControl({ showCompass: false }), "bottom-right");
    map.addControl(new AttributionControl({ compact: true }), "bottom-right");
    map.on("load", () => {
      map.addSource("network-routes", { type: "geojson", data: EMPTY_COLLECTION });
      map.addLayer({
        id: "network-routes-line",
        type: "line",
        source: "network-routes",
        paint: {
          "line-color": "#64748b",
          "line-width": 1.25,
          "line-opacity": 0.42,
        },
      });
      map.addSource("selected-route", { type: "geojson", data: EMPTY_COLLECTION });
      map.addLayer({
        id: "selected-route-casing",
        type: "line",
        source: "selected-route",
        paint: { "line-color": "#ffffff", "line-width": 7, "line-opacity": 0.92 },
      });
      map.addLayer({
        id: "selected-route-line",
        type: "line",
        source: "selected-route",
        paint: {
          "line-color": ["coalesce", ["get", "color"], "#ea580c"],
          "line-width": 4.5,
          "line-opacity": 1,
        },
      });
      map.addSource("selected-stops", { type: "geojson", data: EMPTY_COLLECTION });
      map.addLayer({
        id: "selected-stops-casing",
        type: "circle",
        source: "selected-stops",
        paint: { "circle-radius": 6, "circle-color": "#ffffff", "circle-stroke-width": 0 },
      });
      map.addLayer({
        id: "selected-stops-dot",
        type: "circle",
        source: "selected-stops",
        paint: { "circle-radius": 3.4, "circle-color": "#0f172a", "circle-stroke-color": "#ffffff", "circle-stroke-width": 1 },
      });
      map.addSource("live-vehicles", { type: "geojson", data: EMPTY_COLLECTION });
      map.addLayer({
        id: "live-vehicles-halo",
        type: "circle",
        source: "live-vehicles",
        paint: { "circle-radius": 9, "circle-color": "#f97316", "circle-opacity": 0.18 },
      });
      map.addLayer({
        id: "live-vehicles-dot",
        type: "circle",
        source: "live-vehicles",
        paint: {
          "circle-radius": ["case", ["boolean", ["feature-state", "selected"], false], 7, 5],
          "circle-color": ["case", ["has", "delay_seconds"], "#f97316", "#0284c7"],
          "circle-stroke-color": "#ffffff",
          "circle-stroke-width": 1.5,
        },
      });
      map.on("click", "selected-stops-dot", (event) => {
        const feature = event.features?.[0];
        const coordinates = (feature?.geometry as Point | undefined)?.coordinates;
        const name = feature?.properties?.name as string | undefined;
        const stopId = feature?.properties?.stop_id as string | undefined;
        if (!coordinates || !name) return;
        new Popup({ offset: 12 })
          .setLngLat(coordinates as [number, number])
          .setHTML(`<strong>${name}</strong><br/><span>${stopId ?? ""}</span>`)
          .addTo(map);
      });
      map.on("mouseenter", "selected-stops-dot", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "selected-stops-dot", () => { map.getCanvas().style.cursor = ""; });
      map.on("click", "live-vehicles-dot", (event) => {
        const feature = event.features?.[0] as Feature<Point, VehicleProperties> | undefined;
        const coordinates = feature?.geometry?.coordinates;
        const properties = feature?.properties;
        if (!coordinates || !properties) return;
        setSelectedVehicleId(properties.vehicle_id);
        new Popup({ offset: 12 })
          .setLngLat(coordinates as [number, number])
          .setHTML(`<strong>Vehicle ${properties.vehicle_id}</strong><br/><span>Route ${properties.route_id ?? "unmatched"} · ${formatDelay(properties.delay_seconds)}</span>`)
          .addTo(map);
      });
      map.on("mouseenter", "live-vehicles-dot", () => { map.getCanvas().style.cursor = "pointer"; });
      map.on("mouseleave", "live-vehicles-dot", () => { map.getCanvas().style.cursor = ""; });
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
    source?.setData(asFeatureCollection(vehicles));
  }, [mapLoaded, vehicles]);

  useEffect(() => {
    if (!selectedRouteId) {
      setSelectedRoute(null);
      setSelectedShapes(null);
      setSelectedStops(null);
      setOperations(null);
      setRouteAlerts([]);
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
    if (!mapLoaded) return;
    const map = mapRef.current;
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
      map.fitBounds(bounds, { padding: { top: 80, bottom: 80, left: 420, right: 80 }, maxZoom: 14, duration: 500 });
    }
  }, [mapLoaded, selectedShapes, selectedStops]);

  const filteredRoutes = useMemo(() => {
    const query = search.trim().toLowerCase();
    return routes.filter((route) => {
      const matchesFilter = filter === "all" || routeKind(route.route_type) === filter;
      const searchable = `${route.route_id} ${route.short_name ?? ""} ${route.long_name ?? ""}`.toLowerCase();
      return matchesFilter && (!query || searchable.includes(query));
    });
  }, [filter, routes, search]);
  const selectedVehicle = useMemo(
    () => vehicles?.features.find((feature) => feature.properties?.vehicle_id === selectedVehicleId)?.properties ?? null,
    [selectedVehicleId, vehicles],
  );
  const vehicleFeed = feedStatus(realtimeStatus, "vehicle_positions");

  return (
    <main className="transit-shell">
      <section className="map-pane" aria-label="Edmonton transit network map">
        <div className="map" ref={mapContainerRef} />
        <TransitMapFallback
          networkShapes={networkShapes}
          selectedShapes={selectedShapes}
          selectedStops={selectedStops}
          vehicles={vehicles}
        />
        <div className="map-brand">
          <span className="pulse-mark" aria-hidden="true">●</span>
          <div><strong>TransitPulse</strong><span>Edmonton live operations</span></div>
        </div>
        <div className="map-legend"><span className="legend-line" /> Static network <span className="legend-vehicle" /> Live vehicles</div>
      </section>

      <aside className="route-sidebar">
        <header className="sidebar-header">
          <p className="eyebrow">LIVE OPERATIONS</p>
          <h1>Explore Edmonton transit</h1>
          <p className={`network-status ${networkState}`}>{networkMessage}</p>
          <p className={`realtime-status ${realtimeStatus?.stale ? "stale" : ""}`}>
            <i className={`status-dot ${realtimeStatus && !realtimeStatus.stale ? "online" : "offline"}`} />
            {realtimeStatus?.stale ? "Live feed stale" : `${realtimeStatus?.active_vehicles ?? 0} live vehicles`}
            <small> · updated {formatRealtimeTime(vehicleFeed?.source_timestamp ?? vehicleFeed?.last_success_at)}</small>
          </p>
        </header>

        <div className="route-controls">
          <label className="search-label" htmlFor="route-search">Search routes</label>
          <input id="route-search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Number or destination" />
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
              key={route.route_id}
              onClick={() => setSelectedRouteId(route.route_id)}
              type="button"
            >
              <span className="route-chip" style={{ backgroundColor: `#${route.color ?? "334155"}`, color: `#${route.text_color ?? "FFFFFF"}` }}>{route.short_name ?? route.route_id}</span>
              <span className="route-copy"><strong>{routeLabel(route)}</strong><small>{route.agency_name} · {routeKind(route.route_type)}</small></span>
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
            <dl><div><dt>Agency</dt><dd>{selectedRoute.agency_name}</dd></div><div><dt>Feed version</dt><dd title={selectedRoute.feed_id}>{selectedRoute.feed_id.slice(0, 8)}</dd></div></dl>
            <div className="operations-summary">
              <div className={`service-state ${operations?.service_status?.toLowerCase().replaceAll("_", "-") ?? "no-live-data"}`}>
                <span>Service status</span><strong>{operations?.service_status?.replaceAll("_", " ") ?? "NO LIVE DATA"}</strong>
              </div>
              <div className="metric-grid">
                <div><span>Active</span><strong>{vehicleState === "ready" ? operations?.active_vehicles ?? 0 : "—"}</strong></div>
                <div><span>Avg delay</span><strong>{formatSeconds(operations?.average_delay_seconds ?? null)}</strong></div>
                <div><span>Alerts</span><strong>{operations?.alert_count ?? 0}</strong></div>
              </div>
              <p className="operations-reason">{operations?.status_reason ?? "Loading current route operations…"}</p>
              {operations?.headway_baseline_seconds !== null && operations?.headway_baseline_seconds !== undefined && <p className="headway-note">Predicted headways at stop {operations.prediction_stop_id ?? "—"}: {operations.predicted_headways_seconds.map((value) => formatSeconds(value)).join(", ")} (baseline {formatSeconds(operations.headway_baseline_seconds)})</p>}
              {routeAlerts.length > 0 && <div className="route-alerts">{routeAlerts.slice(0, 2).map((alert) => <p key={alert.id}><strong>Alert:</strong> {alert.header ?? alert.description ?? "Service disruption"}</p>)}</div>}
            </div>
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

        <footer className="service-footer">
          <span><i className={`status-dot ${apiStatus}`} /> API {apiStatus}</span>
          <span><i className={`status-dot ${databaseStatus}`} /> PostGIS {databaseStatus}</span>
        </footer>
      </aside>
    </main>
  );
}
