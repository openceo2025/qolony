import districtCatalog from "./districts.json" with { type: "json" };
import type { SweetBits } from "./liveModel";

export interface Point {
  x: number;
  y: number;
}
export const nest = { x: 0, y: 18 };
export const extent = { x: 2250, y: 1350 };
export const districts = districtCatalog;
export const fieldCrew = districts.flatMap((district) =>
  district.staff.map((name, index) => ({
    id: `field-${district.id}-${index}`,
    name,
    team: district.id,
    carrier: index === 0,
    code: district.code,
    years: [6, 3, 2][index],
    overtime: [7.5, 4, 2.5][index],
  })),
);
export const roles = [
  { id: "mio", name: "ミオ", role: "従業員", max: 2, accessory: "scarf" },
  { id: "ren", name: "レン", role: "係長", max: 3, accessory: "cap" },
  { id: "yui", name: "ユイ", role: "課長", max: 4, accessory: "scarf" },
  { id: "kei", name: "ケイ", role: "社長", max: 5, accessory: "cap" },
  ...fieldCrew
    .filter((member) => member.carrier)
    .map(({ id, name }) => ({ id, name, role: "従業員", max: 2, accessory: "scarf" }) as const),
] as const;
export type Accessory = "scarf" | "cap" | "bow";
export const kinds = ["sugar", "bonbon", "cookie", "chocolate", "lollipop"] as const;
export type Kind = (typeof kinds)[number];
export interface WorkDetail {
  input: string;
  output: string;
  issue: string;
  estimateMH: number;
  bookedMH: number;
  baseProgress: number;
  origin: Point;
}
// 作業開始前に済んでいる準備分を含む、ワールド展示用の架空の案件。
const examples: Record<string, [string, string, string, number, number, number, number, number]> = {
  sugar: ["仕入先の見積書", "承認済み見積書", "納期回答待ち", 2, 0.5, 0.25, -185, 122],
  bar: ["顧客ヒアリング記録", "新商品仕様書", "検収条件を確認中", 12, 2.5, 0.18, 300, -96],
  pop: ["前回の施策レポート", "春の企画提案書", "予算枠を調整中", 16, 8, 0.48, -324, -130],
  wrap: ["営業メモ・見積書", "顧客向け提案資料", "図版の許諾確認", 5, 1.5, 0.32, 283, 153],
  biscuit: ["研修アンケート", "改訂版テキスト", "原稿を校閲中", 8, 3, 0.4, -730, 155],
  audit: ["月次売上明細", "月次照合表", "差額の出所を確認", 3, 1, 0.3, 717, 218],
  manual: ["現行手順・現場メモ", "新版の作業手順書", "レビュー日程調整", 6, 2, 0.3, 660, -365],
  plan: ["部門別実績", "次期の事業計画", "見込み値を精査中", 18, 6, 0.32, -670, -445],
};
export const sweets: Record<Kind, string> = {
  sugar: "角砂糖",
  bonbon: "包みキャンディー",
  cookie: "ビスケット",
  chocolate: "板チョコ",
  lollipop: "ぐるぐるキャンディー",
};
export interface Food extends Point {
  id: string;
  name: string;
  kind: Kind;
  weight: number;
  due: number;
  owner: string | null;
  since: number | null;
  progress: number;
  help: boolean;
  requirement: string;
  sales: number;
  profit: number;
  piece: number;
  detail?: WorkDetail;
  task?: { progress: number; signal: string; status: string; planned: string; outputs: string };
  /** 実タスクのお菓子だけが持つ。どこまで割れて、どこが欠けたか。 */
  bits?: SweetBits;
  /** 当たり判定と吹き出しの基準にする大きさ。無ければ artSize。 */
  size?: number;
}
/** 実タスクの地図で、お菓子のまわりにいるアリ。担当者 × お菓子で1匹。 */
export interface CrewAnt {
  id: string;
  owner: string;
  food: string;
  /** eat = 着手中で食べている / wait = 未着手のかけらの前で作業待ち / carry = 完了分を巣へ運んでいる */
  mode: "eat" | "wait" | "carry";
  point: Point;
  face: 1 | -1;
  offset: number;
}
export interface Team {
  id: string;
  name: string;
  parent: string | null;
  members: string[];
}
export interface Mark {
  id: string;
  name: string;
  team: string | null;
  points: Point[];
  color?: string;
  rounded?: boolean;
  summary?: AreaProgress;
}

