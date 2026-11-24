import type { ActiveTimer, MemberView, Task, User } from "../types";
import type { ColonyMember, CrewAnt, Food, Mark, Point, World } from "./model";
// model.ts の nest と同じ位置。node のテストで実行時 import を避けるため複製している。
const nest = { x: 0, y: 18 };

const areaColors = ["#a65d38", "#317e72", "#91629d", "#527990", "#8c7836", "#ad6570"];
/** お菓子1つの置き場の大きさ。板チョコが最大で幅160ほどになる。 */
const slot = { w: 250, h: 205 };
/** 1つのお菓子のまわりに描くアリの上限。多すぎると何をしているか読めなくなる。 */
const crewCap = { eat: 3, wait: 2, carry: 2 };

function progress(task: Task) {
  return Math.max(0, Math.min(100, Number(task.ProgressRate) || 0));
}

function clean(name: string) {
  return (name ?? "").replace(/_x000D_|\r?\n/gi, " ");
}

/** お菓子の当たり判定と吹き出しの基準になる半径。描画（sweetArt.ts）の大きさに合わせる。 */
export function sweetSize(plan: SweetPlan) {
  const groups = plan.bits.groups.length;
  if (plan.kind === "chocolate") return 34 + Math.min(10, groups) * 6;
  if (plan.kind === "bonbon") return 44;
  return 18 + Math.min(10, groups) * 2.5;
}

function toFood(plan: SweetPlan, point: Point, byId: Map<string, Task>): Food {
  const root = plan.root;
  const leaves = plan.leafIds.map((id) => byId.get(id)!).filter(Boolean);
  const estimate = leaves.reduce((s, t) => s + (t.AssignedManHour || 0), 0) / 60;
  const booked = leaves.reduce((s, t) => s + (t.ActualManHour || 0), 0) / 60;
  const date = Date.parse(root.EndDate + "T23:59:59");
  return {
    id: root.Id,
    name: clean(root.Name),
    ...point,
    kind: plan.kind,
    weight: plan.kind === "chocolate" ? 5 : plan.kind === "bonbon" ? 3 : 1,
    due: Number.isFinite(date) ? date : Infinity,
    // お菓子そのものは引っ張らない。アリの動きは crew で描く。
    since: null,
    owner: null,
    progress: 0,
    help: false,
    sales: 0,
    profit: 0,
    piece: 0,
    requirement: "",
    bits: plan.bits,
    size: sweetSize(plan),
    task: {
      progress: progress(root),
      signal: root.Signal,
      status: root.Status,
      planned: "",
      outputs: "",
    },
    detail: {
      input: "",
      output: "",
      issue: root.Memo?.replace(/_x000D_/gi, "\n") || "未登録",
      estimateMH: estimate,
      bookedMH: booked,
      baseProgress: 0,
      origin: point,
    },
  };
}

function hash(text: string) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967296;
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}
const overlaps = (a: Box, b: Box, gap: number) =>
  a.x < b.x + b.w + gap && b.x < a.x + a.w + gap && a.y < b.y + b.h + gap && b.y < a.y + a.h + gap;

/**
 * 区画を巣のまわりへ渦巻き状に詰める。WBS 順に巣の近くから埋まるので、
 * 会社規模でも縦や横に伸びきらず、全体表示で一望できる。
 */
export function packAroundNest(sizes: { w: number; h: number }[]): Box[] {
  const placed: Box[] = [{ x: nest.x - 230, y: nest.y - 150, w: 460, h: 300 }];
  const out: Box[] = [];
  for (const size of sizes) {
    let found: Box | null = null;
    const step = Math.max(60, Math.sqrt(size.w * size.h) / 5);
    for (let r = 0; !found && r < 200_000; r += step) {
      const count = Math.max(8, Math.ceil((2 * Math.PI * r) / step));
      for (let k = 0; k < count && !found; k++) {
        const a = (k / count) * Math.PI * 2 - Math.PI / 2;
        const box = {
          x: nest.x + Math.cos(a) * r * 1.35 - size.w / 2,
          y: nest.y + Math.sin(a) * r - size.h / 2,
          w: size.w,
          h: size.h,
        };
        if (!placed.some((p) => overlaps(box, p, 60))) found = box;
      }
    }
    const box = found ?? { x: 0, y: 0, ...size };
    placed.push(box);
    out.push(box);
  }
  return out;
}

