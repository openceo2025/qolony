import { createContext, useContext, type ReactNode } from "react";
import {
  formatIquavisConflictValue,
  findUnsupportedIquavisExcelStatus,
  getIquavisConflictFieldLabel,
  IQUAVIS_CONFLICT_BASELINE_GUIDANCE,
  IQUAVIS_CONFLICT_VALUE_LABELS,
  IQUAVIS_PENDING_RECOVERY_GUIDANCE,
  isConflictHold,
  isPendingIquavisSave,
} from "../lib/iquavisConflict";
import type { IquavisConflictDirection, IquavisConflictRecord, Task } from "../types";

interface IquavisConflictContextValue {
  conflictsByTaskId: ReadonlyMap<string, IquavisConflictRecord>;
  resolvingTaskIds: ReadonlySet<string>;
  canResolveTask: (task: Task) => boolean;
  onResolve: (taskId: string, direction: IquavisConflictDirection) => void;
  onHold: (taskId: string, hold: boolean) => void;
}

const EMPTY_MAP = new Map<string, IquavisConflictRecord>();
const EMPTY_SET = new Set<string>();
const IquavisConflictContext = createContext<IquavisConflictContextValue>({
  conflictsByTaskId: EMPTY_MAP,
  resolvingTaskIds: EMPTY_SET,
  canResolveTask: () => false,
  onResolve: () => undefined,
  onHold: () => undefined,
});

export function IquavisConflictProvider({
  value,
  children,
}: {
  value: IquavisConflictContextValue;
  children: ReactNode;
}): JSX.Element {
  return <IquavisConflictContext.Provider value={value}>{children}</IquavisConflictContext.Provider>;
}

export function IquavisSyncChip({ task }: { task: Task }): JSX.Element | null {
  const { conflictsByTaskId } = useContext(IquavisConflictContext);
  const syncState = conflictsByTaskId.get(task.Id)?.sync_state ?? task.IQuavisSyncState;
  if (syncState === "PendingIquavisSave") {
    return <span className="gantt-pair-chip pending">Pending</span>;
  }
  if (syncState === "ConflictHold") {
    return (
      <span className="gantt-pair-chip hold" title={task.IQuavisConflictMessage || "このタスクは手動で同期保留されています"}>
        Hold
      </span>
    );
  }
  if (syncState === "Excluded") {
    return <span className="gantt-pair-chip excluded">Excluded</span>;
  }
  if (syncState === "Conflict") {
    return (
      <span className="gantt-pair-chip conflict" title={task.IQuavisConflictMessage || undefined}>
        Conflict
      </span>
    );
  }
  if (task.IQuavisTaskId) {
    return <span className="gantt-pair-chip paired">iQUAVIS</span>;
  }
  return null;
}

