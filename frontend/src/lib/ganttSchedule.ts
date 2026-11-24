import type { Task as GanttTask } from "gantt-task-react";

export function formatLocalIsoDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

export function buildGanttSchedulePayload(task: Pick<GanttTask, "id" | "start" | "end">): {
  taskId: string;
  startDate: string;
  endDate: string;
} {
  return {
    taskId: String(task.id),
    startDate: formatLocalIsoDate(task.start),
    endDate: formatLocalIsoDate(task.end),
  };
}

export function applyGanttTaskDateChange(tasks: GanttTask[], changedTask: GanttTask): GanttTask[] {
  return tasks.map((task) => (task.id === changedTask.id ? changedTask : task));
}
