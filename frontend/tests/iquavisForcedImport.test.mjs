import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { api } from "../src/api/client.ts";
import {
  canApplyIquavisForcedImport,
  formatForcedImportApplyStatus,
  formatForcedImportConfirmation,
  formatForcedImportFieldCounts,
  formatForcedImportPreviewSummary,
  getForcedImportFieldLabel,
} from "../src/lib/iquavisForcedImport.ts";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function preview(overrides = {}) {
  return {
    workbook_name: "iquavis_editer_latest_ver.xlsm",
    workbook_fingerprint: "sha256:workbook-v1",
    scanned_task_count: 4068,
    new_task_count: 174,
    updated_task_count: 592,
    unchanged_task_count: 3302,
    conflict_task_count: 0,
    local_only_task_count: 1,
    excluded_record_count: 5292,
    missing_qolony_baseline_count: 3894,
    missing_workbook_baseline_count: 0,
    duplicate_local_link_count: 0,
    adopted_field_counts: { status: 497, end_date: 166, name: 73 },
    conflict_field_counts: {},
    conflict_details: [],
    confirmation: "IQUAVIS_FORCE_TO_QOLONY",
    ...overrides,
  };
}

test("forced import apply is bound to the previewed workbook fingerprint", async () => {
  const requests = [];
  globalThis.fetch = async (path, init) => {
    requests.push({ path, body: JSON.parse(init?.body ?? "{}") });
    const body = path.endsWith("/preview")
      ? preview()
      : {
          status: "ok",
          imported_task_count: 174,
          imported_domain_count: 15,
          updated_task_count: 596,
          conflict_task_count: 0,
          baseline_task_count: 4068,
          cleared_state_count: 42,
          local_only_task_count: 1,
          backup_path: "C:/QOLONY_DATA/qolony.pre_iquavis_reconcile_x.db",
          workbook_fingerprint: "sha256:workbook-v1",
        };
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  const previewResult = await api.previewIquavisForcedImport("user-1");
  await api.applyIquavisForcedImport("user-1", previewResult.workbook_fingerprint);

  assert.equal(requests[0].path, "/api/admin/iquavis-forced-import/preview");
  assert.equal(requests[1].path, "/api/admin/iquavis-forced-import/apply");
  assert.equal(requests[1].body.expected_workbook_fingerprint, "sha256:workbook-v1");
  // 確認文字列を固定し、事故で別の取込APIが走らないようにする。
  assert.equal(requests[1].body.confirmation, "IQUAVIS_FORCE_TO_QOLONY");
});

test("preview summary states what will be overwritten and what is held back", () => {
  const summary = formatForcedImportPreviewSummary(preview({ conflict_task_count: 12 }));
  assert.match(summary, /新規取込 174件/);
  assert.match(summary, /iQUAVIS値で上書き 592件/);
  assert.match(summary, /競合として保留 12件/);
});

test("apply is blocked until a preview supplies a fingerprint", () => {
  assert.equal(canApplyIquavisForcedImport(null), false);
  assert.equal(canApplyIquavisForcedImport(preview({ workbook_fingerprint: "" })), false);
  assert.equal(canApplyIquavisForcedImport(preview()), true);
});

test("confirmation text names the tasks that are kept untouched", () => {
  const text = formatForcedImportConfirmation(preview({ conflict_task_count: 3, local_only_task_count: 9 }));
  assert.match(text, /競合として保留する 3件は変更しません/);
  assert.match(text, /QOLONY側だけのタスク 9件は残します/);
});

test("field counts are rendered with Japanese labels", () => {
  assert.equal(getForcedImportFieldLabel("start_date"), "着手日");
  assert.equal(getForcedImportFieldLabel("actual_minutes"), "実績");
  assert.equal(formatForcedImportFieldCounts({}), "なし");
  assert.equal(
    formatForcedImportFieldCounts({ name: 2, status: 5 }),
    "状態 5件 / タスク名 2件"
  );
});

test("apply status reports the recorded baseline count", () => {
  const message = formatForcedImportApplyStatus({
    status: "ok",
    imported_task_count: 174,
    imported_domain_count: 15,
    updated_task_count: 596,
    conflict_task_count: 2,
    baseline_task_count: 4068,
    cleared_state_count: 42,
    local_only_task_count: 1,
    backup_path: "",
    workbook_fingerprint: "sha256:workbook-v1",
  });
  assert.match(message, /合意記録 4068件/);
  assert.match(message, /旧状態クリア 42件/);
});
