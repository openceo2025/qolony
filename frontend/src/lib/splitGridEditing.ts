import type { ColDef } from "ag-grid-community";

export interface SplitGridRow {
  rowNo: number;
  taskName: string;
  mhRatio: string;
  actualManHour: string;
  memo: string;
  dueDate: string;
  assigneeName: string;
  assigneeUserId: string;
  isExisting: boolean;
  existingTaskId: string;
  existingTaskRevision: string;
}

export interface SplitGridAssigneeOption {
  id: string;
  name: string;
}

export interface SplitGridColumnLabels {
  rowNo: string;
  taskName: string;
  mhRatio: string;
  actualManHour: string;
  memo: string;
  dueDate: string;
  assigneeName: string;
}

export type SplitGridEditableField =
  | "taskName"
  | "mhRatio"
  | "actualManHour"
  | "memo"
  | "dueDate"
  | "assigneeName";

export interface SplitGridKeyboardNavigationState {
  key: string;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
}

export type SplitGridDependencyMode = "Serial" | "Parallel";

export interface SplitGridDraft {
  version: 2;
  parentTaskId: string;
  parentTaskRevision: string;
  rows: SplitGridRow[];
  parentChildDependencyMode: SplitGridDependencyMode;
}

export interface SplitGridShortcutHandling {
  action: "grid-fill-down" | "custom-fill-right" | "copy" | "paste";
  preventDefault: boolean;
  stopPropagation: boolean;
}

export interface SplitGridCellPosition {
  rowIndex: number;
  columnIndex: number;
}

const DEFAULT_LABELS: SplitGridColumnLabels = {
  rowNo: "#",
  taskName: "Task Name",
  mhRatio: "MH Ratio",
  actualManHour: "Actual MH",
  memo: "Memo",
  dueDate: "Due Date",
  assigneeName: "Assignee",
};

const IME_SAFE_SINGLE_CLICK_FIELDS = new Set<SplitGridEditableField>([
  "taskName",
  "mhRatio",
  "actualManHour",
  "memo",
  "dueDate",
  "assigneeName",
]);

const KEYBOARD_NAVIGATION_AUTO_EDIT_FIELDS = new Set<SplitGridEditableField>([
  "taskName",
  "mhRatio",
  "actualManHour",
  "memo",
  "dueDate",
  "assigneeName",
]);

const ARROW_NAVIGATION_KEYS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]);

const SPLIT_DRAFT_STORAGE_PREFIX = "qolony:split-draft:v2";

const SPLIT_GRID_ROW_FIELDS: Array<keyof SplitGridRow> = [
  "rowNo",
  "taskName",
  "mhRatio",
  "actualManHour",
  "memo",
  "dueDate",
  "assigneeName",
  "assigneeUserId",
  "isExisting",
  "existingTaskId",
];

export function resolveSplitGridShortcut(
  event: SplitGridKeyboardNavigationState
): SplitGridShortcutHandling | null {
  if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey) return null;

  const key = event.key.toLowerCase();
  if (key === "d") {
    // ブラウザ既定処理を止め、呼び出し側でAG Gridの下方向コピーを明示実行する。
    return {
      action: "grid-fill-down",
      preventDefault: true,
      stopPropagation: true,
    };
  }
  if (key === "r") {
    return {
      action: "custom-fill-right",
      preventDefault: true,
      stopPropagation: true,
    };
  }
  // コピーと貼り付けは既定処理を止めない。呼び出し側が隠しテキスト欄へフォーカスを移し、
  // ブラウザ自身のコピー・貼り付けでクリップボードを読み書きする（http の LAN でも動く）。
  if (key === "c") {
    return { action: "copy", preventDefault: false, stopPropagation: true };
  }
  if (key === "v") {
    return { action: "paste", preventDefault: false, stopPropagation: true };
  }
  return null;
}

export function createSplitDraftStorageKey(parentTaskId: string, userId: string): string {
  return `${SPLIT_DRAFT_STORAGE_PREFIX}:${encodeURIComponent(userId)}:${encodeURIComponent(parentTaskId)}`;
}

export function serializeSplitGridDraft(draft: SplitGridDraft): string {
  return JSON.stringify(draft);
}

