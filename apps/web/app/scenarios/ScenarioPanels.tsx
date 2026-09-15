"use client";

/**
 * The SCENARIOS comparison surface.
 *
 * Three things have to be unmistakable on this screen, because every one of
 * them is a way a planning tool can mislead:
 *
 * 1. **Which numbers are measured and which are estimated.**  The current plan
 *    is read straight from the published schedule; the scenario column is model
 *    output.  They are labelled differently and the estimated column always
 *    carries its range.
 * 2. **How uncertain the estimate is.**  Ranges are shown inline next to the
 *    point value, never behind a tooltip, and the confidence chip states why.
 * 3. **What the model cannot do.**  Ridership, wait time and passenger benefit
 *    are absent by construction, and the caveat saying so is part of the
 *    layout, not a footnote.
 */

import type {
  Interval,
  ScenarioBaseline,
  ScenarioEstimateResponse,
  ScenarioModelCard,
} from "./types";

function n(value: number | null | undefined, digits = 1, suffix = ""): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}${suffix}`;
}

function signed(value: number | null | undefined, digits = 1, suffix = ""): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (Math.abs(value) < 10 ** -digits / 2) return `no change`;
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}${suffix}`;
}

/**
 * Colour for a change. When the estimate's own range still contains today's
 * value, the direction is not established, so the change is shown neutral
 * rather than as a green "improvement" the model cannot support.
 */
function deltaTone(
  value: number | null | undefined,
  betterWhen: "lower" | "higher",
  interval?: Interval | null,
  current?: number | null,
): string {
  if (value === null || value === undefined || Math.abs(value) < 0.05) return "flat";
  if (interval && current !== null && current !== undefined && interval.low <= current && current <= interval.high) {
    return "flat";
  }
  const improving = betterWhen === "lower" ? value < 0 : value > 0;
  return improving ? "better" : "worse";
}

/**
 * Planners schedule whole buses: the requirement is the cycle-to-headway ratio
 * rounded up. The ratio stays visible as the arithmetic behind it.
 */
export function wholeVehicles(ratio: number | null | undefined): string {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio)) return "—";
  return `${Math.ceil(ratio - 1e-6)}`;
}

function wholeDelta(before: number | null | undefined, after: number | null | undefined): number | null {
  if (before === null || before === undefined || after === null || after === undefined) return null;
  return Math.ceil(after - 1e-6) - Math.ceil(before - 1e-6);
}

function range(interval: Interval | null | undefined, digits = 1, suffix = ""): string {
  if (!interval) return "—";
  if (Math.abs(interval.high - interval.low) < 10 ** -digits / 2) return "exact";
  return `${interval.low.toFixed(digits)}–${interval.high.toFixed(digits)}${suffix}`;
}

/** The route exactly as it is scheduled today. No model involved. */
export function CurrentPlanPanel({ baseline }: { baseline: ScenarioBaseline | null }) {
  if (!baseline) {
    return (
      <div className="scenario-column">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">CURRENT PLAN</p>
            <h2>Select a route</h2>
          </div>
        </div>
        <p className="panel-empty">
          Choose an Edmonton route, direction and day type to load its scheduled plan.
        </p>
      </div>
    );
  }
  // Measured and derived are shown as two blocks, because the footer claiming
  // "nothing here is modelled" was false for cycle time and the vehicle
  // estimate — both rest on an assumed recovery policy.
  const measured: Array<[string, string, string]> = [
    ["Route length", n(baseline.one_way_length_km, 2, " km"), "one way, along the shape"],
    ["Stops", `${baseline.stop_count}`, "dominant pattern"],
    ["Stop density", n(baseline.stops_per_km, 2, " /km"), `${n(baseline.mean_stop_spacing_m, 0, " m")} mean spacing`],
    ["Scheduled runtime", n(baseline.scheduled_runtime_minutes, 0, " min"), "median trip, one way"],
    ["Commercial speed", n(baseline.scheduled_commercial_speed_kmh, 1, " km/h"), "includes dwell"],
    ["Peak headway", n(baseline.peak_headway_minutes, 0, " min"), "busiest stop, both peaks"],
    ["Service span", n(baseline.service_span_hours, 1, " h"), "first to last departure"],
  ];
  const derived: Array<[string, string, string]> = [
    ["Cycle time", n(baseline.estimated_cycle_time_minutes, 0, " min"), "round trip + recovery"],
    ["Est. vehicles", wholeVehicles(baseline.estimated_required_vehicles), `cycle ÷ headway = ${n(baseline.estimated_required_vehicles, 2, "")}, rounded up`],
  ];
  return (
    <div className="scenario-column">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">CURRENT PLAN · PUBLISHED SCHEDULE</p>
          <h2>
            Route {baseline.route_label}
            <small>
              {baseline.direction_id === null ? "single direction" : `direction ${baseline.direction_id}`} ·{" "}
              {baseline.day_type}
            </small>
          </h2>
        </div>
      </div>
      <dl className="scenario-facts">
        {measured.map(([label, value, note]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              {value}
              <small>{note}</small>
            </dd>
          </div>
        ))}
      </dl>
      <p className="scenario-source">
        Read from {baseline.agency ?? "the operator"}&rsquo;s published GTFS schedule. Nothing
        above is modelled.
        {baseline.branch_count && baseline.branch_count > 1 ? (
          <>
            {" "}
            This direction operates {baseline.branch_count} stop patterns; the figures describe the
            pattern {Math.round((baseline.dominant_pattern_trip_share ?? 0) * 100)}% of trips use.
          </>
        ) : null}
      </p>

      <p className="eyebrow derived-eyebrow">DERIVED UNDER ASSUMPTIONS</p>
      <dl className="scenario-facts derived">
        {derived.map(([label, value, note]) => (
          <div key={label}>
            <dt>{label}</dt>
            <dd>
              {value}
              <small>{note}</small>
            </dd>
          </div>
        ))}
      </dl>
      <p className="scenario-source assumption">
        Neither is published. Recovery is assumed at 10% of round-trip running time (minimum
        5 minutes), and the vehicle figure is an estimated <strong>requirement</strong>, never
        an assignment &mdash; real blocks interline across routes.
      </p>
    </div>
  );
}

