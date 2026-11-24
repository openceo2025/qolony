import assert from "node:assert/strict";
import test from "node:test";

import { findTimelineScrollLeftForDate } from "../src/lib/ganttTimeline.ts";

test("findTimelineScrollLeftForDate centers the current day when it exists", () => {
  const cells = [
    { key: "2026-4-10", width: 60 },
    { key: "2026-4-11", width: 60 },
    { key: "2026-4-12", width: 60 },
  ];

  assert.equal(findTimelineScrollLeftForDate(cells, "Day", new Date(2026, 4, 12, 12), 120), 90);
});

test("findTimelineScrollLeftForDate maps week view to Monday-start wk cells", () => {
  const cells = [
    { key: "2026-4-4", width: 250 },
    { key: "2026-4-11", width: 250 },
  ];

  const scrollLeft = findTimelineScrollLeftForDate(cells, "Week", new Date(2026, 4, 13), 300);

  assert.ok(Math.abs(scrollLeft - 171.42857142857144) < 0.0001);
});

test("findTimelineScrollLeftForDate centers current time within quarter-day cells", () => {
  const cells = [
    { key: "2026-4-12-0", width: 60 },
    { key: "2026-4-12-6", width: 60 },
    { key: "2026-4-12-12", width: 60 },
  ];

  assert.equal(findTimelineScrollLeftForDate(cells, "Quarter Day", new Date(2026, 4, 12, 15), 120), 90);
});
