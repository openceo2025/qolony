import assert from "node:assert/strict";
import test from "node:test";

import {
  formatGanttTaskRange,
  getGanttTaskSyncClass,
  getGanttTaskSyncLabel,
  isGanttTaskPaired,
} from "../src/lib/ganttViewModel.ts";

test("formatGanttTaskRange combines start and end dates", () => {
  assert.equal(
    formatGanttTaskRange({
      StartDate: "2026-04-04",
      EndDate: "2026-04-08",
    }),
    "2026-04-04 -> 2026-04-08"
  );
});

test("isGanttTaskPaired returns true only when iQUAVIS id exists", () => {
  assert.equal(
    isGanttTaskPaired({
      IQuavisTaskId: "REMOTE-1",
    }),
    true
  );

  assert.equal(
    isGanttTaskPaired({
      IQuavisTaskId: null,
    }),
    false
  );
});

test("pending iQUAVIS save is distinct from conflict and cannot look linked", () => {
  const task = { IQuavisTaskId: "REMOTE-1", IQuavisSyncState: "PendingIquavisSave" };

  assert.equal(getGanttTaskSyncLabel(task), "Excel保存待ち");
  assert.equal(getGanttTaskSyncClass(task), "pending");
});

test("conflict hold is rendered as hold state and class", () => {
  const task = { IQuavisTaskId: "REMOTE-1", IQuavisSyncState: "ConflictHold" };

  assert.equal(getGanttTaskSyncLabel(task), "Hold");
  assert.equal(getGanttTaskSyncClass(task), "hold");
});

