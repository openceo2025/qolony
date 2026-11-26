// 巣の中（地面の下の断面）の並べ方。描画とは切り離した純粋関数だけを置く。
// node のテストランナーは拡張子なしの実行時 import を解決できないので、型以外は import しない。
//
// 2026-11-26 社長決定:
// - 巣は会社で1つ。部屋 = チーム。組織は部屋のツリー（トンネルで枝分かれ）で見せる。輪を重ねる表し方はしない。
// - 所属は掛け持ちできる。部屋を選ぶと、その部屋のメンバーがトンネルを通って集まってくる。
// - 役職の見た目: 社長=葉巻、課長=ネクタイ、係長=黄色のヘルメット、一般=白いヘルメット。
// - 食料庫（会社全体で運び込んだエサ）と育児室（教えた時間）を、入口と社長室のあいだの横穴に置く。
import type { ColonyRole, ColonyTeam } from "../types";

export interface NestPoint {
  x: number;
  y: number;
}

export interface NestRoom {
  /** チームID。社長室だけは ROOT_ROOM。 */
  id: string;
  name: string;
  parent: string | null;
  depth: number;
  x: number;
  y: number;
  w: number;
  h: number;
  emblem: string;
  leader: string | null;
  members: string[];
}

export interface NestTunnel {
  from: string;
  to: string;
  /** 親の部屋の床から子の部屋の天井までの道のり。アリはこの点をたどって歩く。 */
  points: NestPoint[];
}

export interface NestLayout {
  rooms: NestRoom[];
  byId: Map<string, NestRoom>;
  tunnels: NestTunnel[];
  entrance: NestPoint;
  shaft: NestPoint[];
  storage: NestRoom;
  nursery: NestRoom;
  /** 利用者ID → いつもいる部屋。最初に所属した部屋。どこにも所属していなければ社長室。 */
  homes: Map<string, string>;
  extent: { minX: number; maxX: number; maxY: number };
}

export const ROOT_ROOM = "nest-root";
export const STORAGE_ROOM = "nest-storage";
export const NURSERY_ROOM = "nest-nursery";
export const ROOT_Y = 330;
const levelGap = 260;
const roomH = 124;
const siblingGap = 80;

export type RoleAccessory = "cigar" | "tie" | "helmet-yellow" | "helmet-white";
export const roleAccessory: Record<ColonyRole, RoleAccessory> = {
  president: "cigar",
  manager: "tie",
  chief: "helmet-yellow",
  member: "helmet-white",
};
export const roleLabel: Record<ColonyRole, string> = {
  president: "社長",
  manager: "課長",
  chief: "係長",
  member: "一般",
};
export const roleOrder: ColonyRole[] = ["president", "manager", "chief", "member"];
export const sweetSizeLabel = ["かけら", "角砂糖", "包みキャンディー", "板チョコ"];

export function roomWidth(memberCount: number) {
  return Math.max(220, Math.min(420, 180 + memberCount * 22));
}

function curve(a: NestPoint, b: NestPoint, steps = 10): NestPoint[] {
  // 縦に掘ったトンネル。真下へ下りてから横へ曲がり、子の部屋の真上でまた下りる。
  const c1 = { x: a.x, y: a.y + (b.y - a.y) * 0.55 };
  const c2 = { x: b.x, y: a.y + (b.y - a.y) * 0.45 };
  return Array.from({ length: steps + 1 }, (_, i) => {
    const t = i / steps,
      u = 1 - t;
    return {
      x: u * u * u * a.x + 3 * u * u * t * c1.x + 3 * u * t * t * c2.x + t * t * t * b.x,
      y: u * u * u * a.y + 3 * u * u * t * c1.y + 3 * u * t * t * c2.y + t * t * t * b.y,
    };
  });
}

