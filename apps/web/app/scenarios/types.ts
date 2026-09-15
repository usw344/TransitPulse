/**
 * Wire types for the SCENARIOS surface.
 *
 * These mirror `apps/api/transitpulse_api/scenarios.py` exactly.  Units live in
 * the field names on both sides so a number can never quietly change meaning
 * crossing the wire — a runtime in minutes and a runtime in seconds look
 * identical in a chart and not at all identical to a planner.
 */

export interface Interval {
  point: number;
  low: number;
  high: number;
}

export interface ScenarioBaseline {
  key: string;
  route_label: string;
  /** The operator of this route. A regional feed carries several. */
  agency: string | null;
  direction_id: number | null;
  day_type: string;
  one_way_length_km: number;
  stop_count: number;
  stops_per_km: number;
  mean_stop_spacing_m: number | null;
  scheduled_runtime_minutes: number | null;
  scheduled_commercial_speed_kmh: number | null;
  peak_headway_minutes: number | null;
  offpeak_headway_minutes: number | null;
  median_headway_minutes: number | null;
  service_span_hours: number | null;
  trips_per_day: number | null;
  estimated_cycle_time_minutes: number | null;
  estimated_required_vehicles: number | null;
  loop_route: boolean | null;
  branch_count: number | null;
  dominant_pattern_trip_share: number | null;
  directness_ratio: number | null;
  original_route_id: string | null;
}

export interface ScenarioRouteOption {
  key: string;
  route_label: string;
  agency: string | null;
  direction_id: number | null;
  day_type: string;
  one_way_length_km: number;
  stop_count: number;
  scheduled_commercial_speed_kmh: number | null;
  scheduled_runtime_minutes: number | null;
}

export interface ScenarioRoutesResponse {
  city: string;
  dataset: string;
  dataset_checksum_matches: boolean;
  routes: ScenarioRouteOption[];
}

export interface FleetEstimate {
  vehicles: Interval;
  cycle_time_minutes: Interval;
  recovery_minutes: number;
  headway_minutes: number;
  basis: string;
}

export interface OutOfDistributionFlag {
  feature: string;
  value: number;
  trained_range: [number, number];
  direction: "below" | "above";
}

export interface ScenarioDriver {
  label: string;
  from: number | string;
  to: number | string;
  unit: string;
  effect_on_speed: string;
}

export interface ScenarioEstimateBody {
  commercial_speed_kmh: Interval;
  runtime_minutes: Interval;
  fleet: FleetEstimate | null;
  confidence: "high" | "moderate" | "low";
  confidence_reasons: string[];
  out_of_distribution: OutOfDistributionFlag[];
  drivers: ScenarioDriver[];
  baseline_speed_kmh: number;
  predicted_delta_kmh: number;
  /** True only when the proposed plan matches today's in every field. Distinct
   *  from `design_distance === 0`, which a frequency-only change also produces. */
  scenario_unchanged: boolean;
  design_distance: number;
  band_p90_kmh: number;
  method: string;
}

export interface ScenarioModelCard {
  version: string;
  dataset: string;
  dataset_checksum_matches: boolean;
  schema_version: string;
  target: string;
  model_family: string;
  model_selection: string;
  training_rows: number;
  training_cities: string[];
  quality_measured_on: string;
  interval_basis: string;
  held_out_absolute_accuracy: {
    model: string;
    mae_kmh: number;
    per_city_mae_kmh: Record<string, number>;
  } | null;
  held_out_change_skill: {
    operator: string;
    skill_vs_do_nothing: number;
    sign_agreement_on_material_changes: number;
    delta_mae_kmh: number;
    pooled_caveat: string;
    /** Skill in the same buckets the intervals use, so a band width and a skill
     *  figure always describe the same size of change. */
    by_design_distance: Array<{
      max_feature_distance: number;
      pairs: number;
      skill_vs_do_nothing: number;
      sign_agreement: number;
    }>;
    per_city: Record<string, { skill_vs_do_nothing: number; sign_agreement: number }>;
  } | null;
  distinct_route_directions: number | null;
  effective_sample_note: string | null;
  limitations: string[];
}

export interface ScenarioPlanEcho {
  one_way_length_km: number;
  stop_count: number;
  stops_per_km: number;
  mean_stop_spacing_m: number;
  day_type: string;
  peak_headway_minutes: number | null;
  offpeak_headway_minutes: number | null;
  median_headway_minutes: number | null;
  service_span_hours: number | null;
  recovery_fraction: number;
}

export interface ScenarioEstimateResponse {
  baseline: ScenarioBaseline;
  scenario_plan: ScenarioPlanEcho;
  estimate: ScenarioEstimateBody;
  delta: {
    speed_kmh: number | null;
    runtime_minutes: number | null;
    vehicles: number | null;
  };
  model: ScenarioModelCard;
}

/** The controls a planner can move. `null` means "leave as scheduled". */
export interface ScenarioEdits {
  one_way_length_km: number | null;
  stop_count: number | null;
  peak_headway_minutes: number | null;
  service_span_hours: number | null;
  recovery_fraction: number | null;
  day_type: string | null;
}

export const NO_EDITS: ScenarioEdits = {
  one_way_length_km: null,
  stop_count: null,
  peak_headway_minutes: null,
  service_span_hours: null,
  recovery_fraction: null,
  day_type: null,
};

/** True when the planner has not actually changed anything yet. */
export function isUnedited(edits: ScenarioEdits): boolean {
  return Object.values(edits).every((value) => value === null);
}
