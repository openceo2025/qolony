import type { Task } from "../types";
import type { WbsNode } from "./taskTree";

export const WBS_ROOT_PARENT_KEY = "__root__";

export function wbsParentKey(parentTaskId: string | null): string {
  return parentTaskId ?? WBS_ROOT_PARENT_KEY;
}

function compareTaskForWbsOrder(a: Task, b: Task): number {
  if (a.WbsSortOrder !== null || b.WbsSortOrder !== null) {
    if (a.WbsSortOrder === null) return 1;
    if (b.WbsSortOrder === null) return -1;
    if (a.WbsSortOrder !== b.WbsSortOrder) return a.WbsSortOrder - b.WbsSortOrder;
  }
  if (a.EndDate !== b.EndDate) return a.EndDate.localeCompare(b.EndDate);
  if (a.CreateDate !== b.CreateDate) return a.CreateDate.localeCompare(b.CreateDate);
  if (a.Name !== b.Name) return a.Name < b.Name ? -1 : 1;
  return a.Id < b.Id ? -1 : a.Id > b.Id ? 1 : 0;
}

export function buildOrderedChildren(tasks: Task[]): Map<string, string[]> {
  const byParent = new Map<string, Task[]>();
  for (const task of tasks) {
    const key = wbsParentKey(task.ParentTaskId);
    const siblings = byParent.get(key) ?? [];
    siblings.push(task);
    byParent.set(key, siblings);
  }

  const ordered = new Map<string, string[]>();
  for (const [key, siblings] of byParent.entries()) {
    ordered.set(
      key,
      [...siblings].sort(compareTaskForWbsOrder).map((task) => task.Id)
    );
  }
  return ordered;
}

export function getOrderedWbsSiblingIds(tasks: Task[], parentTaskId: string | null): string[] {
  return buildOrderedChildren(tasks).get(wbsParentKey(parentTaskId)) ?? [];
}

export function buildWbsTreeFromOrder(
  taskById: Map<string, Task>,
  orderedChildrenByParent: Map<string, string[]>,
  visibleTaskIds: Set<string>
): WbsNode[] {
  const build = (parentKey: string): WbsNode[] => {
    const orderedIds = orderedChildrenByParent.get(parentKey) ?? [];
    const nodes: WbsNode[] = [];

    for (const taskId of orderedIds) {
      if (!visibleTaskIds.has(taskId)) continue;
      const task = taskById.get(taskId);
      if (!task) continue;

      const children = build(task.Id);
      nodes.push({
        id: task.Id,
        name: task.Name,
        task,
        children: children.length > 0 ? children : undefined,
      });
    }
    return nodes;
  };

  return build(WBS_ROOT_PARENT_KEY);
}

export function moveVisibleSiblingInOrder(
  orderedChildrenByParent: Map<string, string[]>,
  parentTaskId: string | null,
  taskId: string,
  direction: -1 | 1,
  visibleTaskIds: Set<string>
): { nextOrderedChildrenByParent: Map<string, string[]>; orderedSiblingIds: string[] } | null {
  const key = wbsParentKey(parentTaskId);
  const siblingIds = orderedChildrenByParent.get(key);
  if (!siblingIds) return null;

  const visibleSiblingIds = siblingIds.filter((siblingId) => visibleTaskIds.has(siblingId));
  const visibleIndex = visibleSiblingIds.indexOf(taskId);
  const targetVisibleId = visibleSiblingIds[visibleIndex + direction];
  if (visibleIndex < 0 || !targetVisibleId) return null;

  const currentIndex = siblingIds.indexOf(taskId);
  const targetIndex = siblingIds.indexOf(targetVisibleId);
  if (currentIndex < 0 || targetIndex < 0) return null;

  const orderedSiblingIds = [...siblingIds];
  [orderedSiblingIds[currentIndex], orderedSiblingIds[targetIndex]] = [
    orderedSiblingIds[targetIndex],
    orderedSiblingIds[currentIndex],
  ];

  const nextOrderedChildrenByParent = new Map(orderedChildrenByParent);
  nextOrderedChildrenByParent.set(key, orderedSiblingIds);
  return { nextOrderedChildrenByParent, orderedSiblingIds };
}

