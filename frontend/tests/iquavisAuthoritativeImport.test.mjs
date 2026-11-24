import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { api } from "../src/api/client.ts";
import {
  canApplyIquavisAuthoritativeImport,
  formatIquavisDifferenceFields,
  formatIquavisAuthoritativeApplyStatus,
  formatIquavisAuthoritativeConfirmation,
  formatIquavisAuthoritativePreviewStatus,
  getIquavisSnapshotDifferenceCount,
  getIquavisUnknownStatusCount,
  IQUAVIS_AUTHORITATIVE_SAFETY_GUIDANCE,
  IQUAVIS_SNAPSHOT_DIFFERENCE_GUIDANCE,
  normalizeIquavisStatusBreakdown,
} from "../src/lib/iquavisAuthoritativeImport.ts";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function preview(overrides = {}) {
  return {
    ready: true,
    workbook_fingerprint: "sha256:workbook-v1",
    workbook_name: "iquavis_editer_latest_ver.xlsm",
    scanned_task_count: 120,
    existing_task_count: 100,
    new_task_count: 20,
    new_domain_count: 4,
    changed_task_count: 15,
    changed_field_count: 32,
    local_only_task_count: 7,
    excluded_task_count: 3,
    preserved_local_parent_count: 5,
    excel_snapshot_difference_count: 2,
    excel_snapshot_difference_details: [],
    missing_baseline_task_count: 0,
    missing_baseline_details: [],
    dirty_excel_task_count: 0,
    status_breakdown: { NotStart: 80, DuringStart: 25, Completion: 10, Abort: 5 },
    unknown_status_count: 0,
    unknown_status_details: [],
    unsupported_status_count: 0,
    blockers: [],
    warnings: [],
    ...overrides,
  };
}

test("authoritative import API binds apply to the previewed workbook and explicit direction", async () => {
  const requests = [];
  globalThis.fetch = async (path, init) => {
    requests.push({ path, body: JSON.parse(init?.body ?? "{}") });
    const body = path.endsWith("/preview")
      ? preview()
      : {
          status: "ok",
          imported_task_count: 120,
          imported_domain_count: 4,
          updated_task_count: 15,
          cleared_state_count: 8,
          derived_progress_mismatch_count: 0,
          warnings: [],
        };
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  };

  await api.previewIquavisAuthoritativeImport("TOP-1");
  await api.applyIquavisAuthoritativeImport("TOP-1", "sha256:workbook-v1");

  assert.deepEqual(requests, [
    {
      path: "/api/admin/iquavis-authoritative-import/preview",
      body: { operator_user_id: "TOP-1" },
    },
    {
      path: "/api/admin/iquavis-authoritative-import/apply",
      body: {
        operator_user_id: "TOP-1",
        expected_workbook_fingerprint: "sha256:workbook-v1",
        confirmation: "IQUAVIS_TO_QOLONY",
      },
    },
  ]);
});

