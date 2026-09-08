export type ReplayMode = "live" | "replay";

export interface ReplayObservationProperties {
  vehicle_id: string;
  static_feed_id: string;
  observed_at: string | null;
  source_timestamp: string | null;
  recorded_at: string;
}

export interface ReplayObservation {
  type: "Feature";
  geometry: { type: "Point"; coordinates: number[] };
  properties: ReplayObservationProperties;
}

export const MAX_REPLAY_OBSERVATION_AGE_MS = 120_000;

function observationTime(feature: ReplayObservation): number | null {
  const value = feature.properties.observed_at ?? feature.properties.source_timestamp ?? feature.properties.recorded_at;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

export function clampReplayTimestamp(timestamp: number, start: number, end: number): number {
  return Math.min(Math.max(timestamp, start), end);
}

export function replayTimestampAtFraction(fraction: number, start: number, end: number): number {
  return clampReplayTimestamp(start + (end - start) * fraction, start, end);
}

export function advanceReplayTimestamp(
  timestamp: number,
  elapsedMilliseconds: number,
  speed: number,
  start: number,
  end: number,
): number {
  return clampReplayTimestamp(timestamp + elapsedMilliseconds * speed, start, end);
}

/**
 * Use an actual observation at or before the playhead. A vehicle disappears
 * across a significant source gap rather than being moved speculatively.
 */
export function replayFrameAt<T extends ReplayObservation>(
  observations: T[],
  timestamp: number,
  staticFeedId: string,
  maxObservationAgeMilliseconds = MAX_REPLAY_OBSERVATION_AGE_MS,
): T[] {
  const latestByVehicle = new Map<string, { feature: T; timestamp: number }>();
  for (const feature of observations) {
    if (feature.properties.static_feed_id !== staticFeedId) continue;
    const observedAt = observationTime(feature);
    if (observedAt === null || observedAt > timestamp || timestamp - observedAt > maxObservationAgeMilliseconds) continue;
    const existing = latestByVehicle.get(feature.properties.vehicle_id);
    if (!existing || observedAt > existing.timestamp) latestByVehicle.set(feature.properties.vehicle_id, { feature, timestamp: observedAt });
  }
  return [...latestByVehicle.values()]
    .sort((left, right) => left.feature.properties.vehicle_id.localeCompare(right.feature.properties.vehicle_id))
    .map((entry) => entry.feature);
}

export function vehicleDataForMode<T>(mode: ReplayMode, live: T, replay: T): T {
  return mode === "replay" ? replay : live;
}
