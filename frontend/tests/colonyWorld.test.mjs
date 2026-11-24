import test from "node:test";
import assert from "node:assert/strict";
import {
  applyAction,
  actualMH,
  areaProgress,
  detailOf,
  districts,
  fieldCrew,
  deadline,
  distance,
  hour,
  initialWorld,
  makeFood,
  memberCredit,
  nest,
  positionAt,
  populateDistricts,
  progressAt,
} from "../src/colony/model.ts";
import { legPose } from "../src/colony/gait.ts";

const now = Date.UTC(2026, 10, 19);
const action = (w, type, id, actor = "mio", at = now) => applyAction(w, { type, id }, actor, at);
const food = (w, id) => w.foods.find((f) => f.id === id);
test("外側6エリアは48件の操作可能な仕事と実在する担当を持ち、中央の仕事密度を保つ", () => {
  const w = initialWorld(now);
  assert.equal(w.foods.length, 56);
  assert.equal(new Set(w.foods.map((f) => f.id)).size, w.foods.length);
  for (const d of districts) {
    const mark = w.marks.find((m) => m.team === d.id);
    const group = w.foods.filter((f) => f.id.startsWith("field-" + d.id + "-job-"));
    assert.equal(group.length, 8);
    assert.equal(areaProgress(w, mark, now).count, group.length);
    assert.equal(areaProgress(w, mark, now).active, 1);
    const carrying = group.find((f) => f.owner !== null);
    const delivered = action(w, "finish", carrying.id, carrying.owner, now + hour);
    assert.equal(areaProgress(delivered, mark, now + hour).completed, 1);
    for (const f of group) {
      assert.ok(Math.abs(f.x) > 600 || Math.abs(f.y) > 370);
      assert.ok(detailOf(f).output && detailOf(f).input && detailOf(f).issue);
      if (f.owner) assert.ok(fieldCrew.some((m) => m.id === f.owner && m.team === d.id));
    }
  }
});
test("旧保存への追加は一度だけで、既存の分割・納品・独自チームと追加後の納品を維持する", () => {
  const expanded = initialWorld(now);
  const legacy = {
    ...expanded,
    districtEdition: undefined,
    foods: expanded.foods.filter((f) => !f.id.startsWith("field-")),
    teams: expanded.teams.slice(0, 3),
    marks: expanded.marks.slice(0, 3),
  };
  let w = action(action(legacy, "start", "sugar"), "finish", "sugar");
  w = action(w, "split", "bar");
  w = applyAction(
    w,
    { type: "team", team: { id: "myteam", name: "自作", members: ["mio"], parent: "design" } },
    "yui",
    now,
  );
  const next = populateDistricts(w, now + hour);
  assert.deepEqual(next.receipts, w.receipts);
  assert.deepEqual(next.credits, w.credits);
  assert.deepEqual(
    next.foods.filter((f) => !f.id.startsWith("field-")),
    w.foods,
  );
  assert.ok(next.teams.find((t) => t.id === "myteam"));
  const added = next.foods.find((f) => f.id.startsWith("field-") && !f.owner);
  const done = action(action(next, "start", added.id), "finish", added.id);
  assert.equal(populateDistricts(done, now + hour), done);
  assert.equal(food(done, added.id), undefined);
});
test("エリアの円グラフは見積MHで加重し、分割や移動で元の進捗を失わない", () => {
  let w = initialWorld(now);
  const mark = w.marks.find((m) => m.id === "zone-a");
  const initial = areaProgress(w, mark, now);
  assert.equal(initial.count, 2);
  assert.equal(initial.percent, Math.round((100 * (2 * 0.25 + 16 * 0.48)) / 18));
  w = action(w, "start", "pop");
  const later = now + hour;
  const carried = areaProgress(w, mark, later);
  assert.equal(carried.active, 1);
  assert.ok(carried.percent > initial.percent);
  w = action(w, "split", "pop", "mio", later);
  const split = areaProgress(w, mark, later);
  assert.equal(split.percent, carried.percent);
  assert.equal(split.count, 3);
  assert.deepEqual(detailOf(food(w, "pop-a")).origin, { x: -324, y: -130 });
  for (const id of ["pop-a", "pop-b", "sugar"]) {
    w = action(w, "start", id, "mio", later);
    w = action(w, "finish", id, "mio", later);
  }
  const done = areaProgress(w, mark, later);
  assert.deepEqual(done, { percent: 100, count: 3, active: 0, completed: 3 });
  assert.deepEqual(
    areaProgress(
      w,
      {
        ...mark,
        id: "redrawn",
        points: [
          { x: 900, y: 900 },
          { x: 960, y: 900 },
          { x: 940, y: 970 },
        ],
      },
      later,
    ),
    { percent: null, count: 0, active: 0, completed: 0 },
  );
});
test("実績MHは応援と一時停止の前後で二重計上せず、分割後の合計が一致する", () => {
  let w = initialWorld(now);
  const original = detailOf(food(w, "bar"));
  w = action(w, "start", "bar");
  w = action(w, "help", "bar", "ren", now + hour / 2);
  w = action(w, "pause", "bar", "mio", now + hour);
  assert.equal(actualMH(food(w, "bar"), now + 4 * hour), original.bookedMH + 1);
  w = action(w, "start", "bar", "mio", now + 4 * hour);
  w = action(w, "split", "bar", "mio", now + 5 * hour);
  const halves = w.foods.filter((f) => f.id.startsWith("bar-"));
  assert.equal(
    halves.reduce((sum, f) => sum + actualMH(f, now + 6 * hour), 0),
    original.bookedMH + 2,
  );
  assert.equal(
    halves.reduce((sum, f) => sum + detailOf(f).estimateMH, 0),
    original.estimateMH,
  );
});
test("新規仕事のエリアは実際の配置点で決まり、重複する境界の双方に集計する", () => {
  let w = initialWorld(now);
  const f = makeFood("extra", "新規", "sugar", { x: -230, y: -90 }, now + hour);
  f.detail = { ...detailOf(f), origin: { x: 0, y: 0 }, estimateMH: 7, input: "図面", output: "改訂図" };
  w = applyAction(w, { type: "place", food: f }, "mio", now);
  assert.deepEqual(detailOf(food(w, "extra")).origin, { x: -230, y: -90 });
  const mark = w.marks[0];
  assert.equal(areaProgress(w, mark, now).count, 3);
  const duplicate = { ...mark, id: "another", team: "ops" };
  assert.equal(areaProgress(w, duplicate, now).count, 3);
  assert.equal(detailOf({ ...f, id: "bar-a", piece: 1, detail: undefined }).estimateMH, 6);
});
test("両脚の全位相で膝が進行方向を向き、接地中の足裏と短い歩幅を保つ", () => {
  for (const rear of [true, false]) {
    for (let step = 0; step < 120; step++) {
      const phase = (step * Math.PI) / 60;
      const { hip, knee, ankle } = legPose(phase, rear, true, Math.abs(Math.cos(phase)) * 0.55);
      const dx = ankle.x - hip.x,
        dy = ankle.y - hip.y;
      assert.ok((knee.x - hip.x) * dy - (knee.y - hip.y) * dx > 0);
      assert.ok(Math.abs(dx) <= 2.71);
      const cycle = (step / 120 + (rear ? 0.5 : 0)) % 1;
      if (cycle < 0.58) assert.equal(ankle.y, rear ? -1.45 : -1);
    }
  }
});
test("運搬は同時に1件で、停止・再開をまたいで巣のふちで入力を待つ", () => {
  let w = action(initialWorld(now), "start", "sugar");
  assert.throws(() => action(w, "start", "wrap"));
  assert.throws(() => action(w, "finish", "sugar", "ren"));
  w = action(w, "pause", "sugar", "mio", now + 10000);
  const p = progressAt(food(w, "sugar"), now + 20000);
  assert.ok(p > 0);
  assert.equal(p, progressAt(food(w, "sugar"), now + 30000));
  w = action(w, "start", "sugar", "mio", now + 30000);
  const end = positionAt(food(w, "sugar"), now + hour);
  assert.ok(distance(end, nest) > 60);
  assert.deepEqual(end, positionAt(food(w, "sugar"), now + 2 * hour));
  w = action(w, "finish", "sugar", "mio", now + 2 * hour);
  assert.equal(w.delivered, 1);
  assert.ok(w.credits.mio > 1);
  assert.equal(food(w, "sugar"), undefined);
  assert.equal(w.events.at(-1).type, "drop");
  assert.throws(() => action(w, "finish", "sugar"));
});
test("分割は難易度を下げ、予測金額の合計を保ち、親の担当を解除する", () => {
  const w = initialWorld(now);
  Object.assign(food(w, "bar"), { sales: 51, profit: -9 });
  const next = action(w, "split", "bar");
  assert.equal(next.foods.length, w.foods.length + 1);
  const pieces = next.foods.filter((f) => f.id.startsWith("bar-"));
  assert.equal(pieces.length, 2);
  assert.equal(
    pieces.reduce((s, f) => s + f.sales, 0),
    51,
  );
  assert.equal(
    pieces.reduce((s, f) => s + f.profit, 0),
    -9,
  );
  assert.ok(pieces.every((f) => f.weight < 4 && !f.owner && f.due === food(w, "bar").due));
  assert.equal(next.events[0].type, "split");
  assert.throws(() => action(w, "split", "sugar"));
});
test("運搬途中で分割しても運搬距離の貢献を引き継ぐ", () => {
  const w = action(initialWorld(now), "start", "bar");
  const at = now + 18000,
    credit = memberCredit(w, "mio", at);
  assert.ok(credit > 0);
  const next = action(w, "split", "bar", "mio", at);
  assert.equal(memberCredit(next, "mio", at), credit + 16);
});
test("応援は進捗を飛ばさず、期限の見込みと運搬速度に反映される", () => {
  const w = action(initialWorld(now), "start", "bar");
  const at = now + 10000;
  assert.throws(() => action(w, "help", "bar", "mio", at));
  const next = action(w, "help", "bar", "ren", at);
  assert.equal(progressAt(food(w, "bar"), at), progressAt(food(next, "bar"), at));
  assert.ok(progressAt(food(w, "bar"), at + 5000) < progressAt(food(next, "bar"), at + 5000));
  assert.equal(deadline(food(w, "bar"), now).risk, true);
  assert.throws(() => action(next, "help", "bar", "ren", at));
});
test("役職で配置と作図を制限し、チーム境界外の受注を許可する", () => {
  let w = initialWorld(now);
  const f = makeFood("extra", "仕事", "lollipop", { x: 800, y: 400 }, now + hour);
  assert.throws(() => applyAction(w, { type: "place", food: f }, "mio", now));
  w = applyAction(w, { type: "place", food: f }, "kei", now);
  w = action(w, "start", "extra", "mio");
  const team = { id: "solo", name: "一人チーム", parent: "design", members: ["mio"] };
  w = applyAction(w, { type: "team", team }, "yui", now);
  const mark = {
    id: "zone",
    name: team.name,
    team: "solo",
    points: [
      { x: 1, y: 1 },
      { x: 12, y: 1 },
      { x: 4, y: 8 },
    ],
  };
  assert.throws(() => applyAction(w, { type: "mark", mark }, "ren", now));
  w = applyAction(w, { type: "mark", mark }, "yui", now);
  const line = { ...mark, team: null, points: mark.points.slice(0, 2) };
  assert.throws(() => applyAction(w, { type: "mark", mark: line }, "yui", now));
  assert.equal(applyAction(w, { type: "mark", mark: line }, "kei", now).marks.at(-1).team, null);
});
