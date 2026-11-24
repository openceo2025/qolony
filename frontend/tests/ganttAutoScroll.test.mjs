import assert from "node:assert/strict";
import test from "node:test";

import { calculateGanttAutoScrollDelta } from "../src/lib/ganttAutoScroll.ts";

test("calculateGanttAutoScrollDelta is idle away from viewport edges", () => {
  assert.equal(calculateGanttAutoScrollDelta(500, 100, 900), 0);
});

test("calculateGanttAutoScrollDelta scrolls left and accelerates toward the edge", () => {
  const nearThreshold = calculateGanttAutoScrollDelta(155, 100, 900);
  const atEdge = calculateGanttAutoScrollDelta(100, 100, 900);
  assert.ok(nearThreshold < 0);
  assert.ok(atEdge < nearThreshold);
});

test("calculateGanttAutoScrollDelta scrolls right and clamps outside the viewport", () => {
  assert.ok(calculateGanttAutoScrollDelta(850, 100, 900) > 0);
  assert.equal(calculateGanttAutoScrollDelta(1000, 100, 900), 26);
});

test("calculateGanttAutoScrollDelta rejects invalid viewport geometry", () => {
  assert.equal(calculateGanttAutoScrollDelta(100, 500, 500), 0);
});
