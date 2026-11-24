import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TaskCard } from "../components/TaskCard";
import { IquavisConflictPanel } from "../components/IquavisConflictPanel";
import { formatDisplayText } from "../lib/displayText";
import { useI18n } from "../lib/i18n";
import { isTodayOn } from "../lib/taskTree";
import { isAbortedTaskStatus, isTerminalTaskStatus } from "../lib/taskStatus";
import {
  canCompleteSelectedTask,
  consumeRunningTimer,
  markTimerCommitPending,
  resolveDoneTarget,
  resolveLatestRunningTimer,
  timerCommitEndMs,
  type CompletionFlash,
  type TodayRunningTimer,
} from "../lib/todayTimer";
import type { Task, User } from "../types";

interface TimerCommitPayload {
  taskId: string;
  startedAtMs: number;
  endedAtMs: number;
  isOjt: boolean;
  teacherUserId: string | null;
}

interface TodayViewProps {
  selectedTaskId: string | null;
  selectedTask: Task | null;
  todayTasks: Task[];
  users: User[];
  currentUserId: string;
  onSelectTask: (taskId: string) => void;
  onOpenDetail: (taskId: string) => void;
  onOpenSplit: (taskId: string) => void;
  onToggleToday: (taskId: string, enabled: boolean) => void;
  onMoveTask: (taskId: string, direction: -1 | 1) => void;
  onCommitTimerInterval: (
    taskId: string,
    startedAtMs: number,
    endedAtMs: number,
    isOjt: boolean,
    teacherUserId: string | null
  ) => Promise<boolean>;
  onCommitTimerIntervalBeacon: (
    taskId: string,
    startedAtMs: number,
    endedAtMs: number,
    isOjt: boolean,
    teacherUserId: string | null
  ) => void;
  onCompleteTask: (taskId: string) => Promise<boolean>;
}

type RunningTimer = TodayRunningTimer;

