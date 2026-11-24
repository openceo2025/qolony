import assert from "node:assert/strict";
import test from "node:test";

import { executeSplitWithPopup } from "../src/lib/splitExecution.ts";

test("executeSplitWithPopup returns to previous tab and shows success popup", async () => {
  const calls = [];

  const result = await executeSplitWithPopup({
    execute: async () => {
      calls.push("execute");
    },
    invalidateAll: async () => {
      calls.push("invalidate");
    },
    showResult: (message) => {
      calls.push(["popup", message]);
    },
    setCurrentTab: (tab) => {
      calls.push(["tab", tab]);
    },
    previousTab: "wbs",
    successMessage: "Split executed",
  });

  assert.deepEqual(result, { saved: true, conflict: false });
  assert.deepEqual(calls, [
    "execute",
    "invalidate",
    ["popup", "Split executed"],
    ["tab", "wbs"],
  ]);
});

test("executeSplitWithPopup keeps current view and shows failure popup on error", async () => {
  const calls = [];

  const result = await executeSplitWithPopup({
    execute: async () => {
      throw new Error("Split failed");
    },
    invalidateAll: async () => {
      calls.push("invalidate");
    },
    showResult: (message) => {
      calls.push(["popup", message]);
    },
    setCurrentTab: (tab) => {
      calls.push(["tab", tab]);
    },
    previousTab: "wbs",
    successMessage: "Split executed",
  });

  assert.deepEqual(result, { saved: false, conflict: false });
  assert.deepEqual(calls, [
    "invalidate",
    ["popup", "Split failed"],
  ]);
});

test("executeSplitWithPopup appends server warnings to the success popup", async () => {
  const messages = [];

  const result = await executeSplitWithPopup({
    execute: async () => ({ status: "ok", revision: "next", warnings: ["Parent date was adjusted"] }),
    invalidateAll: async () => undefined,
    showResult: (message) => messages.push(message),
    setCurrentTab: () => undefined,
    previousTab: "wbs",
    successMessage: "Split executed",
  });

  assert.deepEqual(result, { saved: true, conflict: false });
  assert.deepEqual(messages, ["Split executed\n\nParent date was adjusted"]);
});

test("executeSplitWithPopup reports a 409 so the stale draft can be cancelled", async () => {
  const messages = [];
  const conflict = Object.assign(new Error("Latest task data was loaded"), { status: 409 });

  const result = await executeSplitWithPopup({
    execute: async () => {
      throw conflict;
    },
    invalidateAll: async () => undefined,
    showResult: (message) => messages.push(message),
    setCurrentTab: () => undefined,
    previousTab: "wbs",
    successMessage: "Split executed",
    conflictRecoveryMessage: "The stale draft was cancelled",
  });

  assert.deepEqual(result, { saved: false, conflict: true });
  assert.deepEqual(messages, ["Latest task data was loaded\n\nThe stale draft was cancelled"]);
});