interface AreaProgress {
  percent: number | null;
  count: number;
  active: number;
  completed: number;
}
export interface ColonyMember {
  name: string;
  role: string;
  point: Point;
}
export interface WorldEvent extends Point {
  id: string;
  type: "split" | "drop";
  at: number;
  kind: Kind;
  piece: number;
  actor: string;
}
export interface World {
  version: 1;
  districtEdition?: 1;
  foods: Food[];
  teams: Team[];
  marks: Mark[];
  events: WorldEvent[];
  delivered: number;
  receipts?: { origin: Point; estimateMH: number }[];
  credits: Record<string, number>;
  accessories: Record<string, Accessory>;
  project?: { members: Record<string, ColonyMember>; extent: Point; crew?: CrewAnt[] };
}
export const hour = 3600000;
export const distance = (a: Point, b: Point) => Math.hypot(a.x - b.x, a.y - b.y);
export const clamp = (v: number, low: number, high: number) => Math.max(low, Math.min(high, v));
function rimDistance(p: Point) {
  return Math.hypot((p.x - nest.x) / 102, (p.y - nest.y) / 63);
}
// 旧デモ保存では詳細がない。既知の分割片は元案件の工数を分配して補う。
export function detailOf(f: Food): WorkDetail {
  if (f.detail) return f.detail;
  const original = f.id.replace(/(?:-[ab])+$/, "");
  const example = examples[original];
  if (example) {
    const [input, output, issue, estimate, booked, baseProgress, x, y] = example;
    const share = 2 ** -f.piece;
    return {
      input,
      output,
      issue,
      estimateMH: estimate * share,
      bookedMH: booked * share,
      baseProgress,
      origin: { x, y },
    };
  }
  return {
    input: "未設定",
    output: "未設定",
    issue: "未登録",
    estimateMH: f.weight * 2,
    bookedMH: 0,
    baseProgress: 0,
    origin: { x: f.x, y: f.y },
  };
}
export function actualMH(f: Food, now: number) {
  return detailOf(f).bookedMH + (f.since === null ? 0 : Math.max(0, now - f.since) / hour);
}
export function workProgress(f: Food, now: number) {
  const base = detailOf(f).baseProgress;
  return base + (1 - base) * progressAt(f, now);
}
export function isRounded(mark: Mark) {
  return mark.rounded || mark.id === "zone-a" || mark.id === "zone-b";
}
export function areaBoundary(mark: Mark): Point[] {
  const pts = mark.points;
  if (!isRounded(mark)) return pts;
  // 初期エリアの角は地面の二次ベジェ曲線に合わせて丸める。
  return pts.flatMap((p, i) => {
    const prev = pts[(i + pts.length - 1) % pts.length],
      next = pts[(i + 1) % pts.length];
    const start = { x: (prev.x + p.x) / 2, y: (prev.y + p.y) / 2 },
      end = { x: (p.x + next.x) / 2, y: (p.y + next.y) / 2 };
    return Array.from({ length: 12 }, (_, k) => {
      const t = k / 12,
        a = 1 - t;
      return {
        x: a * a * start.x + 2 * a * t * p.x + t * t * end.x,
        y: a * a * start.y + 2 * a * t * p.y + t * t * end.y,
      };
    });
  });
}
function containsPoint(points: Point[], p: Point) {
  let inside = false;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    const a = points[i],
      b = points[j];
    if (a.y > p.y !== b.y > p.y && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) inside = !inside;
  }
  return inside;
}
export function areaProgress(world: World, mark: Mark, now: number) {
  if (mark.summary) return mark.summary;
  const polygon = areaBoundary(mark);
  let total = 0,
    earned = 0,
    active = 0,
    completed = 0,
    count = 0;
  for (const f of world.foods) {
    const d = detailOf(f);
    if (!containsPoint(polygon, d.origin)) continue;
    total += d.estimateMH;
    earned += d.estimateMH * workProgress(f, now);
    count++;
    if (f.since !== null) active++;
  }
  for (const receipt of world.receipts ?? []) {
    if (!containsPoint(polygon, receipt.origin)) continue;
    total += receipt.estimateMH;
    earned += receipt.estimateMH;
    completed++;
    count++;
  }
  return { percent: total ? Math.round((100 * earned) / total) : null, count, active, completed };
}
export function makeFood(id: string, name: string, kind: Kind, p: Point, due: number): Food {
  return {
    id,
    name,
    kind,
    ...p,
    due,
    weight: kinds.indexOf(kind) + 1,
    owner: null,
    since: null,
    progress: 0,
    help: false,
    requirement: "",
    sales: 0,
    profit: 0,
    piece: 0,
  };
}
const taskSites: Point[] = [
  { x: -0.65, y: -0.22 },
  { x: -0.22, y: -0.55 },
  { x: 0.29, y: -0.54 },
  { x: 0.66, y: -0.1 },
  { x: 0.54, y: 0.44 },
  { x: 0.04, y: 0.52 },
  { x: -0.42, y: 0.36 },
  { x: -0.13, y: -0.02 },
];
const fieldSweets: Kind[] = [
  "lollipop",
  "chocolate",
  "bonbon",
  "cookie",
  "chocolate",
  "sugar",
  "bonbon",
  "cookie",
];
// 仕事が分割・納品されて消えた後も、次の読込で同じ仕事を配置し直さない。
export function populateDistricts(world: World, now: number): World {
  if (world.districtEdition === 1) return world;
  const foods = [...world.foods],
    teams = [...world.teams],
    marks = [...world.marks];
  districts.forEach((d, index) => {
    const members = fieldCrew.filter((member) => member.team === d.id).map((member) => member.id);
    teams.push({ id: d.id, name: d.name, parent: "division", members });
    const points = Array.from({ length: 10 }, (_, i) => {
      const angle = ((i + index * 0.14) * 2 * Math.PI) / 10;
      const contour = 1 + 0.07 * Math.sin(3 * angle + index) + 0.05 * Math.cos(5 * angle);
      return {
        x: d.x + d.radiusX * (Math.cos(angle) * contour + 0.1 * Math.sin(angle)),
        y: d.y + d.radiusY * Math.sin(angle) * contour,
      };
    });
    marks.push({ id: "field-zone-" + d.id, team: d.id, name: d.name, points, color: d.color, rounded: true });
    d.jobs.forEach(([name, input, output], i) => {
      const site = taskSites[(i + index * 3) % taskSites.length];
      const origin = { x: Math.round(d.x + site.x * d.radiusX), y: Math.round(d.y + site.y * d.radiusY) };
      const kind = fieldSweets[(i + index) % fieldSweets.length];
      const f = makeFood(
        `field-${d.id}-job-${i + 1}`,
        name,
        kind,
        origin,
        now + (32 + ((index * 13 + i * 17) % 136)) * hour,
      );
      const estimateMH = (2 + f.weight * 2) * (i === 0 ? 2 : 1);
      const progress = clamp(d.progress + 0.18 * Math.sin((i + 1) * (index + 1)), 0.05, 0.92);
      f.detail = {
        input,
        output,
        issue: d.issues[i % d.issues.length],
        estimateMH,
        bookedMH: Math.round(estimateMH * progress * 4) / 4,
        baseProgress: progress,
        origin,
      };
      f.requirement = input + "を確認し、" + output + "をまとめる。";
      if (i === 2) {
        f.owner = members[0];
        f.since = now;
        f.help = index === 2 || index === 5;
      }
      foods.push(f);
    });
  });
  return { ...world, foods, teams, marks, districtEdition: 1 };
}
export function initialWorld(now: number): World {
  const task = (id: string, name: string, kind: Kind, x: number, y: number, hours: number) =>
    makeFood(id, name, kind, { x, y }, now + hour * hours);
  const foods = [
    task("sugar", "見積書を確認する", "sugar", -185, 122, 26),
    task("bar", "新商品の仕様をまとめる", "chocolate", 300, -96, 5),
    task("pop", "春のキャンペーンを企画する", "lollipop", -324, -130, 110),
    task("wrap", "提案資料を整える", "bonbon", 283, 153, 36),
    task("biscuit", "研修テキストを編集する", "cookie", -730, 155, 52),
    task("audit", "月次集計を照合する", "sugar", 717, 218, 72),
    task("manual", "手順書を見直す", "cookie", 660, -365, 85),
    task("plan", "次期計画を立てる", "chocolate", -670, -445, 140),
  ];
  foods[0].requirement = "金額と納期を照合して、見積書を共有する。";
  foods[1].requirement = "仕様と受け入れ条件を整理する。分割して進められる。";
  foods[6].owner = "ren";
  foods[6].since = now;
  return populateDistricts(
    {
      version: 1,
      foods,
      delivered: 0,
      credits: {},
      accessories: {},
      events: [],
      teams: [
        { id: "division", name: "事業部", parent: null, members: ["kei"] },
        { id: "design", name: "企画チーム", parent: "division", members: ["mio", "yui"] },
        { id: "ops", name: "運営チーム", parent: "division", members: ["ren"] },
      ],
      marks: [
        {
          id: "zone-a",
          name: "企画チーム",
          team: "design",
          points: [
            { x: -567, y: -242 },
            { x: -225, y: -303 },
            { x: 65, y: -206 },
            { x: 159, y: 36 },
            { x: -33, y: 215 },
            { x: -415, y: 245 },
            { x: -625, y: 68 },
          ],
        },
        {
          id: "zone-b",
          name: "運営チーム",
          team: "ops",
          points: [
            { x: -78, y: -135 },
            { x: 202, y: -275 },
            { x: 553, y: -170 },
            { x: 613, y: 95 },
            { x: 410, y: 259 },
            { x: 87, y: 254 },
            { x: -90, y: 98 },
          ],
        },
        {
          id: "axis",
          name: "新しい取り組み",
          team: null,
          points: [
            { x: -1300, y: -576 },
            { x: 1200, y: -318 },
          ],
        },
      ],
    },
    now,
  );
}
// デモの運搬は早送りする。巣のふちで待機し、完了入力で初めて納品する。
export function progressAt(f: Food, now: number) {
  const routeLength = Math.max(70, distance(f, nest));
  const speed = (f.help ? 6 : 3.4) / Math.sqrt(f.weight);
  const edge = Math.max(0, 1 - 1 / Math.max(0.001, rimDistance(f)));
  return clamp(
    f.progress + (f.since === null ? 0 : (Math.max(0, now - f.since) * 0.001 * speed) / routeLength),
    0,
    edge,
  );
}
export function positionAt(f: Food, now: number): Point {
  if (f.task) {
    // 工数タイマーを模擬せず、記録済みの進捗分だけエサを巣の方向へ動かす。
    const offset = (f.task.progress / 100) * Math.min(32, distance(f, nest) * 0.1);
    const fraction = offset / Math.max(1, distance(f, nest));
    return { x: f.x + (nest.x - f.x) * fraction, y: f.y + (nest.y - f.y) * fraction };
  }
  const p = progressAt(f, now);
  return { x: f.x + (nest.x - f.x) * p, y: f.y + (nest.y - f.y) * p };
}
export function carriedCredit(f: Food, now: number) {
  return Math.round(distance(f, nest) * progressAt(f, now) * f.weight);
}
export function memberCredit(world: World, actor: string, now: number) {
  return (
    (world.credits[actor] ?? 0) +
    world.foods.reduce((sum, f) => sum + (f.owner === actor ? carriedCredit(f, now) : 0), 0)
  );
}
export function deadline(f: Food, now: number) {
  const hours = Math.max(0, (f.due - now) / hour);
  const estimate = (distance(positionAt(f, now), nest) * Math.sqrt(f.weight)) / (f.help ? 72 : 36);
  return {
    label:
      f.task?.status === "Completion"
        ? "完了"
        : f.task?.status === "Abort"
          ? "中止"
          : !Number.isFinite(f.due)
            ? "期日なし"
            : hours === 0
              ? "期限超過"
              : hours <= 24
                ? "残り" + Math.ceil(hours) + "時間"
                : "残り" + Math.ceil(hours / 24) + "日",
    risk: f.task
      ? !["Completion", "Abort"].includes(f.task.status) && (f.task.signal === "Red" || hours === 0)
      : estimate > hours,
    fraction: f.task ? clamp(f.task.progress / 100, 0, 1) : clamp(hours / Math.max(1, estimate * 3), 0.04, 1),
  };
}
export type Action =
  | { type: "start" | "pause" | "finish" | "split" | "help"; id: string }
  | { type: "place"; food: Food }
  | { type: "mark"; mark: Mark }
  | { type: "team"; team: Team }
  | { type: "accessory"; value: Accessory };
