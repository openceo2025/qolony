import type { Task } from "../types";

export function extractMutationWarnings(result: unknown): string[] {
  if (!result || typeof result !== "object" || !("warnings" in result)) return [];
  const warnings = (result as { warnings?: unknown }).warnings;
  return Array.isArray(warnings) ? warnings.filter((item): item is string => typeof item === "string" && item.length > 0) : [];
}

export function rollbackOptimisticTaskPatch(
  current: Task,
  original: Task,
  optimisticPatch: Partial<Task>
): Task {
  const rollbackPatch: Partial<Task> = {};
  for (const [field, optimisticValue] of Object.entries(optimisticPatch) as Array<[keyof Task, unknown]>) {
    if (Object.is(current[field], optimisticValue)) {
      Object.assign(rollbackPatch, { [field]: original[field] });
    }
  }
  return { ...current, ...rollbackPatch };
}

export function rollbackOptimisticTaskInsertion(current: Task[], taskId: string): Task[] {
  return current.filter((task) => task.Id !== taskId);
}

export function enqueueTaskMutation(
  queue: Map<string, Promise<boolean>>,
  taskId: string,
  action: () => Promise<boolean>,
  onSkipped: () => void,
  onDrained: () => void
): Promise<boolean> {
  const previous = queue.get(taskId) ?? Promise.resolve(true);
  const operation = previous.then(
    (previousSucceeded) => {
      if (previousSucceeded) return action();
      onSkipped();
      return false;
    },
    () => {
      onSkipped();
      return false;
    }
  );
  queue.set(taskId, operation);
  const cleanup = () => {
    if (queue.get(taskId) !== operation) return;
    queue.delete(taskId);
    onDrained();
  };
  void operation.then(cleanup, cleanup);
  return operation;
}

export function formatMutationFailure(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return `変更をサーバーへ保存できませんでした。画面を最新状態へ戻します。同じタスクに続けて行った未保存変更もキャンセルします。\n\n${message}`;
}
