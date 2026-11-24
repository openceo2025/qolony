import assert from "node:assert/strict";
import test from "node:test";

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
  prepareSplitClipboardData,
  resolveNextSplitGridCellPosition,
  resolveSplitGridShortcut,
  serializeSplitGridDraft,
  shouldHandleSplitKeyboardNavigation,
  usesImeSafeSingleClickEdit,
  usesImeSafeSuppressPreventDefault,
  usesKeyboardNavigationAutoEdit,
} from "../src/lib/splitGridEditing.ts";

const createRow = (overrides = {}) => ({
  rowNo: 1,
  taskName: "Task A",
  mhRatio: "100",
  actualManHour: "0",
  memo: "",
  dueDate: "2026-07-31",
  assigneeName: "User A",
  assigneeUserId: "user-a",
  isExisting: true,
  existingTaskId: "task-a",
  existingTaskRevision: "revision-a",
  ...overrides,
});

test("resolveSplitGridShortcut reserves Ctrl+D for an explicit AG Grid fill-down", () => {
  const fillDown = resolveSplitGridShortcut({
    key: "d",
    altKey: false,
    ctrlKey: true,
    metaKey: false,
    shiftKey: false,
  });
  assert.deepEqual(fillDown, {
    action: "grid-fill-down",
    preventDefault: true,
    stopPropagation: true,
  });

  assert.deepEqual(
    resolveSplitGridShortcut({
      key: "D",
      altKey: false,
      ctrlKey: false,
      metaKey: true,
      shiftKey: false,
    }),
    fillDown
  );
  assert.deepEqual(
    resolveSplitGridShortcut({
      key: "r",
      altKey: false,
      ctrlKey: true,
      metaKey: false,
      shiftKey: false,
    }),
    {
      action: "custom-fill-right",
      preventDefault: true,
      stopPropagation: true,
    }
  );
  assert.equal(
    resolveSplitGridShortcut({
      key: "d",
      altKey: true,
      ctrlKey: true,
      metaKey: false,
      shiftKey: false,
    }),
    null
  );
});

test("split drafts round-trip per user and parent and detect baseline changes", () => {
  const baselineRows = [createRow()];
  const draftRows = [createRow({ memo: "unfinished note" })];
  const draft = {
    version: 2,
    parentTaskId: "parent/1",
    parentTaskRevision: "parent-revision-1",
    rows: draftRows,
    parentChildDependencyMode: "Parallel",
  };

  assert.equal(
    createSplitDraftStorageKey("parent/1", "user@example.com"),
    "qolony:split-draft:v2:user%40example.com:parent%2F1"
  );
  assert.deepEqual(parseSplitGridDraft(serializeSplitGridDraft(draft), "parent/1"), draft);
  assert.equal(parseSplitGridDraft(serializeSplitGridDraft(draft), "other-parent"), null);
  assert.equal(parseSplitGridDraft("not-json", "parent/1"), null);
  assert.equal(hasSplitGridDraftChanges(baselineRows, "Serial", baselineRows, "Serial"), false);
  assert.equal(hasSplitGridDraftChanges(draftRows, "Serial", baselineRows, "Serial"), true);
  assert.equal(hasSplitGridDraftChanges(baselineRows, "Parallel", baselineRows, "Serial"), true);
});

test("usesImeSafeSingleClickEdit returns true for IME-sensitive fields", () => {
  assert.equal(usesImeSafeSingleClickEdit("taskName"), true);
  assert.equal(usesImeSafeSingleClickEdit("mhRatio"), true);
  assert.equal(usesImeSafeSingleClickEdit("memo"), true);
  assert.equal(usesImeSafeSingleClickEdit("dueDate"), true);
  assert.equal(usesImeSafeSingleClickEdit("assigneeName"), true);
  assert.equal(usesImeSafeSingleClickEdit("actualManHour"), true);
});

test("usesKeyboardNavigationAutoEdit applies to editable keyboard targets", () => {
  assert.equal(usesKeyboardNavigationAutoEdit("taskName"), true);
  assert.equal(usesKeyboardNavigationAutoEdit("mhRatio"), true);
  assert.equal(usesKeyboardNavigationAutoEdit("memo"), true);
  assert.equal(usesKeyboardNavigationAutoEdit("dueDate"), true);
  assert.equal(usesKeyboardNavigationAutoEdit("assigneeName"), true);
  assert.equal(usesKeyboardNavigationAutoEdit("actualManHour"), true);
});

test("Ctrl+C / Ctrl+V はブラウザ既定のコピー・貼り付けを止めずに受け取る", () => {
  const key = (k) => resolveSplitGridShortcut({ key: k, altKey: false, ctrlKey: true, metaKey: false, shiftKey: false });
  assert.deepEqual(key("c"), { action: "copy", preventDefault: false, stopPropagation: true });
  assert.deepEqual(key("V"), { action: "paste", preventDefault: false, stopPropagation: true });
  assert.equal(resolveSplitGridShortcut({ key: "c", altKey: false, ctrlKey: true, metaKey: false, shiftKey: true }), null);
});

