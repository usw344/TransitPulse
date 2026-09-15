"use client";

/**
 * Route selection and the scenario controls, for the operations sidebar.
 *
 * Each control shows the scheduled value it started from, so a planner can
 * always see what they moved and by how much.  Nothing is committed until a
 * value actually differs from the schedule: an untouched control sends nothing,
 * which is what lets an unedited scenario return today's timetable exactly
 * rather than a model's approximation of it.
 */

import { useEffect, useMemo, useRef } from "react";

import type { ScenarioBaseline, ScenarioEdits, ScenarioRouteOption } from "./types";

interface SliderProps {
  label: string;
  unit: string;
  scheduled: number | null;
  value: number | null;
  min: number;
  max: number;
  step: number;
  digits?: number;
  hint?: string;
  /** How to describe the starting value. "scheduled" for a measured field,
   *  "assumed" for one this project chose. */
  scheduledLabel?: string;
  onChange: (value: number | null) => void;
}

function ScenarioSlider({
  label,
  unit,
  scheduled,
  value,
  min,
  max,
  step,
  digits = 1,
  hint,
  scheduledLabel = "scheduled",
  onChange,
}: SliderProps) {
  if (scheduled === null || scheduled === undefined) {
    return (
      <div className="scenario-control disabled">
        <label>
          {label}
          <small>not published for this route</small>
        </label>
      </div>
    );
  }
  const current = value ?? scheduled;
  const changed = value !== null && Math.abs(value - scheduled) > step / 2;
  return (
    <div className={`scenario-control ${changed ? "changed" : ""}`}>
      <label htmlFor={`scenario-${label}`}>
        {label}
        <b>
          {current.toFixed(digits)}
          <i>{unit}</i>
        </b>
      </label>
      <input
        id={`scenario-${label}`}
        max={max}
        min={min}
        onChange={(event) => onChange(Number(event.target.value))}
        step={step}
        type="range"
        value={current}
      />
      <small>
        {changed ? (
          <>
            {scheduledLabel} {scheduled.toFixed(digits)}
            {unit} ·{" "}
            <button className="link-button" onClick={() => onChange(null)} type="button">
              reset
            </button>
          </>
        ) : (
          (hint ?? `as ${scheduledLabel}: ${scheduled.toFixed(digits)}${unit}`)
        )}
      </small>
    </div>
  );
}

