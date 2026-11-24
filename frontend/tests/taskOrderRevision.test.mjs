import assert from "node:assert/strict";
import { test } from "node:test";
import { taskOrderRevision } from "../src/lib/taskOrderRevision.ts";

test("taskOrderRevision matches the backend vectors", () => {
  assert.equal(taskOrderRevision([]), "order-v1-09612b07b5ecb5a5");
  assert.equal(taskOrderRevision(["TASK-A", "TASK-B"]), "order-v1-891b4fcb344dc422");
  assert.equal(taskOrderRevision(["TASK-B", "TASK-A"]), "order-v1-f1e35e3943facb74");
});