/** 運び手の往復。巣とお菓子の距離に応じて速さを変え、遠くても往復が長くなりすぎないようにする。 */
export function carryPose(ant: CrewAnt, home: Point, seconds: number) {
  const length = Math.max(1, Math.hypot(ant.point.x - home.x, ant.point.y - home.y));
  const speed = Math.max(55, length / 16);
  const walk = length / speed,
    pause = 0.9,
    total = walk * 2 + pause * 2;
  const t = ((((seconds / total + ant.offset) % 1) + 1) % 1) * total;
  const at = (k: number) => ({ x: home.x + (ant.point.x - home.x) * k, y: home.y + (ant.point.y - home.y) * k });
  const outward = ant.point.x >= home.x ? 1 : -1;
  // 巣 → お菓子（手ぶら）→ かじり取る → 巣へ（かけらを運ぶ）→ 下ろす
  if (t < walk) return { p: at(t / walk), face: outward, mode: "walk" as const, load: false };
  if (t < walk + pause) return { p: ant.point, face: ant.face, mode: "pull" as const, load: false };
  if (t < walk * 2 + pause)
    return { p: at(1 - (t - walk - pause) / walk), face: -outward, mode: "carry" as const, load: true };
  return { p: home, face: 1, mode: "idle" as const, load: false };
}

export function colonyCanEdit(task: Task, user: User | undefined) {
  return !!user?.IsActive && (task.AssigneeUserId === user.Id || user.IsTopManager);
}

