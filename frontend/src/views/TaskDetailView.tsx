import { useEffect, useMemo, useState } from "react";
import { IquavisConflictPanel } from "../components/IquavisConflictPanel";
import { formatDisplayText } from "../lib/displayText";
import {
  formatChildEstimate,
  formatEstimateBalance,
  getEstimateSignalClass,
  getEstimateSignalLabel,
} from "../lib/estimateSignal";
import { useI18n } from "../lib/i18n";
import { buildDueDateWarnings } from "../lib/taskScheduleWarnings";
import { formatTaskStatus, isAbortedTaskStatus, isTerminalTaskStatus } from "../lib/taskStatus";
import type { ChildDependencyMode, Task, User } from "../types";

interface TaskDetailViewProps {
  task: Task | null;
  allTasks: Task[];
  users: User[];
  maxEndDate: string | null;
  canEdit: boolean;
  isLeafTask: boolean;
  onSaveMemo: (taskId: string, memo: string) => void;
  onUpdateAssignedManHour: (taskId: string, assignedManHour: number) => void;
  onUpdateAssignee: (taskId: string, assigneeUserId: string) => void;
  onUpdateDueDate: (taskId: string, endDate: string) => void;
  onUpdateParent: (taskId: string, parentTaskId: string | null) => void;
  onUpdateChildDependencyMode: (taskId: string, childDependencyMode: ChildDependencyMode) => void;
  onAddOutput: (taskId: string, title: string, url: string) => void;
  onRemoveOutput: (taskId: string, index: number) => void;
  onComplete: (taskId: string) => void;
  onReopen: (taskId: string) => void;
  onOpenSplit: (taskId: string) => void;
}

