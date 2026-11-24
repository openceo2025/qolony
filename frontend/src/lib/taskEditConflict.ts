import type { Task, TaskEditConflictDetail, TaskEditConflictServerValues } from "../types";

/**
 * QOLONY利用者どうしの同時編集がぶつかった状態。
 *
 * 非同期保存なので、自分が押した変更がサーバーへ届く前に別の人が同じタスクを変えることがある。
 * 勝手にどちらかを消さず、誰が・何を変えたのかを見せて、話して決めてもらう。
 */
export interface TaskEditConflict {
  taskId: string;
  taskName: string;
  /** 相手の変更を取り込む前の、自分の画面が持っていた値。 */
  base: Task | null;
  /** 自分がやろうとした変更。 */
  patch: Partial<Task>;
  detail: TaskEditConflictDetail;
  detectedAt: string;
}

const FIELD_LABELS: Record<string, string> = {
  Name: "タスク名",
  Status: "状態",
  ProgressRate: "進捗率",
  StartDate: "着手日",
  EndDate: "期限",
  AssignedManHour: "見積工数",
  ActualManHour: "実績工数",
  Memo: "メモ",
  AssigneeUserId: "担当者",
  TodayOverride: "today",
};

const STATUS_LABELS: Record<string, string> = {
  NotStart: "未着手",
  DuringStart: "作業中",
  Completion: "完了",
  Abort: "中止",
};

export function getTaskFieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

export function formatTaskFieldValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "(未設定)";
  if (field === "Status") return STATUS_LABELS[String(value)] ?? String(value);
  if (field === "ProgressRate") return `${Math.round(Number(value))}%`;
  if (field === "AssignedManHour" || field === "ActualManHour") return `${Number(value)}分`;
  if (typeof value === "boolean") return value ? "ON" : "OFF";
  return String(value);
}

export interface TaskEditConflictRow {
  field: string;
  label: string;
  /** 自分の画面が変更前に持っていた値。 */
  base: string;
  /** 自分が保存しようとした値。 */
  mine: string;
  /** いまサーバーにある、相手の値。 */
  theirs: string;
  /** 相手も同じ項目を変えているか。ここが本当の衝突点。 */
  contested: boolean;
}

function serverValue(server: TaskEditConflictServerValues | undefined, field: string): unknown {
  if (!server) return undefined;
  return (server as unknown as Record<string, unknown>)[field];
}

/**
 * 「自分の元の値 / 自分の変更 / 相手の現在値」を項目ごとに並べる。
 * 自分が触った項目に加え、相手が変えた項目も出す。どちらの変更も見えないと相談できない。
 */
export function buildTaskEditConflictRows(conflict: TaskEditConflict): TaskEditConflictRow[] {
  const server = conflict.detail.server;
  const fields = new Set<string>(Object.keys(conflict.patch));
  if (server) {
    for (const field of Object.keys(FIELD_LABELS)) {
      if (serverValue(server, field) === undefined) continue;
      const baseValue = conflict.base ? (conflict.base as unknown as Record<string, unknown>)[field] : undefined;
      if (baseValue === undefined) continue;
      // 相手が変えた項目も並べる。自分が触っていなくても衝突の全体像が要る。
      if (!Object.is(baseValue, serverValue(server, field))) fields.add(field);
    }
  }

  const rows: TaskEditConflictRow[] = [];
  for (const field of fields) {
    if (!(field in FIELD_LABELS)) continue;
    const baseValue = conflict.base ? (conflict.base as unknown as Record<string, unknown>)[field] : undefined;
    const mineValue = field in conflict.patch
      ? (conflict.patch as unknown as Record<string, unknown>)[field]
      : baseValue;
    const theirsValue = serverValue(server, field);
    const mineChanged = !Object.is(baseValue, mineValue);
    const theirsChanged = !Object.is(baseValue, theirsValue);
    rows.push({
      field,
      label: getTaskFieldLabel(field),
      base: formatTaskFieldValue(field, baseValue),
      mine: formatTaskFieldValue(field, mineValue),
      theirs: formatTaskFieldValue(field, theirsValue),
      contested: mineChanged && theirsChanged && !Object.is(mineValue, theirsValue),
    });
  }
  return rows;
}

export function formatTaskEditConflictHeadline(conflict: TaskEditConflict): string {
  const name = conflict.detail.updated_by_user_name;
  if (name) return `${name} さんが先に「${conflict.taskName}」を変更しました`;
  return `他のユーザーが先に「${conflict.taskName}」を変更しました`;
}

export function formatTaskEditConflictGuidance(conflict: TaskEditConflict): string {
  const name = conflict.detail.updated_by_user_name;
  const who = name ? `${name} さん` : "相手";
  return (
    `${who}と話して、どちらの変更を残すか決めてください。` +
    "「自分の変更で上書き」を押すと、あなたの内容でサーバーへ保存します。" +
    "「相手の変更を採用」を押すと、あなたの変更を破棄して相手の内容に合わせます。"
  );
}

export function upsertTaskEditConflict(
  conflicts: readonly TaskEditConflict[],
  next: TaskEditConflict
): TaskEditConflict[] {
  const others = conflicts.filter((item) => item.taskId !== next.taskId);
  return [...others, next];
}

export function removeTaskEditConflict(
  conflicts: readonly TaskEditConflict[],
  taskId: string
): TaskEditConflict[] {
  return conflicts.filter((item) => item.taskId !== taskId);
}
