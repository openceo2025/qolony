import assert from "node:assert/strict";
import { test } from "node:test";
import {
  applyWbsSiblingOrderToTasks,
  buildOrderedChildren,
  buildWbsTreeFromOrder,
  getOrderedWbsSiblingIds,
  moveVisibleNodeInTree,
  moveVisibleSiblingInOrder,
  WBS_ROOT_PARENT_KEY,
} from "../src/lib/wbsLocalOrder.ts";

function task(id, parentId, sortOrder, assigneeUserId = "u1") {
  return {
    Id: id,
    ParentTaskId: parentId,
    Name: id,
    AssigneeUserId: assigneeUserId,
    AssigneeName: assigneeUserId,
    CreatorUserId: "u1",
    StartDate: "2026-06-01",
    EndDate: "2026-06-10",
    CreateDate: `2026-06-01T00:00:0${sortOrder}`,
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
    WbsSortOrder: sortOrder,
    ChildDependencyMode: "Serial",
    IQuavisTaskId: null,
    IQuavisProjectId: null,
    IQuavisSyncState: "Unlinked",
    IQuavisSyncExcluded: false,
    IQuavisConflictMessage: null,
    PlannedDeliverables: [],
    OutputDeliverables: [],
  };
}

function ids(nodes) {
  return nodes.map((node) => node.id);
}

test("moveVisibleSiblingInOrder swaps visible siblings while preserving hidden sibling ids", () => {
  const tasks = [task("A", null, 1), task("hidden", null, 2), task("B", null, 3)];
  const ordered = buildOrderedChildren(tasks);

  const result = moveVisibleSiblingInOrder(ordered, null, "A", 1, new Set(["A", "B"]));

  assert.deepEqual(result?.orderedSiblingIds, ["B", "hidden", "A"]);
  assert.deepEqual(ordered.get(WBS_ROOT_PARENT_KEY), ["A", "hidden", "B"]);
});

test("moveVisibleNodeInTree swaps only the displayed sibling nodes", () => {
  const tasks = [task("A", null, 1), task("hidden", null, 2), task("B", null, 3)];
  const ordered = buildOrderedChildren(tasks);
  const taskById = new Map(tasks.map((item) => [item.Id, item]));
  const tree = buildWbsTreeFromOrder(taskById, ordered, new Set(["A", "B"]));

  const nextTree = moveVisibleNodeInTree(tree, null, "A", 1);

  assert.deepEqual(ids(tree), ["A", "B"]);
  assert.deepEqual(ids(nextTree ?? []), ["B", "A"]);
});

test("moveVisibleNodeInTree swaps nested siblings without rebuilding unrelated roots", () => {
  const tasks = [
    task("P", null, 1),
    task("other-root", null, 2),
    task("C1", "P", 1),
    task("C2", "P", 2),
  ];
  const ordered = buildOrderedChildren(tasks);
  const taskById = new Map(tasks.map((item) => [item.Id, item]));
  const tree = buildWbsTreeFromOrder(taskById, ordered, new Set(tasks.map((item) => item.Id)));

  const nextTree = moveVisibleNodeInTree(tree, "P", "C1", 1);
  const nextParent = nextTree?.find((node) => node.id === "P");

  assert.deepEqual(ids(nextParent?.children ?? []), ["C2", "C1"]);
  assert.equal(nextTree?.find((node) => node.id === "other-root"), tree.find((node) => node.id === "other-root"));
});

test("applyWbsSiblingOrderToTasks updates only the requested sibling group", () => {
  const tasks = [task("P", null, 1), task("C1", "P", 1), task("C2", "P", 2), task("other", null, 2)];

  const nextTasks = applyWbsSiblingOrderToTasks(tasks, "P", ["C2", "C1"]);

  assert.notEqual(nextTasks, tasks);
  assert.equal(nextTasks.find((item) => item.Id === "C2")?.WbsSortOrder, 1);
  assert.equal(nextTasks.find((item) => item.Id === "C1")?.WbsSortOrder, 2);
  assert.equal(nextTasks.find((item) => item.Id === "P"), tasks.find((item) => item.Id === "P"));
  assert.equal(nextTasks.find((item) => item.Id === "other"), tasks.find((item) => item.Id === "other"));
});

test("applyWbsSiblingOrderToTasks rejects incomplete sibling order", () => {
  const tasks = [task("C1", "P", 1), task("C2", "P", 2)];

  assert.equal(applyWbsSiblingOrderToTasks(tasks, "P", ["C2"]), tasks);
});

test("getOrderedWbsSiblingIds uses a deterministic id tie-breaker", () => {
  const taskB = { ...task("B", "P", null), CreateDate: "2026-06-01T00:00:00", Name: "same" };
  const taskA = { ...task("A", "P", null), CreateDate: "2026-06-01T00:00:00", Name: "same" };

  assert.deepEqual(getOrderedWbsSiblingIds([taskB, taskA], "P"), ["A", "B"]);
});
