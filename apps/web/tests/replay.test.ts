import assert from "node:assert/strict";
import test from "node:test";

import {
  advanceReplayTimestamp,
  clampReplayTimestamp,
  replayFrameAt,
  replayTimestampAtFraction,
  vehicleDataForMode,
  type ReplayObservation,
} from "../app/replay";

const start = Date.parse("2026-09-08T12:00:00Z");
const end = Date.parse("2026-09-08T12:10:00Z");

function observation(vehicleId: string, seconds: number, feed = "feed-a"): ReplayObservation {
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates: [-113.5 + seconds / 1000, 53.5] },
    properties: {
      vehicle_id: vehicleId,
      static_feed_id: feed,
      observed_at: new Date(start + seconds * 1000).toISOString(),
      source_timestamp: null,
      recorded_at: new Date(start + seconds * 1000).toISOString(),
    },
  };
}

test("replay selects the most recent source observation at the playhead", () => {
  const frame = replayFrameAt([observation("bus-1", 0), observation("bus-1", 30), observation("bus-2", 20)], start + 45_000, "feed-a");
  assert.deepEqual(frame.map((feature) => feature.geometry.coordinates[0]), [-113.47, -113.48]);
});

test("replay hides a vehicle across a source gap and rejects a mismatched static feed", () => {
  assert.equal(replayFrameAt([observation("bus-1", 0)], start + 121_000, "feed-a").length, 0);
  assert.equal(replayFrameAt([observation("bus-1", 30, "feed-b")], start + 31_000, "feed-a").length, 0);
});

test("replay clamps timeline endpoints and supports backward scrubbing", () => {
  assert.equal(clampReplayTimestamp(start - 1, start, end), start);
  assert.equal(clampReplayTimestamp(end + 1, start, end), end);
  assert.equal(replayTimestampAtFraction(0.25, start, end), start + 150_000);
  assert.equal(replayTimestampAtFraction(0, start, end), start);
});

test("playback speed math advances recorded time deterministically", () => {
  assert.equal(advanceReplayTimestamp(start, 1_000, 5, start, end), start + 5_000);
  assert.equal(advanceReplayTimestamp(start, 1_000, 20, start, end), start + 20_000);
  assert.equal(advanceReplayTimestamp(start, 1_000, 60, start, end), start + 60_000);
  assert.equal(advanceReplayTimestamp(end - 1_000, 1_000, 60, start, end), end);
});

test("live and replay vehicle sources remain distinct across mode changes", () => {
  assert.equal(vehicleDataForMode("replay", "live-data", "history-data"), "history-data");
  assert.equal(vehicleDataForMode("live", "live-data", "history-data"), "live-data");
});
