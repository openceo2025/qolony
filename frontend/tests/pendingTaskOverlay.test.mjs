import assert from "node:assert/strict";
import { test } from "node:test";

import {
  applyPendingTaskOverlay,
  isPendingTaskOverlayEmpty,
} from "../src/lib/pendingTaskOverlay.ts";

function task(id, overrides = {}) {
  return {
    Id: id,
    Name: `task-${id}`,
    Status: "NotStart",
    ProgressRate: 0,
    ...overrides,
  };
}

test("a completion still saving is not resurrected by a refetch", () => {
  // 先に押した保存の完了で走る再取得は、あとから押した完了をまだ含まないサーバー状態を返す。
  const serverToday = [task("a"), task("b"), task("c")];
  const overlay = new Map([["b", { patch: { Status: "Completion" }, removeFromToday: true }]]);

  const result = applyPendingTaskOverlay(serverToday, overlay, "today");

  assert.deepEqual(result.map((item) => item.Id), ["a", "c"]);
});

test("patches are re-applied on top of refetched rows", () => {
  const serverTasks = [task("a"), task("b")];
  const overlay = new Map([["a", { patch: { Status: "Completion", ProgressRate: 100 } }]]);

  const result = applyPendingTaskOverlay(serverTasks, overlay, "all");

  assert.equal(result[0].Status, "Completion");
  assert.equal(result[0].ProgressRate, 100);
  assert.equal(result[1].Status, "NotStart");
});

test("an empty overlay returns the same array reference", () => {
  const serverTasks = [task("a")];
  assert.equal(applyPendingTaskOverlay(serverTasks, new Map(), "all"), serverTasks);
});

test("an overlay that changes nothing returns the same array reference", () => {
  // 参照が変わると再描画が走り、再帰的な更新も招くので、変化が無いときは据え置く。
  const serverTasks = [task("a", { Status: "Completion" })];
  const overlay = new Map([["a", { patch: { Status: "Completion" } }]]);
  assert.equal(applyPendingTaskOverlay(serverTasks, overlay, "all"), serverTasks);
});

test("removeFromToday only affects the today list", () => {
  const serverTasks = [task("a"), task("b")];
  const overlay = new Map([["b", { patch: { Status: "Completion" }, removeFromToday: true }]]);

  const all = applyPendingTaskOverlay(serverTasks, overlay, "all");
  assert.deepEqual(all.map((item) => item.Id), ["a", "b"]);
  assert.equal(all[1].Status, "Completion");
});

test("ensureInToday restores a row the refetch has not caught up with", () => {
  const serverToday = [task("a")];
  const allTasks = [task("a"), task("b")];
  const overlay = new Map([["b", { patch: { TodayOverride: true }, ensureInToday: true }]]);

  const result = applyPendingTaskOverlay(serverToday, overlay, "today", allTasks);

  assert.deepEqual(result.map((item) => item.Id), ["a", "b"]);
  assert.equal(result[1].TodayOverride, true);
});

test("several completions in a row all stay hidden", () => {
  // 連打しても、片付いていない分だけ被せ続ける。
  const serverToday = [task("a"), task("b"), task("c"), task("d")];
  const overlay = new Map([
    ["a", { patch: { Status: "Completion" }, removeFromToday: true }],
    ["b", { patch: { Status: "Completion" }, removeFromToday: true }],
    ["c", { patch: { Status: "Completion" }, removeFromToday: true }],
  ]);

  const result = applyPendingTaskOverlay(serverToday, overlay, "today");

  assert.deepEqual(result.map((item) => item.Id), ["d"]);
});

test("overlay emptiness drives when it is safe to refetch", () => {
  assert.equal(isPendingTaskOverlayEmpty(new Map()), true);
  assert.equal(isPendingTaskOverlayEmpty(new Map([["a", { patch: {} }]])), false);
});