export function parseSplitGridDraft(raw: string | null, parentTaskId: string): SplitGridDraft | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<SplitGridDraft>;
    if (
      parsed.version !== 2
      || parsed.parentTaskId !== parentTaskId
      || typeof parsed.parentTaskRevision !== "string"
      || !Array.isArray(parsed.rows)
      || (parsed.parentChildDependencyMode !== "Serial" && parsed.parentChildDependencyMode !== "Parallel")
    ) {
      return null;
    }
    if (!parsed.rows.every(isStoredSplitGridRow)) return null;
    return parsed as SplitGridDraft;
  } catch {
    return null;
  }
}

function isStoredSplitGridRow(value: unknown): value is SplitGridRow {
  if (!value || typeof value !== "object") return false;
  const row = value as Partial<SplitGridRow>;
  return typeof row.rowNo === "number"
    && typeof row.taskName === "string"
    && typeof row.mhRatio === "string"
    && typeof row.actualManHour === "string"
    && typeof row.memo === "string"
    && typeof row.dueDate === "string"
    && typeof row.assigneeName === "string"
    && typeof row.assigneeUserId === "string"
    && typeof row.isExisting === "boolean"
    && typeof row.existingTaskId === "string"
    && typeof row.existingTaskRevision === "string";
}

export function areSplitGridRowsEqual(left: SplitGridRow[], right: SplitGridRow[]): boolean {
  if (left.length !== right.length) return false;
  return left.every((row, index) => {
    const other = right[index];
    return !!other && SPLIT_GRID_ROW_FIELDS.every((field) => row[field] === other[field]);
  });
}

export function hasSplitGridDraftChanges(
  rows: SplitGridRow[],
  parentChildDependencyMode: SplitGridDependencyMode,
  baselineRows: SplitGridRow[],
  baselineDependencyMode: SplitGridDependencyMode
): boolean {
  return parentChildDependencyMode !== baselineDependencyMode || !areSplitGridRowsEqual(rows, baselineRows);
}

export function usesImeSafeSingleClickEdit(field: SplitGridEditableField): boolean {
  return IME_SAFE_SINGLE_CLICK_FIELDS.has(field);
}

export function usesImeSafeSuppressPreventDefault(field: SplitGridEditableField): boolean {
  return IME_SAFE_SINGLE_CLICK_FIELDS.has(field) && field !== "assigneeName";
}

export function isSplitGridEditableField(field: string | undefined): field is SplitGridEditableField {
  return field === "taskName"
    || field === "mhRatio"
    || field === "actualManHour"
    || field === "memo"
    || field === "dueDate"
    || field === "assigneeName";
}

export function usesKeyboardNavigationAutoEdit(field: SplitGridEditableField): boolean {
  return KEYBOARD_NAVIGATION_AUTO_EDIT_FIELDS.has(field);
}

export function prepareSplitClipboardData(data: string[][]): string[][] {
  const normalized = data.map((row) => row.map((cell) => cell ?? ""));

  while (normalized.length > 0) {
    const lastRow = normalized[normalized.length - 1];
    if (lastRow.some((cell) => cell !== "")) {
      break;
    }
    normalized.pop();
  }

  return normalized;
}

// ---- 範囲選択とクリップボード（AG Grid 無料版で自前に持つ） ----
// 有料版（Enterprise）の範囲選択・貼り付けは OSS 公開に使えないため、表計算と同じ操作を自前で再現する。
// 実際のセル更新は呼び出し側が rowNode.setDataValue で行い、既存の onCellValueChanged を通す。

export interface SplitCellRange {
  top: number;
  bottom: number;
  left: number;
  right: number;
}
export interface SplitCellUpdate {
  rowIndex: number;
  columnIndex: number;
  value: string;
}

export function normalizeSplitRange(anchor: SplitGridCellPosition, extent: SplitGridCellPosition): SplitCellRange {
  return {
    top: Math.min(anchor.rowIndex, extent.rowIndex),
    bottom: Math.max(anchor.rowIndex, extent.rowIndex),
    left: Math.min(anchor.columnIndex, extent.columnIndex),
    right: Math.max(anchor.columnIndex, extent.columnIndex),
  };
}

export function isInSplitRange(range: SplitCellRange | null, rowIndex: number, columnIndex: number): boolean {
  return !!range
    && rowIndex >= range.top && rowIndex <= range.bottom
    && columnIndex >= range.left && columnIndex <= range.right;
}

