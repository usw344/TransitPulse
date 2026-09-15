import type { StyleSpecification } from "maplibre-gl";

/**
 * TransitPulse's own basemap cartography.
 *
 * MapLibre is a rendering engine here, not the product's visual identity.  An
 * off-the-shelf style is drawn for general navigation: it gives shops, house
 * numbers, side streets and building footprints roughly the same weight as the
 * arterials a bus actually runs on, and the result competes with the transit
 * data instead of supporting it.  On an operations screen the route, the
 * vehicles and the stops must be the brightest things present.
 *
 * So this style is built from the same CARTO vector tiles but with a deliberate
 * hierarchy:
 *
 * - **Removed entirely**: points of interest, house numbers, building
 *   footprints below close zoom, aeroway detail, county and state boundaries.
 *   None of it helps answer a question about a bus route.
 * - **Kept faint, as orientation only**: minor and service roads, landuse,
 *   parks.  A planner needs to recognise the shape of a neighbourhood without
 *   reading it.
 * - **Kept legible**: water, motorways and arterials, place names.  These are
 *   what someone actually navigates by, and water in particular explains why a
 *   route detours.
 *
 * Everything is desaturated toward the slate palette the rest of the
 * application uses, so the map reads as one panel of TransitPulse rather than
 * an embedded web map with a UI drawn on top.
 *
 * Attribution for CARTO and OpenStreetMap is preserved and is required; it is
 * attached by the map's own AttributionControl once tiles genuinely render.
 */

const TILES = "https://tiles.basemaps.cartocdn.com/vector/carto.streets/v1/tiles.json";
const GLYPHS = "https://tiles.basemaps.cartocdn.com/fonts/{fontstack}/{range}.pbf";

/** Slate-family palette shared with `globals.css`. */
export const MAP_COLORS = {
  land: "#eaeff3",
  water: "#ccdae4",
  park: "#e2eae3",
  landuse: "#e4e9ee",
  roadMinor: "#e2e8ed",
  roadMajor: "#dfe6ec",
  roadMajorCasing: "#ccd5dd",
  motorway: "#dae2e9",
  motorwayCasing: "#c3cdd6",
  boundary: "#c2ccd6",
  label: "#6b7d8d",
  labelHalo: "#f4f7f9",
  labelMinor: "#8b9aa8",
} as const;

const FONT = ["Inter Regular", "Open Sans Regular", "Arial Unicode MS Regular"];
const FONT_BOLD = ["Inter Bold", "Open Sans Bold", "Arial Unicode MS Bold"];

/** Roads a bus route plausibly runs on, in descending importance. */
const MAJOR_CLASSES = ["motorway", "trunk", "primary"];
const SECONDARY_CLASSES = ["secondary", "tertiary"];
const MINOR_CLASSES = ["minor", "service", "track"];

