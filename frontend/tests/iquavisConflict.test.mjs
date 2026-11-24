import assert from "node:assert/strict";
import test from "node:test";

import {
  buildIquavisConflictMap,
  findUnsupportedIquavisExcelStatus,
  formatIquavisConflictValue,
  getIquavisConflictFieldLabel,
  IQUAVIS_CONFLICT_BASELINE_GUIDANCE,
  IQUAVIS_CONFLICT_VALUE_LABELS,
  IQUAVIS_PENDING_RECOVERY_GUIDANCE,
  isConflictHold,
  normalizeIquavisConflictResponse,
  patchTaskConflictState,
} from "../src/lib/iquavisConflict.ts";

function task(id, syncState = "Linked") {
  return {
    Id: id,
    Name: `Task ${id}`,
    IQuavisTaskId: `IQ-${id}`,
    IQuavisSyncState: syncState,
    IQuavisConflictMessage: syncState === "Conflict" ? "競合しています" : null,
  };
}

test("normalizeIquavisConflictResponse accepts the backend items contract", () => {
  const response = normalizeIquavisConflictResponse({
    count: 1,
    items: [
      {
        task_id: "T1",
        iquavis_id: "IQ-1",
        task_name: "Task 1",
        sync_state: "Conflict",
        message: "期限が競合しています",
        recorded_at: "2026-07-16T01:00:00Z",
        workbook_fingerprint: "abc",
        fields: [{ field: "end_date", baseline: "2026-07-10", qolony: "2026-07-12", excel: "2026-07-15" }],
      },
    ],
  });

  assert.equal(response.count, 1);
  assert.equal(response.items[0].fields[0].field, "end_date");
  assert.equal(response.items[0].recorded_at, "2026-07-16T01:00:00Z");
});

test("buildIquavisConflictMap falls back to Task state while details load", () => {
  const map = buildIquavisConflictMap([task("T1", "Conflict")], undefined);

  assert.equal(map.get("T1")?.sync_state, "Conflict");
  assert.equal(map.get("T1")?.recorded_at, null);
  assert.deepEqual(map.get("T1")?.fields, []);
});

test("buildIquavisConflictMap applies optimistic pending and resolved overrides", () => {
  const source = normalizeIquavisConflictResponse({
    items: [
      {
        task_id: "T1",
        task_name: "Task 1",
        sync_state: "Conflict",
        recorded_at: "2026-07-16T01:00:00Z",
        fields: [],
      },
    ],
  });
  const pending = { ...source.items[0], sync_state: "PendingIquavisSave" };

  assert.equal(buildIquavisConflictMap([], source, new Map([["T1", pending]])).get("T1")?.sync_state, "PendingIquavisSave");
  assert.equal(buildIquavisConflictMap([], source, new Map([["T1", null]])).has("T1"), false);
});

test("patchTaskConflictState changes only the selected task for optimistic UI and rollback snapshots", () => {
  const tasks = [task("T1", "Conflict"), task("T2", "Conflict")];
  const next = patchTaskConflictState(tasks, "T1", "PendingIquavisSave", "Excel保存待ち");

  assert.equal(next?.[0].IQuavisSyncState, "PendingIquavisSave");
  assert.equal(next?.[0].IQuavisConflictMessage, "Excel保存待ち");
  assert.equal(next?.[1], tasks[1]);
});

test("conflict labels and values are compact and display-safe", () => {
  assert.equal(getIquavisConflictFieldLabel("actual_minutes"), "実績工数");
  assert.equal(getIquavisConflictFieldLabel("custom"), "custom");
  assert.equal(formatIquavisConflictValue(null), "—");
  assert.equal(formatIquavisConflictValue(""), "（空）");
});

test("Abort is a supported iQUAVIS status and only truly unknown statuses restrict Excel adoption", () => {
  const abort = { field: "status", baseline: "Abort", qolony: "NotStart", excel: "Abort" };
  const unknown = { field: "status", baseline: "Paused", qolony: "NotStart", excel: "Paused" };
  const supported = { field: "status", baseline: "NotStart", qolony: "DuringStart", excel: "Completion" };

  assert.equal(findUnsupportedIquavisExcelStatus([abort]), undefined);
  assert.equal(findUnsupportedIquavisExcelStatus([unknown]), unknown);
  assert.equal(findUnsupportedIquavisExcelStatus([supported]), undefined);
});

test("conflict guidance distinguishes the saved baseline, current QOLONY, and displayed Excel values", () => {
  assert.deepEqual(IQUAVIS_CONFLICT_VALUE_LABELS, {
    baseline: "前回iQUAVIS取得/保存成功値",
    qolony: "QOLONY現在値",
    excel: "Excel表示値",
  });
  assert.match(IQUAVIS_CONFLICT_BASELINE_GUIDANCE, /現在のiQUAVISを直接照会した値ではありません/);
  assert.match(IQUAVIS_PENDING_RECOVERY_GUIDANCE, /その0を「サーバーへ保存」しない/);
  assert.match(IQUAVIS_PENDING_RECOVERY_GUIDANCE, /再取得.*全タスク作成.*保存終了.*iQUAVIS→QOLONY 初期取込/);
});

test("isConflictHold detects hold state", () => {
  const task = {
    task_id: "T1",
    iquavis_id: "IQ-1",
    task_name: "Task 1",
    sync_state: "ConflictHold",
    message: "",
    recorded_at: "",
    workbook_fingerprint: null,
    fields: [],
  };

  assert.equal(isConflictHold(task), true);
  assert.equal(isConflictHold({ ...task, sync_state: "Conflict" }), false);
});

