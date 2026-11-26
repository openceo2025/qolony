import test from "node:test";
import assert from "node:assert/strict";
import {
  buildProjectColony,
  carryPose,
  colonyCanEdit,
  kindForHeight,
  packAroundNest,
  planSweets,
  squash,
} from "../src/colony/liveModel.ts";
import { makeFakeTasks } from "../src/colony/fakeTasks.ts";

let order = 0;
const task = (Id, ParentTaskId = null, fields = {}) => ({
  Id,
  Name: Id,
  ParentTaskId,
  Status: "NotStart",
  ProgressRate: 0,
  AssignedManHour: 60,
  ActualManHour: 0,
  EndDate: "2026-12-10",
  CreateDate: "2026-11-01",
  WbsSortOrder: order++,
  Signal: "Blue",
  AssigneeUserId: "u1",
  PlannedDeliverables: [],
  OutputDeliverables: [],
  IQuavisSyncExcluded: false,
  Memo: "",
  ...fields,
});
const user = { Id: "u1", Name: "テスト担当", IsActive: true, IsTopManager: false };

test("深さで種類が決まる: かけら・角砂糖1階層・包みキャンディー2階層・板チョコ3階層", () => {
  assert.deepEqual([0, 1, 2, 3, 5].map((h) => kindForHeight(h)), ["crumb", "sugar", "bonbon", "chocolate", "chocolate"]);
  // 巣の外で置いたお菓子は、置いたときの大きさより小さく見せない（まだ割っていない板チョコ）。
  assert.equal(kindForHeight(0, 3), "chocolate");
  assert.equal(kindForHeight(2, 1), "bonbon");
  const rows = [
    task("area"),
    task("crumb", "area"),
    task("cube", "area"),
    task("cube-1", "cube"),
    task("candy", "area"),
    task("candy-1", "candy"),
    task("candy-1-1", "candy-1"),
    task("bar", "area"),
    task("bar-1", "bar"),
    task("bar-1-1", "bar-1"),
    task("bar-1-1-1", "bar-1-1"),
    task("placed", "area", { ColonySize: 2 }),
  ];
  const { districts } = planSweets(rows);
  assert.deepEqual(
    districts.map((d) => [d.root.Id, d.sweets.map((s) => [s.root.Id, s.kind])]),
    [
      [
        "area",
        [
          ["crumb", "crumb"],
          ["cube", "sugar"],
          ["candy", "bonbon"],
          ["bar", "chocolate"],
          ["placed", "bonbon"],
        ],
      ],
    ],
  );
});

test("エリア（一番上のタスク）はお菓子にせず、深さ3を超える仕事はたどってお菓子にする", () => {
  const rows = [
    task("project"),
    task("phase", "project"),
    task("bar", "phase"),
    task("bar-1", "bar"),
    task("bar-1-1", "bar-1"),
    task("bar-1-1-1", "bar-1-1"),
    task("loose", "phase"),
    task("alone"),
  ];
  const { districts } = planSweets(rows);
  assert.equal(districts.length, 2);
  assert.equal(districts[0].root.Id, "project");
  assert.deepEqual(
    districts[0].sweets.map((s) => [s.root.Id, s.kind]),
    [
      ["bar", "chocolate"],
      ["loose", "crumb"],
    ],
  );
  // 子のない一番上のタスクは、そのまま1つのお菓子になる。
  assert.deepEqual(
    districts[1].sweets.map((s) => [s.root.Id, s.kind]),
    [["alone", "crumb"]],
  );
});

test("割れ方は数だけ大まかに合わせる。1階層は10まで、多い末端はまとめて描く", () => {
  const many = [task("area"), task("cube", "area"), ...Array.from({ length: 13 }, (_, i) => task("c" + i, "cube"))];
  const [sweet] = planSweets(many).districts[0].sweets;
  assert.equal(sweet.bits.groups.length, 10);
  assert.equal(sweet.bits.leaves, 13);
  assert.deepEqual(squash(["done", "done", "open", "active", "done", "done"], 3), ["done", "active", "done"]);
  assert.deepEqual(squash(["open", "done"], 4), ["open", "done"]);
});

test("完了は欠け、着手はアリが食べ、未着手だけの人は待ち、完了だけの人は運ぶ", () => {
  const rows = [
    task("area"),
    task("bar", "area"),
    task("a", "bar", { Status: "DuringStart", AssigneeUserId: "eater" }),
    task("b", "bar", { AssigneeUserId: "eater" }),
    task("c", "bar", { AssigneeUserId: "waiter" }),
    task("d", "bar", { Status: "Completion", AssigneeUserId: "carrier" }),
    task("e", "bar", { Status: "Abort", AssigneeUserId: "carrier" }),
    task("timed", "bar", { AssigneeUserId: "timer" }),
  ];
  const [sweet] = planSweets(rows, { TaskId: "timed" }).districts[0].sweets;
  assert.deepEqual(sweet.bits.groups.flat(), ["active", "open", "open", "done", "done", "active"]);
  assert.equal(sweet.bits.done, 2);
  assert.equal(sweet.bits.active, 2);
  assert.deepEqual(sweet.eaters, ["eater", "timer"]);
  assert.deepEqual(sweet.waiters, ["waiter"]);
  assert.deepEqual(sweet.carriers, ["carrier"]);
  const { world } = buildProjectColony(rows, [user], [], user.Id, { TaskId: "timed" }, false);
  assert.deepEqual(
    world.project.crew.map((a) => [a.owner, a.mode]).sort(),
    [
      ["carrier", "carry"],
      ["eater", "eat"],
      ["timer", "eat"],
      ["waiter", "wait"],
    ],
  );
});

