import test from "node:test";
import assert from "node:assert/strict";
import { buildDueDateWarnings } from "../src/lib/taskScheduleWarnings.ts";

function task(overrides = {}) {
  return {
    Id: "task",
    ParentTaskId: "parent",
    StartDate: "2026-07-10",
    EndDate: "2026-07-20",
    ...overrides,
  };
}

test("due date warnings explain automatic parent/start adjustment without blocking input", () => {
  const current = task();
  assert.deepEqual(
    buildDueDateWarnings(current, [current], "2026-07-01", "2026-07-15"),
    ["期限が着手日より前のため、保存時に着手日も同じ日へ調整されます。"]
  );
  assert.match(
    buildDueDateWarnings(current, [current], "2026-08-01", "2026-07-15")[0],
    /自動調整/
  );
});

test("due date warnings identify children outside the new parent deadline", () => {
  const current = task({ Id: "parent", ParentTaskId: null });
  const child = task({ Id: "child", ParentTaskId: "parent", EndDate: "2026-08-10" });
  assert.match(buildDueDateWarnings(current, [current, child], "2026-08-01", null)[0], /配下タスク/);
});
