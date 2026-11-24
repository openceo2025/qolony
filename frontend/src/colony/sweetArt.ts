import { candy, oval, paint } from "./art";
import type { Food } from "./model";
import type { BitState } from "./liveModel";

// 実タスクのお菓子。PoC の絵柄（art.ts）のまま、割れ方と欠け方だけを足す。
// - 板チョコ: 1階層目の子が列、その下の末端が列のブロック。
// - 包みキャンディー: 手つかずの間は包まれている。食べ始めると包みがほどけ、扇（子）と輪（孫）が見える。
// - 角砂糖: 子の数だけ小さな角砂糖が寄り集まる。
// 運び去られた（完了した）かけらは描かず、小さなくずだけ残す。

type Ctx = CanvasRenderingContext2D;

function crumbs(c: Ctx, x: number, y: number, color: string) {
  oval(c, x - 4, y + 2, 1.6, 1, color);
  oval(c, x + 3, y - 1, 1.2, 0.8, color);
}

function bite(c: Ctx, x: number, y: number, r: number, color: string) {
  // 食べている途中のかけら。端がかじり取られている。
  c.beginPath();
  c.arc(x, y, r, 0, Math.PI * 2);
  c.arc(x + r * 0.9, y - r * 0.5, r * 0.55, 0, Math.PI * 2);
  c.fillStyle = color;
  c.fill();
}

function chocolate(c: Ctx, groups: BitState[][]) {
  const cols = Math.max(1, groups.length),
    rows = Math.max(1, ...groups.map((g) => g.length));
  const k = Math.min(1, 150 / (cols * 38 + (cols - 1) * 5), 118 / (rows * 34));
  const w = cols * 38 + (cols - 1) * 5,
    h = rows * 34;
  c.save();
  c.transform(1, 0.17, -0.32, 0.72, 0, -14);
  c.scale(k, k);
  c.translate(-w / 2, -h / 2);
  groups.forEach((group, i) => {
    group.forEach((state, j) => {
      const x = i * 43,
        y = j * 34;
      if (state === "done") {
        crumbs(c, x + 18, y + 17, "#82432d");
        return;
      }
      c.save();
      c.translate(x, y);
      paint(c, "M0 0 H38 V34 L39 43 H0 L-3 33 Z", "#703622");
      c.fillStyle = "#82432d";
      c.fillRect(0, 0, 38, 34);
      c.translate(1, 1);
      paint(c, "M0 5 L4 1 L33 1 L36 4 L36 30 L32 32 L0 31 Z", "#96503a");
      paint(c, "M1 5 L5 1 H33 L29 6 L6 8 L2 28 Z", "#dc8f67");
      paint(c, "M7 7 L29 5 L30 24 L6 26 Z", "#ad6248");
      paint(c, "M6 26 L30 24 L35 28 L32 31 L2 30 Z", "#87452f");
      if (state === "active") bite(c, 33, 4, 7, "#4a1f14");
      c.restore();
    });
  });
  c.restore();
}

const orange = ["#f39820", "#ffb547", "#f7a93a", "#ffc463"];
function bonbon(c: Ctx, groups: BitState[][], t: number) {
  const touched = groups.some((g) => g.some((s) => s !== "open"));
  if (!touched) {
    candy(c, "bonbon", 0, true);
    return;
  }
  c.save();
  c.translate(0, -10);
  // ほどいた包み紙が下に広がっている
  oval(c, 3, 8, 46, 9, "#85532230");
  paint(c, "M-50 2 L-30 -14 L-12 -8 L12 -10 L32 -15 L52 0 L34 14 L10 12 L-14 15 L-34 12 Z", "#ffcc69");
  paint(c, "M-50 2 L-30 -14 L-26 -2 L-34 12 Z", "#ef9c24");
  paint(c, "M52 0 L32 -15 L30 -2 L34 14 Z", "#ef9c24");
  const r = 25,
    n = Math.max(1, groups.length);
  groups.forEach((group, i) => {
    const a0 = -Math.PI / 2 + (i / n) * Math.PI * 2,
      a1 = -Math.PI / 2 + ((i + 1) / n) * Math.PI * 2;
    const rings = Math.max(1, group.length);
    group.forEach((state, j) => {
      const r0 = r * Math.sqrt(j / rings),
        r1 = r * Math.sqrt((j + 1) / rings);
      if (state === "done") return;
      c.beginPath();
      c.arc(0, 0, r1, a0, a1);
      if (r0 > 0) c.arc(0, 0, r0, a1, a0, true);
      else c.lineTo(0, 0);
      c.closePath();
      c.fillStyle = orange[(i + j) % orange.length];
      c.fill();
      c.strokeStyle = "#fff0c7";
      c.lineWidth = 1.2;
      c.stroke();
      if (state === "active") {
        const a = (a0 + a1) / 2,
          m = (r0 + r1) / 2;
        // かじられた跡がかすかに脈打つ
        c.globalAlpha = 0.65 + 0.2 * Math.sin(t * 4);
        bite(c, Math.cos(a) * m, Math.sin(a) * m, 3.2, "#b8641c");
        c.globalAlpha = 1;
      }
    });
  });
  c.strokeStyle = "#fff0c7";
  c.lineWidth = 3;
  c.beginPath();
  c.arc(0, 0, r * 0.72, Math.PI * 1.1, Math.PI * 1.4);
  c.stroke();
  c.restore();
}