test("地図にはお菓子だけを置く。食べ尽くしたお菓子と同期対象外は既定で描かない", () => {
  const rows = [
    task("root", null, { ProgressRate: 67 }),
    task("sweet", "root"),
    task("leaf", "sweet", { ActualManHour: 90 }),
    task("done", "sweet", { Status: "Completion" }),
    task("hidden", "sweet", { IQuavisSyncExcluded: true }),
    task("eaten", "root"),
    task("eaten-1", "eaten", { Status: "Completion" }),
  ];
  const { world, positions, foodOf } = buildProjectColony(rows, [user], [], user.Id, null, false);
  assert.deepEqual(
    world.foods.map((f) => f.id),
    ["sweet"],
  );
  const food = world.foods[0];
  assert.equal(food.kind, "sugar");
  assert.equal(food.detail.bookedMH, 1.5);
  assert.deepEqual(positions.get("leaf"), { x: food.x, y: food.y });
  assert.equal(foodOf.get("leaf"), "sweet");
  assert.equal(positions.has("hidden"), false);
  assert.ok(positions.has("eaten-1"), "食べ尽くしたお菓子のタスクも一覧から区画へ寄れる");
  assert.equal(world.delivered, 2);
  const all = buildProjectColony(rows, [user], [], user.Id, null, true);
  assert.deepEqual(
    all.world.foods.map((f) => f.id),
    ["sweet", "eaten"],
  );
});

test("並びはWBS順で、入力順に依存しない", () => {
  const rows = [task("z", null, { WbsSortOrder: 0 }), task("a", null, { WbsSortOrder: 1 })];
  const one = buildProjectColony(rows, [user], [], user.Id, null, false);
  const two = buildProjectColony([...rows].reverse(), [user], [], user.Id, null, false);
  assert.deepEqual(
    one.world.foods.map((f) => f.id),
    ["z", "a"],
  );
  assert.deepEqual(one.positions.get("a"), two.positions.get("a"));
});

test("区画は巣のまわりに重ならずに詰める", () => {
  const boxes = packAroundNest(Array.from({ length: 30 }, (_, i) => ({ w: 300 + (i % 4) * 90, h: 260 + (i % 3) * 80 })));
  for (let i = 0; i < boxes.length; i++)
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i],
        b = boxes[j];
      const apart = a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y;
      assert.ok(apart, `${i} と ${j} が重なっている`);
    }
});

test("会社規模（架空1.1万件）でも一望できる広さに収まり、アリは担当者×お菓子で頭打ち", () => {
  const tasks = makeFakeTasks(11_000, 7, 0.72);
  const started = performance.now();
  const { world } = buildProjectColony(tasks, [user], [], user.Id, null, false);
  const elapsed = performance.now() - started;
  const { x, y } = world.project.extent;
  assert.ok(x / y > 0.6 && x / y < 2.4, `extent ${x} x ${y}`);
  assert.ok(world.foods.length < 1000);
  assert.ok(world.project.crew.length <= world.foods.length * 7);
  assert.ok(elapsed < 2000, "build " + elapsed + "ms");
});

test("運び手は巣とお菓子を往復し、帰りだけかけらを持つ", () => {
  const ant = { id: "a", owner: "u1", food: "f", mode: "carry", point: { x: 300, y: 0 }, face: -1, offset: 0 };
  const home = { x: 0, y: 0 };
  assert.deepEqual(carryPose(ant, home, 0).p, home);
  assert.equal(carryPose(ant, home, 0).load, false);
  // 往路 300/55 ≒ 5.45秒。到着後かじって、復路でかけらを持つ。
  assert.deepEqual(carryPose(ant, home, 5.6).p, ant.point);
  const back = carryPose(ant, home, 8);
  assert.equal(back.mode, "carry");
  assert.equal(back.load, true);
  assert.ok(back.p.x > 0 && back.p.x < 300);
});

test("編集は有効な担当者とTop Managerに限る", () => {
  const assigned = task("assigned");
  assert.equal(colonyCanEdit(assigned, user), true);
  assert.equal(colonyCanEdit(assigned, { ...user, Id: "other" }), false);
  assert.equal(colonyCanEdit(assigned, { ...user, Id: "other", IsTopManager: true }), true);
  assert.equal(colonyCanEdit(assigned, { ...user, IsActive: false, IsTopManager: true }), false);
  assert.equal(colonyCanEdit(assigned, undefined), false);
});