export function IquavisConflictPanel({ task }: { task: Task | null | undefined }): JSX.Element | null {
  const { conflictsByTaskId, resolvingTaskIds, canResolveTask, onResolve, onHold } = useContext(IquavisConflictContext);
  if (!task) return null;
  const conflict = conflictsByTaskId.get(task.Id);
  if (!conflict) return null;

  const pending = isPendingIquavisSave(conflict);
  const isHold = isConflictHold(conflict);
  const resolving = resolvingTaskIds.has(task.Id);
  const hasVersion = Boolean(conflict.recorded_at);
  const canOperate = canResolveTask(task);
  const canResolve = canOperate && !pending && !resolving;
  const canSubmit = canResolve && hasVersion;
  const unsupportedExcelStatus = findUnsupportedIquavisExcelStatus(conflict.fields);

  return (
    <section
      className={`iquavis-conflict-panel${pending ? " pending" : ""}${isHold ? " hold" : ""}`}
      aria-live="polite"
      onClick={(event) => event.stopPropagation()}
      onDoubleClick={(event) => event.stopPropagation()}
      onContextMenu={(event) => event.stopPropagation()}
    >
      <div className="iquavis-conflict-header">
        <div>
          <div className="iquavis-conflict-title">{pending ? "Pending" : isHold ? "Conflict Hold" : "iQUAVIS Conflict"}</div>
          <div className="iquavis-conflict-task-name">{conflict.task_name || task.Name}</div>
        </div>
        <span className={`iquavis-conflict-state${pending ? " pending" : isHold ? " hold" : ""}`}>
          {pending ? "Pending" : isHold ? "Hold" : "Conflict"}
        </span>
      </div>

      {conflict.message ? <div className="iquavis-conflict-message">{conflict.message}</div> : null}

      <div className="iquavis-conflict-baseline-note">{IQUAVIS_CONFLICT_BASELINE_GUIDANCE}</div>

      {conflict.fields.length > 0 ? (
        <div className="iquavis-conflict-grid" role="table" aria-label="iQUAVIS conflict detail">
          <div className="iquavis-conflict-grid-head" role="row">
            <span role="columnheader">項目</span>
            <span role="columnheader">{IQUAVIS_CONFLICT_VALUE_LABELS.baseline}</span>
            <span role="columnheader">{IQUAVIS_CONFLICT_VALUE_LABELS.qolony}</span>
            <span role="columnheader">{IQUAVIS_CONFLICT_VALUE_LABELS.excel}</span>
          </div>
          {conflict.fields.map((field) => (
            <div className="iquavis-conflict-grid-row" role="row" key={field.field}>
              <span className="iquavis-conflict-field" role="cell">
                {getIquavisConflictFieldLabel(field.field)}
              </span>
              <span role="cell" data-label={IQUAVIS_CONFLICT_VALUE_LABELS.baseline} title={formatIquavisConflictValue(field.baseline)}>
                {formatIquavisConflictValue(field.baseline)}
              </span>
              <span className="iquavis-conflict-qolony" role="cell" data-label={IQUAVIS_CONFLICT_VALUE_LABELS.qolony} title={formatIquavisConflictValue(field.qolony)}>
                {formatIquavisConflictValue(field.qolony)}
              </span>
              <span className="iquavis-conflict-excel" role="cell" data-label={IQUAVIS_CONFLICT_VALUE_LABELS.excel} title={formatIquavisConflictValue(field.excel)}>
                {formatIquavisConflictValue(field.excel)}
              </span>
            </div>
          ))}
        </div>
      ) : !pending ? (
        <div className="iquavis-conflict-message">差分対象の項目がありません。タスクを保存して再取得してください。</div>
      ) : null}

      {pending ? (
        <div className="iquavis-conflict-pending-note">{IQUAVIS_PENDING_RECOVERY_GUIDANCE}</div>
      ) : isHold ? (
        <div className="iquavis-conflict-actions">
          <button type="button" className="btn" disabled={resolving} onClick={() => onHold(task.Id, false)}>
            {resolving ? "処理中..." : "保留を解除する"}
          </button>
          <span className="iquavis-conflict-help">
            保留中は自動同期・衝突解決を実行しません。解除後に再判定されます。
          </span>
        </div>
      ) : (
        <div className="iquavis-conflict-actions">
          <button
            type="button"
            className="btn primary"
            disabled={!canSubmit || unsupportedExcelStatus !== undefined}
            onClick={() => onResolve(task.Id, "qolony_to_iquavis")}
          >
            {resolving ? "処理中..." : "QOLONYをExcelへ反映"}
          </button>
          <button
            type="button"
            className="btn"
            disabled={!canSubmit || unsupportedExcelStatus !== undefined}
            onClick={() => onResolve(task.Id, "iquavis_to_qolony")}
          >
            {resolving ? "処理中..." : "iQUAVISをQOLONYへ反映"}
          </button>
          <button
            type="button"
            className="btn"
            disabled={!canOperate || pending || resolving}
            onClick={() => onHold(task.Id, true)}
          >
            {resolving ? "処理中..." : "一時保留する"}
          </button>
          {!canOperate ? (
            <span className="iquavis-conflict-help">この画面では操作権限がありません。管理者権限が必要です。</span>
          ) : null}
          {canOperate && !hasVersion ? (
            <span className="iquavis-conflict-help">保存ベースラインが未取得です。最新情報を取得してください。</span>
          ) : null}
          {unsupportedExcelStatus ? (
            <span className="iquavis-conflict-help">
              Excel 側に未対応状態({String(unsupportedExcelStatus.excel)})があり、QOLONYへ採用する対象ではありません。
            </span>
          ) : null}
        </div>
      )}
    </section>
  );
}
