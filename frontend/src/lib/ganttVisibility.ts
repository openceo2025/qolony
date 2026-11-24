export type GanttVisibilityTask = {
  Id: string;
  ParentTaskId: string | null;
  Status: string;
};

export type CompletedTaskDepthCutoff = number | null;

const STORAGE_PREFIX = "qolony:gantt:completed-depth:";
const SHOW_ALL_STORAGE_VALUE = "all";

export function buildGanttTaskDepthMap<T extends Pick<GanttVisibilityTask, "Id" | "ParentTaskId">>(
  tasks: T[]
): Map<string, number> {
  const taskById = new Map(tasks.map((task) => [task.Id, task]));
  const cache = new Map<string, number>();

  const depthOf = (task: T, visiting: Set<string>): number => {
    const cached = cache.get(task.Id);
    if (cached !== undefined) {
      return cached;
    }
    if (!task.ParentTaskId || visiting.has(task.Id)) {
      cache.set(task.Id, 0);
      return 0;
    }
    const parent = taskById.get(task.ParentTaskId);
    if (!parent) {
      cache.set(task.Id, 0);
      return 0;
    }
    visiting.add(task.Id);
    const depth = depthOf(parent, visiting) + 1;
    visiting.delete(task.Id);
    cache.set(task.Id, depth);
    return depth;
  };

  for (const task of tasks) {
    depthOf(task, new Set<string>());
  }
  return cache;
}

export function filterGanttTasksByCompletedDepth<T extends GanttVisibilityTask>(
  tasks: T[],
  cutoff: CompletedTaskDepthCutoff
): T[] {
  if (cutoff === null) {
    return tasks;
  }

  const normalizedCutoff = Math.max(0, Math.floor(cutoff));
  const taskById = new Map(tasks.map((task) => [task.Id, task]));
  const depthById = buildGanttTaskDepthMap(tasks);
  const visibleTaskIds = new Set<string>();

  const keepWithAncestors = (task: T): void => {
    let current: T | undefined = task;
    const visited = new Set<string>();
    while (current && !visited.has(current.Id)) {
      visibleTaskIds.add(current.Id);
      visited.add(current.Id);
      current = current.ParentTaskId ? taskById.get(current.ParentTaskId) : undefined;
    }
  };

  for (const task of tasks) {
    const shouldHide =
      (task.Status === "Completion" || task.Status === "Abort") &&
      (depthById.get(task.Id) ?? 0) >= normalizedCutoff;
    if (!shouldHide) {
      keepWithAncestors(task);
    }
  }

  return tasks.filter((task) => visibleTaskIds.has(task.Id));
}

function storageKey(selectedUserId: string): string {
  return `${STORAGE_PREFIX}${encodeURIComponent(selectedUserId || "anonymous")}`;
}

export function parseCompletedTaskDepthCutoff(value: string | null): CompletedTaskDepthCutoff {
  if (!value || value === SHOW_ALL_STORAGE_VALUE) {
    return null;
  }
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : null;
}

export function readCompletedTaskDepthCutoff(
  selectedUserId: string,
  storage?: Pick<Storage, "getItem"> | null
): CompletedTaskDepthCutoff {
  try {
    const targetStorage = storage === undefined ? (typeof window === "undefined" ? null : window.localStorage) : storage;
    return targetStorage ? parseCompletedTaskDepthCutoff(targetStorage.getItem(storageKey(selectedUserId))) : null;
  } catch {
    return null;
  }
}

export function writeCompletedTaskDepthCutoff(
  selectedUserId: string,
  cutoff: CompletedTaskDepthCutoff,
  storage?: Pick<Storage, "setItem"> | null
): void {
  try {
    const targetStorage = storage === undefined ? (typeof window === "undefined" ? null : window.localStorage) : storage;
    targetStorage?.setItem(
      storageKey(selectedUserId),
      cutoff === null ? SHOW_ALL_STORAGE_VALUE : String(Math.max(0, Math.floor(cutoff)))
    );
  } catch {
    // localStorageが利用できない端末でも、現在の画面内ではフィルターを利用できるようにする。
  }
}