export function layoutNest(
  teams: ColonyTeam[],
  userIds: string[],
  presidentIds: string[],
): NestLayout {
  const known = new Set(teams.map((team) => team.Id));
  const children = new Map<string, ColonyTeam[]>();
  for (const team of [...teams].sort((a, b) => a.DisplayOrder - b.DisplayOrder || a.Id.localeCompare(b.Id))) {
    const parent = team.ParentTeamId && known.has(team.ParentTeamId) ? team.ParentTeamId : ROOT_ROOM;
    children.set(parent, [...(children.get(parent) ?? []), team]);
  }
  const width = new Map<string, number>();
  const visiting = new Set<string>();
  const ownWidth = (id: string, team: ColonyTeam | null) =>
    roomWidth(team ? team.MemberIds.length : Math.max(presidentIds.length, 1) + 2);
  // 部屋の下にある部屋全体の幅。親子が輪になっていても止まるように、たどり中の部屋は数えない。
  const span = (id: string, team: ColonyTeam | null): number => {
    if (width.has(id)) return width.get(id)!;
    if (visiting.has(id)) return 0;
    visiting.add(id);
    const kids = (children.get(id) ?? []).filter((kid) => !visiting.has(kid.Id));
    const below = kids.reduce((sum, kid) => sum + span(kid.Id, kid), 0) + Math.max(0, kids.length - 1) * siblingGap;
    const total = Math.max(ownWidth(id, team) + siblingGap, below);
    visiting.delete(id);
    width.set(id, total);
    return total;
  };
  const rooms: NestRoom[] = [];
  const tunnels: NestTunnel[] = [];
  const placed = new Set<string>();
  const place = (id: string, team: ColonyTeam | null, parent: NestRoom | null, center: number, depth: number) => {
    if (placed.has(id)) return;
    placed.add(id);
    const members = team ? team.MemberIds : [...presidentIds];
    const room: NestRoom = {
      id,
      name: team ? team.Name : "社長室",
      parent: parent?.id ?? null,
      depth,
      x: center,
      y: ROOT_Y + depth * levelGap,
      w: ownWidth(id, team),
      h: roomH,
      emblem: team?.Emblem ?? "crown",
      leader: team?.LeaderUserId ?? null,
      members,
    };
    rooms.push(room);
    if (parent)
      tunnels.push({
        from: parent.id,
        to: id,
        points: curve({ x: parent.x, y: parent.y + parent.h / 2 - 8 }, { x: room.x, y: room.y - room.h / 2 + 8 }),
      });
    const kids = (children.get(id) ?? []).filter((kid) => !placed.has(kid.Id));
    const total = kids.reduce((sum, kid) => sum + span(kid.Id, kid), 0) + Math.max(0, kids.length - 1) * siblingGap;
    let cursor = center - total / 2;
    for (const kid of kids) {
      const w = span(kid.Id, kid);
      place(kid.Id, kid, room, cursor + w / 2, depth + 1);
      cursor += w + siblingGap;
    }
  };
  span(ROOT_ROOM, null);
  place(ROOT_ROOM, null, null, 0, 0);
  // 親をたどれない部屋（輪になっていた等）も、社長室の下に並べて見失わないようにする。
  for (const team of teams) if (!placed.has(team.Id)) place(team.Id, team, rooms[0], (rooms.length - 1) * 120, 1);

  const root = rooms[0];
  const sideY = 150;
  const sideX = Math.max(root.w / 2 + 210, 360);
  const storage: NestRoom = {
    id: STORAGE_ROOM,
    name: "食料庫",
    parent: null,
    depth: 0,
    x: -sideX,
    y: sideY,
    w: 250,
    h: 112,
    emblem: "storage",
    leader: null,
    members: [],
  };
  const nursery: NestRoom = { ...storage, id: NURSERY_ROOM, name: "育児室", x: sideX, emblem: "nursery" };
  const byId = new Map(rooms.map((room) => [room.id, room]));
  byId.set(STORAGE_ROOM, storage);
  byId.set(NURSERY_ROOM, nursery);

  const homes = new Map<string, string>();
  const ordered = rooms.slice(1);
  for (const uid of userIds) {
    if (presidentIds.includes(uid)) {
      homes.set(uid, ROOT_ROOM);
      continue;
    }
    const led = ordered.find((room) => room.leader === uid);
    const first = ordered.find((room) => room.members.includes(uid));
    homes.set(uid, (led ?? first)?.id ?? ROOT_ROOM);
  }
  const xs = rooms.flatMap((room) => [room.x - room.w / 2, room.x + room.w / 2]).concat([storage.x - 140, nursery.x + 140]);
  return {
    rooms,
    byId,
    tunnels,
    entrance: { x: 0, y: 0 },
    shaft: [
      { x: 0, y: -10 },
      { x: 0, y: sideY },
      { x: 0, y: root.y - root.h / 2 + 8 },
    ],
    storage,
    nursery,
    homes,
    extent: {
      minX: Math.min(...xs),
      maxX: Math.max(...xs),
      maxY: Math.max(...rooms.map((room) => room.y + room.h / 2)) + 120,
    },
  };
}

