import type { Task } from "../types";

function isFilterTerminalStatus(status: string): boolean {
  return status === "Completion" || status === "Abort";
}

export function filterSyncVisibleTasks(tasks: Task[]): Task[] {
  return tasks.filter((task) => !task.IQuavisSyncExcluded);
}

export function filterWbsVisibleTasks(
  tasks: Task[],
  showCompletedTasks: boolean,
  showSyncExcludedTasks: boolean
): Task[] {
  return tasks.filter((task) => {
    if (!showCompletedTasks && isFilterTerminalStatus(task.Status)) return false;
    if (!showSyncExcludedTasks && task.IQuavisSyncExcluded) return false;
    return true;
  });
}

export function moveVisibleIdWithinOrderedIds(
  orderedIds: string[],
  visibleIds: string[],
  taskId: string,
  direction: -1 | 1
): string[] | null {
  const visibleIndex = visibleIds.indexOf(taskId);
  const targetVisibleId = visibleIds[visibleIndex + direction];
  if (visibleIndex < 0 || !targetVisibleId) return null;

  const currentIndex = orderedIds.indexOf(taskId);
  const targetIndex = orderedIds.indexOf(targetVisibleId);
  if (currentIndex < 0 || targetIndex < 0) return null;

  const nextIds = [...orderedIds];
  [nextIds[currentIndex], nextIds[targetIndex]] = [nextIds[targetIndex], nextIds[currentIndex]];
  return nextIds;
}

export function applyTodayOrderToTasks(tasks: Task[], orderedTaskIds: string[]): Task[] {
  if (tasks.length !== orderedTaskIds.length || new Set(tasks.map((task) => task.Id)).size !== new Set(orderedTaskIds).size) {
    return tasks;
  }
  const taskIds = new Set(tasks.map((task) => task.Id));
  if (orderedTaskIds.some((taskId) => !taskIds.has(taskId))) {
    return tasks;
  }

  const nextSortOrderById = new Map(orderedTaskIds.map((taskId, index) => [taskId, index + 1]));
  let changed = false;
  const nextTasks = tasks.map((task) => {
    const nextSortOrder = nextSortOrderById.get(task.Id);
    if (nextSortOrder === undefined || task.TodaySortOrder === nextSortOrder) {
      return task;
    }
    changed = true;
    return { ...task, TodaySortOrder: nextSortOrder };
  });
  return changed ? nextTasks : tasks;
}
