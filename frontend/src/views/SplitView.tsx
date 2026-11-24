import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AgGridReact } from "ag-grid-react";
import { ApiError } from "../api/client";
import type {
  CellValueChangedEvent,
  ColDef,
  GetRowIdParams,
  GridApi,
  GridReadyEvent,
  TabToNextCellParams,
} from "ag-grid-community";
import { formatDisplayText } from "../lib/displayText";
import { computeAssignedMinutes } from "../lib/taskTree";
import {
  buildSplitColumnDefs,
  createSplitDraftStorageKey,
  formatSplitClipboardText,
  hasSplitGridDraftChanges,
  isInSplitRange,
  isSplitGridEditableField,
  normalizeSplitRange,
  parseSplitClipboardText,
  parseSplitGridDraft,
  planSplitFill,
  planSplitPaste,
  resolveNextSplitGridCellPosition,
  type SplitCellRange,
  type SplitCellUpdate,
  type SplitGridCellPosition,
  resolveSplitGridShortcut,
  serializeSplitGridDraft,
  shouldHandleSplitKeyboardNavigation,
  type SplitGridDraft,
  type SplitGridRow,
  usesKeyboardNavigationAutoEdit,
} from "../lib/splitGridEditing";
import { useI18n } from "../lib/i18n";
import type { SplitExecutionResult } from "../lib/splitExecution";
import type {
  BulkTaskFieldsResponse,
  ChildDependencyMode,
  SplitTemplate,
  Task,
  User,
} from "../types";

interface SplitViewProps {
  parentTask: Task | null;
  allTasks: Task[];
  users: User[];
  canEdit: boolean;
  currentUserId: string;
  isCurrentUserTopManager: boolean;
  templates: SplitTemplate[];
  templateIndex: number;
  setTemplateIndex: (index: number) => void;
  onExecute: (
    children: Array<{
      ExistingTaskId?: string;
      ExpectedRevision?: string;
      Name: string;
      AssignedManHour: number;
      ActualManHour: number;
      EndDate: string;
      Memo: string;
      AssigneeUserId: string;
    }>,
    parentChildDependencyMode: ChildDependencyMode,
    expectedParentRevision: string
  ) => Promise<SplitExecutionResult>;
  onUpdateExistingRows: (
    updates: Array<{
      task_id: string;
      expected_revision?: string;
      name: string;
      assigned_manhour: number;
      memo: string;
      end_date: string;
      assignee_user_id: string;
    }>,
    parentTaskId: string,
    expectedParentRevision: string
  ) => Promise<BulkTaskFieldsResponse>;
  showExecutionMessage: (message: string) => void;
}

interface AssigneeUpdateError {
  taskId: string;
  rowNo: number;
  taskName: string;
  message: string;
}

const TOTAL_ROWS = 256;

const normalizeText = (value: unknown): string => (value == null ? "" : String(value));

const normalizeRow = (row: SplitGridRow): SplitGridRow => ({
  ...row,
  taskName: normalizeText(row.taskName),
  mhRatio: normalizeText(row.mhRatio),
  actualManHour: normalizeText(row.actualManHour),
  memo: normalizeText(row.memo),
  dueDate: normalizeText(row.dueDate),
  assigneeName: normalizeText(row.assigneeName),
  assigneeUserId: normalizeText(row.assigneeUserId),
  existingTaskId: normalizeText(row.existingTaskId),
  existingTaskRevision: normalizeText(row.existingTaskRevision),
});

const formatRatio = (value: number): string => {
  if (!Number.isFinite(value)) return "0";
  const rounded = Math.round(value * 10000) / 10000;
  return Number.isInteger(rounded) ? String(rounded) : String(rounded).replace(/\.?0+$/, "");
};

const distributeEvenly = (total: number, count: number): number[] => {
  if (count <= 0) return [];
  const base = Math.floor(Math.max(0, total) / count);
  const remain = Math.max(0, total) - base * count;
  return Array.from({ length: count }, (_value, index) => base + (index < remain ? 1 : 0));
};

const sortTasks = (a: Task, b: Task): number => {
  if (a.EndDate !== b.EndDate) return a.EndDate.localeCompare(b.EndDate);
  if (a.CreateDate !== b.CreateDate) return a.CreateDate.localeCompare(b.CreateDate);
  return a.Name.localeCompare(b.Name);
};

const createRows = (
  parentTask: Task | null,
  existingChildren: Task[],
  isResplitMode: boolean
): SplitGridRow[] => {
  const parentDueDate = parentTask?.EndDate ?? "";
  const defaultAssigneeName = parentTask?.AssigneeName ?? "";
  const defaultAssigneeUserId = parentTask?.AssigneeUserId ?? "";
  const parentAssigned = Math.max(1, parentTask?.AssignedManHour ?? 1);

  const rows: SplitGridRow[] = existingChildren.map((task, idx) => ({
    rowNo: idx + 1,
    taskName: task.Name,
    mhRatio: formatRatio((task.AssignedManHour * 100) / parentAssigned),
    actualManHour: String(task.ActualManHour),
    memo: task.Memo ?? "",
    dueDate: task.EndDate,
    assigneeName: task.AssigneeName,
    assigneeUserId: task.AssigneeUserId,
    isExisting: true,
    existingTaskId: task.Id,
    existingTaskRevision: task.Revision,
  }));

  while (rows.length < TOTAL_ROWS) {
    rows.push({
      rowNo: rows.length + 1,
      taskName: "",
      mhRatio: "",
      actualManHour: "0",
      memo: "",
      dueDate: parentDueDate,
      assigneeName: defaultAssigneeName,
      assigneeUserId: defaultAssigneeUserId,
      isExisting: false,
      existingTaskId: "",
      existingTaskRevision: "",
    });
  }

  return rows;
};

