import { actualMH, areaProgress, clamp, deadline, detailOf, distance, fieldCrew, sweets } from "./model";
import type { Food, Point, World } from "./model";

type Box = { x: number; y: number; w: number; h: number };
type Note = {
  id: string;
  kind: "food" | "ant" | "area" | "line";
  label: string;
  value: string;
  anchor: Point;
  box: Box;
  alpha: number;
  percent?: number | null;
  caption?: string;
  tone?: string;
};
export type AntAnchor = { id: string; profile: string; p: Point; doing?: string };
type Project = (p: Point) => Point;
const profiles: Record<string, [string, string, string, string, number]> = {
  mio: ["ミオ", "従業員", "PLN-01", "3年目", 6.5],
  ren: ["レン", "係長", "OPS-02", "8年目", 12],
  yui: ["ユイ", "課長", "PLN-01", "12年目", 9],
  kei: ["ケイ", "社長", "HQ-01", "18年目", 4],
  sora: ["ソラ", "従業員", "PLN-01", "2年目", 4],
  hana: ["ハナ", "従業員", "OPS-02", "5年目", 8],
  aoi: ["アオイ", "従業員", "PLN-01", "4年目", 3.5],
  ritsu: ["リツ", "従業員", "OPS-02", "6年目", 7],
  nao: ["ナオ", "従業員", "OPS-02", "3年目", 5.5],
  mei: ["メイ", "従業員", "PLN-01", "2年目", 2],
  rui: ["ルイ", "従業員", "OPS-02", "7年目", 10],
  yuna: ["ユナ", "従業員", "PLN-01", "1年目", 2.5],
  helper: ["ヒナ", "応援担当", "SUP-01", "5年目", 6],
};
export const roamingProfiles = ["sora", "hana", "aoi", "ritsu", "nao", "mei", "rui", "yuna"];
for (const member of fieldCrew)
  profiles[member.id] = [member.name, "従業員", member.code, member.years + "年目", member.overtime];
