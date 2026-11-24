import type { Task } from "../types";

export interface WbsNode {
  id: string;
  name: string;
  task: Task;
  children?: WbsNode[];
}

export const compareTask = (a: Task, b: Task): number => {
  if (a.WbsSortOrder !== null || b.WbsSortOrder !== null) {
    if (a.WbsSortOrder === null) return 1;
    if (b.WbsSortOrder === null) return -1;
    if (a.WbsSortOrder !== b.WbsSortOrder) return a.WbsSortOrder - b.WbsSortOrder;
  }
  if (a.EndDate !== b.EndDate) return a.EndDate.localeCompare(b.EndDate);
  if (a.CreateDate !== b.CreateDate) return a.CreateDate.localeCompare(b.CreateDate);
  return a.Name.localeCompare(b.Name);
};

export function buildTree(tasks: Task[]): WbsNode[] {
  const byParent = new Map<string, Task[]>();
  for (const task of tasks) {
    const key = task.ParentTaskId ?? "__root__";
    const list = byParent.get(key) ?? [];
    list.push(task);
    byParent.set(key, list);
  }

  const build = (parentId: string): WbsNode[] => {
    const tasksForParent = [...(byParent.get(parentId) ?? [])].sort(compareTask);
    return tasksForParent.map((task) => {
      const children = build(task.Id);
      return {
        id: task.Id,
        name: task.Name,
        task,
        children: children.length > 0 ? children : undefined,
      };
    });
  };

  return build("__root__");
}

export function isLeafTask(taskId: string, tasks: Task[]): boolean {
  return !tasks.some((task) => task.ParentTaskId === taskId);
}

export function isTodayAutoOn(endDate: string): boolean {
  const now = new Date();
  const tomorrow = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  const due = new Date(`${endDate}T00:00:00`);
  return due <= tomorrow;
}

export function isTodayOn(task: Task): boolean {
  if (task.TodayOverride !== null && task.TodayOverride !== undefined) {
    return !!task.TodayOverride;
  }
  return isTodayAutoOn(task.EndDate);
}

export function buildChildMap(tasks: Task[]): Map<string, Task[]> {
  const map = new Map<string, Task[]>();
  for (const task of tasks) {
    const key = task.ParentTaskId ?? "__root__";
    const list = map.get(key) ?? [];
    list.push(task);
    map.set(key, list);
  }
  return map;
}

export function hasDescendantAssignee(
  taskId: string,
  selectedUserId: string,
  childMap: Map<string, Task[]>,
  memo: Map<string, boolean> = new Map()
): boolean {
  if (memo.has(taskId)) return memo.get(taskId) ?? false;
  const children = childMap.get(taskId) ?? [];
  for (const child of children) {
    if (child.AssigneeUserId === selectedUserId || hasDescendantAssignee(child.Id, selectedUserId, childMap, memo)) {
      memo.set(taskId, true);
      return true;
    }
  }
  memo.set(taskId, false);
  return false;
}

export function computeAssignedMinutes(parentAssigned: number, ratios: number[]): number[] {
  const parentMinutes = Math.max(0, Math.floor(parentAssigned));
  return ratios.map((ratio) => Math.max(0, Math.round((parentMinutes * Math.max(0, ratio)) / 100)));
}