test("範囲は起点と終点の向きに依存せず正規化される", () => {
  const range = normalizeSplitRange({ rowIndex: 4, columnIndex: 5 }, { rowIndex: 1, columnIndex: 2 });
  assert.deepEqual(range, { top: 1, bottom: 4, left: 2, right: 5 });
  assert.equal(isInSplitRange(range, 2, 3), true);
  assert.equal(isInSplitRange(range, 0, 3), false);
  assert.equal(isInSplitRange(null, 2, 3), false);
});

test("表計算ソフトのタブ区切りを読み書きできる（引用符内の改行・タブも保つ）", () => {
  assert.deepEqual(parseSplitClipboardText("A\tB\r\n1\t2\r\n"), [
    ["A", "B"],
    ["1", "2"],
  ]);
  assert.deepEqual(parseSplitClipboardText("\"line1\nline2\"\t\"say \"\"hi\"\"\""), [["line1\nline2", "say \"hi\""]]);
  const cells = [
    ["x", "tab\there"],
    ["\"q\"", ""],
  ];
  assert.deepEqual(parseSplitClipboardText(formatSplitClipboardText(cells)), cells);
});

test("貼り付けは選択の左上から広げ、表の外と編集できないセルは飛ばす", () => {
  const updates = planSplitPaste(
    [
      ["a", "b", "c"],
      ["d", "e", "f"],
    ],
    { top: 1, bottom: 1, left: 1, right: 1 },
    2,
    3,
    (row, col) => !(row === 1 && col === 2)
  );
  assert.deepEqual(updates, [
    { rowIndex: 1, columnIndex: 1, value: "a" },
  ]);
  // 1セルだけをコピーして範囲に貼ると、範囲全体へ繰り返す
  const filled = planSplitPaste([["z"]], { top: 0, bottom: 1, left: 1, right: 2 }, 5, 5, () => true);
  assert.equal(filled.length, 4);
  assert.ok(filled.every((u) => u.value === "z"));
});

test("Ctrl+D は先頭行を下へ、Ctrl+R は左端列を右へ写す", () => {
  const grid = [
    ["r0c0", "r0c1", "r0c2"],
    ["r1c0", "r1c1", "r1c2"],
  ];
  const at = (r, c) => grid[r][c];
  const range = { top: 0, bottom: 1, left: 0, right: 2 };
  assert.deepEqual(
    planSplitFill("down", range, at, (r, c) => c !== 2),
    [
      { rowIndex: 1, columnIndex: 0, value: "r0c0" },
      { rowIndex: 1, columnIndex: 1, value: "r0c1" },
    ]
  );
  assert.deepEqual(
    planSplitFill("right", range, at, () => true).map((u) => u.value),
    ["r0c0", "r0c0", "r1c0", "r1c0"]
  );
});

test("prepareSplitClipboardData removes only trailing blank rows", () => {
  assert.deepEqual(
    prepareSplitClipboardData([
      ["A", "B"],
      ["1", "2"],
      ["", ""],
      ["", ""],
    ]),
    [
      ["A", "B"],
      ["1", "2"],
    ]
  );
  assert.deepEqual(
    prepareSplitClipboardData([
      ["A", ""],
      ["", "still-kept"],
      ["", ""],
    ]),
    [
      ["A", ""],
      ["", "still-kept"],
    ]
  );
});

test("isSplitGridEditableField narrows only split-editable columns", () => {
  assert.equal(isSplitGridEditableField("taskName"), true);
  assert.equal(isSplitGridEditableField("actualManHour"), true);
  assert.equal(isSplitGridEditableField("rowNo"), false);
  assert.equal(isSplitGridEditableField(undefined), false);
});

test("shouldHandleSplitKeyboardNavigation reacts to plain arrows, enter, and tab", () => {
  assert.equal(
    shouldHandleSplitKeyboardNavigation({
      key: "ArrowRight",
      altKey: false,
      ctrlKey: false,
      metaKey: false,
      shiftKey: false,
    }),
    true
  );
  assert.equal(
    shouldHandleSplitKeyboardNavigation({
      key: "ArrowRight",
      altKey: false,
      ctrlKey: false,
      metaKey: false,
      shiftKey: true,
    }),
    false
  );
  assert.equal(
    shouldHandleSplitKeyboardNavigation({
      key: "Enter",
      altKey: false,
      ctrlKey: false,
      metaKey: false,
      shiftKey: true,
    }),
    true
  );
  assert.equal(
    shouldHandleSplitKeyboardNavigation({
      key: "Tab",
      altKey: false,
      ctrlKey: false,
      metaKey: false,
      shiftKey: true,
    }),
    true
  );
  assert.equal(
    shouldHandleSplitKeyboardNavigation({
      key: "a",
      altKey: false,
      ctrlKey: false,
      metaKey: false,
      shiftKey: false,
    }),
    false
  );
});