export function buildProjectColony(
  tasks: Task[],
  users: User[],
  members: MemberView[],
  actor: string,
  activeTimer: ActiveTimer | null,
  showClosed: boolean,
) {
  const byId = new Map(tasks.map((task) => [task.Id, task]));
  const { districts, parentIds } = planSweets(tasks, activeTimer);
  const shown = districts
    .map((d) => ({ ...d, all: d.sweets, sweets: d.sweets.filter((s) => showClosed || !s.finished) }))
    .filter((d) => d.sweets.length);
  const sizes = shown.map((d) => {
    const columns = Math.max(1, Math.ceil(Math.sqrt(d.sweets.length * 1.3)));
    const rows = Math.ceil(d.sweets.length / columns);
    return { w: columns * slot.w + 60, h: rows * slot.h + 70, columns };
  });
  const boxes = packAroundNest(sizes);
  const marks: Mark[] = [];
  const foods: Food[] = [];
  const crew: CrewAnt[] = [];
  const positions = new Map<string, Point>();
  /** タスクID → それを含むお菓子（ルートのタスクID）。 */
  const foodOf = new Map<string, string>();
  const firstSeen = new Map<string, Point>();
  // 区画ごと食べ尽くして描かないタスクは、運び込まれた先の巣を指す（描く区画は下で上書きする）。
  for (const d of districts)
    for (const plan of d.sweets)
      for (const id of [d.root.Id, plan.root.Id, ...plan.leafIds]) positions.set(id, { x: nest.x, y: nest.y });
  let extentX = 625,
    extentY = 370;
  shown.forEach((district, index) => {
    const box = boxes[index],
      { columns } = sizes[index];
    const center = { x: box.x + box.w / 2, y: box.y + box.h / 2 };
    extentX = Math.max(extentX, Math.abs(box.x), Math.abs(box.x + box.w));
    extentY = Math.max(extentY, Math.abs(box.y), Math.abs(box.y + box.h));
    // 食べ尽くして描かないお菓子のタスクも、一覧から選んだら区画へ寄れるようにする。
    for (const plan of district.all) for (const id of [plan.root.Id, ...plan.leafIds]) positions.set(id, center);
    positions.set(district.root.Id, center);
    let leaves = 0,
      active = 0,
      done = 0;
    district.sweets.forEach((plan, i) => {
      // 置き場の中で少しずらし、格子に並んだ印象を和らげる。
      const jitter = hash(plan.root.Id);
      const point = {
        x: box.x + 30 + (i % columns) * slot.w + slot.w / 2 + (jitter - 0.5) * 36,
        y: box.y + 70 + Math.floor(i / columns) * slot.h + slot.h * 0.6 + (hash(plan.root.Id + "y") - 0.5) * 24,
      };
      const food = toFood(plan, point, byId);
      foods.push(food);
      for (const id of [plan.root.Id, ...plan.leafIds]) foodOf.set(id, plan.root.Id);
      for (const id of [plan.root.Id, ...plan.leafIds]) positions.set(id, point);
      leaves += plan.bits.leaves;
      active += plan.bits.active;
      done += plan.bits.done;
      const size = food.size!;
      const ring = (who: string[], mode: CrewAnt["mode"], cap: number, radius: number, start: number) =>
        who.slice(0, cap).forEach((owner, k) => {
          const angle = start + (k / Math.max(1, Math.min(cap, who.length))) * Math.PI * 2 + jitter * 3;
          const at = { x: point.x + Math.cos(angle) * radius * 1.25, y: point.y + Math.sin(angle) * radius * 0.55 + 4 };
          crew.push({
            id: mode + ":" + plan.root.Id + ":" + owner,
            owner,
            food: plan.root.Id,
            mode,
            point: at,
            face: at.x < point.x ? 1 : -1,
            offset: hash(owner + plan.root.Id),
          });
          if (!firstSeen.has(owner)) firstSeen.set(owner, at);
        });
      ring(plan.eaters, "eat", crewCap.eat, size * 0.82, 0.3);
      ring(plan.waiters, "wait", crewCap.wait, size * 1.15, 1.9);
      if (!plan.finished) ring(plan.carriers, "carry", crewCap.carry, size * 0.9, 4.2);
    });
    const root = district.root;
    marks.push({
      id: root.Id,
      team: root.Id,
      name: clean(root.Name),
      color: areaColors[index % areaColors.length],
      rounded: true,
      points: [
        { x: box.x, y: box.y },
        { x: center.x, y: box.y - 16 },
        { x: box.x + box.w, y: box.y },
        { x: box.x + box.w + 16, y: center.y },
        { x: box.x + box.w, y: box.y + box.h },
        { x: center.x, y: box.y + box.h + 16 },
        { x: box.x, y: box.y + box.h },
        { x: box.x - 16, y: center.y },
      ],
      summary: { percent: Math.round(progress(root)), count: leaves, active, completed: done },
    });
  });
  // 区画とお菓子の間にある中間の親は、子孫のお菓子の位置へ寄せる。
  const childIds = new Map<string, string[]>();
  for (const task of tasks)
    if (task.ParentTaskId) childIds.set(task.ParentTaskId, [...(childIds.get(task.ParentTaskId) ?? []), task.Id]);
  const locate = (id: string, guard = 0): Point | undefined => {
    const known = positions.get(id);
    if (known || guard > 64) return known;
    for (const child of childIds.get(id) ?? []) {
      const p = locate(child, guard + 1);
      if (p) {
        positions.set(id, p);
        return p;
      }
    }
    return undefined;
  };
  for (const task of tasks) if (!task.IQuavisSyncExcluded) locate(task.Id);
  const profiles = new Map(members.map((member) => [member.UserId, member.Profile.FormationRole]));
  const liveMembers: Record<string, ColonyMember> = {};
  for (const user of users) {
    liveMembers[user.Id] = {
      point: firstSeen.get(user.Id) ?? { x: 115, y: 65 },
      name: user.Name,
      role: profiles.get(user.Id) || (user.IsTopManager ? "Top Manager" : ""),
    };
  }
  const delivered = districts.reduce((s, d) => s + d.sweets.reduce((t, p) => t + p.bits.done, 0), 0);
  const world: World = {
    version: 1,
    foods,
    marks,
    teams: [],
    events: [],
    credits: {},
    accessories: {},
    delivered,
    project: { members: liveMembers, extent: { x: extentX + 160, y: extentY + 150 }, crew },
  };
  return { world, positions, parentIds, foodOf, kinds: sweetRules };
}

