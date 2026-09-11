import assert from "node:assert/strict";
import test from "node:test";

import { canDisplayReliabilityEstimates } from "../app/analytics";

test("reliability estimates stay hidden when overall history is insufficient", () => {
  assert.equal(canDisplayReliabilityEstimates(false, true), false);
});

test("reliability estimates stay hidden when delay samples are insufficient", () => {
  assert.equal(canDisplayReliabilityEstimates(true, false), false);
});

test("reliability estimates display only after both sufficiency gates pass", () => {
  assert.equal(canDisplayReliabilityEstimates(true, true), true);
});
