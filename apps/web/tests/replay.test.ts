import assert from "node:assert/strict";
import test from "node:test";

import {
  advanceReplayTimestamp,
  clampReplayTimestamp,
  replayCoverageBuckets,
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

function coverageObservation(at: string, vehicleId = "1"): ReplayObservation {
  return {
    type: "Feature",
    geometry: { type: "Point", coordinates: [-113.5, 53.5] },
    properties: {
      vehicle_id: vehicleId,
      static_feed_id: "feed-1",
      observed_at: at,
      source_timestamp: null,
      recorded_at: at,
    },
  };
}

test("replay coverage keeps empty buckets so recording gaps stay visible", () => {
  const start = "2026-09-11T20:00:00.000Z";
  const end = "2026-09-11T20:40:00.000Z";
  // Two observations in the first 10-minute bucket, one in the last.
  const features = [
    coverageObservation("2026-09-11T20:01:00.000Z"),
    coverageObservation("2026-09-11T20:02:00.000Z"),
    coverageObservation("2026-09-11T20:35:00.000Z"),
  ];
  const buckets = replayCoverageBuckets(features, start, end, 4);
  assert.equal(buckets.length, 4);
  assert.deepEqual(buckets.map((bucket) => bucket.count), [2, 0, 0, 1]);
  assert.equal(buckets[0].start, start);
});

test("replay coverage ignores observations outside the window and bad input", () => {
  const start = "2026-09-11T20:00:00.000Z";
  const end = "2026-09-11T20:40:00.000Z";
  const buckets = replayCoverageBuckets(
    [coverageObservation("2026-09-11T19:00:00.000Z"), coverageObservation("2026-09-11T21:00:00.000Z")],
    start,
    end,
    4,
  );
  assert.deepEqual(buckets.map((bucket) => bucket.count), [0, 0, 0, 0]);

  assert.deepEqual(replayCoverageBuckets([], start, end, 4), []);
  assert.deepEqual(replayCoverageBuckets([coverageObservation(start)], null, end, 4), []);
  // An inverted window is not a window.
  assert.deepEqual(replayCoverageBuckets([coverageObservation(start)], end, start, 4), []);
});

test("replay coverage places a boundary observation in the final bucket", () => {
  const start = "2026-09-11T20:00:00.000Z";
  const end = "2026-09-11T20:40:00.000Z";
  const buckets = replayCoverageBuckets([coverageObservation(end)], start, end, 4);
  assert.deepEqual(buckets.map((bucket) => bucket.count), [0, 0, 0, 1]);
});