/** 表計算ソフトからのタブ区切りを2次元配列へ。引用符で囲まれたセル内の改行・タブも扱う。 */
export function parseSplitClipboardText(text: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  const source = text.replace(/\r\n?/g, "\n");
  for (let i = 0; i < source.length; i += 1) {
    const ch = source[i];
    if (quoted) {
      if (ch === "\"" && source[i + 1] === "\"") {
        cell += "\"";
        i += 1;
      } else if (ch === "\"") {
        quoted = false;
      } else {
        cell += ch;
      }
      continue;
    }
    if (ch === "\"" && cell === "") {
      quoted = true;
    } else if (ch === "\t") {
      row.push(cell);
      cell = "";
    } else if (ch === "\n") {
      row.push(cell);
      rows.push(row);
      row = [];
      cell = "";
    } else {
      cell += ch;
    }
  }
  row.push(cell);
  rows.push(row);
  return prepareSplitClipboardData(rows);
}

/** 2次元配列をタブ区切りへ。タブ・改行・引用符を含むセルは引用符で囲む（表計算ソフトと同じ）。 */
export function formatSplitClipboardText(cells: string[][]): string {
  const quote = (value: string) => (/[\t\n\r"]/.test(value) ? `"${value.replace(/"/g, "\"\"")}"` : value);
  return cells.map((row) => row.map((value) => quote(value ?? "")).join("\t")).join("\r\n");
}

/**
 * 貼り付けで書き換えるセル。1セルだけをコピーして範囲へ貼ると範囲全体に繰り返す（表計算と同じ）。
 * 表の外にはみ出す分は捨てる。編集できないセルは飛ばす。
 */
export function planSplitPaste(
  data: string[][],
  target: SplitCellRange,
  rowCount: number,
  columnCount: number,
  canEdit: (rowIndex: number, columnIndex: number) => boolean
): SplitCellUpdate[] {
  if (!data.length || !data[0].length) return [];
  const single = data.length === 1 && data[0].length === 1;
  const height = single ? target.bottom - target.top + 1 : data.length;
  const updates: SplitCellUpdate[] = [];
  for (let r = 0; r < height; r += 1) {
    const rowIndex = target.top + r;
    if (rowIndex >= rowCount) break;
    const values = single ? Array(target.right - target.left + 1).fill(data[0][0]) : data[r];
    values.forEach((value, c) => {
      const columnIndex = target.left + c;
      if (columnIndex >= columnCount || !canEdit(rowIndex, columnIndex)) return;
      updates.push({ rowIndex, columnIndex, value: value ?? "" });
    });
  }
  return updates;
}

/** Ctrl+D: 範囲の先頭行の値を下の行へ。Ctrl+R: 範囲の左端列の値を右の列へ。 */
export function planSplitFill(
  direction: "down" | "right",
  range: SplitCellRange,
  valueAt: (rowIndex: number, columnIndex: number) => string,
  canEdit: (rowIndex: number, columnIndex: number) => boolean
): SplitCellUpdate[] {
  const updates: SplitCellUpdate[] = [];
  for (let rowIndex = range.top; rowIndex <= range.bottom; rowIndex += 1) {
    for (let columnIndex = range.left; columnIndex <= range.right; columnIndex += 1) {
      if (direction === "down" ? rowIndex === range.top : columnIndex === range.left) continue;
      if (!canEdit(rowIndex, columnIndex)) continue;
      const value = direction === "down" ? valueAt(range.top, columnIndex) : valueAt(rowIndex, range.left);
      updates.push({ rowIndex, columnIndex, value });
    }
  }
  return updates;
}

export function shouldHandleSplitKeyboardNavigation(event: SplitGridKeyboardNavigationState): boolean {
  if (event.altKey || event.ctrlKey || event.metaKey) {
    return false;
  }

  if (ARROW_NAVIGATION_KEYS.has(event.key)) {
    return !event.shiftKey;
  }

  return event.key === "Enter" || event.key === "Tab";
}

export function resolveNextSplitGridCellPosition(
  current: SplitGridCellPosition,
  event: SplitGridKeyboardNavigationState,
  rowCount: number,
  columnCount: number
): SplitGridCellPosition | null {
  if (rowCount <= 0 || columnCount <= 0) return null;

  switch (event.key) {
    case "ArrowUp":
      return current.rowIndex > 0
        ? { rowIndex: current.rowIndex - 1, columnIndex: current.columnIndex }
        : null;
    case "ArrowDown":
      return current.rowIndex + 1 < rowCount
        ? { rowIndex: current.rowIndex + 1, columnIndex: current.columnIndex }
        : null;
    case "ArrowLeft":
      return current.columnIndex > 0
        ? { rowIndex: current.rowIndex, columnIndex: current.columnIndex - 1 }
        : null;
    case "ArrowRight":
      return current.columnIndex + 1 < columnCount
        ? { rowIndex: current.rowIndex, columnIndex: current.columnIndex + 1 }
        : null;
    case "Enter": {
      const nextRowIndex = current.rowIndex + (event.shiftKey ? -1 : 1);
      if (nextRowIndex < 0 || nextRowIndex >= rowCount) return null;
      return { rowIndex: nextRowIndex, columnIndex: current.columnIndex };
    }
    case "Tab": {
      const direction = event.shiftKey ? -1 : 1;
      let nextRowIndex = current.rowIndex;
      let nextColumnIndex = current.columnIndex + direction;

      if (nextColumnIndex >= columnCount) {
        nextColumnIndex = 0;
        nextRowIndex += 1;
      } else if (nextColumnIndex < 0) {
        nextColumnIndex = columnCount - 1;
        nextRowIndex -= 1;
      }

      if (nextRowIndex < 0 || nextRowIndex >= rowCount) return null;
      return { rowIndex: nextRowIndex, columnIndex: nextColumnIndex };
    }
    default:
      return null;
  }
}

export function buildSplitColumnDefs(
  canEditCell: (field: SplitGridEditableField, row: SplitGridRow | undefined) => boolean,
  assignees: SplitGridAssigneeOption[],
  labels: Partial<SplitGridColumnLabels> = {},
  assigneeErrorTaskIds: ReadonlySet<string> = new Set()
): ColDef<SplitGridRow>[] {
  const mergedLabels: SplitGridColumnLabels = { ...DEFAULT_LABELS, ...labels };
  const assigneeNames = assignees.map((assignee) => assignee.name);

  return [
    { headerName: mergedLabels.rowNo, field: "rowNo", editable: false, width: 64, pinned: "left" },
    {
      headerName: mergedLabels.taskName,
      field: "taskName",
      editable: (params) => canEditCell("taskName", params.data),
      singleClickEdit: usesImeSafeSingleClickEdit("taskName"),
      cellEditorParams: { suppressPreventDefault: usesImeSafeSuppressPreventDefault("taskName") },
      flex: 1.6,
    },
    {
      headerName: mergedLabels.mhRatio,
      field: "mhRatio",
      editable: (params) => canEditCell("mhRatio", params.data),
      singleClickEdit: usesImeSafeSingleClickEdit("mhRatio"),
      cellEditorParams: { suppressPreventDefault: usesImeSafeSuppressPreventDefault("mhRatio") },
      width: 120,
    },
    {
      headerName: mergedLabels.actualManHour,
      field: "actualManHour",
      editable: (params) => canEditCell("actualManHour", params.data),
      singleClickEdit: usesImeSafeSingleClickEdit("actualManHour"),
      cellEditorParams: { suppressPreventDefault: usesImeSafeSuppressPreventDefault("actualManHour") },
      width: 120,
      cellClass: (params) => (canEditCell("actualManHour", params.data) ? "" : "split-cell-locked"),
    },
    {
      headerName: mergedLabels.memo,
      field: "memo",
      editable: (params) => canEditCell("memo", params.data),
      // 日本語 IME では、選択済みセルへ printable key を流すと先頭キーだけ半角確定されやすい。
      // 単クリック編集と printable key の既定注入抑止を組み合わせ、IME の誤入力を減らす。
      singleClickEdit: usesImeSafeSingleClickEdit("memo"),
      cellEditorParams: { suppressPreventDefault: usesImeSafeSuppressPreventDefault("memo") },
      flex: 1.4,
    },
    {
      headerName: mergedLabels.dueDate,
      field: "dueDate",
      editable: (params) => canEditCell("dueDate", params.data),
      singleClickEdit: usesImeSafeSingleClickEdit("dueDate"),
      cellEditorParams: { suppressPreventDefault: usesImeSafeSuppressPreventDefault("dueDate") },
      width: 140,
    },
    {
      headerName: mergedLabels.assigneeName,
      field: "assigneeName",
      editable: (params) => canEditCell("assigneeName", params.data),
      singleClickEdit: usesImeSafeSingleClickEdit("assigneeName"),
      width: 180,
      cellClass: (params) => {
        if (params.data?.existingTaskId && assigneeErrorTaskIds.has(params.data.existingTaskId)) {
          return "split-cell-error";
        }
        return canEditCell("assigneeName", params.data) ? "" : "split-cell-locked";
      },
      cellEditor: "agSelectCellEditor",
      cellEditorParams: { values: assigneeNames },
    },
  ];
}
