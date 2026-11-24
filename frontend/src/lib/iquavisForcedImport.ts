import type { IquavisForcedImportApplyResponse, IquavisForcedImportPreviewResponse } from "../types";

export const IQUAVIS_FORCED_IMPORT_GUIDANCE =
  "xlsmの非表示シート（iQUAVIS取得成功値）を正として、QOLONYへ上書きします。" +
  "両側が別々に変更されたタスクだけ取込対象から外し、各画面で採択できるようにします。" +
  "この処理はxlsmへ書き込みません。QOLONY側だけのタスクも削除しません。";

export const IQUAVIS_FORCED_IMPORT_BASELINE_NOTE =
  "QOLONY側に合意記録（前回iQUAVISと一致した値）が無いタスクは、iQUAVISを正として取り込み、" +
  "そこで合意記録を作ります。次回以降はその記録を基準に3値比較します。";

const FIELD_LABELS: Record<string, string> = {
  name: "タスク名",
  start_date: "着手日",
  end_date: "期限",
  actual_minutes: "実績",
  status: "状態",
  progress_rate: "進捗率",
  memo: "メモ",
};

export function getForcedImportFieldLabel(field: string): string {
  return FIELD_LABELS[field] ?? field;
}

export function formatForcedImportFieldCounts(counts: Record<string, number> | undefined): string {
  const entries = Object.entries(counts ?? {}).filter(([, count]) => count > 0);
  if (entries.length === 0) return "なし";
  return entries
    .sort((left, right) => right[1] - left[1])
    .map(([field, count]) => `${getForcedImportFieldLabel(field)} ${count}件`)
    .join(" / ");
}

export function canApplyIquavisForcedImport(
  preview: IquavisForcedImportPreviewResponse | null | undefined
): boolean {
  return Boolean(preview?.workbook_fingerprint);
}

/** 事前確認の結果を、実行前に読む1行の要約にする。 */
export function formatForcedImportPreviewSummary(
  preview: IquavisForcedImportPreviewResponse | null | undefined
): string {
  if (!preview) return "事前確認を実行してください。";
  return (
    `新規取込 ${preview.new_task_count}件 / ` +
    `iQUAVIS値で上書き ${preview.updated_task_count}件 / ` +
    `変更なし ${preview.unchanged_task_count}件 / ` +
    `競合として保留 ${preview.conflict_task_count}件`
  );
}

export function formatForcedImportApplyStatus(
  result: IquavisForcedImportApplyResponse | null | undefined
): string {
  if (!result) return "";
  return (
    `iQUAVIS強制取込 完了: 新規 ${result.imported_task_count}件 / ` +
    `更新 ${result.updated_task_count}件 / ` +
    `競合 ${result.conflict_task_count}件 / ` +
    `合意記録 ${result.baseline_task_count}件 / ` +
    `旧状態クリア ${result.cleared_state_count}件`
  );
}

export function formatForcedImportConfirmation(
  preview: IquavisForcedImportPreviewResponse | null | undefined
): string {
  if (!preview) return "";
  return (
    "iQUAVISを正として、QOLONYのタスクを上書きします。\n\n" +
    `${formatForcedImportPreviewSummary(preview)}\n\n` +
    `競合として保留する ${preview.conflict_task_count}件は変更しません。\n` +
    `QOLONY側だけのタスク ${preview.local_only_task_count}件は残します。\n\n` +
    "実行しますか？"
  );
}
