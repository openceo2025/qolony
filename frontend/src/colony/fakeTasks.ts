import type { Task } from "../types";

// 検証用の架空タスク。会社データは使わず、会社規模（約1.1万件）の形だけを真似る。
// プロジェクト → 工程（区画）→ 作業（食べ物）→ 最大3階層の分割、という形で作る。

function seeded(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(1664525, s) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}

const phaseNames = ["企画", "基本設計", "詳細設計", "試作", "評価", "量産準備", "文書", "保守"];
const workNames = ["図面", "解析", "部品表", "治具", "試験", "レビュー", "手配", "報告書", "検図", "見積"];
const minutesPool = [30, 30, 60, 60, 60, 120, 120, 180, 240, 360, 480, 0];

export function fakeUsers(count = 24) {
  return Array.from({ length: count }, (_, i) => ({
    Id: "u" + String(i + 1).padStart(2, "0"),
    Name: "メンバー" + String(i + 1).padStart(2, "0"),
  }));
}

export function makeFakeTasks(count: number, seed = 7, doneRatio = 0.6): Task[] {
  const r = seeded(seed);
  const users = fakeUsers();
  const tasks: Task[] = [];
  let serial = 0;
  const add = (name: string, parent: string | null, leaf: boolean, order: number): Task => {
    serial += 1;
    const roll = r();
    const status = !leaf
      ? "NotStart"
      : roll < doneRatio
        ? "Completion"
        : roll < doneRatio + 0.02
          ? "Abort"
          : roll < doneRatio + 0.1
            ? "DuringStart"
            : "NotStart";
    const minutes = leaf ? minutesPool[Math.floor(r() * minutesPool.length)] : 0;
    const task = {
      Id: "t" + String(serial).padStart(6, "0"),
      ParentTaskId: parent,
      Name: name,
      AssigneeUserId: users[Math.floor(r() * users.length)].Id,
      AssigneeName: "",
      CreatorUserId: users[0].Id,
      StartDate: "2026-11-01",
      EndDate: "2026-12-" + String(1 + Math.floor(r() * 28)).padStart(2, "0"),
      CreateDate: "2026-10-01",
      Revision: "1",
      AssignedManHour: minutes,
      ActualManHour: status === "Completion" ? minutes : 0,
      ChildAssignedManHour: 0,
      EstimateBalanceManHour: 0,
      EstimateSignal: "None",
      EstimateSignalMessage: null,
      ProgressRate: status === "Completion" ? 100 : status === "DuringStart" ? 40 : 0,
      Signal: "Blue",
      Status: status,
      Type: "Task",
      IsRework: false,
      PreventedByTaskId: null,
      Memo: "",
      TodayOverride: null,
      TodaySortOrder: null,
      WbsSortOrder: order,
      ChildDependencyMode: "Parallel",
      IQuavisTaskId: null,
      IQuavisProjectId: null,
      IQuavisSyncState: "Unlinked",
      IQuavisSyncExcluded: false,
      IQuavisConflictMessage: null,
      PlannedDeliverables: [],
      OutputDeliverables: [],
    } as Task;
    task.AssigneeName = users.find((u) => u.Id === task.AssigneeUserId)!.Name;
    tasks.push(task);
    return task;
  };
  // 作業を height 階層まで割る。たまに 10 を超える割りすぎを混ぜる。
  const grow = (name: string, parent: string | null, height: number, order: number) => {
    const node = add(name, parent, height === 0, order);
    if (height === 0) return;
    const n = r() < 0.03 ? 12 : 2 + Math.floor(r() * 5);
    for (let i = 0; i < n; i++) {
      const childHeight = r() < 0.25 ? 0 : height - 1;
      grow(name + "-" + (i + 1), node.Id, Math.max(0, childHeight), i);
    }
  };
  let project = 0;
  while (tasks.length < count) {
    project += 1;
    const root = add("プロジェクト" + project, null, false, project);
    const phases = 3 + Math.floor(r() * 4);
    for (let p = 0; p < phases && tasks.length < count; p++) {
      const phase = add(phaseNames[p % phaseNames.length], root.Id, false, p);
      const works = 2 + Math.floor(r() * 7);
      for (let w = 0; w < works && tasks.length < count; w++) {
        const height = [0, 1, 1, 2, 2, 3][Math.floor(r() * 6)];
        grow(workNames[(w + p) % workNames.length] + " " + project + "." + (p + 1) + "." + (w + 1), phase.Id, height, w);
      }
    }
  }
  return tasks;
}