export function applyAction(previous: World, action: Action, actor: string, now: number): World {
  const role = roles.find((r) => r.id === actor);
  if (!role) throw Error("アリを選んでください。");
  const w: World = {
    ...previous,
    foods: [...previous.foods],
    events: previous.events.filter((e) => now - e.at < 2400),
  };
  if (action.type === "accessory") return { ...w, accessories: { ...w.accessories, [actor]: action.value } };
  if (action.type === "place") {
    const f = action.food;
    if (!f.name.trim() || f.weight > role.max || f.due <= now || rimDistance(f) < 1.15)
      throw Error("名前・期限・役職の上限を確認し、巣から少し離して置いてください。");
    return { ...w, foods: [...w.foods, { ...f, detail: { ...detailOf(f), origin: { x: f.x, y: f.y } } }] };
  }
  if (action.type === "team") {
    const t = action.team;
    if (
      role.max < 4 ||
      !t.name.trim() ||
      !t.members.length ||
      (t.parent && !w.teams.some((p) => p.id === t.parent))
    )
      throw Error("課長以上でチーム名とメンバーを指定してください。");
    return { ...w, teams: [...w.teams, t] };
  }
  if (action.type === "mark") {
    const m = action.mark;
    if (
      !m.name.trim() ||
      (m.team
        ? role.max < 4 || !w.teams.some((t) => t.id === m.team) || m.points.length < 3
        : role.max < 5 || m.points.length < 2)
    )
      throw Error("役職と線を描く点の数を確認してください。");
    return { ...w, marks: [...w.marks.filter((old) => !m.team || old.team !== m.team), m] };
  }
  const i = w.foods.findIndex((f) => f.id === action.id);
  if (i < 0) throw Error("この仕事はすでに移動または完了しました。");
  const food = w.foods[i],
    p = positionAt(food, now),
    progress = progressAt(food, now);
  const update = (changes: Partial<Food>) => {
    w.foods[i] = { ...food, ...changes };
    return w;
  };
  if (action.type === "help") {
    if (role.max < 3 || food.help) throw Error("応援は係長以上が一度だけ呼べます。");
    return update({
      help: true,
      progress,
      detail: { ...detailOf(food), bookedMH: actualMH(food, now) },
      since: food.since === null ? null : now,
    });
  }
  if (food.owner && food.owner !== actor) throw Error("別のアリが担当しています。");
  if (action.type === "start") {
    if (food.since !== null || w.foods.some((f) => f.owner === actor && f.since !== null))
      throw Error("いま運んでいる仕事を先に一時停止してください。");
    return update({ owner: actor, since: now });
  }
  if (action.type === "pause") {
    if (food.owner !== actor || food.since === null) throw Error("運搬中の自分の仕事を選んでください。");
    return update({ progress, since: null, detail: { ...detailOf(food), bookedMH: actualMH(food, now) } });
  }
  if (action.type === "finish" && food.owner !== actor) throw Error("先にこの仕事を引き受けてください。");
  if (action.type === "split" && food.weight <= 1) throw Error("これ以上は分割できません。");
  const splitting = action.type === "split";
  w.events.push({
    id: food.id + "-" + action.type,
    type: splitting ? "split" : "drop",
    at: now,
    kind: food.kind,
    piece: food.piece,
    actor,
    ...p,
  });
  w.credits = {
    ...w.credits,
    [actor]: (w.credits[actor] ?? 0) + carriedCredit(food, now) + (splitting ? food.weight * food.weight : 1),
  };
  w.foods.splice(i, 1);
  const detail = detailOf(food);
  if (!splitting)
    return {
      ...w,
      delivered: w.delivered + 1,
      receipts: [...(w.receipts ?? []), { origin: detail.origin, estimateMH: detail.estimateMH }],
    };
  for (const side of [-1, 1]) {
    const first = side < 0;
    const half = (amount: number) => (first ? Math.floor(amount / 2) : Math.ceil(amount / 2));
    const offset = food.kind === "chocolate" && !food.piece ? 46 : 32;
    w.foods.push({
      ...food,
      id: food.id + (first ? "-a" : "-b"),
      name: food.name + (first ? " ①" : " ②"),
      x: p.x + side * offset,
      y: p.y + side * 7,
      weight: Math.max(1, Math.ceil(food.weight / 2)),
      piece: food.piece + 1,
      detail: {
        ...detail,
        estimateMH: detail.estimateMH / 2,
        bookedMH: actualMH(food, now) / 2,
        baseProgress: workProgress(food, now),
      },
      sales: half(food.sales),
      profit: half(food.profit),
      progress: 0,
      owner: null,
      since: null,
      help: false,
    });
  }
  return w;
}