test("resolveNextSplitGridCellPosition moves with arrow keys without wrapping", () => {
  assert.deepEqual(
    resolveNextSplitGridCellPosition(
      { rowIndex: 3, columnIndex: 2 },
      { key: "ArrowLeft", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false },
      10,
      6
    ),
    { rowIndex: 3, columnIndex: 1 }
  );
  assert.deepEqual(
    resolveNextSplitGridCellPosition(
      { rowIndex: 3, columnIndex: 2 },
      { key: "ArrowDown", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false },
      10,
      6
    ),
    { rowIndex: 4, columnIndex: 2 }
  );
  assert.equal(
    resolveNextSplitGridCellPosition(
      { rowIndex: 0, columnIndex: 0 },
      { key: "ArrowUp", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false },
      10,
      6
    ),
    null
  );
});

test("resolveNextSplitGridCellPosition moves vertically on enter", () => {
  assert.deepEqual(
    resolveNextSplitGridCellPosition(
      { rowIndex: 3, columnIndex: 2 },
      { key: "Enter", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false },
      10,
      6
    ),
    { rowIndex: 4, columnIndex: 2 }
  );
  assert.deepEqual(
    resolveNextSplitGridCellPosition(
      { rowIndex: 3, columnIndex: 2 },
      { key: "Enter", altKey: false, ctrlKey: false, metaKey: false, shiftKey: true },
      10,
      6
    ),
    { rowIndex: 2, columnIndex: 2 }
  );
});

test("resolveNextSplitGridCellPosition wraps like tab navigation", () => {
  assert.deepEqual(
    resolveNextSplitGridCellPosition(
      { rowIndex: 3, columnIndex: 5 },
      { key: "Tab", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false },
      10,
      6
    ),
    { rowIndex: 4, columnIndex: 0 }
  );
  assert.deepEqual(
    resolveNextSplitGridCellPosition(
      { rowIndex: 3, columnIndex: 0 },
      { key: "Tab", altKey: false, ctrlKey: false, metaKey: false, shiftKey: true },
      10,
      6
    ),
    { rowIndex: 2, columnIndex: 5 }
  );
  assert.equal(
    resolveNextSplitGridCellPosition(
      { rowIndex: 0, columnIndex: 0 },
      { key: "Tab", altKey: false, ctrlKey: false, metaKey: false, shiftKey: true },
      10,
      6
    ),
    null
  );
});

test("buildSplitColumnDefs enables singleClickEdit on editable input columns", () => {
  const defs = buildSplitColumnDefs(() => true, [{ id: "user-1", name: "山田" }]);
  const byField = new Map(defs.map((def) => [String(def.field), def]));

  for (const field of ["taskName", "mhRatio", "actualManHour", "memo", "dueDate", "assigneeName"]) {
    assert.equal(byField.get(field)?.singleClickEdit, true, `${field} should use singleClickEdit`);
  }

  assert.notEqual(byField.get("rowNo")?.singleClickEdit, true);
});

test("buildSplitColumnDefs applies localized headers", () => {
  const defs = buildSplitColumnDefs(
    () => true,
    [{ id: "user-1", name: "山田" }],
    {
      rowNo: "番号",
      taskName: "タスク名",
      mhRatio: "MH比率",
      actualManHour: "実績MH",
      memo: "メモ",
      dueDate: "期限",
      assigneeName: "担当",
    }
  );
  const byField = new Map(defs.map((def) => [String(def.field), def]));

  assert.equal(byField.get("rowNo")?.headerName, "番号");
  assert.equal(byField.get("taskName")?.headerName, "タスク名");
  assert.equal(byField.get("assigneeName")?.headerName, "担当");
});

test("usesImeSafeSuppressPreventDefault applies only to text-like editors", () => {
  assert.equal(usesImeSafeSuppressPreventDefault("taskName"), true);
  assert.equal(usesImeSafeSuppressPreventDefault("mhRatio"), true);
  assert.equal(usesImeSafeSuppressPreventDefault("memo"), true);
  assert.equal(usesImeSafeSuppressPreventDefault("dueDate"), true);
  assert.equal(usesImeSafeSuppressPreventDefault("assigneeName"), false);
  assert.equal(usesImeSafeSuppressPreventDefault("actualManHour"), true);
});

test("buildSplitColumnDefs enables suppressPreventDefault for printable-key editing columns", () => {
  const defs = buildSplitColumnDefs(() => true, [{ id: "user-1", name: "山田" }]);
  const byField = new Map(defs.map((def) => [String(def.field), def]));

  for (const field of ["taskName", "mhRatio", "actualManHour", "memo", "dueDate"]) {
    assert.equal(
      byField.get(field)?.cellEditorParams?.suppressPreventDefault,
      true,
      `${field} should suppress default printable-key injection`
    );
  }

  assert.notEqual(byField.get("assigneeName")?.cellEditorParams?.suppressPreventDefault, true);
});

test("buildSplitColumnDefs marks failed assignee cells red by task id", () => {
  const defs = buildSplitColumnDefs(
    () => true,
    [{ id: "user-a", name: "User A" }],
    {},
    new Set(["task-a"])
  );
  const assigneeDef = defs.find((def) => def.field === "assigneeName");
  assert.equal(typeof assigneeDef?.cellClass, "function");
  assert.equal(assigneeDef.cellClass({ data: createRow() }), "split-cell-error");
  assert.equal(
    assigneeDef.cellClass({ data: createRow({ existingTaskId: "task-b" }) }),
    ""
  );
});
