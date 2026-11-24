import type {
  IquavisConflictField,
  IquavisConflictListResponse,
  IquavisConflictRecord,
  Task,
} from "../types";

const FIELD_LABELS: Record<string, string> = {
  name: "タスク名",
  start_date: "着手日",
  end_date: "期限",
  actual_minutes: "実績工数",
  status: "状態",
  progress_rate: "進捗率",
  memo: "メモ",
};

export const IQUAVIS_CONFLICT_VALUE_LABELS = {
  baseline: "前回iQUAVIS取得/保存成功値",
  qolony: "QOLONY現在値",
  excel: "Excel表示値",
} as const;

export const IQUAVIS_CONFLICT_BASELINE_GUIDANCE =
  "「前回iQUAVIS取得/保存成功値」は、Excelが最後にiQUAVISから全件取得、またはサーバー保存を正常終了した時点の値です。現在のiQUAVISを直接照会した値ではありません。";

export const IQUAVIS_PENDING_RECOVERY_GUIDANCE =
  "PendingでExcel表示値が0のままなら、その0を「サーバーへ保存」しないでください。Excelで「iQUAVISから再取得」→「全タスク作成」→保存終了を確認してExcelを閉じる→QOLONYのAdmin「iQUAVIS→QOLONY 初期取込」の順に進めてください。";

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object";
}

function normalizeField(value: unknown): IquavisConflictField | null {
  if (!isObject(value) || typeof value.field !== "string" || !value.field) return null;
  return {
    field: value.field,
    baseline: value.baseline,
    qolony: value.qolony,
    excel: value.excel,
  };
}

function normalizeRecord(value: unknown): IquavisConflictRecord | null {
  if (!isObject(value)) return null;
  const taskId = value.task_id;
  if (typeof taskId !== "string" || !taskId) return null;
  const fields = Array.isArray(value.fields) ? value.fields.map(normalizeField).filter((field) => field !== null) : [];
  return {
    task_id: taskId,
    iquavis_id: typeof value.iquavis_id === "string" ? value.iquavis_id : null,
    task_name: typeof value.task_name === "string" ? value.task_name : taskId,
    sync_state: typeof value.sync_state === "string" ? value.sync_state : "Conflict",
    message: typeof value.message === "string" ? value.message : "",
    recorded_at: typeof value.recorded_at === "string" ? value.recorded_at : null,
    workbook_fingerprint: typeof value.workbook_fingerprint === "string" ? value.workbook_fingerprint : null,
    fields,
  };
}

export function normalizeIquavisConflictResponse(value: unknown): IquavisConflictListResponse {
  const rawItems = Array.isArray(value)
    ? value
    : isObject(value) && Array.isArray(value.items)
      ? value.items
      : isObject(value) && Array.isArray(value.conflicts)
        ? value.conflicts
        : [];
  const items = rawItems.map(normalizeRecord).filter((record) => record !== null);
  const count = isObject(value) && typeof value.count === "number" ? value.count : items.length;
  return { items, count };
}

export function isConflictHold(record: IquavisConflictRecord | undefined): boolean {
  return record?.sync_state === "ConflictHold";
}

export function buildIquavisConflictMap(
  tasks: Task[],
  response: IquavisConflictListResponse | unknown,
  overrides?: ReadonlyMap<string, IquavisConflictRecord | null>
): Map<string, IquavisConflictRecord> {
  const normalized = normalizeIquavisConflictResponse(response);
  const map = new Map(normalized.items.map((record) => [record.task_id, record]));

  // 専用APIの再取得前でも、Task DTOの状態から表示だけは即時に復元する。
  for (const task of tasks) {
    if (map.has(task.Id)) continue;
    if (
      task.IQuavisSyncState !== "Conflict" &&
      task.IQuavisSyncState !== "ConflictHold" &&
      task.IQuavisSyncState !== "PendingIquavisSave"
    ) {
      continue;
    }
    map.set(task.Id, {
      task_id: task.Id,
      iquavis_id: task.IQuavisTaskId,
      task_name: task.Name,
      sync_state: task.IQuavisSyncState,
      message: task.IQuavisConflictMessage ?? "",
      recorded_at: null,
      workbook_fingerprint: null,
      fields: [],
    });
  }

  for (const [taskId, record] of overrides ?? []) {
    if (record) map.set(taskId, record);
    else map.delete(taskId);
  }
  return map;
}

export function patchTaskConflictState(
  tasks: Task[] | undefined,
  taskId: string,
  syncState: string,
  message: string | null
): Task[] | undefined {
  return tasks?.map((task) =>
    task.Id === taskId ? { ...task, IQuavisSyncState: syncState, IQuavisConflictMessage: message } : task
  );
}

export function getIquavisConflictFieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

export function formatIquavisConflictValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (value === "") return "（空）";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function isPendingIquavisSave(record: IquavisConflictRecord | undefined): boolean {
  return record?.sync_state === "PendingIquavisSave";
}

export function findUnsupportedIquavisExcelStatus(
  fields: IquavisConflictField[]
): IquavisConflictField | undefined {
  return fields.find(
    (field) =>
      field.field === "status" &&
      !["NotStart", "DuringStart", "Completion", "Abort"].includes(String(field.excel))
  );
}
