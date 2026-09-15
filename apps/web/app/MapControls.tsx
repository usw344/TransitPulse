"use client";

/**
 * Application-owned map controls.
 *
 * MapLibre's stock NavigationControl is a recognisable piece of someone else's
 * UI: it announces "this is an embedded web map" in the corner of a product
 * that is otherwise a transit operations console. These are the same actions in
 * TransitPulse's own visual language, plus the ones a planner actually reaches
 * for — fit the route, fit the network, and toggle the three data layers — which
 * the stock control does not offer at all.
 */

export interface MapLayerToggles {
  routes: boolean;
  stops: boolean;
  vehicles: boolean;
}

export function MapControls({
  layers,
  onToggle,
  onZoom,
  onFitRoute,
  onFitNetwork,
  canFitRoute,
  showVehicleToggle,
}: {
  layers: MapLayerToggles;
  onToggle: (layer: keyof MapLayerToggles) => void;
  onZoom: (delta: number) => void;
  onFitRoute: () => void;
  onFitNetwork: () => void;
  canFitRoute: boolean;
  showVehicleToggle: boolean;
}) {
  return (
    <div className="map-controls">
      <div className="map-control-group" role="group" aria-label="Zoom">
        <button aria-label="Zoom in" onClick={() => onZoom(1)} title="Zoom in" type="button">
          +
        </button>
        <button aria-label="Zoom out" onClick={() => onZoom(-1)} title="Zoom out" type="button">
          −
        </button>
      </div>

      <div className="map-control-group" role="group" aria-label="Framing">
        <button
          disabled={!canFitRoute}
          onClick={onFitRoute}
          title={canFitRoute ? "Zoom to the selected route" : "Select a route first"}
          type="button"
        >
          Fit route
        </button>
        <button onClick={onFitNetwork} title="Zoom to the whole network" type="button">
          Fit network
        </button>
      </div>

      <div className="map-control-group layers" role="group" aria-label="Map layers">
        <span>Layers</span>
        <button
          aria-pressed={layers.routes}
          className={layers.routes ? "on" : ""}
          onClick={() => onToggle("routes")}
          type="button"
        >
          Routes
        </button>
        <button
          aria-pressed={layers.stops}
          className={layers.stops ? "on" : ""}
          onClick={() => onToggle("stops")}
          type="button"
        >
          Stops
        </button>
        {showVehicleToggle ? (
          <button
            aria-pressed={layers.vehicles}
            className={layers.vehicles ? "on" : ""}
            onClick={() => onToggle("vehicles")}
            type="button"
          >
            Vehicles
          </button>
        ) : null}
      </div>
    </div>
  );
}