/** Model output for the proposed design, with its range always attached. */
export function EstimatePanel({
  result,
  pending,
}: {
  result: ScenarioEstimateResponse | null;
  pending: boolean;
}) {
  if (!result) {
    return (
      <div className="scenario-column">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">MODEL ESTIMATE</p>
            <h2>No scenario yet</h2>
          </div>
        </div>
        <p className="panel-empty">
          Change the route length, stop count, headway or span on the right to see an estimate.
        </p>
      </div>
    );
  }

  const { estimate, delta, baseline, scenario_plan: plan } = result;
  // `design_distance === 0` is also true for a frequency-only change, which
  // very much does change the fleet. Only a plan identical in every field is
  // "unchanged".
  const unchanged = estimate.scenario_unchanged;
  const speedHeldConstant = !unchanged && estimate.design_distance < 1e-9;

  return (
    <div className={`scenario-column ${pending ? "pending" : ""}`}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">
            {unchanged
              ? "SCENARIO · PUBLISHED SCHEDULE"
              : speedHeldConstant
                ? "SCENARIO · SCHEDULE ARITHMETIC"
                : "SCENARIO · ESTIMATED"}
          </p>
          <h2>
            Proposed plan
            <small>
              {n(plan.one_way_length_km, 2, " km")} · {plan.stop_count} stops ·{" "}
              {n(plan.stops_per_km, 2, " /km")}
            </small>
          </h2>
        </div>
        <span className={`confidence-chip ${estimate.confidence}`}>
          {unchanged
            ? "Published schedule"
            : speedHeldConstant
              ? "Schedule arithmetic"
              : estimate.confidence === "high"
              ? "Higher confidence"
              : estimate.confidence === "moderate"
                ? "Moderate confidence"
                : "Low confidence"}
        </span>
      </div>

      {unchanged ? (
        <p className="scenario-unchanged">
          Nothing has been changed, so this is the published schedule rather than an estimate.
        </p>
      ) : null}
      {speedHeldConstant ? (
        <p className="scenario-unchanged frequency-only">
          Only frequency or span changed. Those are deliberately not applied to running speed,
          so the speed and runtime below are today&rsquo;s schedule &mdash; but the vehicle
          requirement does change, and rests on the assumed recovery policy.
        </p>
      ) : null}

      <div className="estimate-grid">
        <div className="estimate-card">
          <span>Runtime</span>
          <strong>{n(estimate.runtime_minutes.point, 0, "")}<i> min</i></strong>
          <em>{unchanged || speedHeldConstant ? "as scheduled" : `range ${range(estimate.runtime_minutes, 0, " min")}`}</em>
          <b className={deltaTone(delta.runtime_minutes, "lower", estimate.runtime_minutes, baseline.scheduled_runtime_minutes)}>
            {signed(delta.runtime_minutes, 0, " min")}
          </b>
        </div>
        <div className="estimate-card">
          <span>Commercial speed</span>
          <strong>{n(estimate.commercial_speed_kmh.point, 1, "")}<i> km/h</i></strong>
          <em>{unchanged || speedHeldConstant ? "as scheduled" : `range ${range(estimate.commercial_speed_kmh, 1, " km/h")}`}</em>
          <b className={deltaTone(delta.speed_kmh, "higher", estimate.commercial_speed_kmh, baseline.scheduled_commercial_speed_kmh)}>{signed(delta.speed_kmh, 1, " km/h")}</b>
        </div>
        <div className="estimate-card">
          <span>Est. vehicles required</span>
          <strong>{wholeVehicles(estimate.fleet?.vehicles.point)}</strong>
          <em>
            {estimate.fleet
              ? unchanged || speedHeldConstant
                ? `ratio ${n(estimate.fleet.vehicles.point, 2, "")} · headway ${n(estimate.fleet.headway_minutes, 0, " min")}`
                : `range ${wholeVehicles(estimate.fleet.vehicles.low)}–${wholeVehicles(estimate.fleet.vehicles.high)} · ratio ${n(estimate.fleet.vehicles.point, 2, "")} · headway ${n(estimate.fleet.headway_minutes, 0, " min")}`
              : "no headway to size against"}
          </em>
          <b
            className={deltaTone(
              wholeDelta(baseline.estimated_required_vehicles, estimate.fleet?.vehicles.point),
              "lower",
              estimate.fleet && !speedHeldConstant
                ? { point: estimate.fleet.vehicles.point, low: Math.ceil(estimate.fleet.vehicles.low - 1e-6), high: Math.ceil(estimate.fleet.vehicles.high - 1e-6) }
                : null,
              baseline.estimated_required_vehicles === null || baseline.estimated_required_vehicles === undefined
                ? null
                : Math.ceil(baseline.estimated_required_vehicles - 1e-6),
            )}
          >
            {signed(wholeDelta(baseline.estimated_required_vehicles, estimate.fleet?.vehicles.point), 0, "")}
          </b>
        </div>
        <div className="estimate-card">
          <span>Cycle time</span>
          <strong>{n(estimate.fleet?.cycle_time_minutes.point, 0, "")}<i> min</i></strong>
          <em>
            {estimate.fleet
              ? unchanged || speedHeldConstant
                ? `incl. ${n(estimate.fleet.recovery_minutes, 0, " min")} recovery`
                : `range ${range(estimate.fleet.cycle_time_minutes, 0, "")} · incl. ${n(estimate.fleet.recovery_minutes, 0, " min")} recovery`
              : "not derivable"}
          </em>
          <b className="flat">
            {signed(
              estimate.fleet && baseline.estimated_cycle_time_minutes
                ? estimate.fleet.cycle_time_minutes.point - baseline.estimated_cycle_time_minutes
                : null,
              0,
              " min",
            )}
          </b>
        </div>
      </div>

      {estimate.fleet ? (
        <p className="scenario-source assumption">{estimate.fleet.basis}</p>
      ) : null}

      {estimate.drivers.length > 0 ? (
        <div className="scenario-drivers">
          <p className="eyebrow">WHAT YOU CHANGED</p>
          <ul>
            {estimate.drivers.map((driver) => (
              <li key={driver.label}>
                <strong>{driver.label}</strong>
                <span>
                  {driver.from}
                  {driver.unit ? ` ${driver.unit}` : ""} → {driver.to}
                  {driver.unit ? ` ${driver.unit}` : ""}
                </span>
                <em>{driver.effect_on_speed}</em>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="scenario-confidence">
        <p className="eyebrow">WHY THIS CONFIDENCE</p>
        <ul>
          {estimate.confidence_reasons.map((reason) => (
            <li key={reason}>{reason.charAt(0).toUpperCase() + reason.slice(1)}</li>
          ))}
        </ul>
        {estimate.out_of_distribution.length > 0 ? (
          <ul className="ood-list">
            {estimate.out_of_distribution.map((flag) => (
              <li key={flag.feature}>
                <strong>{flag.feature.replaceAll("_", " ")}</strong> {flag.value} is {flag.direction} the
                trained range {flag.trained_range[0]}–{flag.trained_range[1]}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </div>
  );
}

/** Provenance and limits, shown as part of the screen rather than hidden away. */
export function ModelCardPanel({
  model,
  designDistance,
}: {
  model: ScenarioModelCard | null;
  designDistance: number | null;
}) {
  if (!model) return null;
  const skill = model.held_out_change_skill;
  const edmontonSkill = skill?.per_city?.Edmonton;
  // The pooled figure is dominated by pairs of wholly different routes. A
  // planner trimming a few stops is in a regime where the model is barely
  // better than assuming nothing happens, so quote the bucket they are in.
  const bucket =
    designDistance !== null && skill
      ? skill.by_design_distance.find((b) => designDistance <= b.max_feature_distance)
      : undefined;
  return (
    <div className="scenario-column model-card">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">HOW ESTIMATES ARE MADE</p>
          <h2>
            Cross-city planning model
            <small title={model.model_family}>
              Regularized linear model (ridge) · {model.training_rows.toLocaleString()} route
              records
            </small>
          </h2>
        </div>
        {model.dataset_checksum_matches ? null : (
          <span className="confidence-chip low">Dataset mismatch</span>
        )}
      </div>

      <dl className="scenario-facts compact">
        <div>
          <dt>Trained on</dt>
          <dd>
            {model.training_cities.length} cities
            <small>{model.training_cities.join(", ")}</small>
          </dd>
        </div>
        <div>
          <dt>Validated by</dt>
          <dd>
            Leave-one-city-out
            <small>every score comes from a city the model had not seen</small>
          </dd>
        </div>
        <div>
          <dt>Speed error</dt>
          <dd>
            {n(model.held_out_absolute_accuracy?.mae_kmh, 2, " km/h")}
            <small>mean absolute, held-out cities</small>
          </dd>
        </div>
        {/* Edmonton's own held-out figures lead, because Edmonton is the city
            this product serves and its skill is the weakest of the positives.
            Quoting the pooled number as the headline would flatter the tool
            exactly where a planner is relying on it. */}
        <div>
          <dt>Direction of change</dt>
          <dd>
            {edmontonSkill
              ? `${Math.round(edmontonSkill.sign_agreement * 100)}% correct`
              : skill
                ? `${Math.round(skill.sign_agreement_on_material_changes * 100)}% correct`
                : "—"}
            <small>
              faster vs slower, all change sizes, Edmonton held out
              {skill ? ` · ${Math.round(skill.sign_agreement_on_material_changes * 100)}% across all cities` : ""}
            </small>
          </dd>
        </div>
        <div className={bucket && bucket.skill_vs_do_nothing < 0.05 ? "warn" : undefined}>
          <dt>Beats &ldquo;no effect&rdquo; by</dt>
          <dd>
            {bucket
              ? `${Math.round(bucket.skill_vs_do_nothing * 100)}%`
              : edmontonSkill
                ? `${Math.round(edmontonSkill.skill_vs_do_nothing * 100)}%`
                : "—"}
            <small>
              {bucket
                ? `for a change of this size (${bucket.pairs.toLocaleString()} held-out pairs)`
                : "on Edmonton, held out"}
              {skill ? ` · ${Math.round(skill.skill_vs_do_nothing * 100)}% pooled over all change sizes` : ""}
            </small>
          </dd>
        </div>
        <div>
          <dt>Source data</dt>
          <dd title={`${model.dataset} · schema ${model.schema_version} · model ${model.version}`}>
            Published GTFS schedules
            <small>each agency&rsquo;s official static feed</small>
          </dd>
        </div>
        <div>
          <dt>Evidence</dt>
          <dd>
            {model.distinct_route_directions
              ? model.distinct_route_directions.toLocaleString()
              : model.training_rows.toLocaleString()}
            <small>
              distinct route directions
              {model.distinct_route_directions
                ? ` · ${model.training_rows.toLocaleString()} rows once day types are counted separately`
                : ""}
            </small>
          </dd>
        </div>
      </dl>

      {bucket && bucket.skill_vs_do_nothing < 0.05 ? (
        <p className="scenario-source assumption">
          For a change this small the model is <strong>not measurably better</strong> than
          assuming the change has no effect. Treat the direction as a hint and the range as
          the real answer.
        </p>
      ) : null}

      <div className="scenario-limits">
        <p className="eyebrow">WHAT THIS CANNOT TELL YOU</p>
        <ul>
          {/* The artifact's limitation text points developers at its own JSON
              fields ("See held_out_change_skill…"); that pointer means nothing
              on screen, and the numbers it refers to are shown above. */}
          {model.limitations.map((limitation) => (
            <li key={limitation}>{limitation.replace(/\s*See [a-z_]+(\.[a-z_]+)*\.$/, "")}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}
