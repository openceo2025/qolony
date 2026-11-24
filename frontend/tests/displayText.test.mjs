import assert from "node:assert/strict";
import test from "node:test";

import { formatDisplayText } from "../src/lib/displayText.ts";

test("formatDisplayText hides Excel escaped carriage returns without deleting line break information", () => {
  assert.equal(formatDisplayText("親_x000D_子"), "親\n子");
  assert.equal(formatDisplayText("親_X000D_子"), "親\n子");
});

test("formatDisplayText keeps empty values display-safe", () => {
  assert.equal(formatDisplayText(null), "");
  assert.equal(formatDisplayText(undefined), "");
});