function moveInSiblingNodes(nodes: WbsNode[], taskId: string, direction: -1 | 1): WbsNode[] | null {
  const currentIndex = nodes.findIndex((node) => node.id === taskId);
  const targetIndex = currentIndex + direction;
  if (currentIndex < 0 || targetIndex < 0 || targetIndex >= nodes.length) return null;

  const next = [...nodes];
  [next[currentIndex], next[targetIndex]] = [next[targetIndex], next[currentIndex]];
  return next;
}

export function moveVisibleNodeInTree(
  nodes: WbsNode[],
  parentTaskId: string | null,
  taskId: string,
  direction: -1 | 1
): WbsNode[] | null {
  if (parentTaskId === null) {
    return moveInSiblingNodes(nodes, taskId, direction);
  }

  let moved = false;
  const visit = (currentNodes: WbsNode[]): WbsNode[] => {
    let changed = false;
    const nextNodes = currentNodes.map((node) => {
      if (node.id === parentTaskId) {
        const movedChildren = moveInSiblingNodes(node.children ?? [], taskId, direction);
        if (!movedChildren) return node;
        moved = true;
        changed = true;
        return { ...node, children: movedChildren.length > 0 ? movedChildren : undefined };
      }

      if (!node.children) return node;
      const nextChildren = visit(node.children);
      if (nextChildren === node.children) return node;
      changed = true;
      return { ...node, children: nextChildren };
    });

    return changed ? nextNodes : currentNodes;
  };

  const nextTree = visit(nodes);
  return moved ? nextTree : null;
}

export function buildLeafTaskIds(tasks: Task[]): Set<string> {
  const parentIds = new Set(tasks.map((task) => task.ParentTaskId).filter((parentId): parentId is string => parentId !== null));
  return new Set(tasks.filter((task) => !parentIds.has(task.Id)).map((task) => task.Id));
}

export function buildDescendantAssigneeMap(tasks: Task[], selectedUserId: string): Map<string, boolean> {
  const childrenByParent = new Map<string, Task[]>();
  for (const task of tasks) {
    const key = wbsParentKey(task.ParentTaskId);
    const children = childrenByParent.get(key) ?? [];
    children.push(task);
    childrenByParent.set(key, children);
  }

  const memo = new Map<string, boolean>();
  const visit = (taskId: string): boolean => {
    if (memo.has(taskId)) return memo.get(taskId) ?? false;
    const children = childrenByParent.get(taskId) ?? [];
    const hasDescendant = children.some((child) => child.AssigneeUserId === selectedUserId || visit(child.Id));
    memo.set(taskId, hasDescendant);
    return hasDescendant;
  };

  for (const task of tasks) {
    visit(task.Id);
  }
  return memo;
}

export function applyWbsSiblingOrderToTasks(
  tasks: Task[],
  parentTaskId: string | null,
  orderedTaskIds: string[]
): Task[] {
  const siblingIds = tasks.filter((task) => task.ParentTaskId === parentTaskId).map((task) => task.Id);
  if (siblingIds.length !== orderedTaskIds.length || new Set(siblingIds).size !== new Set(orderedTaskIds).size) {
    return tasks;
  }
  const siblingIdSet = new Set(siblingIds);
  if (orderedTaskIds.some((taskId) => !siblingIdSet.has(taskId))) {
    return tasks;
  }

  const nextSortOrderById = new Map(orderedTaskIds.map((taskId, index) => [taskId, index + 1]));
  let changed = false;
  const nextTasks = tasks.map((task) => {
    const nextSortOrder = nextSortOrderById.get(task.Id);
    if (nextSortOrder === undefined || task.WbsSortOrder === nextSortOrder) {
      return task;
    }
    changed = true;
    return { ...task, WbsSortOrder: nextSortOrder };
  });
  return changed ? nextTasks : tasks;
}
