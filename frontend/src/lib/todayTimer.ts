export interface TodayRunningTimer {
  taskId: string;
  taskName: string;
  startedAtMs: number;
  baseElapsedMs: number;
  isOjt: boolean;
  teacherUserId: string | null;
  pendingEndedAtMs?: number;
}

export interface CompletionFlash {
  taskId: string;
  taskName: string;
}

export interface RunningTimerRef {
  current: TodayRunningTimer | null;
}

export function consumeRunningTimer(timerRef: RunningTimerRef): TodayRunningTimer | null {
  const active = timerRef.current;
  timerRef.current = null;
  return active;
}

export function timerCommitEndMs(timer: TodayRunningTimer, nowMs: number): number {
  return timer.pendingEndedAtMs ?? nowMs;
}

export function markTimerCommitPending(
  timer: TodayRunningTimer,
  endedAtMs: number
): TodayRunningTimer {
  return { ...timer, pendingEndedAtMs: timer.pendingEndedAtMs ?? endedAtMs };
}

export function canCompleteSelectedTask(
  selectedTaskId: string | null,
  selectedTaskStatus: string | null | undefined
): boolean {
  return Boolean(selectedTaskId) && selectedTaskStatus !== "Completion" && selectedTaskStatus !== "Abort";
}

export function resolveLatestRunningTimer(
  stateTimer: TodayRunningTimer | null,
  refTimer: TodayRunningTimer | null
): TodayRunningTimer | null {
  // state更新は非同期なので、refを優先しつつstateをフォールバックに使う。
  return refTimer ?? stateTimer;
}

export function resolveDoneTarget(
  stateTimer: TodayRunningTimer | null,
  refTimer: TodayRunningTimer | null,
  selectedTaskId: string | null,
  selectedTaskName: string | null
): CompletionFlash | null {
  const active = resolveLatestRunningTimer(stateTimer, refTimer);
  if (active) {
    return {
      taskId: active.taskId,
      taskName: active.taskName,
    };
  }
  if (!selectedTaskId) {
    return null;
  }
  return {
    taskId: selectedTaskId,
    taskName: selectedTaskName ?? "-",
  };
}
