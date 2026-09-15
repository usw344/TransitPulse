"use client";

/**
 * State for the SCENARIOS surface: the route catalogue, the selected baseline,
 * the planner's edits, and the estimate that follows from them.
 *
 * Estimates are debounced and every in-flight request is abortable, because the
 * controls are sliders: dragging one emits a value per frame, and without this
 * the panel would flicker between stale responses arriving out of order.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { fetchJson, responseError, API_NOT_RESPONDING } from "../format";
import {
  NO_EDITS,
  type ScenarioEdits,
  type ScenarioEstimateResponse,
  type ScenarioModelCard,
  type ScenarioRouteOption,
  type ScenarioRoutesResponse,
} from "./types";

const DEBOUNCE_MS = 180;

/**
 * The scenario the surface opens on: ETS Route 002, direction 0, weekday — the
 * documented demo route (docs/demo_scenarios.md).  The catalogue is sorted by
 * label, so without a preference the first entry is a regional partner's route
 * rather than an Edmonton Transit Service one.
 */
const DEFAULT_SCENARIO_KEY = "002|0|weekday";

export interface ScenarioState {
  available: boolean;
  unavailableReason: string | null;
  routes: ScenarioRouteOption[];
  selectedKey: string | null;
  result: ScenarioEstimateResponse | null;
  model: ScenarioModelCard | null;
  edits: ScenarioEdits;
  pending: boolean;
  error: string | null;
  search: string;
  setSearch: (value: string) => void;
  select: (key: string) => void;
  edit: (patch: Partial<ScenarioEdits>) => void;
  reset: () => void;
}

export function useScenarios(apiBase: string, active: boolean): ScenarioState {
  const [routes, setRoutes] = useState<ScenarioRouteOption[]>([]);
  const [model, setModel] = useState<ScenarioModelCard | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [edits, setEdits] = useState<ScenarioEdits>(NO_EDITS);
  const [result, setResult] = useState<ScenarioEstimateResponse | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unavailableReason, setUnavailableReason] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  // A ref, not state: flipping a state flag inside this effect would re-run the
  // effect, and its cleanup would abort the very request it just started.
  const loadedRef = useRef(false);

  // Load the catalogue once, and only when the surface is first opened: the
  // estimator artifact may not exist on a fresh checkout, and a 503 should not
  // be requested on every app start.
  useEffect(() => {
    if (!active || loadedRef.current) return;
    const controller = new AbortController();
    loadedRef.current = true;
    (async () => {
      try {
        const payload = await fetchJson<ScenarioRoutesResponse>(
          `${apiBase}/api/scenarios/routes`,
          controller.signal,
        );
        setRoutes(payload.routes);
        setUnavailableReason(null);
        const preferred =
          payload.routes.find((route) => route.key === DEFAULT_SCENARIO_KEY) ??
          payload.routes.find(
            (route) => route.day_type === "weekday" && route.agency === "Edmonton Transit Service",
          ) ??
          payload.routes.find((route) => route.day_type === "weekday") ??
          payload.routes[0];
        if (preferred) setSelectedKey(preferred.key);
      } catch (cause) {
        if (controller.signal.aborted) return;
        setUnavailableReason(
          cause instanceof Error ? cause.message : "scenario estimator is unavailable",
        );
      }
    })();
    return () => controller.abort();
  }, [active, apiBase]);

  useEffect(() => {
    if (!active || model) return;
    const controller = new AbortController();
    fetchJson<ScenarioModelCard>(`${apiBase}/api/scenarios/model`, controller.signal)
      .then(setModel)
      .catch(() => undefined);
    return () => controller.abort();
  }, [active, apiBase, model]);

  const select = useCallback((key: string) => {
    setSelectedKey(key);
    // A different route means the old edits describe a route that is no longer
    // on screen; carrying them over would silently apply one route's redesign
    // to another.
    setEdits(NO_EDITS);
    setError(null);
  }, []);

  const edit = useCallback((patch: Partial<ScenarioEdits>) => {
    setEdits((previous) => ({ ...previous, ...patch }));
  }, []);

  const reset = useCallback(() => setEdits(NO_EDITS), []);

  const requestBody = useMemo(() => {
    if (!selectedKey) return null;
    const body: Record<string, unknown> = { key: selectedKey };
    for (const [name, value] of Object.entries(edits)) {
      if (value !== null) body[name] = value;
    }
    return body;
  }, [selectedKey, edits]);

  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!active || !requestBody) return;
    const timer = window.setTimeout(() => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setPending(true);
      (async () => {
        try {
          const response = await fetch(`${apiBase}/api/scenarios/estimate`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(requestBody),
            signal: controller.signal,
          });
          if (!response.ok) throw await responseError(response);
          setResult((await response.json()) as ScenarioEstimateResponse);
          setError(null);
        } catch (cause) {
          if (controller.signal.aborted) return;
          setError(
            cause instanceof TypeError
              ? API_NOT_RESPONDING
              : cause instanceof Error
                ? cause.message
                : "The estimate could not be calculated.",
          );
        } finally {
          if (!controller.signal.aborted) setPending(false);
        }
      })();
    }, DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [active, apiBase, requestBody]);

  return {
    available: unavailableReason === null && routes.length > 0,
    unavailableReason,
    routes,
    selectedKey,
    result,
    model,
    edits,
    pending,
    error,
    search,
    setSearch,
    select,
    edit,
    reset,
  };
}
