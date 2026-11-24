import assert from "node:assert/strict";
import test from "node:test";

import { applyGanttTaskDateChange, buildGanttSchedulePayload, formatLocalIsoDate } from "../src/lib/ganttSchedule.ts";

test("formatLocalIsoDate keeps local calendar date", () => {
  const value = new Date(2026, 2, 28, 23, 59, 59);
  assert.equal(formatLocalIsoDate(value), "2026-03-28");
});

test("buildGanttSchedulePayload converts gantt task dates to API payload", () => {
  const payload = buildGanttSchedulePayload({
    id: "task-1",
    start: new Date(2026, 2, 28, 0, 0, 0),
    end: new Date(2026, 3, 2, 23, 59, 59),
  });
  assert.deepEqual(payload, {
    taskId: "task-1",
    startDate: "2026-03-28",
    endDate: "2026-04-02",
  });
});

test("applyGanttTaskDateChange replaces only the changed task", () => {
  const original = [
    { id: "task-1", start: new Date(2026, 2, 28), end: new Date(2026, 2, 29) },
    { id: "task-2", start: new Date(2026, 3, 1), end: new Date(2026, 3, 2) },
  ];
  const changed = { id: "task-2", start: new Date(2026, 3, 3), end: new Date(2026, 3, 4) };
  const next = applyGanttTaskDateChange(original, changed);

  assert.equal(next[0], original[0]);
  assert.equal(next[1], changed);
});
