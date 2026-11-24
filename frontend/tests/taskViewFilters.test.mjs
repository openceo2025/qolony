import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyTodayOrderToTasks,
  filterSyncVisibleTasks,
  filterWbsVisibleTasks,
  moveVisibleIdWithinOrderedIds,
} from "../src/lib/taskViewFilters.ts";

function task(id, excluded) {
  return {
    Id: id,
    ParentTaskId: null,
    Name: id,
    AssigneeUserId: "u1",
    AssigneeName: "u1",
    CreatorUserId: "u1",
    StartDate: "2026-06-01",
    EndDate: "2026-06-10",
    CreateDate: "2026-06-01T00:00:00",
    AssignedManHour: 0,
    ActualManHour: 0,
    ProgressRate: 0,
    Signal: "Green",
    Status: "NotStart",
    Type: "Task",
    IsRework: false,
    PreventedByTaskId: null,
    Memo: "",
    TodayOverride: null,
    TodaySortOrder: null,
    WbsSortOrder: null,
    ChildDependencyMode: "Serial",
    IQuavisTaskId: null,
    IQuavisProjectId: null,
    IQuavisSyncState: excluded ? "Excluded" : "Linked",
    IQuavisSyncExcluded: excluded,
    IQuavisConflictMessage: null,
    PlannedDeliverables: [],
    OutputDeliverables: [],
  };
}

test("filterSyncVisibleTasks removes iQUAVIS sync-excluded tasks", () => {
  assert.deepEqual(
    filterSyncVisibleTasks([task("visible", false), task("excluded", true)]).map((item) => item.Id),
    ["visible"]
  );
});

test("filterWbsVisibleTasks hides completed and aborted tasks by default", () => {
  const active = task("active", false);
  const completed = { ...task("completed", false), Status: "Completion" };
  const aborted = { ...task("aborted", false), Status: "Abort" };

  assert.deepEqual(
    filterWbsVisibleTasks([active, completed, aborted], false, false).map((item) => item.Id),
    ["active"]
  );
  assert.deepEqual(
    filterWbsVisibleTasks([active, completed, aborted], true, false).map((item) => item.Id),
    ["active", "completed", "aborted"]
  );
});

test("filterWbsVisibleTasks keeps the completed and sync-excluded toggles independent", () => {
  const completedExcluded = { ...task("completed-excluded", true), Status: "Completion" };

  assert.deepEqual(filterWbsVisibleTasks([completedExcluded], true, false), []);
  assert.deepEqual(
    filterWbsVisibleTasks([completedExcluded], true, true).map((item) => item.Id),
    ["completed-excluded"]
  );
});

test("moveVisibleIdWithinOrderedIds preserves hidden ids while moving visible ids", () => {
  assert.deepEqual(
    moveVisibleIdWithinOrderedIds(["A", "hidden", "B"], ["A", "B"], "A", 1),
    ["B", "hidden", "A"]
  );
});

test("applyTodayOrderToTasks updates today sort order only", () => {
  const tasks = [task("A", false), task("B", false)];

  const nextTasks = applyTodayOrderToTasks(tasks, ["B", "A"]);

  assert.equal(nextTasks.find((item) => item.Id === "B")?.TodaySortOrder, 1);
  assert.equal(nextTasks.find((item) => item.Id === "A")?.TodaySortOrder, 2);
  assert.equal(nextTasks.find((item) => item.Id === "B")?.WbsSortOrder, null);
});
