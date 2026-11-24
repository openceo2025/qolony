import type {
  IquavisAuthoritativeImportApplyResponse,
  IquavisAuthoritativeImportPreviewResponse,
  IquavisAuthoritativeStatusBreakdownItem,
} from "../types";

const FIELD_LABELS: Record<string, string> = {
  name: "タスク名",
  start_date: "着手日",
  end_date: "期限",
  assigned_minutes: "見積工数",
  actual_minutes: "実績工数",
  status: "状態",
  progress_rate: "進捗率",
  memo: "メモ",
};

export const IQUAVIS_SNAPSHOT_DIFFERENCE_GUIDANCE =
  "Excel表示値と前回取得・保存成功値の差です。同じiQUAVIS IDの項目差であり、QOLONYとiQUAVISのID件数差ではありません。";

export const IQUAVIS_AUTHORITATIVE_SAFETY_GUIDANCE =
  "iQUAVIS側だけのタスクとドメインはQOLONYへ取り込み、リンク済みタスクはiQUAVIS値を優先します。QOLONY側だけのローカル専用タスク、同期対象外タスク、QOLONYで付け替えた親は保持し、タスクの削除とiQUAVISサーバーへの保存は行いません。";

export function canApplyIquavisAuthoritativeImport(
  preview: IquavisAuthoritativeImportPreviewResponse | null | undefined
): boolean {
  return Boolean(
    preview?.ready &&
    preview.blockers.length === 0 &&
    preview.workbook_fingerprint.trim().length > 0
  );
}

export function getIquavisSnapshotDifferenceCount(
  preview: IquavisAuthoritativeImportPreviewResponse
): number {
  return preview.excel_snapshot_difference_count ?? preview.dirty_excel_task_count;
}

export function getIquavisUnknownStatusCount(
  preview: IquavisAuthoritativeImportPreviewResponse
): number {
  return preview.unknown_status_count ?? preview.unsupported_status_count;
}

export function normalizeIquavisStatusBreakdown(
  preview: IquavisAuthoritativeImportPreviewResponse
): IquavisAuthoritativeStatusBreakdownItem[] {
  const breakdown = preview.status_breakdown;
  if (!breakdown) return [];
  const items = Array.isArray(breakdown)
    ? breakdown
    : Object.entries(breakdown).map(([status, count]) => ({ status, count }));
  return items
    .filter((item) => item && typeof item.status === "string" && Number.isFinite(Number(item.count)))
    .map((item) => ({ status: item.status || "（空欄）", count: Number(item.count) }))
    .sort((left, right) => right.count - left.count || left.status.localeCompare(right.status, "ja"));
}

export function formatIquavisDifferenceFields(fields: string[] | undefined): string {
  if (!fields || fields.length === 0) return "項目詳細なし";
  return fields.map((field) => FIELD_LABELS[field] ?? field).join("、");
}

export function formatIquavisAuthoritativePreviewStatus(
  preview: IquavisAuthoritativeImportPreviewResponse
): string {
  const state = canApplyIquavisAuthoritativeImport(preview) ? "適用可能" : "要対応";
  return `iQUAVIS→QOLONY 初期取込 事前確認${state}: Excel ${preview.scanned_task_count}件 / iQUAVIS側のみ${preview.new_task_count}件 / リンク済み更新${preview.changed_task_count}件 / QOLONY側のみ${preview.local_only_task_count ?? "—"}件`;
}

export function formatIquavisAuthoritativeApplyStatus(
  result: IquavisAuthoritativeImportApplyResponse
): string {
  return `iQUAVIS→QOLONY 初期取込完了: 取込${result.imported_task_count}件 / ドメイン${result.imported_domain_count}件 / 更新${result.updated_task_count}件 / 競合状態クリア${result.cleared_state_count}件`;
}

export function formatIquavisAuthoritativeConfirmation(
  preview: IquavisAuthoritativeImportPreviewResponse
): string {
  const snapshotDifferenceCount = getIquavisSnapshotDifferenceCount(preview);
  const unknownStatusCount = getIquavisUnknownStatusCount(preview);
  return [
    "iQUAVIS最新値を優先してQOLONYへ安全に反映します。",
    `対象ブック: ${preview.workbook_name}`,
    `iQUAVIS側のみ: タスク${preview.new_task_count}件 / ドメイン${preview.new_domain_count ?? "—"}件`,
    `リンク済み更新: ${preview.changed_task_count}件 / ${preview.changed_field_count}項目`,
    `QOLONY側のみ: ${preview.local_only_task_count ?? "—"}件（ローカル専用として保持）`,
    `Excel取得履歴との差分: ${snapshotDifferenceCount}件 / 未知の状態: ${unknownStatusCount}件`,
    `同期対象外: ${preview.excluded_task_count ?? "—"}件 / QOLONY親維持: ${preview.preserved_local_parent_count ?? "—"}件`,
    "削除は行いません。QOLONY側だけのタスク、同期対象外、QOLONYで付け替えた親を保持します。",
    "この処理ではExcelからiQUAVISサーバーへの保存を行いません。実行しますか？",
  ].join("\n");
}
