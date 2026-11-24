import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildTaskEditConflictRows,
  formatTaskEditConflictGuidance,
  formatTaskEditConflictHeadline,
  formatTaskFieldValue,
  removeTaskEditConflict,
  upsertTaskEditConflict,
} from "../src/lib/taskEditConflict.ts";
import { resolveForcedTaskEditKind } from "../src/lib/forcedTaskEdit.ts";

function conflict(overrides = {}) {
  return {
    taskId: "TASK-1",
    taskName: "休暇",
    base: {
      Id: "TASK-1",
      Name: "休暇",
      Status: "NotStart",
      ProgressRate: 0,
      StartDate: "2026-08-01",
      EndDate: "2026-08-02",
      AssignedManHour: 480,
      ActualManHour: 0,
      Memo: "",
      AssigneeUserId: "USER-1",
      TodayOverride: true,
    },
    patch: { Status: "Completion", ProgressRate: 100 },
    detail: {
      task_id: "TASK-1",
      task_name: "休暇",
      updated_by_user_name: "鈴木",
      updated_at: "2026-08-05T10:00:00",
      server: {
        Name: "休暇",
        Status: "Abort",
        ProgressRate: 0,
        StartDate: "2026-08-01",
        EndDate: "2026-08-02",
        AssignedManHour: 480,
        ActualManHour: 0,
        Memo: "",
        AssigneeUserId: "USER-1",
        AssigneeName: "山田",
        TodayOverride: true,
      },
    },
    detectedAt: "2026-08-05T10:00:05",
    ...overrides,
  };
}

test("the headline names who got there first", () => {
  assert.match(formatTaskEditConflictHeadline(conflict()), /鈴木 さんが先に「休暇」を変更しました/);
});

test("the headline stays usable when the other user is unknown", () => {
  const anonymous = conflict();
  anonymous.detail = { ...anonymous.detail, updated_by_user_name: null };
  assert.match(formatTaskEditConflictHeadline(anonymous), /他のユーザーが先に/);
});

test("guidance tells users to talk before overwriting", () => {
  const text = formatTaskEditConflictGuidance(conflict());
  assert.match(text, /鈴木 さんと話して/);
  assert.match(text, /自分の変更で上書き/);
  assert.match(text, /相手の変更を採用/);
});

test("rows show base, mine and theirs for the contested field", () => {
  const rows = buildTaskEditConflictRows(conflict());
  const status = rows.find((row) => row.field === "Status");
  assert.ok(status);
  assert.equal(status.base, "未着手");
  assert.equal(status.mine, "完了");
  assert.equal(status.theirs, "中止");
  // 両者が同じ項目を別の値へ変えた、本当の衝突点。
  assert.equal(status.contested, true);
});

test("a field only the other user changed is listed but not contested", () => {
  const other = conflict({ patch: { Memo: "自分のメモ" } });
  other.detail = {
    ...other.detail,
    server: { ...other.detail.server, Status: "Completion", Memo: "" },
  };
  const rows = buildTaskEditConflictRows(other);
  const status = rows.find((row) => row.field === "Status");
  assert.ok(status, "相手だけが変えた項目も見えないと相談できない");
  assert.equal(status.contested, false);
  const memo = rows.find((row) => row.field === "Memo");
  assert.equal(memo.mine, "自分のメモ");
  assert.equal(memo.contested, false);
});

test("values are formatted for humans", () => {
  assert.equal(formatTaskFieldValue("Status", "DuringStart"), "作業中");
  assert.equal(formatTaskFieldValue("ProgressRate", 100), "100%");
  assert.equal(formatTaskFieldValue("AssignedManHour", 480), "480分");
  assert.equal(formatTaskFieldValue("Memo", ""), "(未設定)");
  assert.equal(formatTaskFieldValue("TodayOverride", true), "ON");
});

test("conflicts are tracked one per task", () => {
  const first = conflict();
  const second = conflict({ taskId: "TASK-2" });
  let list = upsertTaskEditConflict([], first);
  list = upsertTaskEditConflict(list, second);
  assert.equal(list.length, 2);

  // 同じタスクの再衝突は積み増さず、最新の1件に置き換える。
  list = upsertTaskEditConflict(list, conflict({ detectedAt: "2026-08-05T10:01:00" }));
  assert.equal(list.length, 2);
  assert.equal(list.at(-1).detectedAt, "2026-08-05T10:01:00");

  list = removeTaskEditConflict(list, "TASK-1");
  assert.deepEqual(list.map((item) => item.taskId), ["TASK-2"]);
});

test("force-resend picks the operation the patch came from", () => {
  assert.equal(resolveForcedTaskEditKind({ Status: "Completion", ProgressRate: 100 }), "complete");
  assert.equal(resolveForcedTaskEditKind({ Status: "NotStart", ProgressRate: 0 }), "reopen");
  assert.equal(resolveForcedTaskEditKind({ StartDate: "2026-08-01", EndDate: "2026-08-05" }), "schedule");
  assert.equal(resolveForcedTaskEditKind({ Memo: "x" }), "memo");
  assert.equal(resolveForcedTaskEditKind({ ActualManHour: 60 }), "actual_manhour");
  assert.equal(resolveForcedTaskEditKind({ AssignedManHour: 60 }), "assigned_manhour");
  assert.equal(resolveForcedTaskEditKind({ TodayOverride: true }), "today_on");
  assert.equal(resolveForcedTaskEditKind({ TodayOverride: false }), "today_off");
});

test("an unmappable patch refuses force-resend instead of guessing", () => {
  assert.equal(resolveForcedTaskEditKind({}), null);
  assert.equal(resolveForcedTaskEditKind({ Name: "renamed" }), null);
});