export function ScenarioControls({
  routes,
  baseline,
  selectedKey,
  edits,
  onSelect,
  onEdit,
  onReset,
  search,
  onSearch,
}: {
  routes: ScenarioRouteOption[];
  baseline: ScenarioBaseline | null;
  selectedKey: string | null;
  edits: ScenarioEdits;
  onSelect: (key: string) => void;
  onEdit: (patch: Partial<ScenarioEdits>) => void;
  onReset: () => void;
  search: string;
  onSearch: (value: string) => void;
}) {
  // One entry per route label, so the picker lists routes rather than the
  // route x direction x day-type product, which would be ~660 rows. Each entry
  // shows the variant matching the direction and day type on screen (weekday,
  // direction 0 by default), so its speed agrees with the panels.
  const labels = useMemo(() => {
    const dayType = baseline?.day_type ?? "weekday";
    const directionId = baseline?.direction_id ?? 0;
    const score = (option: ScenarioRouteOption) =>
      (option.day_type === dayType ? 4 : option.day_type === "weekday" ? 2 : 0) +
      (option.direction_id === directionId ? 1 : 0);
    const seen = new Map<string, ScenarioRouteOption>();
    for (const route of routes) {
      const current = seen.get(route.route_label);
      if (!current || score(route) > score(current)) seen.set(route.route_label, route);
    }
    const query = search.trim().toLowerCase();
    const all = [...seen.values()].sort((a, b) =>
      a.route_label.localeCompare(b.route_label, undefined, { numeric: true }),
    );
    return query ? all.filter((r) => r.route_label.toLowerCase().includes(query)) : all;
  }, [routes, search, baseline?.day_type, baseline?.direction_id]);

  const activeLabel = baseline?.route_label ?? null;

  // Keep the selected route visible in the short catalogue list; otherwise the
  // default route can sit below the list's fold with nothing showing it is chosen.
  const listRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const list = listRef.current;
    const row = list?.querySelector<HTMLElement>(".route-row.selected");
    if (!list || !row) return;
    const top = row.offsetTop - list.offsetTop;
    if (top < list.scrollTop || top + row.offsetHeight > list.scrollTop + list.clientHeight) {
      list.scrollTop = Math.max(0, top - 8);
    }
  }, [activeLabel, labels]);

  const variants = useMemo(
    () => routes.filter((route) => route.route_label === activeLabel),
    [routes, activeLabel],
  );

  const directions = useMemo(
    () => [...new Set(variants.map((v) => v.direction_id))].sort((a, b) => (a ?? 0) - (b ?? 0)),
    [variants],
  );
  const dayTypes = useMemo(
    () =>
      ["weekday", "saturday", "sunday"].filter((day) =>
        variants.some((v) => v.day_type === day && v.direction_id === (baseline?.direction_id ?? null)),
      ),
    [variants, baseline],
  );

  /**
   * Open a route on the variant closest to what is on screen: same direction
   * and day type if it runs them, otherwise direction 0 on a weekday.  Taking
   * the catalogue's first entry could silently switch a weekday comparison to
   * Saturday.
   */
  function openRoute(label: string) {
    const options = routes.filter((route) => route.route_label === label);
    const score = (option: ScenarioRouteOption) =>
      (option.day_type === (baseline?.day_type ?? "weekday") ? 4 : option.day_type === "weekday" ? 2 : 0) +
      (option.direction_id === (baseline?.direction_id ?? 0) ? 1 : 0);
    const best = [...options].sort((a, b) => score(b) - score(a))[0];
    if (best) onSelect(best.key);
  }

  function pickVariant(directionId: number | null, dayType: string) {
    const match = variants.find((v) => v.direction_id === directionId && v.day_type === dayType);
    if (match) onSelect(match.key);
  }

  return (
    <>
      <div className="route-controls">
        <label className="search-label" htmlFor="scenario-search">
          Edmonton region route
        </label>
        <input
          id="scenario-search"
          onChange={(event) => onSearch(event.target.value)}
          placeholder="Route number"
          value={search}
        />
      </div>

      <div className="route-list scenario-route-list" ref={listRef}>
        {labels.map((route) => (
          <button
            className={`route-row ${route.route_label === activeLabel ? "selected" : ""}`}
            key={route.route_label}
            onClick={() => openRoute(route.route_label)}
            title={`Route ${route.route_label}`}
            type="button"
          >
            <span className="route-badge">{route.route_label}</span>
            <span className="route-copy">
              <strong>{route.one_way_length_km.toFixed(1)} km</strong>
              <small title={route.agency ?? undefined}>
                {route.stop_count} stops ·{" "}
                {route.scheduled_commercial_speed_kmh
                  ? `${route.scheduled_commercial_speed_kmh.toFixed(1)} km/h`
                  : "no speed"}
                {/* Edmonton's feed carries regional partners — St. Albert,
                    Strathcona, Leduc, Beaumont — and labelling their routes ETS
                    would attribute them to the wrong operator. */}
                {route.agency && route.agency !== "Edmonton Transit Service"
                  ? ` · ${route.agency}`
                  : ""}
              </small>
            </span>
          </button>
        ))}
        {labels.length === 0 ? (
          <p className="panel-empty">
            {search.trim()
              ? "No route in the Edmonton region feed matches that number."
              : "Scenario routes are not loaded."}
          </p>
        ) : null}
      </div>

      {baseline ? (
        <div className="scenario-editor">
          <div className="scenario-variant">
            <div>
              <span className="control-eyebrow">Direction</span>
              <div className="mode-switch">
                {directions.map((direction) => (
                  <button
                    className={baseline.direction_id === direction ? "active" : ""}
                    key={String(direction)}
                    onClick={() => pickVariant(direction, baseline.day_type)}
                    type="button"
                  >
                    {direction === null ? "single" : direction === 0 ? "Dir 0" : "Dir 1"}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <span className="control-eyebrow">Day type</span>
              <div className="mode-switch">
                {dayTypes.map((day) => (
                  <button
                    className={baseline.day_type === day ? "active" : ""}
                    key={day}
                    onClick={() => pickVariant(baseline.direction_id, day)}
                    type="button"
                  >
                    {day === "weekday" ? "Wkdy" : day === "saturday" ? "Sat" : "Sun"}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="scenario-editor-heading">
            <p className="eyebrow">SCENARIO CONTROLS</p>
            <button className="link-button" onClick={onReset} type="button">
              Reset all
            </button>
          </div>

          <ScenarioSlider
            digits={1}
            label="Route length"
            max={Math.max(4, Math.round(baseline.one_way_length_km * 1.4))}
            min={Math.max(1, Math.round(baseline.one_way_length_km * 0.5))}
            onChange={(value) => onEdit({ one_way_length_km: value })}
            scheduled={baseline.one_way_length_km}
            step={0.1}
            unit=" km"
            value={edits.one_way_length_km}
          />
          <ScenarioSlider
            digits={0}
            label="Stops"
            max={Math.round(baseline.stop_count * 1.5)}
            min={Math.max(2, Math.round(baseline.stop_count * 0.5))}
            onChange={(value) => onEdit({ stop_count: value })}
            scheduled={baseline.stop_count}
            step={1}
            unit=""
            value={edits.stop_count}
          />
          <ScenarioSlider
            digits={0}
            hint={`as scheduled: ${baseline.peak_headway_minutes ?? "—"} min · sizes the fleet, not the speed`}
            label="Peak headway"
            max={60}
            min={4}
            onChange={(value) => onEdit({ peak_headway_minutes: value })}
            scheduled={baseline.peak_headway_minutes}
            step={1}
            unit=" min"
            value={edits.peak_headway_minutes}
          />
          <ScenarioSlider
            digits={1}
            label="Service span"
            max={24}
            min={2}
            onChange={(value) => onEdit({ service_span_hours: value })}
            scheduled={baseline.service_span_hours}
            step={0.5}
            unit=" h"
            value={edits.service_span_hours}
          />
          {/* 10% is this project's planning assumption, not a published ETS
              layover policy, so it must not be labelled "as scheduled" the way
              the measured controls above are. */}
          <ScenarioSlider
            digits={0}
            hint="assumed 10% of round-trip running time — a planning approximation, not a published policy"
            label="Recovery"
            max={30}
            min={0}
            onChange={(value) => onEdit({ recovery_fraction: value === null ? null : value / 100 })}
            scheduled={10}
            scheduledLabel="assumed"
            step={1}
            unit="%"
            value={edits.recovery_fraction === null ? null : edits.recovery_fraction * 100}
          />

          <p className="scenario-derived">
            Stop density follows from length and stop count:{" "}
            <strong>
              {(
                (edits.stop_count ?? baseline.stop_count) /
                (edits.one_way_length_km ?? baseline.one_way_length_km)
              ).toFixed(2)}{" "}
              stops/km
            </strong>
            {" "}(scheduled {baseline.stops_per_km.toFixed(2)}).
          </p>
        </div>
      ) : null}
    </>
  );
}