function formatElapsedHms(totalSeconds: number): string {
  const normalized = Math.max(0, totalSeconds);
  const hours = Math.floor(normalized / 3600);
  const minutes = Math.floor((normalized % 3600) / 60);
  const seconds = normalized % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function formatMilliseconds(totalMs: number): string {
  return String(Math.max(0, totalMs % 1000)).padStart(3, "0");
}

function elapsedMsForTimer(timer: RunningTimer, nowMs: number): number {
  const effectiveNowMs = timerCommitEndMs(timer, nowMs);
  return Math.max(timer.baseElapsedMs, timer.baseElapsedMs + (effectiveNowMs - timer.startedAtMs));
}

function buildCommitPayload(timer: RunningTimer, endedAtMs: number): TimerCommitPayload | null {
  if (endedAtMs <= timer.startedAtMs) return null;
  return {
    taskId: timer.taskId,
    startedAtMs: timer.startedAtMs,
    endedAtMs,
    isOjt: timer.isOjt,
    teacherUserId: timer.teacherUserId,
  };
}

export function TodayView({
  selectedTaskId,
  selectedTask,
  todayTasks,
  users,
  currentUserId,
  onSelectTask,
  onOpenDetail,
  onOpenSplit,
  onToggleToday,
  onMoveTask,
  onCommitTimerInterval,
  onCommitTimerIntervalBeacon,
  onCompleteTask,
}: TodayViewProps): JSX.Element {
  const { t } = useI18n();
  const [elapsedMs, setElapsedMs] = useState(0);
  const [elapsedByTask, setElapsedByTask] = useState<Record<string, number>>({});
  const [doneTaskIds, setDoneTaskIds] = useState<Record<string, true>>({});
  const [completionFlash, setCompletionFlash] = useState<CompletionFlash | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [runningTimer, setRunningTimer] = useState<RunningTimer | null>(null);
  const [teacherUserId, setTeacherUserId] = useState("");
  const runningTimerRef = useRef<RunningTimer | null>(null);
  const timerBeaconCallbackRef = useRef(onCommitTimerIntervalBeacon);
  timerBeaconCallbackRef.current = onCommitTimerIntervalBeacon;
  const completeTimeoutRef = useRef<number | null>(null);
  const teacherOptions = useMemo(
    () => users.filter((user) => user.Id !== currentUserId),
    [currentUserId, users]
  );

  const syncRunningTimer = useCallback((next: RunningTimer | null) => {
    // Done直後のクリック競合でも整合がずれないよう、refとstateを同時更新する。
    runningTimerRef.current = next;
    setRunningTimer(next);
  }, []);

  const clearCompletionFlash = useCallback(() => {
    setCompletionFlash(null);
    if (completeTimeoutRef.current !== null) {
      window.clearTimeout(completeTimeoutRef.current);
      completeTimeoutRef.current = null;
    }
  }, []);

  const commitRunningTimer = useCallback(async (): Promise<boolean> => {
    const active = consumeRunningTimer(runningTimerRef);
    if (!active) return true;

    const endedAtMs = timerCommitEndMs(active, Date.now());
    const frozenElapsedMs = elapsedMsForTimer(active, endedAtMs);
    const payload = buildCommitPayload(active, endedAtMs);

    syncRunningTimer(null);
    setElapsedByTask((current) => ({
      ...current,
      [active.taskId]: Math.max(current[active.taskId] ?? 0, frozenElapsedMs),
    }));
    if (selectedTaskId === active.taskId) {
      setElapsedMs(frozenElapsedMs);
    }

    if (!payload) {
      return true;
    }
    const committed = await onCommitTimerInterval(
      payload.taskId,
      payload.startedAtMs,
      payload.endedAtMs,
      payload.isOjt,
      payload.teacherUserId
    );
    if (!committed) {
      // 応答だけ失われた場合も同じ区間を再送できるよう、終了時刻を固定して保留する。
      syncRunningTimer(markTimerCommitPending(active, endedAtMs));
    }
    return committed;
  }, [onCommitTimerInterval, selectedTaskId, syncRunningTimer]);

  const commitRunningTimerWithBeacon = useCallback(() => {
    const active = consumeRunningTimer(runningTimerRef);
    if (!active) return;

    setRunningTimer(null);
    const endedAtMs = timerCommitEndMs(active, Date.now());
    const payload = buildCommitPayload(active, endedAtMs);
    if (!payload) return;
    timerBeaconCallbackRef.current(
      payload.taskId,
      payload.startedAtMs,
      payload.endedAtMs,
      payload.isOjt,
      payload.teacherUserId
    );
  }, []);

  useEffect(() => {
    if (!teacherUserId) return;
    if (teacherOptions.some((user) => user.Id === teacherUserId)) return;
    setTeacherUserId("");
  }, [teacherOptions, teacherUserId]);

  useEffect(() => {
    if (!runningTimer || runningTimer.pendingEndedAtMs) return;

    const tick = () => {
      setElapsedMs(elapsedMsForTimer(runningTimer, Date.now()));
    };
    tick();
    const intervalId = window.setInterval(tick, 50);
    return () => window.clearInterval(intervalId);
  }, [runningTimer]);

  useEffect(() => {
    if (runningTimer) return;
    if (!selectedTaskId) {
      setElapsedMs(0);
      return;
    }
    setElapsedMs(elapsedByTask[selectedTaskId] ?? 0);
  }, [runningTimer, selectedTaskId, elapsedByTask]);

  useEffect(() => {
    // タイマー起動中に画面離脱した場合のみ、Pause相当の実績を確定送信する。
    const flushOnExit = () => {
      commitRunningTimerWithBeacon();
    };
    window.addEventListener("pagehide", flushOnExit);
    window.addEventListener("beforeunload", flushOnExit);
    return () => {
      window.removeEventListener("pagehide", flushOnExit);
      window.removeEventListener("beforeunload", flushOnExit);
      if (completeTimeoutRef.current !== null) {
        window.clearTimeout(completeTimeoutRef.current);
      }
      commitRunningTimerWithBeacon();
    };
  }, [commitRunningTimerWithBeacon]);

  const canDoneWithoutActive = !runningTimer && canCompleteSelectedTask(selectedTaskId, selectedTask?.Status);
  const persistentComplete =
    !!selectedTaskId &&
    (doneTaskIds[selectedTaskId] === true || selectedTask?.Status === "Completion");
  const isCompleteVisual = completionFlash !== null || persistentComplete;
  const isAbortedVisual = !runningTimer && isAbortedTaskStatus(selectedTask?.Status);
  const timerStateClass = isAbortedVisual
    ? "aborted"
    : isCompleteVisual
    ? "complete"
    : runningTimer?.pendingEndedAtMs
      ? "paused"
      : runningTimer
        ? "running"
        : elapsedMs > 0
          ? "paused"
          : "idle";
  const timerTaskName = formatDisplayText(runningTimer?.taskName ?? completionFlash?.taskName ?? selectedTask?.Name ?? "-");
  const elapsedSeconds = Math.floor(elapsedMs / 1000);

  return (
    <div className="view-stack">
      <IquavisConflictPanel task={selectedTask} />
      <div className="panel">
        <div className="panel-title">{t("today.timer_title", "Timer")}</div>
        <div className="kv-row">
          <span className="k">{t("today.active_task", "Active Task")}</span>
          <span className="v">{timerTaskName}</span>
        </div>
        <div className={`timer-display ${timerStateClass}`}>
          {isAbortedVisual ? (
            t("today.aborted", "中止")
          ) : isCompleteVisual ? (
            t("today.complete", "Complete")
          ) : (
            <>
              <span>{formatElapsedHms(elapsedSeconds)}</span>
              <span className="timer-ms">.{formatMilliseconds(elapsedMs)}</span>
            </>
          )}
        </div>
        <div className={`timer-status ${timerStateClass}`}>
          {isAbortedVisual
            ? t("today.aborted_status", "iQUAVISで中止されたタスクです")
            : isCompleteVisual
            ? t("today.complete", "Complete")
            : runningTimer?.pendingEndedAtMs
              ? t("today.paused", "Paused")
              : runningTimer
              ? t("today.running", "Running")
              : elapsedSeconds > 0
                ? t("today.paused", "Paused")
                : t("today.ready", "Ready")}
        </div>
        <div className="today-ojt-row">
          <label>
            <span>{t("today.ojt_teacher", "OJT Teacher")}</span>
            <select
              value={teacherUserId}
              onChange={(event) => setTeacherUserId(event.currentTarget.value)}
              disabled={isSubmitting || Boolean(runningTimer) || isTerminalTaskStatus(selectedTask?.Status)}
            >
              <option value="">{t("today.ojt_teacher_none", "None")}</option>
              {teacherOptions.map((user) => (
                <option key={user.Id} value={user.Id}>
                  {user.Name}
                </option>
              ))}
            </select>
          </label>
          <span className="subtext">
            {teacherUserId ? t("today.ojt_timer_only", "OJT is recorded only by timer.") : ""}
          </span>
        </div>

        <div className="toolbar-row">
          <button
            className="btn primary"
            type="button"
            onClick={async () => {
              if (isSubmitting) return;
              if (!selectedTaskId) return;
              if (isTerminalTaskStatus(selectedTask?.Status)) return;

              clearCompletionFlash();
              setDoneTaskIds((current) => {
                const next = { ...current };
                delete next[selectedTaskId];
                return next;
              });

              setIsSubmitting(true);
              try {
                const active = resolveLatestRunningTimer(runningTimer, runningTimerRef.current);
                if (active) {
                  if (active.taskId === selectedTaskId && !active.pendingEndedAtMs) return;
                  const switched = await commitRunningTimer();
                  if (!switched) return;
                }

                const baseElapsedMs = elapsedByTask[selectedTaskId] ?? 0;
                syncRunningTimer({
                  taskId: selectedTaskId,
                  taskName: selectedTask?.Name ?? "-",
                  startedAtMs: Date.now(),
                  baseElapsedMs,
                  isOjt: Boolean(teacherUserId),
                  teacherUserId: teacherUserId || null,
                });
                setElapsedMs(baseElapsedMs);
              } finally {
                setIsSubmitting(false);
              }
            }}
            disabled={isSubmitting || !selectedTaskId || isTerminalTaskStatus(selectedTask?.Status)}
          >
            {t("today.start", "Start")}
          </button>
          <button
            className="btn warn"
            type="button"
            onClick={async () => {
              if (isSubmitting) return;
              clearCompletionFlash();
              setIsSubmitting(true);
              try {
                await commitRunningTimer();
              } finally {
                setIsSubmitting(false);
              }
            }}
            disabled={isSubmitting || !runningTimer}
          >
            {t("today.pause", "Pause")}
          </button>
          <button
            className="btn danger"
            type="button"
            onClick={async () => {
              if (isSubmitting) return;
              const doneTarget = resolveDoneTarget(
                runningTimer,
                runningTimerRef.current,
                selectedTaskId,
                selectedTask?.Name ?? null
              );
              if (!doneTarget) return;

              setCompletionFlash(doneTarget);
              setDoneTaskIds((current) => ({ ...current, [doneTarget.taskId]: true }));
              if (completeTimeoutRef.current !== null) {
                window.clearTimeout(completeTimeoutRef.current);
              }
              completeTimeoutRef.current = window.setTimeout(() => {
                setCompletionFlash(null);
                completeTimeoutRef.current = null;
              }, 1600);

              const rollbackOptimisticCompletion = () => {
                setDoneTaskIds((current) => {
                  const next = { ...current };
                  delete next[doneTarget.taskId];
                  return next;
                });
                setCompletionFlash((current) => current?.taskId === doneTarget.taskId ? null : current);
              };

              // 完了要求の往復はボタンを塞がない。次のタスクをすぐ完了できるようにする。
              const requestCompletion = () => {
                void onCompleteTask(doneTarget.taskId).then((completed) => {
                  if (!completed) rollbackOptimisticCompletion();
                });
              };

              const active = resolveLatestRunningTimer(runningTimer, runningTimerRef.current);
              if (!active) {
                requestCompletion();
                return;
              }

              // 計測中のタスクだけは、実績を確定してから完了を送る必要がある。
              setIsSubmitting(true);
              try {
                const committed = await commitRunningTimer();
                if (!committed) {
                  rollbackOptimisticCompletion();
                  return;
                }
              } finally {
                setIsSubmitting(false);
              }
              requestCompletion();
            }}
            disabled={isSubmitting || (!runningTimer && !canDoneWithoutActive)}
          >
            {t("today.done", "Done")}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">{t("today.tasks_title", "Today Tasks")}</div>
        {todayTasks.length === 0 ? <div className="empty">{t("today.no_tasks", "No today tasks")}</div> : null}
        {todayTasks.map((task) => (
          <TaskCard
            key={task.Id}
            task={task}
            active={selectedTaskId === task.Id}
            showTodayToggle
            todayEnabled={isTodayOn(task)}
            ownTask
            showReorder
            onMoveUp={() => onMoveTask(task.Id, -1)}
            onMoveDown={() => onMoveTask(task.Id, 1)}
            onClick={() => onSelectTask(task.Id)}
            onDoubleClick={() => onOpenDetail(task.Id)}
            onContextMenu={(event) => {
              event.preventDefault();
              if (!isTerminalTaskStatus(task.Status)) onOpenSplit(task.Id);
            }}
            onToggleToday={(enabled) => onToggleToday(task.Id, enabled)}
          />
        ))}
      </div>
    </div>
  );
}
