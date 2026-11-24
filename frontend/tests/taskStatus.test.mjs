import assert from "node:assert/strict";
import test from "node:test";

import {
  ABORTED_TASK_MARKER_COLOR,
  formatTaskStatus,
  getTaskMarkerColor,
  isAbortedTaskStatus,
  isTerminalTaskStatus,
} from "../src/lib/taskStatus.ts";

test("Abort is displayed as a terminal cancelled task", () => {
  assert.equal(isAbortedTaskStatus("Abort"), true);
  assert.equal(isTerminalTaskStatus("Abort"), true);
  assert.equal(isTerminalTaskStatus("Completion"), true);
  assert.equal(isTerminalTaskStatus("DuringStart"), false);
  assert.equal(formatTaskStatus("Abort"), "中止（Abort）");
});

test("Abort marker remains gray even when iQUAVIS supplied a signal color", () => {
  assert.equal(
    getTaskMarkerColor("Abort", "Red", "2026-07-01", "2026-07-22"),
    ABORTED_TASK_MARKER_COLOR
  );
  assert.equal(getTaskMarkerColor("Completion", "", "2026-07-01", "2026-07-22"), "#3A86FF");
  assert.equal(getTaskMarkerColor("DuringStart", "Red", "2026-07-30", "2026-07-22"), "#E63946");
});
