import test from "node:test";
import assert from "node:assert/strict";
import {
  enqueueTaskMutation,
  extractMutationWarnings,
  formatMutationFailure,
  rollbackOptimisticTaskInsertion,
  rollbackOptimisticTaskPatch,
} from "../src/lib/taskMutationFeedback.ts";

test("extractMutationWarnings accepts only server warning strings", () => {
  assert.deepEqual(extractMutationWarnings({ warnings: ["parent adjusted", 1, ""] }), ["parent adjusted"]);
  assert.deepEqual(extractMutationWarnings({ status: "ok" }), []);
});

test("formatMutationFailure produces an explicit rollback dialog message", () => {
  assert.match(formatMutationFailure(new Error("conflict")), /最新状態へ戻します/);
  assert.match(formatMutationFailure(new Error("conflict")), /conflict/);
});

test("rollbackOptimisticTaskPatch restores only fields still owned by the failed request", () => {
  const original = { Id: "task-1", Memo: "before", Status: "NotStart", Revision: "r1" };
  const current = { Id: "task-1", Memo: "draft", Status: "Completion", Revision: "r2" };
  const rolledBack = rollbackOptimisticTaskPatch(current, original, { Memo: "draft" });

  assert.equal(rolledBack.Memo, "before");
  assert.equal(rolledBack.Status, "Completion");
  assert.equal(rolledBack.Revision, "r2");
});

test("rollbackOptimisticTaskInsertion removes a failed Today insertion without a refetch", () => {
  const tasks = [{ Id: "task-1" }, { Id: "task-2" }];

  assert.deepEqual(rollbackOptimisticTaskInsertion(tasks, "task-2"), [{ Id: "task-1" }]);
});

test("enqueueTaskMutation serializes requests for the same task", async () => {
  const queue = new Map();
  const events = [];
  let releaseFirst;
  const firstGate = new Promise((resolve) => {
    releaseFirst = resolve;
  });
  let drained = 0;

  const first = enqueueTaskMutation(queue, "task-1", async () => {
    events.push("first:start");
    await firstGate;
    events.push("first:end");
    return true;
  }, () => {
    events.push("first:skipped");
  }, () => {
    drained += 1;
  });
  const second = enqueueTaskMutation(queue, "task-1", async () => {
    events.push("second:start");
    return true;
  }, () => {
    events.push("second:skipped");
  }, () => {
    drained += 1;
  });

  await Promise.resolve();
  await Promise.resolve();
  assert.deepEqual(events, ["first:start"]);
  releaseFirst();
  assert.equal(await first, true);
  assert.equal(await second, true);
  await Promise.resolve();
  assert.deepEqual(events, ["first:start", "first:end", "second:start"]);
  assert.equal(queue.size, 0);
  assert.equal(drained, 1);
});

test("enqueueTaskMutation cancels stale successors after a failed predecessor", async () => {
  const queue = new Map();
  const events = [];

  const first = enqueueTaskMutation(queue, "task-1", async () => {
    events.push("first:failed");
    return false;
  }, () => {
    events.push("first:skipped");
  }, () => {});
  const second = enqueueTaskMutation(queue, "task-1", async () => {
    events.push("second:sent");
    return true;
  }, () => {
    events.push("second:cancelled");
  }, () => {});

  assert.equal(await first, false);
  assert.equal(await second, false);
  assert.deepEqual(events, ["first:failed", "second:cancelled"]);
});
