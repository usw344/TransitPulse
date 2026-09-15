/**
 * Map symbology owned by TransitPulse rather than inherited from MapLibre.
 *
 * Two things live here: the directional vehicle marker, and the paint
 * expressions that give routes, stops and vehicles a consistent hierarchy
 * across the four product modes.
 *
 * The hierarchy rule the whole map follows: **transit data is always brighter
 * and heavier than geography, and the thing the current mode is about is always
 * the brightest transit data on screen.**  In LIVE that is the vehicles; in
 * ANALYTICS it is the reliability evidence; in SCENARIOS it is the route
 * alignment itself, with no vehicles drawn at all.
 */

import type { ExpressionSpecification } from "maplibre-gl";

export type MapMode = "live" | "replay" | "analytics" | "scenarios";

/** Vehicle states, in the order a dispatcher cares about them. */
export const VEHICLE_COLORS = {
  normal: "#0284c7",
  delayed: "#f97316",
  severe: "#e11d48",
  unknown: "#64748b",
  stale: "#94a3b8",
  selected: "#0f766e",
} as const;

/** Delay thresholds shared with the status badges (MINOR / MAJOR DELAY). */
export const VEHICLE_DELAY_THRESHOLDS = { minorSeconds: 60, majorSeconds: 300 } as const;

export const ROUTE_COLORS = {
  network: "#8399ab",
  selected: "#0f766e",
  selectedCasing: "#ffffff",
  scenario: "#c2410c",
} as const;

/**
 * A white heading chevron drawn directly on the vehicle's coloured state dot.
 *
 * An earlier version put the chevron on a white disc. At network zoom that disc
 * covered most of the dot, leaving a hairline of colour, so the overview read as
 * a map of grey vehicles with no delay information. The dot underneath carries
 * the state; this icon adds only the heading.
 *
 * Drawn to a canvas rather than loaded as a sprite, so it survives a style
 * reload without shipping image files.
 */
export function vehicleIcon(size = 44): { width: number; height: number; data: Uint8Array } | null {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const context = canvas.getContext("2d");
  if (!context) return null;

  const c = size / 2;
  const r = size * 0.34;

  // Chevron pointing up; MapLibre rotates the whole icon by the bearing. A thin
  // dark edge keeps it legible on the pale "no delay data" grey as well.
  context.beginPath();
  context.moveTo(c, c - r * 0.78);
  context.lineTo(c + r * 0.62, c + r * 0.66);
  context.lineTo(c, c + r * 0.26);
  context.lineTo(c - r * 0.62, c + r * 0.66);
  context.closePath();
  context.lineJoin = "round";
  context.lineWidth = size * 0.04;
  context.strokeStyle = "rgba(15,23,42,0.45)";
  context.stroke();
  context.fillStyle = "#ffffff";
  context.fill();

  const image = context.getImageData(0, 0, size, size);
  return { width: size, height: size, data: new Uint8Array(image.data.buffer) };
}

/**
 * Vehicle colour by state.
 *
 * `stale` wins over everything: a marker whose position is minutes old must not
 * be shown in a colour that implies a confirmed on-time vehicle. Severity
 * thresholds match the operations status vocabulary so the map and the route
 * list cannot disagree.
 */
export const vehicleColorExpression: ExpressionSpecification = [
  "case",
  ["boolean", ["feature-state", "selected"], false],
  VEHICLE_COLORS.selected,
  ["boolean", ["get", "stale"], false],
  VEHICLE_COLORS.stale,
  // A vehicle with no reported delay is not "on time"; it gets its own colour.
  ["!=", ["typeof", ["get", "delay_seconds"]], "number"],
  VEHICLE_COLORS.unknown,
  [">", ["get", "delay_seconds"], VEHICLE_DELAY_THRESHOLDS.majorSeconds],
  VEHICLE_COLORS.severe,
  [">", ["get", "delay_seconds"], VEHICLE_DELAY_THRESHOLDS.minorSeconds],
  VEHICLE_COLORS.delayed,
  VEHICLE_COLORS.normal,
];

/** Unselected network lines: present for context, never competing. */
export function networkLineWidth(): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], 9, 0.5, 12, 1.1, 15, 2.2, 17, 3.4];
}

export function networkLineOpacity(mode: MapMode): ExpressionSpecification | number {
  // In SCENARIOS the whole point is one route's alignment, so the rest of the
  // network drops back further than it does in operations modes.
  return mode === "scenarios" ? 0.22 : ["interpolate", ["linear"], ["zoom"], 9, 0.3, 13, 0.45];
}

export function selectedRouteWidth(): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], 9, 2.4, 12, 4, 15, 6.5, 17, 9];
}

export function selectedRouteCasingWidth(): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], 9, 4.6, 12, 7, 15, 11, 17, 15];
}

/**
 * Stops stay almost invisible at network scale and resolve as the planner zooms
 * in. Showing every stop at z10 produces a dotted smear that hides the line it
 * belongs to.
 */
export function stopRadius(): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], 11, 0, 12.5, 1.8, 14, 3.2, 17, 5.5];
}

export function stopCasingRadius(): ExpressionSpecification {
  return ["interpolate", ["linear"], ["zoom"], 11, 0, 12.5, 3.4, 14, 5.4, 17, 8.5];
}

export function vehicleIconSize(mode: MapMode): ExpressionSpecification {
  const base = mode === "live" ? 0.56 : 0.5;
  return ["interpolate", ["linear"], ["zoom"], 9, base * 0.62, 13, base, 16, base * 1.35];
}