export function SplitView({
  parentTask,
  allTasks,
  users,
  canEdit,
  currentUserId,
  isCurrentUserTopManager,
  templates,
  templateIndex,
  setTemplateIndex,
  onExecute,
  onUpdateExistingRows,
  showExecutionMessage,
}: SplitViewProps): JSX.Element {
  const { t } = useI18n();
  const existingChildren = useMemo(() => {
    if (!parentTask) return [];
    return allTasks.filter((task) => task.ParentTaskId === parentTask.Id).sort(sortTasks);
  }, [allTasks, parentTask]);

  const isResplitMode = existingChildren.length > 0;
  const baselineRows = useMemo(
    () => createRows(parentTask, existingChildren, isResplitMode),
    [existingChildren, isResplitMode, parentTask]
  );
  const baselineRowByTaskId = useMemo(
    () => new Map(baselineRows.filter((row) => row.existingTaskId).map((row) => [row.existingTaskId, row])),
    [baselineRows]
  );
  const baselineDependencyMode: ChildDependencyMode =
    parentTask?.ChildDependencyMode === "Parallel" ? "Parallel" : "Serial";
  const draftStorageKey = parentTask ? createSplitDraftStorageKey(parentTask.Id, currentUserId) : "";

  const gridApiRef = useRef<GridApi<SplitGridRow> | null>(null);
  const initializedDraftKeyRef = useRef<string | null>(null);
  const draftHasChangesRef = useRef(false);
  const isSavingExistingRowsRef = useRef(false);

  const [rows, setRows] = useState<SplitGridRow[]>(() => baselineRows.map(normalizeRow));
  const rowsRef = useRef<SplitGridRow[]>(rows);
  const [parentChildDependencyMode, setParentChildDependencyMode] =
    useState<ChildDependencyMode>(baselineDependencyMode);
  const [parentTaskRevision, setParentTaskRevision] = useState(parentTask?.Revision ?? "");
  const [hasDraft, setHasDraft] = useState(false);
  const [isSavingExistingRows, setIsSavingExistingRows] = useState(false);
  const [assigneeUpdateErrors, setAssigneeUpdateErrors] = useState<AssigneeUpdateError[]>([]);
  useEffect(() => {
    rowsRef.current = rows;
  }, [rows]);
  const getRowId = useCallback((params: GetRowIdParams<SplitGridRow>): string => String(params.data.rowNo), []);
  const tabToNextCell = useCallback((params: TabToNextCellParams<SplitGridRow>) => {
    const columns = params.api.getAllDisplayedColumns();
    const previous = params.previousCellPosition;
    if (columns.length === 0 || !previous) return null;

    const currentColIndex = columns.findIndex((column) => column.getColId() === previous.column.getColId());
    if (currentColIndex < 0) return previous;

    let rowIndex = previous.rowIndex;
    let colIndex = currentColIndex + (params.backwards ? -1 : 1);

    if (colIndex >= columns.length) {
      colIndex = 0;
      rowIndex += 1;
    } else if (colIndex < 0) {
      colIndex = columns.length - 1;
      rowIndex -= 1;
    }

    if (rowIndex < 0 || rowIndex >= rows.length) {
      return previous;
    }

    return {
      rowIndex,
      rowPinned: previous.rowPinned,
      column: columns[colIndex],
    };
  }, [rows.length]);

  useEffect(() => {
    if (initializedDraftKeyRef.current === draftStorageKey) return;
    initializedDraftKeyRef.current = draftStorageKey;
    let storedDraft: SplitGridDraft | null = null;
    if (draftStorageKey && parentTask) {
      try {
        storedDraft = parseSplitGridDraft(sessionStorage.getItem(draftStorageKey), parentTask.Id);
      } catch {
        // sessionStorageが使えない環境でも、Split編集自体は継続できる。
      }
    }

    const storedRowsAreValid = storedDraft?.rows.length === TOTAL_ROWS;
    const draftHasChanges = !!storedDraft
      && storedRowsAreValid
      && hasSplitGridDraftChanges(
        storedDraft.rows,
        storedDraft.parentChildDependencyMode,
        baselineRows,
        baselineDependencyMode
      );

    if (storedDraft && draftHasChanges) {
      draftHasChangesRef.current = true;
      setRows(storedDraft.rows.map(normalizeRow));
      setParentChildDependencyMode(storedDraft.parentChildDependencyMode);
      setParentTaskRevision(storedDraft.parentTaskRevision);
      setHasDraft(true);
    } else {
      draftHasChangesRef.current = false;
      // AG Gridは行オブジェクトを直接更新するため、サーバー基準値とは参照を分離する。
      setRows(baselineRows.map(normalizeRow));
      setParentChildDependencyMode(baselineDependencyMode);
      setParentTaskRevision(parentTask?.Revision ?? "");
      setHasDraft(false);
      if (draftStorageKey) {
        try {
          sessionStorage.removeItem(draftStorageKey);
        } catch {
          // sessionStorageが使えない環境では削除を省略する。
        }
      }
    }
    setAssigneeUpdateErrors([]);
  }, [baselineDependencyMode, baselineRows, draftStorageKey, parentTask]);

  useEffect(() => {
    if (initializedDraftKeyRef.current !== draftStorageKey || hasDraft || draftHasChangesRef.current) return;
    if ((gridApiRef.current?.getEditingCells().length ?? 0) > 0) return;
    setRows(baselineRows.map(normalizeRow));
    setParentChildDependencyMode(baselineDependencyMode);
    setParentTaskRevision(parentTask?.Revision ?? "");
  }, [baselineDependencyMode, baselineRows, draftStorageKey, hasDraft, parentTask?.Revision]);

  const persistDraftState = useCallback((
    nextRows: SplitGridRow[],
    nextDependencyMode: ChildDependencyMode,
    comparisonRows: SplitGridRow[] = baselineRows,
    comparisonDependencyMode: ChildDependencyMode = baselineDependencyMode,
    nextParentTaskRevision: string = parentTaskRevision
  ): void => {
    const changed = !!parentTask && hasSplitGridDraftChanges(
      nextRows,
      nextDependencyMode,
      comparisonRows,
      comparisonDependencyMode
    );
    draftHasChangesRef.current = changed;
    setHasDraft(changed);
    if (!draftStorageKey || !parentTask) return;

    try {
      if (!changed) {
        sessionStorage.removeItem(draftStorageKey);
        return;
      }
      sessionStorage.setItem(
        draftStorageKey,
        serializeSplitGridDraft({
          version: 2,
          parentTaskId: parentTask.Id,
          parentTaskRevision: nextParentTaskRevision,
          rows: nextRows.map(normalizeRow),
          parentChildDependencyMode: nextDependencyMode,
        })
      );
    } catch {
      // 保存容量不足などでも画面内の下書きは維持する。
    }
  }, [baselineDependencyMode, baselineRows, draftStorageKey, parentTask, parentTaskRevision]);

  const clearDraft = useCallback((): void => {
    if (draftStorageKey) {
      try {
        sessionStorage.removeItem(draftStorageKey);
      } catch {
        // sessionStorageが使えない環境では削除を省略する。
      }
    }
    setRows(baselineRows.map(normalizeRow));
    setParentChildDependencyMode(baselineDependencyMode);
    setParentTaskRevision(parentTask?.Revision ?? "");
    draftHasChangesRef.current = false;
    setHasDraft(false);
    setAssigneeUpdateErrors([]);
  }, [baselineDependencyMode, baselineRows, draftStorageKey, parentTask?.Revision]);

  const taskById = useMemo(() => new Map(allTasks.map((task) => [task.Id, task])), [allTasks]);
  const hasChildrenByTaskId = useMemo(() => {
    const map = new Map<string, boolean>();
    for (const task of allTasks) {
      if (!task.ParentTaskId) continue;
      map.set(task.ParentTaskId, true);
    }
    return map;
  }, [allTasks]);

  const canChangeAssigneeForExistingTask = useCallback((task: Task): boolean => {
    const parentTaskOfCurrent = task.ParentTaskId ? taskById.get(task.ParentTaskId) : null;
    const hasPermission =
      isCurrentUserTopManager ||
      task.AssigneeUserId === currentUserId ||
      parentTaskOfCurrent?.AssigneeUserId === currentUserId;
    if (!hasPermission) return false;
    const hasChildren = hasChildrenByTaskId.get(task.Id) === true;
    if (hasChildren) return true;
    return task.ActualManHour === 0;
  }, [currentUserId, hasChildrenByTaskId, isCurrentUserTopManager, taskById]);

  const canEditCell = (field: keyof SplitGridRow, row: SplitGridRow | undefined): boolean => {
    if (!parentTask || !row) return false;
    if (isSavingExistingRows || isSavingExistingRowsRef.current) return false;
    if (field === "assigneeName") {
      if (!row.isExisting) return canEdit;
      const existingTask = taskById.get(row.existingTaskId);
      if (!existingTask) return false;
      return canChangeAssigneeForExistingTask(existingTask);
    }
    if (!canEdit) return false;
    if (field === "actualManHour") {
      return !isResplitMode && !row.isExisting;
    }
    return true;
  };
  // 範囲選択は自前で持つ（AG Grid 無料版には無い）。anchor = 起点、extent = Shift で伸ばした先。
  const selectionRef = useRef<{ anchor: SplitGridCellPosition; extent: SplitGridCellPosition } | null>(null);
  const clipboardRef = useRef<HTMLTextAreaElement | null>(null);
  const selectedRange = (): SplitCellRange | null => {
    const api = gridApiRef.current;
    const selection = selectionRef.current;
    if (selection) return normalizeSplitRange(selection.anchor, selection.extent);
    const focused = api?.getFocusedCell();
    if (!api || !focused) return null;
    const columnIndex = api.getAllDisplayedColumns().findIndex((column) => column.getColId() === focused.column.getColId());
    if (columnIndex < 0) return null;
    return normalizeSplitRange({ rowIndex: focused.rowIndex, columnIndex }, { rowIndex: focused.rowIndex, columnIndex });
  };
  const setSelection = (anchor: SplitGridCellPosition, extent: SplitGridCellPosition) => {
    selectionRef.current = { anchor, extent };
    gridApiRef.current?.refreshCells({ force: true });
  };
  const cellEditableAt = (rowIndex: number, columnIndex: number): boolean => {
    const api = gridApiRef.current;
    const field = api?.getAllDisplayedColumns()[columnIndex]?.getColDef().field;
    const row = api?.getDisplayedRowAtIndex(rowIndex)?.data;
    return isSplitGridEditableField(field) && canEditCell(field, row);
  };
  const cellTextAt = (rowIndex: number, columnIndex: number): string => {
    const api = gridApiRef.current;
    const field = api?.getAllDisplayedColumns()[columnIndex]?.getColDef().field as keyof SplitGridRow | undefined;
    const row = api?.getDisplayedRowAtIndex(rowIndex)?.data;
    return field && row ? normalizeText(row[field]) : "";
  };
  const applyCellUpdates = (updates: SplitCellUpdate[]): void => {
    const api = gridApiRef.current;
    if (!api || updates.length === 0) return;
    api.stopEditing(false);
    const columns = api.getAllDisplayedColumns();
    for (const update of updates) {
      const rowNode = api.getDisplayedRowAtIndex(update.rowIndex);
      const column = columns[update.columnIndex];
      if (rowNode && column) rowNode.setDataValue(column, update.value);
    }
  };
  const applyFill = (direction: "down" | "right"): void => {
    const api = gridApiRef.current;
    if (!api) return;
    // 1クリックで編集が始まるので、Ctrl+D/R は編集中に押されることが多い。
    // 先に編集を確定させ、その反映を待ってから写す。同時に行うと確定した空欄で上書きされる。
    api.stopEditing(false);
    window.setTimeout(() => {
      const range = selectedRange();
      if (!range) return;
      applyCellUpdates(planSplitFill(direction, range, cellTextAt, cellEditableAt));
    }, 0);
  };
  /**
   * コピー・貼り付けはブラウザ自身にさせる。キー操作の瞬間に隠しテキスト欄へフォーカスを移すと、
   * そのままブラウザの既定のコピー・貼り付けがその欄に対して働く。navigator.clipboard と違い
   * https でない社内 LAN（http://IP:8000）でも動く。
   */
  const beginClipboard = (action: "copy" | "paste"): void => {
    const area = clipboardRef.current;
    const range = selectedRange();
    if (!area || !range) return;
    if (action === "copy") {
      const cells: string[][] = [];
      for (let rowIndex = range.top; rowIndex <= range.bottom; rowIndex += 1) {
        const row: string[] = [];
        for (let columnIndex = range.left; columnIndex <= range.right; columnIndex += 1) {
          row.push(cellTextAt(rowIndex, columnIndex));
        }
        cells.push(row);
      }
      area.value = formatSplitClipboardText(cells);
    } else {
      area.value = "";
    }
    area.focus({ preventScroll: true });
    area.select();
    // コピーはその場で済むので、少し後にグリッドへフォーカスを戻す。
    // 貼り付けは paste イベントで戻すが、クリップボードが空などで来なかったときも隠し欄に取り残さない。
    window.setTimeout(
      () => {
        if (document.activeElement === area) restoreGridFocus(range);
      },
      action === "copy" ? 0 : 400
    );
  };
  const restoreGridFocus = (range: SplitCellRange): void => {
    const api = gridApiRef.current;
    const column = api?.getAllDisplayedColumns()[range.left];
    if (api && column) api.setFocusedCell(range.top, column);
  };
  const onClipboardPaste = (event: React.ClipboardEvent<HTMLTextAreaElement>): void => {
    event.preventDefault();
    const api = gridApiRef.current;
    const range = selectedRange();
    if (!api || !range) return;
    const data = parseSplitClipboardText(event.clipboardData.getData("text/plain"));
    applyCellUpdates(planSplitPaste(data, range, rows.length, api.getAllDisplayedColumns().length, cellEditableAt));
    restoreGridFocus(range);
  };
  /**
   * 選択はブラウザの mousedown で即座に記録する。AG Grid の cellClicked は非同期に届くことがあり、
   * クリック直後の Ctrl+V が古い選択範囲へ貼り付けてしまう。
   */
  const onGridMouseDownCapture = (event: React.MouseEvent<HTMLDivElement>): void => {
    const api = gridApiRef.current;
    const cell = (event.target as HTMLElement).closest?.(".ag-cell");
    const row = cell?.closest(".ag-row");
    const colId = cell?.getAttribute("col-id");
    const rowIndex = Number(row?.getAttribute("row-index"));
    if (!api || !colId || !Number.isInteger(rowIndex)) return;
    const columnIndex = api.getAllDisplayedColumns().findIndex((column) => column.getColId() === colId);
    if (columnIndex < 0) return;
    const here = { rowIndex, columnIndex };
    setSelection(event.shiftKey && selectionRef.current ? selectionRef.current.anchor : here, here);
  };
  const onGridReady = useCallback((event: GridReadyEvent<SplitGridRow>) => {
    gridApiRef.current = event.api;
  }, []);

  useEffect(() => {
    // ユーザー切替や画面再遷移時に、担当変更可否のグレーアウト状態を即時反映する。
    gridApiRef.current?.refreshCells({ force: true });
  }, [currentUserId, isCurrentUserTopManager, rows, allTasks]);

  const focusAndStartEditingCell = useCallback((rowIndex: number, columnIndex: number): void => {
    const api = gridApiRef.current;
    if (!api) return;

    const columns = api.getAllDisplayedColumns();
    const column = columns[columnIndex];
    if (!column) return;

    api.ensureIndexVisible(rowIndex);
    api.ensureColumnVisible(column);
    api.setFocusedCell(rowIndex, column);

    const field = column.getColDef().field;
    if (!isSplitGridEditableField(field) || !usesKeyboardNavigationAutoEdit(field)) return;

    const rowNode = api.getDisplayedRowAtIndex(rowIndex);
    const rowData = rowNode?.data;
    if (!canEditCell(field, rowData)) return;

    api.startEditingCell({
      rowIndex,
      colKey: column,
    });
  }, [canEditCell]);

  const onGridKeyDownCapture = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.target === clipboardRef.current) return;
    const shortcut = resolveSplitGridShortcut(event);
    // 1セルだけ選んで編集中なら、Ctrl+C / Ctrl+V はセル内の文字のコピー・貼り付けとしてそのまま使う。
    // 複数セルを選んでいるときは範囲のコピー・貼り付けにする（1クリックで編集が始まるため、範囲選択の最後のセルは編集中になる）。
    const editing = (gridApiRef.current?.getEditingCells().length ?? 0) > 0;
    const currentRange = selectedRange();
    const multiCell = !!currentRange && (currentRange.top !== currentRange.bottom || currentRange.left !== currentRange.right);
    const clipboardAction = shortcut?.action === "copy" || shortcut?.action === "paste";
    if (shortcut && !(editing && clipboardAction && !multiCell)) {
      if (editing && clipboardAction) gridApiRef.current?.stopEditing(false);
      if (shortcut.preventDefault) event.preventDefault();
      if (shortcut.stopPropagation) {
        event.stopPropagation();
      }
      if (shortcut.action === "custom-fill-right") applyFill("right");
      if (shortcut.action === "grid-fill-down") applyFill("down");
      if (shortcut.action === "copy" || shortcut.action === "paste") beginClipboard(shortcut.action);
      return;
    }
    if (shortcut) return;

    const api = gridApiRef.current;
    if (!api) return;
    // Shift+矢印で選択範囲を伸ばす（表計算と同じ）。
    if (event.shiftKey && !event.ctrlKey && !event.metaKey && !event.altKey && event.key.startsWith("Arrow") && !editing) {
      const range = selectedRange();
      const selection = selectionRef.current;
      if (!range) return;
      const anchor = selection?.anchor ?? { rowIndex: range.top, columnIndex: range.left };
      const extent = selection?.extent ?? anchor;
      const columnsCount = api.getAllDisplayedColumns().length;
      const next = {
        rowIndex: Math.max(0, Math.min(rows.length - 1, extent.rowIndex + (event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0))),
        columnIndex: Math.max(1, Math.min(columnsCount - 1, extent.columnIndex + (event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0))),
      };
      event.preventDefault();
      event.stopPropagation();
      setSelection(anchor, next);
      return;
    }
    if (!shouldHandleSplitKeyboardNavigation(event)) return;

    const focusedCell = api.getFocusedCell();
    if (!focusedCell) return;

    const columns = api.getAllDisplayedColumns();
    const columnIndex = columns.findIndex((column) => column.getColId() === focusedCell.column.getColId());
    if (columnIndex < 0) return;

    const nextPosition = resolveNextSplitGridCellPosition(
      {
        rowIndex: focusedCell.rowIndex,
        columnIndex,
      },
      event,
      rows.length,
      columns.length
    );
    if (!nextPosition) return;

    event.preventDefault();
    event.stopPropagation();

    if (api.getEditingCells().length > 0) {
      api.stopEditing(false);
    }

    // 矢印などで移動したら選択は移動先の1セルに戻す。
    selectionRef.current = {
      anchor: { rowIndex: nextPosition.rowIndex, columnIndex: nextPosition.columnIndex },
      extent: { rowIndex: nextPosition.rowIndex, columnIndex: nextPosition.columnIndex },
    };
    api.refreshCells({ force: true });

    window.setTimeout(() => {
      focusAndStartEditingCell(nextPosition.rowIndex, nextPosition.columnIndex);
    }, 0);
  };

  const assigneeErrorTaskIds = useMemo(
    () => new Set(assigneeUpdateErrors.map((error) => error.taskId)),
    [assigneeUpdateErrors]
  );

  const colDefs = useMemo<ColDef<SplitGridRow>[]>(
    () =>
      buildSplitColumnDefs(
        canEditCell,
        users.map((user) => ({
          id: user.Id,
          name: user.Name,
        })),
        {
          rowNo: t("split.row_label", "#"),
          taskName: t("split.column_task_name", "Task Name"),
          mhRatio: t("split.column_mh_ratio", "MH Ratio"),
          actualManHour: t("split.column_actual_mh", "Actual MH"),
          memo: t("split.column_memo", "Memo"),
          dueDate: t("split.column_due_date", "Due Date"),
          assigneeName: t("split.column_assignee", "Assignee"),
        },
        assigneeErrorTaskIds
      ),
    [assigneeErrorTaskIds, canEditCell, t, users]
  );

  const onCellValueChanged = (event: CellValueChangedEvent<SplitGridRow>) => {
    if (!event.data) return;

    const index = event.data.rowNo - 1;
    // 貼り付けや一括入力では1回の操作で何度も呼ばれる。描画前の古い rows ではなく最新の控えから作る。
    const next = [...rowsRef.current];
    const normalized = normalizeRow(event.data);

    if (event.colDef.field === "assigneeName") {
      const matched = users.find((user) => user.Name === normalized.assigneeName.trim());
      normalized.assigneeUserId = matched?.Id ?? "";
    }
    if (normalized.existingTaskId) {
      setAssigneeUpdateErrors((current) =>
        current.filter((error) => error.taskId !== normalized.existingTaskId)
      );
    }

    next[index] = normalized;
    rowsRef.current = next;
    setRows(next);
    persistDraftState(next, parentChildDependencyMode);
  };

  const resolveUsedRows = (): Array<SplitGridRow & { index: number }> => {
    const normalized = rows.map(normalizeRow);

    for (const row of normalized.filter((item) => item.isExisting)) {
      if (row.taskName.trim().length === 0) {
        throw new Error(t("split.existing_task_name_required", "Existing child task names cannot be blank"));
      }
    }

    const used = normalized
      .map((row, index) => ({ ...row, index }))
      .filter((row) => row.taskName.trim().length > 0);

    if (used.length === 0) {
      throw new Error(t("split.rows_required", "No task rows entered"));
    }
    if (used.length < 2) {
      throw new Error(t("split.split_min_rows", "Split requires at least two rows"));
    }

    const last = used[used.length - 1].index;
    for (let index = 0; index <= last; index += 1) {
      if (normalized[index].taskName.trim().length === 0) {
        throw new Error(t("split.blank_line_error", "Blank rows are not allowed between task rows"));
      }
    }

    if (isResplitMode) {
      const existingInUsed = used.filter((row) => row.isExisting).length;
      if (existingInUsed !== existingChildren.length) {
        throw new Error(t("split.resplit_include_all", "Include all existing child tasks during re-split"));
      }
    }

    return used;
  };

  const applyTemplate = () => {
    if (!parentTask || !canEdit) return;
    const template = templates[templateIndex];
    if (!template) {
      return;
    }

    const startIndex = isResplitMode ? existingChildren.length : 0;

    const next = rows.map(normalizeRow);
    template.rows.forEach((row, offset) => {
      const targetIndex = startIndex + offset;
      if (targetIndex >= TOTAL_ROWS) return;
      if (next[targetIndex].isExisting) return;

      next[targetIndex] = {
        ...next[targetIndex],
        taskName: row.task_name,
        mhRatio: String(row.ratio),
        actualManHour: "0",
        memo: "",
        dueDate: parentTask.EndDate,
        assigneeName: parentTask.AssigneeName,
        assigneeUserId: parentTask.AssigneeUserId,
      };
    });
    setRows(next);
    persistDraftState(next, parentChildDependencyMode);
  };

  const equalizeRatios = () => {
    try {
      if (!canEdit) {
        throw new Error(t("split.edit_permission_error", "Only the assignee or Admin can edit"));
      }
      const used = resolveUsedRows();
      const base = Math.floor(10000 / used.length);
      const remain = 10000 % used.length;
      const actuals = !isResplitMode && parentTask ? distributeEvenly(parentTask.ActualManHour, used.length) : [];

      const next = rows.map(normalizeRow);
      used.forEach((row, idx) => {
        const value = (base + (idx < remain ? 1 : 0)) / 100;
        next[row.index] = {
          ...next[row.index],
          mhRatio: formatRatio(value),
          actualManHour: actuals[idx] !== undefined ? String(actuals[idx]) : next[row.index].actualManHour,
        };
      });
      setRows(next);
      persistDraftState(next, parentChildDependencyMode);
    } catch (error) {
      console.warn(error);
    }
  };

  const resolveAssigneeUserId = (row: SplitGridRow): string => {
    const normalizedName = row.assigneeName.trim();
    const normalizedId = row.assigneeUserId.trim();
    if (normalizedName) {
      const matchedByName = users.find((user) => user.Name === normalizedName);
      return matchedByName?.Id ?? "";
    }
    const matchedById = users.find((user) => user.Id === normalizedId);
    if (matchedById) return matchedById.Id;
    return "";
  };

  const executeExistingRowUpdates = async () => {
    if (isSavingExistingRowsRef.current) return;
    isSavingExistingRowsRef.current = true;
    setIsSavingExistingRows(true);
    gridApiRef.current?.stopEditing(false);
    try {
      if (!parentTask) {
        throw new Error(t("split.no_active_task", "No active task"));
      }
      if (!canEdit) {
        throw new Error(t("split.edit_permission_error", "Only the assignee or Admin can edit"));
      }

      const updates: Array<{
        task_id: string;
        expected_revision?: string;
        name: string;
        assigned_manhour: number;
        memo: string;
        end_date: string;
        assignee_user_id: string;
      }> = [];
      const rowsAtSubmit = rowsRef.current.map(normalizeRow);
      const updateByTaskId = new Map<string, typeof updates[number] & { mhRatio: string; assigneeName: string }>();
      const localErrors: AssigneeUpdateError[] = [];
      for (const row of rowsAtSubmit.filter((item) => item.isExisting && item.existingTaskId)) {
        const task = taskById.get(row.existingTaskId);
        if (!task) {
          localErrors.push({
            taskId: row.existingTaskId,
            rowNo: row.rowNo,
            taskName: row.taskName,
            message: t("split.existing_task_missing", "Existing child task was not found"),
          });
          continue;
        }

        const name = row.taskName.trim();
        if (!name) {
          localErrors.push({
            taskId: task.Id,
            rowNo: row.rowNo,
            taskName: task.Name,
            message: t("split.task_name_required", "Enter a task name"),
          });
          continue;
        }

        if (row.mhRatio.includes("%")) {
          localErrors.push({
            taskId: task.Id,
            rowNo: row.rowNo,
            taskName: name,
            message: t("split.ratio_numeric_only", "Enter MH ratio as numbers only"),
          });
          continue;
        }
        const ratio = Number(row.mhRatio);
        if (!Number.isFinite(ratio) || ratio < 0) {
          localErrors.push({
            taskId: task.Id,
            rowNo: row.rowNo,
            taskName: name,
            message: t("split.ratio_non_negative", "Enter MH ratio as 0 or more"),
          });
          continue;
        }
        const baselineRow = baselineRowByTaskId.get(task.Id);
        const ratioWasEdited = row.mhRatio.trim() !== baselineRow?.mhRatio.trim();
        const assignedManHour = ratioWasEdited
          ? computeAssignedMinutes(parentTask.AssignedManHour, [ratio])[0]
          : task.AssignedManHour;

        const endDate = row.dueDate.trim();
        if (!/^\d{4}-\d{2}-\d{2}$/.test(endDate)) {
          localErrors.push({
            taskId: task.Id,
            rowNo: row.rowNo,
            taskName: name,
            message: t("split.due_date_required", "Enter a due date"),
          });
          continue;
        }

        const nextAssigneeUserId = resolveAssigneeUserId(row);
        if (!nextAssigneeUserId) {
          if (row.assigneeName.trim() === task.AssigneeName && row.assigneeUserId.trim() === task.AssigneeUserId) {
            continue;
          }
          localErrors.push({
            taskId: task.Id,
            rowNo: row.rowNo,
            taskName: task.Name,
            message: t("split.invalid_assignee", "Invalid assignee: {name}", {
              name: formatDisplayText(task.Name),
            }),
          });
          continue;
        }
        if (nextAssigneeUserId !== task.AssigneeUserId && !canChangeAssigneeForExistingTask(task)) {
          localErrors.push({
            taskId: task.Id,
            rowNo: row.rowNo,
            taskName: task.Name,
            message: t("split.cannot_change_assignee", "Cannot change assignee: {name}", {
              name: formatDisplayText(task.Name),
            }),
          });
          continue;
        }

        const memo = row.memo.trim();
        const changed = name !== task.Name
          || assignedManHour !== task.AssignedManHour
          || memo !== (task.Memo ?? "")
          || endDate !== task.EndDate
          || nextAssigneeUserId !== task.AssigneeUserId;
        if (!changed) continue;

        const update = {
          task_id: task.Id,
          expected_revision: row.existingTaskRevision,
          name,
          assigned_manhour: assignedManHour,
          memo,
          end_date: endDate,
          assignee_user_id: nextAssigneeUserId,
          mhRatio: row.mhRatio,
          assigneeName: users.find((user) => user.Id === nextAssigneeUserId)?.Name ?? row.assigneeName,
        };
        updates.push(update);
        updateByTaskId.set(task.Id, update);
      }

      if (updates.length === 0) {
        setAssigneeUpdateErrors(localErrors);
        if (localErrors.length > 0) {
          showExecutionMessage(
            t("split.existing_update_failed_summary", "Existing task update failed for {failed} row(s)", {
              failed: localErrors.length,
            })
          );
        } else {
          showExecutionMessage(t("split.no_existing_changes", "There are no existing task changes to save"));
        }
        return;
      }

      const result = await onUpdateExistingRows(updates, parentTask.Id, parentTaskRevision);
      const rowByTaskId = new Map(
        rowsAtSubmit.filter((row) => row.existingTaskId).map((row) => [row.existingTaskId, row])
      );
      const serverErrors: AssigneeUpdateError[] = result.results
        .filter((row) => !row.success)
        .map((row) => {
          const gridRow = rowByTaskId.get(row.task_id);
          return {
            taskId: row.task_id,
            rowNo: gridRow?.rowNo ?? 0,
            taskName: gridRow?.taskName ?? row.task_id,
            message: row.message,
          };
        });
      const errors = [...localErrors, ...serverErrors];
      setAssigneeUpdateErrors(errors);

      const successfulResults = new Map(
        result.results.filter((row) => row.success).map((row) => [row.task_id, row])
      );
      const conflictResults = new Map(
        result.results
          .filter((row) => !row.success && row.code === "task_conflict" && row.current && row.revision)
          .map((row) => [row.task_id, row])
      );
      const applyServerResults = (source: SplitGridRow[]): SplitGridRow[] => source.map((row) => {
        const successful = successfulResults.get(row.existingTaskId);
        const update = updateByTaskId.get(row.existingTaskId);
        if (successful && update) {
          return {
            ...normalizeRow(row),
            taskName: update.name,
            mhRatio: update.mhRatio,
            memo: update.memo,
            dueDate: update.end_date,
            assigneeUserId: update.assignee_user_id,
            assigneeName: update.assigneeName,
            existingTaskRevision: successful.revision ?? row.existingTaskRevision,
          };
        }

        const conflict = conflictResults.get(row.existingTaskId);
        if (conflict?.current && conflict.revision) {
          const current = conflict.current;
          return {
            ...normalizeRow(row),
            taskName: current.name,
            mhRatio: formatRatio((current.assigned_manhour * 100) / Math.max(1, parentTask.AssignedManHour)),
            memo: current.memo,
            dueDate: current.end_date,
            assigneeUserId: current.assignee_user_id,
            assigneeName: users.find((user) => user.Id === current.assignee_user_id)?.Name ?? row.assigneeName,
            existingTaskRevision: conflict.revision,
          };
        }
        return normalizeRow(row);
      });
      const nextRows = applyServerResults(rowsAtSubmit);
      const nextBaselineRows = applyServerResults(baselineRows);
      rowsRef.current = nextRows;
      setRows(nextRows);
      const nextParentTaskRevision = result.parent_revision ?? parentTaskRevision;
      setParentTaskRevision(nextParentTaskRevision);
      persistDraftState(
        nextRows,
        parentChildDependencyMode,
        nextBaselineRows,
        baselineDependencyMode,
        nextParentTaskRevision
      );

      const warnings = result.results
        .filter((row) => row.success)
        .flatMap((row) => row.warnings ?? []);
      const feedbackMessages = [...new Set(warnings)];
      if (errors.length > 0) {
        feedbackMessages.push(
          t(
            "split.existing_update_partial_summary",
            "Existing task update: {changed} succeeded, {failed} failed",
            { changed: result.changed, failed: errors.length }
          )
        );
      }
      if (feedbackMessages.length > 0) showExecutionMessage(feedbackMessages.join("\n"));
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        clearDraft();
        showExecutionMessage(
          `${error.message}\n\n${t("split.conflict_draft_cancelled", "The stale draft was cancelled. Review the latest values before editing again.")}`
        );
      } else {
        showExecutionMessage(error instanceof Error ? error.message : String(error));
      }
    } finally {
      isSavingExistingRowsRef.current = false;
      setIsSavingExistingRows(false);
    }
  };

  const canRunExistingUpdate = useMemo(() => {
    if (!parentTask || !canEdit || isSavingExistingRows) return false;
    for (const row of rows) {
      if (!row.isExisting || !row.existingTaskId) continue;
      const task = taskById.get(row.existingTaskId);
      if (!task) continue;
      const normalized = normalizeRow(row);
      const nextAssigneeUserId = resolveAssigneeUserId(normalized);
      const ratio = Number(normalized.mhRatio);
      const baselineRow = baselineRowByTaskId.get(task.Id);
      const ratioWasEdited = normalized.mhRatio.trim() !== baselineRow?.mhRatio.trim();
      const assignedManHour = ratioWasEdited && Number.isFinite(ratio) && ratio >= 0
        ? computeAssignedMinutes(parentTask.AssignedManHour, [ratio])[0]
        : task.AssignedManHour;
      const displayedValueChanged = normalized.taskName.trim() !== task.Name
        || ratioWasEdited
        || assignedManHour !== task.AssignedManHour
        || normalized.memo.trim() !== (task.Memo ?? "")
        || normalized.dueDate.trim() !== task.EndDate
        || normalized.assigneeName.trim() !== task.AssigneeName
        || (!!nextAssigneeUserId && nextAssigneeUserId !== task.AssigneeUserId);
      if (displayedValueChanged) {
        return true;
      }
    }
    return false;
  }, [baselineRowByTaskId, canEdit, isSavingExistingRows, parentTask, rows, taskById, users]);

  const executeSplit = async () => {
    try {
      if (!parentTask) {
        throw new Error(t("split.no_active_task", "No active task"));
      }
      if (!canEdit) {
        throw new Error(t("split.split_permission_error", "Only the assignee or Admin can split"));
      }

      const used = resolveUsedRows();

      const ratios = used.map((row) => {
        if (row.mhRatio.includes("%")) {
          throw new Error(t("split.ratio_numeric_only", "Enter MH ratio as numbers only"));
        }
        const value = Number(row.mhRatio);
        if (!Number.isFinite(value)) {
          throw new Error(t("split.ratio_number_only", "Enter MH ratio as numeric values"));
        }
        if (value < 0) {
          throw new Error(t("split.ratio_non_negative", "Enter MH ratio as 0 or more"));
        }
        return value;
      });

      const assigned = computeAssignedMinutes(parentTask.AssignedManHour, ratios);

      const actuals = used.map((row) => {
        const value = Number(row.actualManHour);
        if (!Number.isFinite(value) || !Number.isInteger(value) || value < 0) {
          throw new Error(t("split.actual_mh_integer_error", "Actual MH must be an integer of 0 or more"));
        }
        return value;
      });
      const actualTotal = actuals.reduce((sum, value) => sum + value, 0);
      if (!isResplitMode && actualTotal !== parentTask.ActualManHour) {
        throw new Error(t("split.actual_total_error", "Actual MH total must match the parent task actual MH"));
      }

      const children = used.map((row, idx) => {
        const dueDate = (row.dueDate || parentTask.EndDate).trim();
        if (!dueDate) {
          throw new Error(t("split.due_date_required", "Enter a due date"));
        }

        let assigneeUserId = "";
        let actualManHour = actuals[idx];

        if (row.isExisting) {
          if (!row.existingTaskId) {
            throw new Error(t("split.existing_task_id_invalid", "Existing child task identifier is invalid"));
          }
          const existingTask = taskById.get(row.existingTaskId);
          if (!existingTask) {
            throw new Error(t("split.existing_task_missing", "Existing child task was not found"));
          }
          // 既存子タスクの担当者変更は「担当者変更の実行」ボタンで行う。
          assigneeUserId = existingTask.AssigneeUserId;
        } else {
          const assignee = users.find((user) => user.Name === row.assigneeName.trim())
            ?? users.find((user) => user.Id === row.assigneeUserId.trim());
          if (!assignee) {
            throw new Error(t("split.assignee_select_error", "Select a valid assignee"));
          }
          assigneeUserId = assignee.Id;
          if (isResplitMode) actualManHour = 0;
        }

        return {
          ...(row.isExisting ? { ExistingTaskId: row.existingTaskId } : {}),
          ...(row.isExisting && row.existingTaskId
            ? { ExpectedRevision: row.existingTaskRevision }
            : {}),
          Name: row.taskName.trim(),
          AssignedManHour: assigned[idx],
          ActualManHour: actualManHour,
          EndDate: dueDate,
          Memo: row.memo.trim(),
          AssigneeUserId: assigneeUserId,
        };
      });

      const result = await onExecute(children, parentChildDependencyMode, parentTaskRevision);
      if (result.saved || result.conflict) {
        // 409時は古いリビジョンを保持した下書きを破棄し、再取得済みの基準へ戻す。
        clearDraft();
      }
    } catch (error) {
      showExecutionMessage(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <div className="view-stack">
      <div className="subtext">
        {parentTask
          ? t("split.target_summary", "Target: {name} / Estimate:{assigned} min / Actual:{actual} min / Due:{dueDate}", {
            name: formatDisplayText(parentTask.Name),
            assigned: parentTask.AssignedManHour,
            actual: parentTask.ActualManHour,
            dueDate: parentTask.EndDate,
          })
          : t("split.no_active_task", "No active task")}
      </div>
      <div className="subtext warning-guidance">
        {t(
          "split.due_date_free",
          "Due dates can be entered freely. Parent dates are adjusted automatically where possible, and remaining inconsistencies are reported after saving."
        )}
      </div>

      <div className="toolbar-row">
        <label className="subtext" htmlFor="parent-dependency-mode">
          {t("split.parent_dependency", "Parent Dependency")}
        </label>
        <select
          id="parent-dependency-mode"
          value={parentChildDependencyMode}
          onChange={(e) => {
            const nextMode = e.currentTarget.value as ChildDependencyMode;
            setParentChildDependencyMode(nextMode);
            persistDraftState(rows, nextMode);
          }}
          disabled={!parentTask || !canEdit}
        >
          <option value="Serial">{t("common.serial", "Serial")}</option>
          <option value="Parallel">{t("common.parallel", "Parallel")}</option>
        </select>
        <select
          value={String(templateIndex)}
          onChange={(e) => setTemplateIndex(Number(e.currentTarget.value || "0"))}
          disabled={!parentTask || !canEdit}
        >
          {templates.map((template, index) => (
            <option key={template.title} value={index}>
              {template.title}
            </option>
          ))}
        </select>
        <button className="btn primary" type="button" disabled={!parentTask || !canEdit} onClick={applyTemplate}>
          {t("split.apply_template", "Apply Template")}
        </button>
        <button className="btn" type="button" disabled={!parentTask || !canEdit} onClick={equalizeRatios}>
          {t("split.equalize_mh", "Equalize MH")}
        </button>
        <button
          className="btn primary"
          type="button"
          disabled={!parentTask || !canEdit}
          onClick={() => void executeSplit()}
        >
          {t("split.execute_split", "Execute Split")}
        </button>
        <button
          className="btn"
          type="button"
          disabled={!canRunExistingUpdate}
          onClick={() => void executeExistingRowUpdates()}
        >
          {t("split.execute_existing_update", "Save Existing Task Changes")}
        </button>
        <button
          className="btn split-discard-draft"
          type="button"
          disabled={!hasDraft}
          onClick={() => {
            clearDraft();
            showExecutionMessage(t("split.draft_discarded", "Split draft was discarded"));
          }}
        >
          {t("split.discard_draft", "Discard Draft")}
        </button>
      </div>
      {hasDraft ? (
        <div className="split-draft-notice" role="status">
          {t("split.draft_saved", "Draft saved for this parent task")}
        </div>
      ) : null}
      {!canEdit && parentTask ? <div className="subtext">{t("split.edit_rule", "Only the parent task assignee or Admin can edit splits")}</div> : null}
      <div className="subtext">{t("split.assignee_rule", "Existing name, MH ratio, memo, due date, and assignee changes can be saved without executing a split. Assignee changes to leaf tasks require actual MH to be 0.")}</div>

      <div className="subtext">
        {t("split.columns_hint", "Columns: Task Name / MH Ratio / Actual MH / Memo / Due Date / Assignee")}
      </div>
      <div className="subtext">
        {t("split.clipboard_hint", "Clipboard: Ctrl+C / Ctrl+V / Ctrl+D (fill down) / Ctrl+R (fill right)")}
      </div>
      {isResplitMode ? (
        <div className="subtext">{t("split.resplit_hint", "Existing child tasks appear at the top. Existing children keep assignee and actual MH fixed, while name, memo, due date, and MH ratio remain editable.")}</div>
      ) : (
        <div className="subtext">{t("split.actual_hint", "Actual MH is entered per child. The child total must match the parent actual MH; zero-actual child tasks are allowed.")}</div>
      )}

      {assigneeUpdateErrors.length > 0 ? (
        <div className="split-assignee-errors" role="alert">
          <div className="split-assignee-errors-title">
            {t("split.assignee_update_errors_title", "Rows that could not be updated")}
          </div>
          <ul>
            {assigneeUpdateErrors.map((error) => (
              <li key={`${error.taskId}:${error.message}`}>
                {t("split.assignee_update_error_row", "Row {row}: {task} - {message}", {
                  row: error.rowNo || "-",
                  task: formatDisplayText(error.taskName),
                  message: error.message,
                })}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div
        className={`ag-theme-quartz split-grid${hasDraft ? " split-grid-draft" : ""}`}
        aria-label="split grid"
        onKeyDownCapture={onGridKeyDownCapture}
        onMouseDownCapture={onGridMouseDownCapture}
      >
        {/* コピー・貼り付けの受け渡し用。画面には見せない。 */}
        <textarea
          ref={clipboardRef}
          className="split-clipboard"
          aria-hidden="true"
          tabIndex={-1}
          onPaste={onClipboardPaste}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              const range = selectedRange();
              if (range) restoreGridFocus(range);
            }
          }}
          readOnly={false}
        />
        <AgGridReact<SplitGridRow>
          rowData={rows}
          onGridReady={onGridReady}
          getRowId={getRowId}
          getRowClass={(params) =>
            params.data?.existingTaskId && assigneeErrorTaskIds.has(params.data.existingTaskId)
              ? "split-row-error"
              : undefined
          }
          columnDefs={colDefs}
          defaultColDef={{
            sortable: false,
            filter: false,
            resizable: true,
            cellClassRules: {
              "split-cell-selected": (params) => {
                const selection = selectionRef.current;
                if (!selection || params.node.rowIndex == null) return false;
                const range = normalizeSplitRange(selection.anchor, selection.extent);
                if (range.top === range.bottom && range.left === range.right) return false;
                const columnIndex = params.api
                  .getAllDisplayedColumns()
                  .findIndex((column) => column.getColId() === params.column.getColId());
                return isInSplitRange(range, params.node.rowIndex, columnIndex);
              },
            },
          }}
          enterNavigatesVertically
          enterNavigatesVerticallyAfterEdit
          tabToNextCell={tabToNextCell}
          suppressScrollOnNewData
          stopEditingWhenCellsLoseFocus
          onCellValueChanged={onCellValueChanged}
          suppressDragLeaveHidesColumns
          suppressMovableColumns
        />
      </div>
    </div>
  );
}













