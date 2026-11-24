import type { Task } from "../types";

export function buildDueDateWarnings(
  task: Task,
  allTasks: Task[],
  nextEndDate: string,
  parentOrProjectEndDate: string | null
): string[] {
  if (!nextEndDate) return [];
  const warnings: string[] = [];
  if (nextEndDate < task.StartDate) {
    warnings.push("期限が着手日より前のため、保存時に着手日も同じ日へ調整されます。");
  }
  if (parentOrProjectEndDate && nextEndDate > parentOrProjectEndDate) {
    warnings.push("親またはプロジェクトの期限を超えています。親タスク期間は保存時に自動調整されます。");
  }
  const childEndDates = allTasks
    .filter((candidate) => candidate.ParentTaskId === task.Id)
    .map((candidate) => candidate.EndDate);
  childEndDates.sort();
  const latestChildEndDate = childEndDates[childEndDates.length - 1];
  if (latestChildEndDate && nextEndDate < latestChildEndDate) {
    warnings.push("この期限より後に完了する配下タスクがあります。配下タスクの日程も確認してください。");
  }
  return warnings;
}
