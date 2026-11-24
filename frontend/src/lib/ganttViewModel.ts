import type { Task } from "../types";

export function formatGanttTaskRange(task: Pick<Task, "StartDate" | "EndDate">): string {
  return `${task.StartDate} -> ${task.EndDate}`;
}

export function isGanttTaskPaired(task: Pick<Task, "IQuavisTaskId">): boolean {
  return !!task.IQuavisTaskId;
}

export function getGanttTaskSyncLabel(task: Pick<Task, "IQuavisTaskId" | "IQuavisSyncState">): string | null {
  if (task.IQuavisSyncState === "PendingIquavisSave") return "Excel保存待ち";
  if (task.IQuavisSyncState === "ConflictHold") return "Hold";
  if (task.IQuavisSyncState === "Excluded") return "Excluded";
  if (task.IQuavisSyncState === "Conflict") return "Conflict";
  if (task.IQuavisTaskId) return "iQUAVIS";
  return null;
}

export function getGanttTaskSyncClass(task: Pick<Task, "IQuavisTaskId" | "IQuavisSyncState">): string {
  if (task.IQuavisSyncState === "PendingIquavisSave") return "pending";
  if (task.IQuavisSyncState === "ConflictHold") return "hold";
  if (task.IQuavisSyncState === "Excluded") return "excluded";
  if (task.IQuavisSyncState === "Conflict") return "conflict";
  if (task.IQuavisTaskId) return "paired";
  return "local";
}
