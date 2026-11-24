import assert from "node:assert/strict";
import test from "node:test";

import {
  canCompleteSelectedTask,
  consumeRunningTimer,
  markTimerCommitPending,
  resolveDoneTarget,
  resolveLatestRunningTimer,
  timerCommitEndMs,
} from "../src/lib/todayTimer.ts";

const runningTimer = {
  taskId: "task-1",
  taskName: "設計",
  startedAtMs: 1000,
  baseElapsedMs: 5000,
};

test("resolveLatestRunningTimer falls back to state when ref is not synchronized yet", () => {
  assert.deepEqual(resolveLatestRunningTimer(runningTimer, null), runningTimer);
});

test("resolveLatestRunningTimer prefers ref when both are present", () => {
  const newerRef = {
    ...runningTimer,
    taskId: "task-2",
    taskName: "実装",
  };
  assert.deepEqual(resolveLatestRunningTimer(runningTimer, newerRef), newerRef);
});

test("consumeRunningTimer allows only one commit for the same local interval", () => {
  const timerRef = { current: runningTimer };

  assert.deepEqual(consumeRunningTimer(timerRef), runningTimer);
  assert.equal(consumeRunningTimer(timerRef), null);
});

test("failed timer commits preserve one fixed interval for an idempotent retry", () => {
  const pending = markTimerCommitPending(runningTimer, 9000);

  assert.equal(timerCommitEndMs(pending, 12000), 9000);
  assert.equal(timerCommitEndMs(markTimerCommitPending(pending, 15000), 20000), 9000);
});

test("resolveDoneTarget uses running timer even if selected task differs", () => {
  assert.deepEqual(resolveDoneTarget(runningTimer, null, "task-9", "別タスク"), {
    taskId: "task-1",
    taskName: "設計",
  });
});

test("resolveDoneTarget falls back to selected task when no timer is running", () => {
  assert.deepEqual(resolveDoneTarget(null, null, "task-9", "レビュー"), {
    taskId: "task-9",
    taskName: "レビュー",
  });
});

test("canCompleteSelectedTask allows completion without a running timer", () => {
  assert.equal(canCompleteSelectedTask("task-9", "DuringStart"), true);
  assert.equal(canCompleteSelectedTask("task-9", "NotStart"), true);
  assert.equal(canCompleteSelectedTask("task-9", "Completion"), false);
  assert.equal(canCompleteSelectedTask("task-9", "Abort"), false);
  assert.equal(canCompleteSelectedTask(null, "DuringStart"), false);
});
