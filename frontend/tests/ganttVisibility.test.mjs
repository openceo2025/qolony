import assert from "node:assert/strict";
import test from "node:test";

import {
  buildGanttTaskDepthMap,
  filterGanttTasksByCompletedDepth,
  parseCompletedTaskDepthCutoff,
  readCompletedTaskDepthCutoff,
  writeCompletedTaskDepthCutoff,
} from "../src/lib/ganttVisibility.ts";

const tasks = [
  { Id: "root", ParentTaskId: null, Status: "DuringStart" },
  { Id: "done-parent", ParentTaskId: "root", Status: "Completion" },
  { Id: "done-leaf", ParentTaskId: "done-parent", Status: "Completion" },
  { Id: "active-leaf", ParentTaskId: "done-parent", Status: "NotStart" },
  { Id: "done-branch", ParentTaskId: "root", Status: "Completion" },
  { Id: "done-branch-leaf", ParentTaskId: "done-branch", Status: "Completion" },
];

test("buildGanttTaskDepthMap assigns root as hierarchy 1", () => {
  const depths = buildGanttTaskDepthMap(tasks);
  assert.equal(depths.get("root"), 0);
  assert.equal(depths.get("done-parent"), 1);
  assert.equal(depths.get("active-leaf"), 2);
});

test("filterGanttTasksByCompletedDepth hides completed branches but keeps an ancestor needed by an active task", () => {
  const visible = filterGanttTasksByCompletedDepth(tasks, 1);
  assert.deepEqual(
    visible.map((task) => task.Id),
    ["root", "done-parent", "active-leaf"]
  );
});

test("filterGanttTasksByCompletedDepth keeps completed tasks above the cutoff", () => {
  const visible = filterGanttTasksByCompletedDepth(tasks, 2);
  assert.deepEqual(
    visible.map((task) => task.Id),
    ["root", "done-parent", "active-leaf", "done-branch"]
  );
});

test("filterGanttTasksByCompletedDepth treats Abort as a terminal task", () => {
  const tasks = [
    { Id: "root", ParentTaskId: null, Status: "DuringStart" },
    { Id: "aborted", ParentTaskId: "root", Status: "Abort" },
  ];

  assert.deepEqual(filterGanttTasksByCompletedDepth(tasks, 1).map((task) => task.Id), ["root"]);
});

test("show-all cutoff preserves the original task array", () => {
  assert.equal(filterGanttTasksByCompletedDepth(tasks, null), tasks);
});

test("completed depth preference round-trips through per-user storage", () => {
  const values = new Map();
  const storage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };

  writeCompletedTaskDepthCutoff("user/1", 2, storage);
  writeCompletedTaskDepthCutoff("user/2", null, storage);

  assert.equal(readCompletedTaskDepthCutoff("user/1", storage), 2);
  assert.equal(readCompletedTaskDepthCutoff("user/2", storage), null);
  assert.equal(parseCompletedTaskDepthCutoff("invalid"), null);
});