test("authoritative import API sends ignored iquavis ids when provided", async () => {
  const requests = [];
  globalThis.fetch = async (path, init) => {
    requests.push({ path, body: JSON.parse(init?.body ?? "{}") });
    return new Response(
      JSON.stringify({
        ready: true,
        workbook_fingerprint: "sha256:workbook-v1",
        workbook_name: "iquavis_editer_latest_ver.xlsm",
        scanned_task_count: 120,
        existing_task_count: 100,
        new_task_count: 20,
        new_domain_count: 4,
        changed_task_count: 15,
        changed_field_count: 32,
        local_only_task_count: 7,
        excluded_task_count: 3,
        preserved_local_parent_count: 5,
        dirty_excel_task_count: 0,
        status_breakdown: { NotStart: 80, DuringStart: 25, Completion: 10, Abort: 5 },
        unsupported_status_count: 0,
        blockers: [],
        warnings: [],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  };

  await api.previewIquavisAuthoritativeImport("TOP-1", ["TASK-CHILD", "TASK-DOMAIN"]);
  await api.applyIquavisAuthoritativeImport(
    "TOP-1",
    "sha256:workbook-v1",
    ["TASK-CHILD", "TASK-DOMAIN"]
  );

  assert.deepEqual(requests, [
    {
      path: "/api/admin/iquavis-authoritative-import/preview",
      body: {
        operator_user_id: "TOP-1",
        ignored_iquavis_ids: ["TASK-CHILD", "TASK-DOMAIN"],
      },
    },
    {
      path: "/api/admin/iquavis-authoritative-import/apply",
      body: {
        operator_user_id: "TOP-1",
        expected_workbook_fingerprint: "sha256:workbook-v1",
        confirmation: "IQUAVIS_TO_QOLONY",
        ignored_iquavis_ids: ["TASK-CHILD", "TASK-DOMAIN"],
      },
    },
  ]);
});

test("authoritative import API sends ignored review item keys when provided", async () => {
  const requests = [];
  globalThis.fetch = async (path, init) => {
    requests.push({ path, body: JSON.parse(init?.body ?? "{}") });
    return new Response(
      JSON.stringify({
        ready: true,
        workbook_fingerprint: "sha256:workbook-v1",
        workbook_name: "iquavis_editer_latest_ver.xlsm",
        scanned_task_count: 120,
        existing_task_count: 100,
        new_task_count: 20,
        new_domain_count: 4,
        changed_task_count: 15,
        changed_field_count: 32,
        local_only_task_count: 7,
        excluded_task_count: 3,
        preserved_local_parent_count: 5,
        dirty_excel_task_count: 0,
        status_breakdown: { NotStart: 80, DuringStart: 25, Completion: 10, Abort: 5 },
        unsupported_status_count: 0,
        blockers: [],
        warnings: [],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } }
    );
  };

  await api.previewIquavisAuthoritativeImport("TOP-1", [], ["row:12", "id:TASK-UNKNOWN"]);
  await api.applyIquavisAuthoritativeImport(
    "TOP-1",
    "sha256:workbook-v1",
    [],
    ["row:12", "id:TASK-UNKNOWN"]
  );

  assert.deepEqual(requests, [
    {
      path: "/api/admin/iquavis-authoritative-import/preview",
      body: {
        operator_user_id: "TOP-1",
        ignored_review_item_keys: ["row:12", "id:TASK-UNKNOWN"],
      },
    },
    {
      path: "/api/admin/iquavis-authoritative-import/apply",
      body: {
        operator_user_id: "TOP-1",
        expected_workbook_fingerprint: "sha256:workbook-v1",
        confirmation: "IQUAVIS_TO_QOLONY",
        ignored_review_item_keys: ["row:12", "id:TASK-UNKNOWN"],
      },
    },
  ]);
});

test("apply is enabled for ordinary snapshot differences but not hard blockers", () => {
  assert.equal(canApplyIquavisAuthoritativeImport(preview()), true);
  assert.equal(canApplyIquavisAuthoritativeImport(preview({ excel_snapshot_difference_count: 42 })), true);
  assert.equal(canApplyIquavisAuthoritativeImport(preview({ ready: false })), false);
  assert.equal(canApplyIquavisAuthoritativeImport(preview({ blockers: ["Excel保存待ちがあります"] })), false);
  assert.equal(canApplyIquavisAuthoritativeImport(preview({ workbook_fingerprint: "" })), false);
  assert.equal(canApplyIquavisAuthoritativeImport(null), false);
});

test("operator messages make the direction and affected counts explicit", () => {
  const target = preview();
  const previewStatus = formatIquavisAuthoritativePreviewStatus(target);
  const confirmation = formatIquavisAuthoritativeConfirmation(target);
  const applyStatus = formatIquavisAuthoritativeApplyStatus({
    status: "ok",
    imported_task_count: 120,
    imported_domain_count: 4,
    updated_task_count: 15,
    cleared_state_count: 8,
    derived_progress_mismatch_count: 2,
    warnings: [],
  });

  assert.match(previewStatus, /事前確認適用可能/);
  assert.match(previewStatus, /iQUAVIS側のみ20件/);
  assert.match(previewStatus, /QOLONY側のみ7件/);
  assert.match(confirmation, /iQUAVIS最新値を優先してQOLONYへ安全に反映/);
  assert.match(confirmation, /iquavis_editer_latest_ver\.xlsm/);
  assert.match(confirmation, /QOLONY側のみ: 7件（ローカル専用として保持）/);
  assert.match(confirmation, /同期対象外: 3件 \/ QOLONY親維持: 5件/);
  assert.match(confirmation, /削除は行いません/);
  assert.match(confirmation, /iQUAVISサーバーへの保存を行いません/);
  assert.match(applyStatus, /取込120件/);
  assert.match(applyStatus, /競合状態クリア8件/);
});

test("preview helpers distinguish same-ID snapshot differences from ID existence differences", () => {
  const target = preview({ excel_snapshot_difference_count: 42, unknown_status_count: 6 });

  assert.equal(getIquavisSnapshotDifferenceCount(target), 42);
  assert.equal(getIquavisUnknownStatusCount(target), 6);
  assert.match(IQUAVIS_SNAPSHOT_DIFFERENCE_GUIDANCE, /同じiQUAVIS IDの項目差/);
  assert.match(IQUAVIS_SNAPSHOT_DIFFERENCE_GUIDANCE, /ID件数差ではありません/);
  assert.match(IQUAVIS_AUTHORITATIVE_SAFETY_GUIDANCE, /ローカル専用タスク/);
  assert.match(IQUAVIS_AUTHORITATIVE_SAFETY_GUIDANCE, /QOLONYで付け替えた親/);
  assert.equal(formatIquavisDifferenceFields(["name", "status"]), "タスク名、状態");
});

test("status breakdown accepts object and array API representations and includes Abort", () => {
  assert.deepEqual(normalizeIquavisStatusBreakdown(preview()), [
    { status: "NotStart", count: 80 },
    { status: "DuringStart", count: 25 },
    { status: "Completion", count: 10 },
    { status: "Abort", count: 5 },
  ]);
  assert.deepEqual(
    normalizeIquavisStatusBreakdown(preview({ status_breakdown: [{ status: "Abort", count: 126 }] })),
    [{ status: "Abort", count: 126 }]
  );
});

test("legacy preview fields remain readable while a company PC is updating versions", () => {
  const target = preview({
    excel_snapshot_difference_count: undefined,
    unknown_status_count: undefined,
    dirty_excel_task_count: 41,
    unsupported_status_count: 126,
  });

  assert.equal(getIquavisSnapshotDifferenceCount(target), 41);
  assert.equal(getIquavisUnknownStatusCount(target), 126);
});