// タスクツリーを「お菓子」にまとめる純粋関数（2026-11-22 決定のルール）。
// - 角砂糖（規模 小）= 1階層、包みキャンディー（中）= 2階層、板チョコ（大）= 3階層まで分解できる。種類は深さで決まる。
// - 1階層あたりの分割は最大 10。
// - 見た目は「階層と個数がなんとなく合っている」程度にとどめる。仕事の中身や工数の比はこの画面で表さない。
// - 末端の状態だけを見る。完了・中止 = 運び去られて欠ける、着手 = アリが食べている。

export type SweetKind = "sugar" | "bonbon" | "chocolate";
export type BitState = "open" | "active" | "done";

export const sweetRules: Record<SweetKind, { label: string; scale: string; levels: number }> = {
  sugar: { label: "角砂糖", scale: "小", levels: 1 },
  bonbon: { label: "包みキャンディー", scale: "中", levels: 2 },
  chocolate: { label: "板チョコ", scale: "大", levels: 3 },
};
export const maxSplit = 10;
/** 1つの房（チョコなら列、キャンディーなら扇）に描くかけらの上限。超えた分はまとめて1つに描く。 */
const cellCap: Record<SweetKind, number> = { sugar: 1, bonbon: 4, chocolate: 9 };

export interface SweetBits {
  /** 1階層目の子ごとの、描くかけらの状態。角砂糖は子1つ = かけら1つ。 */
  groups: BitState[][];
  leaves: number;
  done: number;
  active: number;
}

export interface SweetPlan {
  root: Task;
  kind: SweetKind;
  bits: SweetBits;
  leafIds: string[];
  /** 担当者ごとの関わり方。着手中・未着手のみ・完了のみ。 */
  eaters: string[];
  waiters: string[];
  carriers: string[];
  finished: boolean;
}

export interface DistrictPlan {
  root: Task;
  sweets: SweetPlan[];
}

// lib/wbsLocalOrder.ts の compareTaskForWbsOrder と同じ順序。
// node のテストランナーは拡張子なしの実行時 import を解決できないため、ここに複製している。
export function compareWbs(a: Task, b: Task): number {
  if (a.WbsSortOrder != null || b.WbsSortOrder != null) {
    if (a.WbsSortOrder == null) return 1;
    if (b.WbsSortOrder == null) return -1;
    if (a.WbsSortOrder !== b.WbsSortOrder) return a.WbsSortOrder - b.WbsSortOrder;
  }
  if (a.EndDate !== b.EndDate) return (a.EndDate ?? "").localeCompare(b.EndDate ?? "");
  if (a.CreateDate !== b.CreateDate) return (a.CreateDate ?? "").localeCompare(b.CreateDate ?? "");
  if (a.Name !== b.Name) return a.Name < b.Name ? -1 : 1;
  return a.Id < b.Id ? -1 : a.Id > b.Id ? 1 : 0;
}

export function kindForHeight(height: number): SweetKind {
  return height >= 3 ? "chocolate" : height === 2 ? "bonbon" : "sugar";
}

function stateOf(task: Task, timer: ActiveTimer | null | undefined): BitState {
  if (task.Status === "Completion" || task.Status === "Abort") return "done";
  if (task.Status === "DuringStart" || task.Id === timer?.TaskId) return "active";
  return "open";
}