/** 部屋から部屋への道のり。共通の上の部屋まで上り、目的の部屋へ下りる。 */
export function pathBetween(layout: NestLayout, from: string, to: string): NestPoint[] {
  const a = layout.byId.get(from),
    b = layout.byId.get(to);
  if (!a || !b) return [];
  if (from === to) return [{ x: b.x, y: b.y }];
  const side = (room: NestRoom) => room.id === STORAGE_ROOM || room.id === NURSERY_ROOM;
  const chain = (room: NestRoom): NestRoom[] => {
    const list: NestRoom[] = [];
    let current: NestRoom | undefined = side(room) ? layout.byId.get(ROOT_ROOM) : room;
    let guard = 0;
    while (current && guard++ < 64) {
      list.push(current);
      current = current.parent ? layout.byId.get(current.parent) : undefined;
    }
    return list;
  };
  const up = chain(a),
    down = chain(b);
  const common = up.find((room) => down.includes(room));
  const tunnel = (parent: string, child: string) =>
    layout.tunnels.find((t) => t.from === parent && t.to === child)?.points ?? [];
  const points: NestPoint[] = [{ x: a.x, y: a.y }];
  const sideRoute = (room: NestRoom) => [
    { x: room.x, y: room.y },
    { x: 0, y: room.y },
    { x: 0, y: layout.shaft[2].y },
  ];
  if (side(a)) points.push(...sideRoute(a).slice(1));
  for (const room of up) {
    if (room === common) break;
    if (room.parent) points.push(...[...tunnel(room.parent, room.id)].reverse());
  }
  const descent: NestPoint[] = [];
  for (const room of down) {
    if (room === common) break;
    if (room.parent) descent.unshift(...tunnel(room.parent, room.id));
  }
  points.push(...descent);
  if (side(b)) points.push(...sideRoute(b).reverse());
  points.push({ x: b.x, y: b.y });
  return points;
}

/** 部屋の床に並ぶ立ち位置。人数が多いと後ろの列ほど少し上に下がる。 */
export function standingSpot(room: NestRoom, index: number, count: number): NestPoint {
  const perRow = Math.max(3, Math.floor((room.w - 60) / 42));
  const row = Math.floor(index / perRow);
  const inRow = Math.min(perRow, count - row * perRow);
  const col = index % perRow;
  const spacing = Math.min(42, (room.w - 70) / Math.max(1, inRow - 1 || 1));
  return {
    x: room.x + (col - (inRow - 1) / 2) * spacing,
    y: room.y + room.h / 2 - 16 - row * 22,
  };
}

/** 点列の上を、全体の割合 t の位置。 */
export function along(points: NestPoint[], t: number): NestPoint {
  if (points.length < 2) return points[0] ?? { x: 0, y: 0 };
  const lengths = points.slice(1).map((p, i) => Math.hypot(p.x - points[i].x, p.y - points[i].y));
  const total = lengths.reduce((s, v) => s + v, 0) || 1;
  let remaining = Math.max(0, Math.min(1, t)) * total;
  for (let i = 0; i < lengths.length; i++) {
    if (remaining <= lengths[i] || i === lengths.length - 1) {
      const k = lengths[i] ? Math.min(1, remaining / lengths[i]) : 1;
      return { x: points[i].x + (points[i + 1].x - points[i].x) * k, y: points[i].y + (points[i + 1].y - points[i].y) * k };
    }
    remaining -= lengths[i];
  }
  return points[points.length - 1];
}

export function pathLength(points: NestPoint[]) {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += Math.hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y);
  return total;
}

/** その部屋の所属を変えられるか（画面で錠前を出すための目安。最終判定はサーバー）。 */
export function canEditRoom(
  permissions: { EditAllTeams: boolean; EditableTeamIds: string[] },
  roomId: string,
) {
  return permissions.EditAllTeams || permissions.EditableTeamIds.includes(roomId);
}

/** 部屋の主・埋め戻しを決められるか。課長は自分が主の部屋より下だけ。 */
export function canManageRoom(
  role: ColonyRole,
  viewer: string,
  roomId: string,
  layout: NestLayout,
  permissions: { EditAllTeams: boolean; EditableTeamIds: string[] },
) {
  if (permissions.EditAllTeams) return true;
  if (role !== "manager") return false;
  const room = layout.byId.get(roomId);
  return !!room && room.leader !== viewer && permissions.EditableTeamIds.includes(roomId);
}

export function canDigBelow(
  permissions: { EditAllTeams: boolean; EditableTeamIds: string[] },
  roomId: string,
) {
  if (roomId === ROOT_ROOM) return permissions.EditAllTeams;
  return canEditRoom(permissions, roomId);
}

export function canChangeRole(
  viewerRole: ColonyRole,
  viewer: string,
  target: string,
  targetRole: ColonyRole,
  next: ColonyRole,
  targetRooms: string[],
  permissions: { EditableTeamIds: string[] },
) {
  if (viewerRole === "president") return true;
  if (viewerRole !== "manager" || target === viewer) return false;
  if (!(next === "chief" || next === "member") || !(targetRole === "chief" || targetRole === "member")) return false;
  return targetRooms.some((room) => permissions.EditableTeamIds.includes(room));
}
