import React from "react";
import { IquavisSyncChip } from "./IquavisConflictPanel";
import { formatDisplayText } from "../lib/displayText";
import { formatEstimateBalance, getEstimateSignalClass, getEstimateSignalLabel } from "../lib/estimateSignal";
import { useI18n } from "../lib/i18n";
import { formatTaskStatus, getTaskMarkerColor, isAbortedTaskStatus, isTerminalTaskStatus } from "../lib/taskStatus";
import type { Task } from "../types";

interface TaskCardProps {
  task: Task;
  active: boolean;
  showTodayToggle?: boolean;
  todayEnabled?: boolean;
  showReorder?: boolean;
  ownTask?: boolean;
  mutedTask?: boolean;
  onClick: () => void;
  onDoubleClick?: () => void;
  onContextMenu?: (event: React.MouseEvent) => void;
  onToggleToday?: (enabled: boolean) => void;
  onMoveUp?: () => void;
  onMoveDown?: () => void;
}

export function TaskCard({
  task,
  active,
  showTodayToggle = false,
  todayEnabled = false,
  showReorder = false,
  ownTask = false,
  mutedTask = false,
  onClick,
  onDoubleClick,
  onContextMenu,
  onToggleToday,
  onMoveUp,
  onMoveDown,
}: TaskCardProps): JSX.Element {
  const { t } = useI18n();
  const markerColor = getTaskMarkerColor(task.Status, task.Signal, task.EndDate, new Date().toISOString().slice(0, 10));
  const estimateSignalLabel = getEstimateSignalLabel(task);

  return (
    <div
      className={`task-card${active ? " active" : ""}${ownTask ? " own" : ""}${mutedTask ? " muted" : ""}`}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      onContextMenu={onContextMenu}
      role="button"
      tabIndex={0}
    >
      <div className="task-card-marker" style={{ backgroundColor: markerColor }} />
      <div className="task-card-main">
        <div className="task-card-name-row">
          <div className="task-card-name">{formatDisplayText(task.Name)}</div>
          <IquavisSyncChip task={task} />
          {estimateSignalLabel ? (
            <span
              className={`estimate-signal-chip ${getEstimateSignalClass(task)}`}
              title={task.EstimateSignalMessage ?? undefined}
            >
              {estimateSignalLabel}
            </span>
          ) : null}
        </div>
        <div className="task-card-meta">
          {t("task_card.meta", "{assignee} | Due:{dueDate} | Estimate:{assigned} min Actual:{actual} min | {progress}%", {
            assignee: task.AssigneeName,
            dueDate: task.EndDate,
            assigned: task.AssignedManHour,
            actual: task.ActualManHour,
            progress: task.ProgressRate.toFixed(1),
          })}
          {isAbortedTaskStatus(task.Status) ? <> | {formatTaskStatus(task.Status)}</> : null}
          {" | "}
          {formatEstimateBalance(task)}
        </div>
      </div>

      {showTodayToggle && !isTerminalTaskStatus(task.Status) ? (
        <label className="task-card-toggle" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={todayEnabled}
            onChange={(e) => {
              onToggleToday?.(e.currentTarget.checked);
            }}
          />
          <span>{t("task_card.today_label", "today")}</span>
        </label>
      ) : null}

      {showReorder ? (
        <div className="task-card-order" onClick={(e) => e.stopPropagation()}>
          <button type="button" onClick={onMoveUp}>
            ↑
          </button>
          <button type="button" onClick={onMoveDown}>
            ↓
          </button>
        </div>
      ) : null}
    </div>
  );
}