/** 多すぎる末端を cap 個のかけらにまとめる。全部済み → 欠け、どれか着手 → 着手、それ以外 → 残り。 */
export function squash(states: BitState[], cap: number): BitState[] {
  if (states.length <= cap) return states;
  return Array.from({ length: cap }, (_, i) => {
    const part = states.slice(Math.floor((i * states.length) / cap), Math.floor(((i + 1) * states.length) / cap));
    if (part.every((s) => s === "done")) return "done";
    return part.some((s) => s === "active") ? "active" : "open";
  });
}

export function planSweets(tasks: Task[], timer?: ActiveTimer | null) {
  const visible = tasks.filter((task) => !task.IQuavisSyncExcluded);
  const byId = new Map(visible.map((task) => [task.Id, task]));
  const children = new Map<string | null, Task[]>();
  for (const task of visible) {
    const key = task.ParentTaskId && byId.has(task.ParentTaskId) ? task.ParentTaskId : null;
    const list = children.get(key) ?? [];
    list.push(task);
    children.set(key, list);
  }
  for (const list of children.values()) list.sort(compareWbs);
  const kids = (id: string) => children.get(id) ?? [];
  const parentIds = new Set([...children.keys()].filter((key): key is string => key !== null));
  const heights = new Map<string, number>();
  const heightOf = (id: string, guard = 0): number => {
    const cached = heights.get(id);
    if (cached !== undefined) return cached;
    let h = 0;
    // 親子が循環していても止まるように深さを打ち切る。
    if (guard < 64) for (const child of kids(id)) h = Math.max(h, heightOf(child.Id, guard + 1) + 1);
    heights.set(id, h);
    return h;
  };
  const leavesOf = (task: Task, guard = 0): Task[] =>
    !kids(task.Id).length || guard > 8 ? [task] : kids(task.Id).flatMap((child) => leavesOf(child, guard + 1));

  function plan(root: Task): SweetPlan {
    const kind = kindForHeight(heightOf(root.Id));
    const firsts = kids(root.Id).length ? kids(root.Id) : [root];
    const leaves = leavesOf(root);
    const groups = firsts.map((first) =>
      squash(
        leavesOf(first).map((leaf) => stateOf(leaf, timer)),
        cellCap[kind],
      ),
    );
    const states = leaves.map((leaf) => stateOf(leaf, timer));
    const owners = (wanted: BitState) =>
      new Set(leaves.filter((_, i) => states[i] === wanted).flatMap((leaf) => (leaf.AssigneeUserId ? [leaf.AssigneeUserId] : [])));
    const active = owners("active"),
      open = owners("open"),
      done = owners("done");
    const sorted = (set: Set<string>) => [...set].sort();
    const doneCount = states.filter((s) => s === "done").length;
    return {
      root,
      kind,
      bits: {
        groups: groups.slice(0, maxSplit),
        leaves: leaves.length,
        done: doneCount,
        active: states.filter((s) => s === "active").length,
      },
      leafIds: leaves.map((leaf) => leaf.Id),
      eaters: sorted(active),
      waiters: sorted(new Set([...open].filter((id) => !active.has(id)))),
      carriers: sorted(new Set([...done].filter((id) => !active.has(id) && !open.has(id)))),
      finished: doneCount === leaves.length,
    };
  }

  // 一番上のタスクごとに区画を作る。深さ3以内に収まる部分木をお菓子にする。
  const districts: DistrictPlan[] = [];
  for (const top of children.get(null) ?? []) {
    const sweets: SweetPlan[] = [];
    const visit = (task: Task, guard: number) => {
      if (heightOf(task.Id) <= 3 || guard > 64) sweets.push(plan(task));
      else for (const child of kids(task.Id)) visit(child, guard + 1);
    };
    visit(top, 0);
    districts.push({ root: top, sweets });
  }
  return { districts, parentIds };
}