const ns = "http://www.w3.org/2000/svg";
const reveal = (zoom: number, threshold: number) => clamp((zoom - threshold) / 0.1, 0, 1);
const mh = (value: number) => value.toLocaleString("ja-JP", { maximumFractionDigits: 2 }) + " MH";
interface Frame {
  world: World;
  now: number;
  zoom: number;
  width: number;
  height: number;
  project: Project;
  ants: AntAnchor[];
  foods: { f: Food; p: Point }[];
  aim: Point;
  selected: string | null;
}
function overlaps(a: Box, b: Box, gap = 5) {
  return (
    Math.max(0, Math.min(a.x + a.w, b.x + b.w + gap) - Math.max(a.x, b.x - gap)) *
    Math.max(0, Math.min(a.y + a.h, b.y + b.h + gap) - Math.max(a.y, b.y - gap))
  );
}
// 地図上の対象に近い場所を優先し、他の吹き出しや操作パネルをよける。
function place(ideal: Box, width: number, height: number, obstacles: Box[], anchor: Point) {
  let result = ideal,
    min = Infinity;
  for (const dx of [0, 48, -48, 108, -108, 200, -200]) {
    for (const dy of [0, -30, 30, -65, 65, -105, 105]) {
      const b = {
        ...ideal,
        x: clamp(ideal.x + dx, 8, Math.max(8, width - ideal.w - 8)),
        y: clamp(ideal.y + dy, 8, Math.max(8, height - ideal.h - 8)),
      };
      const cost =
        obstacles.reduce((s, r) => s + overlaps(b, r), 0) * 30 +
        Math.hypot(b.x - ideal.x, b.y - ideal.y) +
        (anchor.x > b.x && anchor.x < b.x + b.w && anchor.y > b.y && anchor.y < b.y + b.h ? 500 : 0);
      if (cost < min) {
        min = cost;
        result = b;
      }
      if (cost === 0) return b;
    }
  }
  return result;
}
function nearest<T extends { anchor: Point }>(items: T[], aim: Point) {
  return items.reduce<T | undefined>(
    (best, v) => (!best || distance(v.anchor, aim) < distance(best.anchor, aim) ? v : best),
    undefined,
  );
}
function nearestOnLine(points: Point[], center: Point) {
  let best = points[0];
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1],
      b = points[i],
      dx = b.x - a.x,
      dy = b.y - a.y;
    const t = clamp(((center.x - a.x) * dx + (center.y - a.y) * dy) / (dx * dx + dy * dy || 1), 0, 1);
    const p = { x: a.x + dx * t, y: a.y + dy * t };
    if (distance(center, p) < distance(center, best)) best = p;
  }
  return best;
}
function notesFor(
  frame: Frame,
  obstacles: Box[],
  stats: (mark: World["marks"][number]) => ReturnType<typeof areaProgress>,
) {
  const { world, now, zoom: z, width, height, project } = frame;
  const notes: Note[] = [];
  const add = (n: Note) => {
    if (n.alpha <= 0) return;
    n.box = place(n.box, width, height, obstacles, n.anchor);
    obstacles.push(n.box);
    notes.push(n);
  };
  const visible = (p: Point) => p.x >= 2 && p.x <= width - 2 && p.y >= 2 && p.y <= height - 2;
  const exposed = (p: Point) =>
    visible(p) && !obstacles.some((r) => p.x >= r.x && p.x <= r.x + r.w && p.y >= r.y && p.y <= r.y + r.h);
  if (z < 0.76) {
    const alpha = clamp((0.76 - z) / 0.2, 0, 1);
    let marks = [...world.marks.entries()];
    if (world.project) {
      // 実タスクの区画は数十になる。画面で小さすぎる区画は札を出さず、画面中央に近い順に8枚までにする。
      const middle = { x: width / 2, y: height / 2 };
      marks = marks
        .map(([i, m]) => {
          const pts = m.points.map(project);
          const span = Math.max(...pts.map((p) => p.x)) - Math.min(...pts.map((p) => p.x));
          const center = pts.reduce((p, v) => ({ x: p.x + v.x / pts.length, y: p.y + v.y / pts.length }), { x: 0, y: 0 });
          return { i, m, span, d: distance(center, middle) };
        })
        .filter((v) => v.span > 150)
        .sort((a, b) => a.d - b.d)
        .slice(0, 8)
        .map(({ i, m }) => [i, m] as [number, typeof m]);
    }
    for (const [i, m] of marks) {
      const pts = m.points.map(project);
      if (!m.team) {
        const anchor = nearestOnLine(pts, { x: width / 2, y: height / 2 });
        if (visible(anchor))
          add({
            id: "line-" + m.id,
            kind: "line",
            label: "方針線",
            value: m.name,
            anchor,
            box: { x: anchor.x - 83, y: anchor.y - 61, w: 166, h: 44 },
            alpha,
          });
        continue;
      }
      const center = pts.reduce((p, v) => ({ x: p.x + v.x / pts.length, y: p.y + v.y / pts.length }), {
        x: 0,
        y: 0,
      });
      const anchor = visible(center)
        ? center
        : nearestOnLine([...pts, pts[0]], { x: width / 2, y: height / 2 });
      if (!visible(anchor)) continue;
      const data = stats(m),
        left = center.x < project({ x: 0, y: 0 }).x;
      const reach = clamp(
        (Math.max(...pts.map((p) => p.x)) - Math.min(...pts.map((p) => p.x))) * 0.42,
        60,
        160,
      );
      const outer = !!world.project || m.id.startsWith("field-zone-");
      const regionHeight = Math.max(...pts.map((p) => p.y)) - Math.min(...pts.map((p) => p.y));
      const above = center.y < project({ x: 0, y: 0 }).y;
      const cardWidth = 188;
      const box = outer
        ? {
            x: anchor.x - cardWidth / 2,
            y: above ? anchor.y - regionHeight * 0.4 - 54 : anchor.y + regionHeight * 0.38,
            w: cardWidth,
            h: 51,
          }
        : { x: anchor.x + (left ? -reach - cardWidth : reach), y: anchor.y + 24, w: cardWidth, h: 51 };
      add({
        id: "area-" + m.id,
        kind: "area",
        label: m.name,
        value: data.percent === null ? "—" : data.percent + "%",
        anchor,
        percent: data.percent,
        caption: world.project?.crew
          ? `かけら ${data.count} · 食べている ${data.active} · 運んだ ${data.completed}`
          : data.count
            ? `仕事 ${data.count}件 · 運搬中 ${data.active} · 納品 ${data.completed}`
            : "仕事 0件",
        tone: m.color ?? (i % 2 ? "#317e72" : "#a65535"),
        box,
        alpha,
      });
    }
    return notes;
  }
  if (z <= 1.32) return notes;
  const visibleFoods = frame.foods
    .map(({ f, p }) => ({ f, anchor: project({ x: p.x, y: p.y - (f.kind === "lollipop" ? 44 : 11) }) }))
    .filter((v) => exposed(v.anchor));
  const visibleAnts = frame.ants
    .map((a) => ({ ...a, anchor: project(a.p) }))
    .filter((v) => exposed(v.anchor));
  const focusFood = visibleFoods.find((v) => v.f.id === frame.selected) ?? nearest(visibleFoods, frame.aim);
  const focusAnt = nearest(visibleAnts, frame.aim);
  if (!focusFood && !focusAnt) return notes;
  for (const item of [focusFood, focusAnt])
    if (item) {
      const p = item.anchor,
        r = item === focusFood ? 43 : 23;
      obstacles.push({ x: p.x - r, y: p.y - r, w: 2 * r, h: 2 * r });
    }
  // 近接したアリとエサは左右に分け、同一対象の窓は同じ方向に扇状に引き出す。
  let foodRight = focusFood ? focusFood.anchor.x < width / 2 : true;
  if (focusFood && focusAnt && distance(focusFood.anchor, focusAnt.anchor) < 400)
    foodRight = focusFood.anchor.x >= focusAnt.anchor.x;
  const group = (
    id: string,
    kind: "food" | "ant",
    p: Point,
    right: boolean,
    fields: [string, string, number][],
  ) => {
    const rows = fields.filter((f) => z > f[2]);
    const w = kind === "food" ? 162 : 128;
    const heights = rows.map(([, value]) => {
      const lines = Math.ceil(value.length / Math.floor((w - 20) / 13));
      return Math.max(41, 24 + 16 * (world.project ? Math.min(3, lines) : lines));
    });
    const total = heights.reduce((a, b) => a + b, 0) + (rows.length - 1) * 5;
    let y = clamp(p.y - total / 2, 8, Math.max(8, height - total - 8));
    if (right && p.x + w + 48 > width) right = false;
    else if (!right && p.x - w - 48 < 0) right = true;
    rows.forEach(([label, value, threshold], i) => {
      const reach = (kind === "food" ? 88 : 55) + 14 * Math.sin(((i + 0.5) * Math.PI) / rows.length);
      add({
        id: id + "/" + label,
        kind,
        label,
        value,
        anchor: p,
        box: { x: p.x + (right ? reach : -reach - w), y, w, h: heights[i] },
        alpha: reveal(z, threshold),
      });
      y += heights[i] + 5;
    });
  };
  if (focusFood) {
    const { f, anchor } = focusFood,
      d = detailOf(f);
    group(
      f.id,
      "food",
      anchor,
      foodRight,
      f.bits
        ? [
            // 実タスクのお菓子は、寄るほど少しずつ情報を足す。細かい中身はタスク詳細で見る。
            ["件名", f.name, 1.32],
            ["かけら", `残り ${f.bits.leaves - f.bits.done} / ${f.bits.leaves}（${sweets[f.kind]}）`, 1.49],
            ["食べている", f.bits.active ? f.bits.active + " かけら" : "なし", 1.62],
            ["期日", deadline(f, now).label, 1.75],
            ["見積 / 実績", mh(d.estimateMH) + " / " + mh(d.bookedMH), 1.89],
          ]
        : f.task
        ? [
            ["件名", f.name, 1.32],
            ["予定成果物", f.task.planned, 1.49],
            ["登録成果物", f.task.outputs, 1.62],
            ["見積MH", mh(d.estimateMH), 1.72],
            ["実績MH", mh(d.bookedMH), 1.82],
            ["メモ", d.issue, 1.89],
            ["期日", deadline(f, now).label, 2.33],
          ]
        : [
            ["件名", f.name, 1.32],
            ["インプット", d.input, 1.49],
            ["アウトプット", d.output, 1.62],
            ["見積MH", mh(d.estimateMH), 1.72],
            ["実績MH", mh(actualMH(f, now)), 1.82],
            ["課題", d.issue, 1.89],
            ["期限", deadline(f, now).label, 2.33],
          ],
    );
  }
  if (focusAnt) {
    const [name, role, dept, years, ot] = profiles[focusAnt.profile] ?? profiles.helper;
    const near = !!focusFood && distance(focusFood.anchor, focusAnt.anchor) < 400;
    const member = world.project?.members[focusAnt.profile];
    group(
      "ant-" + focusAnt.id,
      "ant",
      focusAnt.anchor,
      near ? !foodRight : focusAnt.anchor.x < width / 2,
      member
        ? [
            ["名前", member.name, 1.32],
            ...(focusAnt.doing ? [["いま", focusAnt.doing, 1.48] as [string, string, number]] : []),
            ...(member.role ? [["役職", member.role, 1.62] as [string, string, number]] : []),
          ]
        : [
            ["名前", name, 1.32],
            ["役職", role, 1.48],
            ["所属コード", dept, 1.61],
            ["勤続", years, 1.75],
            ["疲労度の目安", "今月の残業 " + ot + " h", 1.88],
            [
              "所属チーム",
              world.teams
                .filter((t) => t.members.includes(focusAnt.profile))
                .map((t) => t.name)
                .join("・") ||
                (dept === "PLN-01" ? "企画チーム" : dept === "SUP-01" ? "応援チーム" : "運営チーム"),
              2.33,
            ],
          ],
    );
  }
  return notes;
}
function entry(root: HTMLDivElement, svg: SVGSVGElement) {
  const path = document.createElementNS(ns, "path"),
    dot = document.createElementNS(ns, "circle");
  dot.setAttribute("r", "2.7");
  svg.append(path, dot);
  const card = document.createElement("div"),
    label = document.createElement("small"),
    value = document.createElement("strong"),
    caption = document.createElement("span");
  card.append(label, value, caption);
  root.append(card);
  return { path, dot, card, label, value, caption };
}
export function mapAnnotations(root: HTMLDivElement) {
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("aria-hidden", "true");
  root.replaceChildren(svg);
  const entries = new Map<string, ReturnType<typeof entry>>();
  let lastWorld: World | undefined,
    statsTime = 0,
    lastMeasure = -Infinity,
    obstacles: Box[] = [];
  const summaries = new Map<string, ReturnType<typeof areaProgress>>();
  const setText = (element: HTMLElement, text: string) => {
    if (element.textContent !== text) element.textContent = text;
  };
  function render(frame: Frame) {
    if (frame.now - lastMeasure > 140) {
      lastMeasure = frame.now;
      const origin = root.getBoundingClientRect();
      obstacles = Array.from(
        root.parentElement!.querySelectorAll(
          ".panel,.map-nav,.top-left,.top-right,.bottom-left,.zoom-menu,.drawing,.notice,.map-hint",
        ),
      ).map((el) => {
        const r = el.getBoundingClientRect();
        return { x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height };
      });
    }
    if (lastWorld !== frame.world || frame.now - statsTime > 400) {
      lastWorld = frame.world;
      statsTime = frame.now;
      summaries.clear();
    }
    const notes = notesFor(frame, [...obstacles], (m) => {
      let v = summaries.get(m.id);
      if (!v) {
        v = areaProgress(frame.world, m, frame.now);
        summaries.set(m.id, v);
      }
      return v;
    });
    const keep = new Set(notes.map((n) => n.id));
    for (const [id, e] of entries)
      if (!keep.has(id)) {
        e.card.remove();
        e.path.remove();
        e.dot.remove();
        entries.delete(id);
      }
    for (const n of notes) {
      let e = entries.get(n.id);
      if (!e) {
        e = entry(root, svg);
        entries.set(n.id, e);
        e.card.className = "field-note note-" + n.kind;
        e.card.dataset.field = n.label;
      }
      setText(e.label, n.label);
      setText(e.value, n.value);
      setText(e.caption, n.caption ?? "");
      const { x, y, w, h } = n.box,
        p = n.anchor;
      e.card.style.transform = `translate(${x.toFixed(1)}px,${y.toFixed(1)}px)`;
      e.card.style.width = w + "px";
      e.card.style.height = h + "px";
      e.card.style.opacity = String(n.alpha);
      const ink = n.tone ?? (n.kind === "ant" ? "#2c746b" : n.kind === "food" ? "#885239" : "#645442");
      e.card.style.setProperty("--ink", ink);
      if (n.kind === "area") {
        e.card.style.setProperty("--angle", (n.percent ?? 0) * 3.6 + "deg");
        e.card.title = frame.world.project
          ? "WBSのルートタスクの進捗"
          : "エリアに置かれた仕事の見積MHで重み付けした進捗";
      }
      const end = { x: clamp(p.x, x, x + w), y: clamp(p.y, y + 6, y + h - 6) };
      if (p.x > x && p.x < x + w) end.y = p.y < y ? y : y + h;
      const elbow = { x: end.x + (p.x < end.x ? -12 : 12), y: end.y };
      e.path.setAttribute(
        "d",
        `M${p.x.toFixed(1)},${p.y.toFixed(1)} L${elbow.x.toFixed(1)},${elbow.y.toFixed(1)} L${end.x.toFixed(1)},${end.y.toFixed(1)}`,
      );
      e.path.setAttribute("stroke", ink);
      e.path.style.opacity = String(n.alpha * 0.7);
      e.dot.setAttribute("cx", String(p.x));
      e.dot.setAttribute("cy", String(p.y));
      e.dot.setAttribute("fill", ink);
      e.dot.style.opacity = String(n.alpha);
    }
  }
  return {
    render,
    clear() {
      root.replaceChildren();
    },
  };
}
