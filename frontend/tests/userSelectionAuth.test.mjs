import assert from "node:assert/strict";
import test from "node:test";

import {
  parseUnlockedUserIds,
  serializeUnlockedUserIds,
  shouldRequireSelectionPassword,
} from "../src/lib/userSelectionAuth.ts";

test("shouldRequireSelectionPassword only targets top manager accounts with passwords", () => {
  assert.equal(
    shouldRequireSelectionPassword({
      IsTopManager: true,
      HasPassword: true,
    }),
    true
  );
  assert.equal(
    shouldRequireSelectionPassword({
      IsTopManager: false,
      HasPassword: true,
    }),
    false
  );
  assert.equal(
    shouldRequireSelectionPassword({
      IsTopManager: true,
      HasPassword: false,
    }),
    false
  );
});

test("parseUnlockedUserIds ignores invalid payloads", () => {
  assert.deepEqual(parseUnlockedUserIds(null), {});
  assert.deepEqual(parseUnlockedUserIds("{"), {});
  assert.deepEqual(parseUnlockedUserIds("{\"a\":true}"), {});
});

test("serializeUnlockedUserIds round-trips unlocked ids", () => {
  const serialized = serializeUnlockedUserIds({
    "user-b": true,
    "user-a": true,
  });
  assert.equal(serialized, "[\"user-a\",\"user-b\"]");
  assert.deepEqual(parseUnlockedUserIds(serialized), {
    "user-a": true,
    "user-b": true,
  });
});
