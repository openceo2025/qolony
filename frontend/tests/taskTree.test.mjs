import assert from "node:assert/strict";
import test from "node:test";

import { buildTree, computeAssignedMinutes } from "../src/lib/taskTree.ts";

const baseTask = {
  Id: "",
  ParentTaskId: null,
  Name: "",
  AssigneeUserId: "u",
  AssigneeName: "user",
  CreatorUserId: "u",
  StartDate: "2026-05-01",
  EndDate: "2026-05-31",
  CreateDate: "2026-05-01T00:00:00",
  AssignedManHour: 0,
  ActualManHour: 0,
  ChildAssignedManHour: 0,
  EstimateBalanceManHour: 0,
  EstimateSignal: "None",
  EstimateSignalMessage: null,
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
  IQuavisSyncState: "Unlinked",
  IQuavisSyncExcluded: false,
  IQuavisConflictMessage: null,
  PlannedDeliverables: [],
  OutputDeliverables: [],
};

function task(overrides) {
  return { ...baseTask, ...overrides };
}

test("buildTree uses WBS sort order within each sibling group", () => {
  const tree = buildTree([
    task({ Id: "parent", Name: "parent", WbsSortOrder: 1 }),
    task({ Id: "second", ParentTaskId: "parent", Name: "second", WbsSortOrder: 2 }),
    task({ Id: "first", ParentTaskId: "parent", Name: "first", WbsSortOrder: 1 }),
  ]);

  assert.deepEqual(tree.map((node) => node.id), ["parent"]);
  assert.deepEqual(tree[0].children.map((node) => node.id), ["first", "second"]);
});

test("computeAssignedMinutes allows estimate balance and debt", () => {
  assert.deepEqual(computeAssignedMinutes(600, [50, 25]), [300, 150]);
  assert.deepEqual(computeAssignedMinutes(600, [100, 50]), [600, 300]);
  assert.deepEqual(computeAssignedMinutes(600, [0, 50]), [0, 300]);
});