export function transitPulseBasemapStyle(): StyleSpecification {
  return {
    version: 8,
    name: "TransitPulse Operations",
    glyphs: GLYPHS,
    sources: {
      carto: { type: "vector", url: TILES },
    },
    layers: [
      {
        id: "background",
        type: "background",
        paint: { "background-color": MAP_COLORS.land },
      },

      // --- surfaces ----------------------------------------------------
      {
        id: "landuse",
        type: "fill",
        source: "carto",
        "source-layer": "landuse",
        filter: ["in", ["get", "class"], ["literal", ["residential", "commercial", "industrial"]]],
        paint: { "fill-color": MAP_COLORS.landuse, "fill-opacity": 0.55 },
      },
      {
        id: "park",
        type: "fill",
        source: "carto",
        "source-layer": "park",
        paint: { "fill-color": MAP_COLORS.park, "fill-opacity": 0.85 },
      },
      {
        id: "water",
        type: "fill",
        source: "carto",
        "source-layer": "water",
        paint: { "fill-color": MAP_COLORS.water },
      },
      {
        id: "waterway",
        type: "line",
        source: "carto",
        "source-layer": "waterway",
        minzoom: 9,
        paint: {
          "line-color": MAP_COLORS.water,
          "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.6, 16, 3],
        },
      },

      // --- roads, weighted by whether a bus could use them ---------------
      // Minor streets appear only once a planner has zoomed in far enough to be
      // looking at a specific stop; at network scale they are pure noise.
      {
        id: "road-minor",
        type: "line",
        source: "carto",
        "source-layer": "transportation",
        minzoom: 12,
        filter: ["in", ["get", "class"], ["literal", MINOR_CLASSES]],
        paint: {
          "line-color": MAP_COLORS.roadMinor,
          "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.4, 16, 2.2],
          "line-opacity": ["interpolate", ["linear"], ["zoom"], 12, 0, 13.5, 0.9],
        },
      },
      {
        id: "road-secondary",
        type: "line",
        source: "carto",
        "source-layer": "transportation",
        minzoom: 10,
        filter: ["in", ["get", "class"], ["literal", SECONDARY_CLASSES]],
        paint: {
          "line-color": MAP_COLORS.roadMajor,
          "line-width": ["interpolate", ["linear"], ["zoom"], 10, 0.5, 14, 2.4, 17, 6],
        },
      },
      {
        id: "road-major-casing",
        type: "line",
        source: "carto",
        "source-layer": "transportation",
        filter: ["in", ["get", "class"], ["literal", MAJOR_CLASSES]],
        paint: {
          "line-color": MAP_COLORS.roadMajorCasing,
          "line-width": ["interpolate", ["linear"], ["zoom"], 8, 1.2, 12, 3.2, 17, 11],
        },
        layout: { "line-cap": "round", "line-join": "round" },
      },
      {
        id: "road-major",
        type: "line",
        source: "carto",
        "source-layer": "transportation",
        filter: ["in", ["get", "class"], ["literal", MAJOR_CLASSES]],
        paint: {
          "line-color": MAP_COLORS.motorway,
          "line-width": ["interpolate", ["linear"], ["zoom"], 8, 0.6, 12, 2.2, 17, 8.5],
        },
        layout: { "line-cap": "round", "line-join": "round" },
      },

      // Buildings only at the zoom where a planner is inspecting one corner.
      {
        id: "building",
        type: "fill",
        source: "carto",
        "source-layer": "building",
        minzoom: 15,
        paint: {
          "fill-color": "#e0e6ec",
          "fill-opacity": ["interpolate", ["linear"], ["zoom"], 15, 0, 16.5, 0.6],
        },
      },

      {
        id: "boundary",
        type: "line",
        source: "carto",
        "source-layer": "boundary",
        // admin_level is absent on some boundary features; comparing null to a
        // number makes MapLibre discard the whole filter and draw nothing.
        filter: ["all", ["has", "admin_level"], ["<=", ["to-number", ["get", "admin_level"]], 4]],
        paint: {
          "line-color": MAP_COLORS.boundary,
          "line-dasharray": [3, 2],
          "line-width": 0.8,
        },
      },

      // --- labels: orientation, never decoration -------------------------
      {
        id: "place-neighbourhood",
        type: "symbol",
        source: "carto",
        "source-layer": "place",
        minzoom: 12.5,
        filter: ["in", ["get", "class"], ["literal", ["suburb", "neighbourhood", "quarter"]]],
        layout: {
          "text-field": ["get", "name"],
          "text-font": FONT,
          "text-size": ["interpolate", ["linear"], ["zoom"], 12.5, 9.5, 16, 12],
          "text-letter-spacing": 0.06,
          "text-max-width": 8,
        },
        paint: {
          "text-color": MAP_COLORS.labelMinor,
          "text-halo-color": MAP_COLORS.labelHalo,
          "text-halo-width": 1.2,
        },
      },
      {
        id: "place-town",
        type: "symbol",
        source: "carto",
        "source-layer": "place",
        filter: ["in", ["get", "class"], ["literal", ["town", "village", "hamlet"]]],
        layout: {
          "text-field": ["get", "name"],
          "text-font": FONT,
          "text-size": ["interpolate", ["linear"], ["zoom"], 8, 10, 14, 13],
          "text-letter-spacing": 0.04,
        },
        paint: {
          "text-color": MAP_COLORS.label,
          "text-halo-color": MAP_COLORS.labelHalo,
          "text-halo-width": 1.4,
        },
      },
      {
        id: "place-city",
        type: "symbol",
        source: "carto",
        "source-layer": "place",
        filter: ["==", ["get", "class"], "city"],
        layout: {
          "text-field": ["get", "name"],
          "text-font": FONT_BOLD,
          "text-size": ["interpolate", ["linear"], ["zoom"], 7, 11, 13, 16],
          "text-letter-spacing": 0.09,
          "text-transform": "uppercase",
        },
        paint: {
          "text-color": MAP_COLORS.label,
          "text-halo-color": MAP_COLORS.labelHalo,
          "text-halo-width": 1.6,
        },
      },
      {
        id: "water-label",
        type: "symbol",
        source: "carto",
        "source-layer": "water_name",
        minzoom: 10,
        layout: {
          "text-field": ["get", "name"],
          "text-font": FONT,
          "text-size": 11,
          "text-max-width": 8,
        },
        paint: {
          "text-color": "#8aa2b4",
          "text-halo-color": MAP_COLORS.labelHalo,
          "text-halo-width": 1.1,
        },
      },
      // Street names only when zoomed to a corridor, and only for roads a route
      // would use. At network scale they would bury the route line.
      {
        id: "road-label",
        type: "symbol",
        source: "carto",
        "source-layer": "transportation_name",
        minzoom: 13.5,
        filter: ["in", ["get", "class"], ["literal", [...MAJOR_CLASSES, ...SECONDARY_CLASSES]]],
        layout: {
          "symbol-placement": "line",
          "text-field": ["get", "name"],
          "text-font": FONT,
          "text-size": 10.5,
          "text-letter-spacing": 0.03,
        },
        paint: {
          "text-color": MAP_COLORS.labelMinor,
          "text-halo-color": MAP_COLORS.labelHalo,
          "text-halo-width": 1.3,
        },
      },
    ],
  } as StyleSpecification;
}