export function TaskDetailView({
  task,
  allTasks,
  users,
  maxEndDate,
  canEdit,
  isLeafTask,
  onSaveMemo,
  onUpdateAssignedManHour,
  onUpdateAssignee,
  onUpdateDueDate,
  onUpdateParent,
  onUpdateChildDependencyMode,
  onAddOutput,
  onRemoveOutput,
  onComplete,
  onReopen,
  onOpenSplit,
}: TaskDetailViewProps): JSX.Element {
  const { t } = useI18n();
  const [memo, setMemo] = useState("");
  const [assignedManHourText, setAssignedManHourText] = useState("0");
  const [assigneeUserId, setAssigneeUserId] = useState("");
  const [parentTaskId, setParentTaskId] = useState("");
  const [endDate, setEndDate] = useState("");
  const [childDependencyMode, setChildDependencyMode] = useState<ChildDependencyMode>("Serial");
  const [outTitle, setOutTitle] = useState("");
  const [outUrl, setOutUrl] = useState("");

  useEffect(() => setMemo(task?.Memo ?? ""), [task?.Id, task?.Memo]);
  useEffect(
    () => setAssignedManHourText(String(task?.AssignedManHour ?? 0)),
    [task?.AssignedManHour, task?.Id]
  );
  useEffect(() => setAssigneeUserId(task?.AssigneeUserId ?? ""), [task?.AssigneeUserId, task?.Id]);
  useEffect(() => setParentTaskId(task?.ParentTaskId ?? ""), [task?.Id, task?.ParentTaskId]);
  useEffect(() => setEndDate(task?.EndDate ?? ""), [task?.EndDate, task?.Id]);
  useEffect(
    () => setChildDependencyMode(task?.ChildDependencyMode === "Parallel" ? "Parallel" : "Serial"),
    [task?.ChildDependencyMode, task?.Id]
  );
  useEffect(() => {
    setOutTitle("");
    setOutUrl("");
  }, [task?.Id]);

  const parentCandidates = useMemo(() => {
    if (!task) return [];
    const childrenByParent = new Map<string, string[]>();
    for (const item of allTasks) {
      if (!item.ParentTaskId) continue;
      childrenByParent.set(item.ParentTaskId, [...(childrenByParent.get(item.ParentTaskId) ?? []), item.Id]);
    }
    const descendants = new Set<string>();
    const stack = [...(childrenByParent.get(task.Id) ?? [])];
    while (stack.length > 0) {
      const current = stack.pop();
      if (!current || descendants.has(current)) continue;
      descendants.add(current);
      stack.push(...(childrenByParent.get(current) ?? []));
    }
    return allTasks.filter((item) => item.Id !== task.Id && !descendants.has(item.Id) && !isTerminalTaskStatus(item.Status));
  }, [allTasks, task]);

  if (!task) {
    return <div className="empty">{t("detail.empty", "No task selected")}</div>;
  }

  const isAborted = isAbortedTaskStatus(task.Status);
  const isReadOnly = isTerminalTaskStatus(task.Status);
  const assignedManHourValue = Number(assignedManHourText);
  const canSaveAssignedManHour = Number.isInteger(assignedManHourValue) && assignedManHourValue >= 0;
  const estimateSignalLabel = getEstimateSignalLabel(task);
  const canChangeAssigneeByTaskRule = !isLeafTask || task.ActualManHour === 0;
  const canEditAssignee = !isReadOnly && canEdit && canChangeAssigneeByTaskRule;
  const canEditDueDate = !isReadOnly && canEdit;
  const canEditParent = !isReadOnly && canEdit;
  const canSubmitAssignee = canEditAssignee && !!assigneeUserId;
  const canSubmitDueDate = canEditDueDate && !!endDate;
  const canSubmitParent = canEditParent && parentTaskId !== (task.ParentTaskId ?? "");
  const canEditChildDependencyMode = canEdit && !isAborted;
  const canSubmitChildDependencyMode = canEditChildDependencyMode;
  const dueDateWarnings = buildDueDateWarnings(task, allTasks, endDate, maxEndDate);

  return (
    <div className="view-stack">
      <IquavisConflictPanel task={task} />
      <div className="panel">
        <div className="panel-title">{formatDisplayText(task.Name)}</div>
        <div className="subtext">
          {t("common.assignee", "Assignee")}:{task.AssigneeName} / {t("common.due_date", "Due")}:{task.EndDate} /{" "}
          {t("common.status", "Status")}:{formatTaskStatus(task.Status)} / {t("common.progress", "Progress")}:{task.ProgressRate.toFixed(1)}%
        </div>
        <div className="subtext">
          {t("common.estimate", "Estimate")}:{task.AssignedManHour}
          {t("common.minutes", "min")} / {t("common.actual", "Actual")}:{task.ActualManHour}
          {t("common.minutes", "min")}
        </div>
        <div className="task-detail-estimate-row">
          <span className="subtext">{formatChildEstimate(task)}</span>
          <span className="subtext">{formatEstimateBalance(task)}</span>
          {estimateSignalLabel ? (
            <span
              className={`estimate-signal-chip ${getEstimateSignalClass(task)}`}
              title={task.EstimateSignalMessage ?? undefined}
            >
              {estimateSignalLabel}
            </span>
          ) : null}
        </div>
        {task.EstimateSignalMessage ? <div className="subtext estimate-message">{task.EstimateSignalMessage}</div> : null}
      </div>

      <div className="panel">
        <div className="panel-title">{t("detail.title_parent_change", "Change Parent")}</div>
        <div className="toolbar-row">
          <select value={parentTaskId} onChange={(e) => setParentTaskId(e.currentTarget.value)} disabled={!canEditParent}>
            <option value="">{t("detail.parent_root", "Root")}</option>
            {parentCandidates.map((candidate) => (
              <option key={candidate.Id} value={candidate.Id}>
                {formatDisplayText(candidate.Name)}
              </option>
            ))}
          </select>
          <button
            className="btn primary"
            type="button"
            onClick={() => onUpdateParent(task.Id, parentTaskId || null)}
            disabled={!canSubmitParent}
          >
            {t("detail.button_change_parent", "Change Parent")}
          </button>
        </div>
        <div className="subtext">
          {t("detail.parent_change_note", "Changing parent changes QOLONY rollups only; iQUAVIS link IDs are kept.")}
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">{t("detail.title_assignee_change", "Change Assignee")}</div>
        <div className="toolbar-row">
          <select value={assigneeUserId} onChange={(e) => setAssigneeUserId(e.currentTarget.value)} disabled={!canEditAssignee}>
            {users.map((user) => (
              <option key={user.Id} value={user.Id}>
                {user.Name}
              </option>
            ))}
          </select>
          <button
            className="btn primary"
            type="button"
            onClick={() => onUpdateAssignee(task.Id, assigneeUserId)}
            disabled={!canSubmitAssignee}
          >
            {t("detail.button_change_assignee", "Change Assignee")}
          </button>
        </div>
        {!canEditAssignee ? (
          <div className="subtext">{t("detail.leaf_assignee_rule", "Leaf task assignee can be changed only when actual MH is 0")}</div>
        ) : null}
      </div>

      <div className="panel">
        <div className="panel-title">{t("detail.title_due_date_change", "Change Due Date")}</div>
        <div className="toolbar-row">
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.currentTarget.value)}
            disabled={!canEditDueDate}
          />
          <button
            className="btn primary"
            type="button"
            onClick={() => onUpdateDueDate(task.Id, endDate)}
            disabled={!canSubmitDueDate}
          >
            {t("detail.button_change_due_date", "Change Due Date")}
          </button>
        </div>
        <div className="subtext">
          {t("detail.due_date_free", "The due date can be changed freely. Related parent dates are adjusted automatically; remaining inconsistencies are shown as warnings.")}
        </div>
        {dueDateWarnings.map((warning) => (
          <div className="subtext warning-text" key={warning}>{warning}</div>
        ))}
        {!canEditDueDate ? (
          <div className="subtext">{t("detail.due_date_rule", "Due date can be changed only by the assignee or Admin")}</div>
        ) : null}
      </div>

      <div className="panel">
        <div className="panel-title">{t("detail.title_child_dependency_mode", "Child Dependency Mode")}</div>
        <div className="toolbar-row">
          <select
            value={childDependencyMode}
            onChange={(e) => setChildDependencyMode(e.currentTarget.value as ChildDependencyMode)}
            disabled={!canEditChildDependencyMode}
          >
            <option value="Serial">{t("common.serial", "Serial")}</option>
            <option value="Parallel">{t("common.parallel", "Parallel")}</option>
          </select>
          <button
            className="btn primary"
            type="button"
            onClick={() => onUpdateChildDependencyMode(task.Id, childDependencyMode)}
            disabled={!canSubmitChildDependencyMode}
          >
            {t("detail.button_change_dependency_mode", "Update Dependency Mode")}
          </button>
        </div>
        {!canEditChildDependencyMode ? (
          <div className="subtext">{t("detail.dependency_mode_rule", "Dependency mode can be changed only by the assignee or Admin")}</div>
        ) : null}
      </div>

      <div className="panel">
        <div className="panel-title">{t("detail.title_assigned_mh", "Assigned MH")}</div>
        <div className="toolbar-row">
          <input
            type="number"
            min={0}
            step={1}
            value={assignedManHourText}
            onChange={(e) => setAssignedManHourText(e.currentTarget.value)}
            disabled={isReadOnly || !canEdit}
          />
          <span className="subtext">{t("common.minutes", "min")}</span>
          <button
            className="btn primary"
            type="button"
            onClick={() => onUpdateAssignedManHour(task.Id, assignedManHourValue)}
            disabled={isReadOnly || !canEdit || !canSaveAssignedManHour}
          >
            {t("detail.button_save_assigned_mh", "Save Assigned MH")}
          </button>
        </div>
        <div className="subtext">{t("detail.assigned_balance_rule", "Child estimates are not cascaded. Any difference remains on this task as estimate balance or debt.")}</div>
      </div>

      <div className="panel">
        <div className="panel-title">{t("detail.title_deliverables", "Deliverables")}</div>
        {(task.OutputDeliverables || []).length === 0 ? (
          <div className="empty">{t("detail.no_outputs", "No deliverables")}</div>
        ) : null}
        {(task.OutputDeliverables || []).map((item, index) => (
          <div className="list-row" key={`${item.title}-${index}`}>
            <span className="grow">
              {item.title} ({item.url})
            </span>
            <button className="btn" type="button" onClick={() => onRemoveOutput(task.Id, index)} disabled={isReadOnly || !canEdit}>
              {t("common.delete", "Delete")}
            </button>
          </div>
        ))}
        <div className="toolbar-row">
          <input
            value={outTitle}
            onChange={(e) => setOutTitle(e.currentTarget.value)}
            placeholder={t("detail.output_title_placeholder", "Title")}
            disabled={isReadOnly || !canEdit}
          />
          <input
            className="grow"
            value={outUrl}
            onChange={(e) => setOutUrl(e.currentTarget.value)}
            placeholder={t("detail.output_url_placeholder", "URL")}
            disabled={isReadOnly || !canEdit}
          />
          <button
            className="btn primary"
            type="button"
            onClick={() => onAddOutput(task.Id, outTitle, outUrl)}
            disabled={isReadOnly || !canEdit}
          >
            {t("detail.button_add_output", "Add")}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">{t("detail.title_memo", "Memo")}</div>
        <textarea value={memo} onChange={(e) => setMemo(e.currentTarget.value)} rows={6} disabled={isReadOnly || !canEdit} />
        <div className="toolbar-row">
          <button className="btn primary" type="button" onClick={() => onSaveMemo(task.Id, memo)} disabled={isReadOnly || !canEdit}>
            {t("detail.button_save_memo", "Save Memo")}
          </button>
          <button className="btn" type="button" onClick={() => onOpenSplit(task.Id)} disabled={isReadOnly || !canEdit}>
            {t("detail.button_open_split", "Open Split View")}
          </button>
        </div>
        {isReadOnly ? <div className="subtext">{t("detail.terminal_rule", "完了・中止タスクは編集できません")}</div> : null}
        {!isReadOnly && !canEdit ? <div className="subtext">{t("detail.edit_rule", "Only the assignee or Admin can edit this task")}</div> : null}
      </div>

      <div className="panel">
        <div className="toolbar-row">
          <button className="btn primary" type="button" onClick={() => onComplete(task.Id)} disabled={isReadOnly || !canEdit}>
            {t("detail.button_complete", "Complete")}
          </button>
          <button className="btn" type="button" onClick={() => onReopen(task.Id)} disabled={!canEdit || isAborted}>
            {t("detail.button_reopen", "Reopen")}
          </button>
        </div>
      </div>
    </div>
  );
}
