import test from "node:test";
import assert from "node:assert/strict";
import {
  ROOT_ROOM,
  STORAGE_ROOM,
  along,
  canChangeRole,
  canDigBelow,
  canEditRoom,
  layoutNest,
  pathBetween,
  roleAccessory,
  standingSpot,
} from "../src/colony/nestModel.ts";

const team = (Id, ParentTeamId, MemberIds, extra = {}) => ({
  Id,
  Name: Id,
  ParentTeamId,
  LeaderUserId: null,
  Emblem: "leaf",
  DisplayOrder: 0,
  MemberIds,
  ...extra,
});

test("rooms form a tree below the president room, children under their parent", () => {
  const layout = layoutNest(
    [
      team("sales", null, ["a", "b"]),
      team("dev", null, ["c"]),
      team("sales-1", "sales", ["b", "d"]),
      team("sales-2", "sales", ["e"]),
      team("deep", "sales-1", ["f"]),
    ],
    ["boss", "a", "b", "c", "d", "e", "f", "g"],
    ["boss"],
  );
  const room = (id) => layout.byId.get(id);
  assert.equal(layout.rooms[0].id, ROOT_ROOM);
  assert.equal(room("sales").depth, 1);
  assert.equal(room("deep").depth, 3);
  assert.ok(room("deep").y > room("sales-1").y && room("sales-1").y > room("sales").y);
  // 同じ深さの部屋は重ならない
  const level = layout.rooms.filter((r) => r.depth === 2).sort((x, y) => x.x - y.x);
  for (let i = 1; i < level.length; i++)
    assert.ok(level[i].x - level[i].w / 2 >= level[i - 1].x + level[i - 1].w / 2, "siblings overlap");
  assert.ok(room("sales-1").x < room("sales-2").x);
  // いつもいる部屋: 最初に所属した部屋、どこにもいなければ社長室
  assert.equal(layout.homes.get("b"), "sales");
  assert.equal(layout.homes.get("g"), ROOT_ROOM);
  assert.equal(layout.homes.get("boss"), ROOT_ROOM);
  assert.equal(layout.tunnels.length, 5);
});

test("leaders live in the room they lead", () => {
  const layout = layoutNest(
    [team("a", null, ["x"]), team("b", "a", ["x"], { LeaderUserId: "x" })],
    ["x"],
    [],
  );
  assert.equal(layout.homes.get("x"), "b");
});

test("a cycle in parents does not hang and every room is placed", () => {
  const layout = layoutNest([team("p", "q", []), team("q", "p", [])], [], []);
  assert.equal(layout.rooms.length, 3);
});

test("walking path goes up to the shared room and down", () => {
  const layout = layoutNest(
    [team("s", null, []), team("s1", "s", []), team("d", null, [])],
    [],
    [],
  );
  const path = pathBetween(layout, "s1", "d");
  const s1 = layout.byId.get("s1"),
    d = layout.byId.get("d"),
    root = layout.byId.get(ROOT_ROOM);
  assert.deepEqual(path[0], { x: s1.x, y: s1.y });
  assert.deepEqual(path.at(-1), { x: d.x, y: d.y });
  // 途中で社長室の高さまで上がる
  assert.ok(Math.min(...path.map((p) => p.y)) <= root.y + root.h / 2);
  const toStorage = pathBetween(layout, "d", STORAGE_ROOM);
  assert.deepEqual(toStorage.at(-1), { x: layout.storage.x, y: layout.storage.y });
  const mid = along(path, 0.5);
  assert.ok(Number.isFinite(mid.x) && Number.isFinite(mid.y));
});

test("standing spots stay inside the room", () => {
  const layout = layoutNest([team("r", null, ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"])], [], []);
  const room = layout.byId.get("r");
  for (let i = 0; i < 10; i++) {
    const p = standingSpot(room, i, 10);
    assert.ok(Math.abs(p.x - room.x) < room.w / 2);
    assert.ok(p.y < room.y + room.h / 2 && p.y > room.y - room.h / 2);
  }
});

test("role looks and edit permissions", () => {
  assert.deepEqual(roleAccessory, {
    president: "cigar",
    manager: "tie",
    chief: "helmet-yellow",
    member: "helmet-white",
  });
  const mine = { EditAllTeams: false, EditableTeamIds: ["r1"] };
  assert.equal(canEditRoom(mine, "r1"), true);
  assert.equal(canEditRoom(mine, "r2"), false);
  assert.equal(canDigBelow(mine, ROOT_ROOM), false);
  assert.equal(canDigBelow({ EditAllTeams: true, EditableTeamIds: [] }, ROOT_ROOM), true);
  assert.equal(canChangeRole("manager", "m", "x", "member", "chief", ["r1"], mine), true);
  assert.equal(canChangeRole("manager", "m", "x", "member", "manager", ["r1"], mine), false);
  assert.equal(canChangeRole("manager", "m", "x", "member", "chief", ["r2"], mine), false);
  assert.equal(canChangeRole("chief", "c", "x", "member", "member", ["r1"], mine), false);
});
