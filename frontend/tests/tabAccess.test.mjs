import assert from "node:assert/strict";
import test from "node:test";

import { canAccessAdminTab, canAccessTab } from "../src/lib/tabAccess.ts";

test("canAccessTab keeps ranking open for general users", () => {
  const generalUser = {
    Id: "user-1",
    Name: "一般ユーザー",
    LoginId: "general",
    IsTopManager: false,
    FreeTimeRate: 0.2,
    HasPassword: false,
    CreateDate: null,
  };

  assert.equal(canAccessTab("ranking", generalUser, {}), true);
  assert.equal(canAccessTab("admin", generalUser, {}), false);
});

test("canAccessAdminTab requires top-manager unlock when password exists", () => {
  const topManager = {
    Id: "admin-1",
    Name: "山田",
    LoginId: "u0001",
    IsTopManager: true,
    FreeTimeRate: 0.2,
    HasPassword: true,
    CreateDate: null,
  };

  assert.equal(canAccessAdminTab(topManager, {}), false);
  assert.equal(canAccessAdminTab(topManager, { "admin-1": true }), true);
});