function sugarPile(c: Ctx, groups: BitState[][], t: number) {
  const states = groups.map((g) => g[0] ?? "open");
  if (states.length <= 1) {
    if (states[0] !== "done") candy(c, "sugar", 0, true);
    return;
  }
  const perRow = Math.min(4, Math.ceil(Math.sqrt(states.length * 1.6)));
  const rows = Math.ceil(states.length / perRow);
  oval(c, 2, 6, 10 + perRow * 9, 4 + rows, "#85532230");
  // 奥の段から描いて手前を重ねる
  for (let row = rows - 1; row >= 0; row--) {
    for (let col = 0; col < perRow; col++) {
      const i = row * perRow + col;
      if (i >= states.length) continue;
      const x = (col - (perRow - 1) / 2) * 17 + (row % 2) * 6,
        y = -row * 9;
      if (states[i] === "done") {
        crumbs(c, x, y, "#e3cda5");
        continue;
      }
      c.save();
      c.translate(x, y + (states[i] === "active" ? Math.sin(t * 6 + i) * 0.8 : 0));
      c.scale(0.55, 0.55);
      candy(c, "sugar", 0, false);
      c.restore();
    }
  }
}

/**
 * 遠景用。数百個のお菓子を毎フレーム描くので、形と残り具合だけを数回の塗りで描く。
 * 板チョコは列ごとの残りの長さ、キャンディーは残りの扇、角砂糖は残りの粒数で欠け方を残す。
 */
export function drawSweetFar(c: Ctx, food: Food) {
  const groups = food.bits?.groups ?? [["open"]];
  const left = (g: BitState[]) => g.filter((s) => s !== "done").length;
  if (food.kind === "chocolate") {
    const cols = Math.max(1, groups.length),
      rows = Math.max(1, ...groups.map((g) => g.length));
    const k = Math.min(1, 150 / (cols * 43), 118 / (rows * 34));
    const cw = 38 * k,
      gap = 5 * k,
      ch = 34 * k * 0.72;
    const x0 = -(cols * (cw + gap) - gap) / 2,
      y0 = -14 - (rows * ch) / 2;
    c.fillStyle = "#87452f";
    groups.forEach((g, i) => {
      const n = left(g);
      if (n) c.fillRect(x0 + i * (cw + gap), y0, cw, n * ch);
    });
    return;
  }
  if (food.kind === "bonbon") {
    const total = groups.reduce((s, g) => s + g.length, 0) || 1;
    const remain = groups.reduce((s, g) => s + left(g), 0);
    if (!remain) return;
    c.fillStyle = "#f39820";
    c.beginPath();
    c.moveTo(0, -10);
    c.arc(0, -10, 25, -Math.PI / 2, -Math.PI / 2 + (Math.PI * 2 * remain) / total);
    c.closePath();
    c.fill();
    return;
  }
  const remain = groups.filter((g) => left(g)).length;
  if (!remain) return;
  const side = 12 + Math.sqrt(remain) * 7;
  c.fillStyle = "#fff6d9";
  c.fillRect(-side / 2, -side - 2, side, side);
}

export function drawSweet(c: Ctx, food: Food, t: number) {
  const groups = food.bits?.groups ?? [["open"]];
  if (food.kind === "chocolate") chocolate(c, groups);
  else if (food.kind === "bonbon") bonbon(c, groups, t);
  else sugarPile(c, groups, t);
}
