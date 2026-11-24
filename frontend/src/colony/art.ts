import type { Accessory, Kind } from "./model";
import { legPose } from "./gait";

type Ctx = CanvasRenderingContext2D;
// 同じ形の文字列を毎回 Path2D に解析すると、会社規模ではフレームごとに数千個の一時オブジェクトができ、
// ガベージコレクションで数百msの引っかかりが出る。形は固定なので解析結果を使い回す。
const shapes = new Map<string, Path2D>();
function shape(d: string) {
  let path = shapes.get(d);
  if (!path) {
    path = new Path2D(d);
    if (shapes.size < 4000) shapes.set(d, path);
  }
  return path;
}
export function paint(c: Ctx, d: string, fill: string | CanvasGradient) {
  c.fillStyle = fill;
  c.fill(shape(d));
}
function stroke(c: Ctx, d: string, color: string, width: number) {
  c.strokeStyle = color;
  c.lineWidth = width;
  c.lineCap = "round";
  c.lineJoin = "round";
  c.stroke(shape(d));
}
export function oval(c: Ctx, x: number, y: number, rx: number, ry: number, color: string | CanvasGradient, a = 0) {
  c.beginPath();
  c.ellipse(x, y, rx, ry, a, 0, Math.PI * 2);
  c.fillStyle = color;
  c.fill();
}
function fade(c: Ctx, x: number, y: number, x2: number, y2: number, a: string, b: string) {
  const g = c.createLinearGradient(x, y, x2, y2);
  g.addColorStop(0, a);
  g.addColorStop(1, b);
  return g;
}
export function seeded(seed: number) {
  let s = seed >>> 0;
  return () => {
    s = (Math.imul(1664525, s) + 1013904223) >>> 0;
    return s / 4294967296;
  };
}
export function makeSoil(c: Ctx) {
  const tile = document.createElement("canvas");
  tile.width = 480;
  tile.height = 480;
  const p = tile.getContext("2d")!,
    r = seeded(31);
  for (let i = 0; i < 1250; i++) {
    const x = r() * 480,
      y = r() * 480,
      s = 0.22 + r() * 0.74;
    oval(p, x, y, s, s * 0.63, i % 3 ? "#9f641d25" : "#fff4c940");
  }
  for (let i = 0; i < 22; i++) {
    const x = r() * 480,
      y = r() * 480,
      s = 1 + r() * 1.8;
    oval(p, x, y, s * 1.3, s * 0.62, "#a876343a");
  }
  return c.createPattern(tile, "repeat");
}
export function tuft(c: Ctx, x: number, y: number, scale: number, variant: number) {
  c.save();
  c.translate(x, y);
  c.scale(scale, scale);
  if (variant % 3 === 0) {
    for (let i = 0; i < 4; i++) {
      c.save();
      c.rotate(i * 1.5 + 0.3);
      paint(
        c,
        "M0 0 Q-25 -2 -19 -17 Q-15 -29 -6 -21 Q4 -29 10 -17 Q14 -8 0 0",
        ["#789047", "#8ca65a", "#a6b363", "#647c3e"][i],
      );
      c.restore();
    }
    stroke(c, "M2 0 Q9 13 17 15", "#718245", 2.5);
  } else {
    paint(
      c,
      "M0 0 Q-8 -25 -23 -35 Q-7 -31 3 -9 Q1 -43 9 -53 Q10 -31 7 -11 Q15 -27 28 -31 Q17 -17 11 -1 Z",
      "#608046",
    );
    paint(c, "M3 0 Q-5 -17 -15 -20 Q-4 -20 7 -7 Q13 -19 18 -20 L11 0 Z", "#96a957");
  }
  c.restore();
}
export function stone(c: Ctx, x: number, y: number, s: number) {
  c.save();
  c.translate(x, y);
  c.scale(s, s);
  oval(c, 1, 2, 7, 2.5, "#af792d50");
  paint(c, "M-6 1 L-4 -3 L1 -5 L6 -1 L5 1 L0 2 Z", "#c0975e");
  paint(c, "M-6 1 L-4 -3 L1 -5 L3 -2 L-1 0 Z", "#dfbc83");
  c.restore();
}
export function nestBack(c: Ctx) {
  oval(c, 5, 11, 100, 33, "#bd7c2e44");
  paint(
    c,
    "M-101 5 Q-99 -8 -83 -15 L-76 -24 Q-64 -30 -47 -28 Q-23 -42 8 -38 L28 -39 Q49 -39 65 -28 L77 -25 L85 -15 Q103 -9 102 5 Q99 23 73 32 Q26 51 -29 43 Q-74 37 -97 19 Z",
    fade(c, 0, -37, 0, 46, "#d5974c", "#c7833d"),
  );
  oval(c, 0, -1, 76, 30, "#a7592c", -0.04);
  oval(c, 0, -3, 69, 25, fade(c, 0, -28, 0, 21, "#482036", "#76372c"), -0.04);
  const r = seeded(124);
  for (let i = 0; i < 42; i++) {
    const a = r() * Math.PI * 2,
      rx = 79 + r() * 17,
      ry = 29 + r() * 8;
    const x = Math.cos(a) * rx,
      y = Math.sin(a) * ry;
    oval(c, x, y, 1 + r() * 3, 0.7 + r() * 1.5, i % 3 ? "#ad703a" : "#efd092", -r() * 0.4);
  }
}
export function nestLip(c: Ctx) {
  paint(
    c,
    "M-78 5 Q-56 26 -9 25 Q48 26 74 6 L70 18 Q41 40 -7 38 Q-53 38 -75 19 Z",
    fade(c, 0, 5, 0, 42, "#e6aa5d", "#cf8c44"),
  );
  for (const [x, y, s] of [
    [-78, 18, 0.85],
    [-45, 32, 0.55],
    [28, 34, 0.65],
    [75, 21, 0.6],
    [95, 17, 0.5],
  ])
    stone(c, x, y, s);
}
export const artSize: Record<Kind, number> = {
  sugar: 27,
  bonbon: 58,
  cookie: 51,
  chocolate: 88,
  lollipop: 79,
};
function sugar(c: Ctx) {
  paint(c, "M-22 -28 L3 -39 L25 -29 L0 -16 Z", "#fff6d9");
  paint(c, "M-22 -28 L0 -16 L0 9 L-22 -3 Z", "#f6e3b6");
  paint(c, "M0 -16 L25 -29 L24 -3 L0 9 Z", "#ddc298");
  const r = seeded(3);
  for (let i = 0; i < 15; i++) {
    const x = r() * 14 - 18,
      y = r() * 14 - 17;
    oval(c, x, y + x * 0.34, 1.1, 0.8, i % 2 ? "#fff4d7" : "#e3cda5");
  }
  stroke(c, "M-18 -27 L1 -18 L19 -25", "#fff9e8", 1.2);
}
function pop(c: Ctx) {
  c.save();
  c.rotate(-0.17);
  stroke(c, "M3 -30 L50 26", "#b77d41", 10);
  stroke(c, "M3 -32 L49 23", "#fff0c7", 8);
  c.translate(-5, -48);
  c.scale(1, 0.92);
  oval(c, 2, 4, 52, 51, "#bb4f34");
  oval(c, 0, 0, 51, 51, "#f55541");
  const spiral = new Path2D();
  for (let i = 0; i <= 230; i++) {
    const t = i / 230,
      a = t * 13.7 + 0.2,
      r = t * 50;
    const x = Math.cos(a) * r,
      y = Math.sin(a) * r;
    if (!i) spiral.moveTo(x, y);
    else spiral.lineTo(x, y);
  }
  c.save();
  c.beginPath();
  c.arc(0, 0, 50, 0, Math.PI * 2);
  c.clip();
  c.lineCap = "round";
  c.strokeStyle = "#fff1ce";
  c.lineWidth = 10;
  c.stroke(spiral);
  c.restore();
  stroke(c, "M-40 -25 Q-32 -40 -19 -44", "#ffd5ad", 2.4);
  c.restore();
}
function chocolate(c: Ctx, part: number) {
  c.save();
  c.transform(1, 0.17, -0.32, 0.72, 0, -14);
  const cols = part ? 2 : 3,
    rows = part > 1 ? 1 : 2,
    w = cols * 38,
    h = rows * 34,
    x = -w / 2,
    y = -h / 2;
  c.translate(x, y);
  paint(c, `M0 0 H${w} V${h} L${w + 1} ${h + 9} H0 L-3 ${h - 1} Z`, "#703622");
  c.fillStyle = "#82432d";
  c.fillRect(0, 0, w, h);
  for (let j = 0; j < rows; j++)
    for (let i = 0; i < cols; i++) {
      c.save();
      c.translate(i * 38 + 1, j * 34 + 1);
      paint(c, "M0 5 L4 1 L33 1 L36 4 L36 30 L32 32 L0 31 Z", "#96503a");
      paint(c, "M1 5 L5 1 H33 L29 6 L6 8 L2 28 Z", "#dc8f67");
      paint(c, "M7 7 L29 5 L30 24 L6 26 Z", "#ad6248");
      paint(c, "M6 26 L30 24 L35 28 L32 31 L2 30 Z", "#87452f");
      c.restore();
    }
  c.restore();
}
function wrapper(c: Ctx) {
  c.save();
  c.rotate(-0.18);
  c.translate(0, -10);
  for (const flip of [-1, 1]) {
    c.save();
    c.scale(flip, 1);
    paint(c, "M18 -2 L48 -21 L45 -8 L51 2 L47 6 L51 18 L20 8 Z", "#ef9c24");
    paint(c, "M21 2 L47 -17 L42 -6 Z", "#ffcc69");
    paint(c, "M22 3 L46 11 L44 3 Z", "#cf7b19");
    c.restore();
  }
  oval(c, 0, 0, 29, 22, fade(c, -15, -20, 16, 20, "#ffd479", "#f39820"));
  stroke(c, "M-19 -8 Q-11 -18 -2 -17", "#fff0c7", 3);
  stroke(c, "M-22 -5 Q-12 2 6 4 Q15 6 22 13", "#ffe0a1", 3);
  c.restore();
}
function cookie(c: Ctx) {
  c.save();
  c.rotate(-0.19);
  oval(c, 1, -9, 38, 26, "#ab6031");
  oval(c, 0, -14, 38, 26, "#cb854b");
  oval(c, 0, -17, 36, 23, "#edb56c");
  for (const [x, y] of [
    [-12, -24],
    [14, -22],
    [-22, -12],
    [0, -10],
    [20, -7],
    [-2, -30],
  ]) {
    oval(c, x, y, 3, 2, "#ad6a3d", 0.2);
  }
  c.restore();
}
export function candy(c: Ctx, kind: Kind, piece: number, shadow = true) {
  c.save();
  const size = piece && kind !== "chocolate" ? Math.pow(0.76, piece) : 1;
  c.scale(size, size);
  if (shadow) {
    const w = artSize[kind] * (piece ? 0.55 : 0.8);
    oval(c, 3, 7, w, w * 0.19, "#85532239", 0.02);
  }
  switch (kind) {
    case "sugar":
      c.scale(0.66, 0.66);
      sugar(c);
      break;
    case "lollipop":
      pop(c);
      break;
    case "chocolate":
      chocolate(c, piece);
      break;
    case "bonbon":
      wrapper(c);
      break;
    case "cookie":
      cookie(c);
  }
  c.restore();
}
export function ant(
  c: Ctx,
  x: number,
  y: number,
  face: number,
  phase: number,
  mode: "walk" | "carry" | "pull" | "idle",
  accessory: Accessory,
  scale = 1,
) {
  c.save();
  c.translate(x, y);
  c.scale(scale * face, scale);
  const stepping = mode === "walk" || mode === "carry";
  const ink = "#452238",
    cycle = Math.sin(phase),
    stride = stepping ? cycle * 2.7 : 0;
  const bounce = stepping ? Math.abs(Math.cos(phase)) * 0.55 : Math.sin(phase) * 0.28;
  oval(c, 0, 1, 13, 2.6, "#71432340");
  for (const rear of [true, false]) {
    const { hip, knee, ankle } = legPose(phase, rear, stepping, bounce);
    const color = rear ? "#60384a" : ink;
    c.beginPath();
    c.moveTo(hip.x, hip.y);
    c.lineTo(knee.x, knee.y);
    c.lineTo(ankle.x, ankle.y);
    c.lineWidth = rear ? 2.6 : 3;
    c.lineCap = "round";
    c.lineJoin = "round";
    c.strokeStyle = color;
    c.stroke();
    oval(c, ankle.x + 0.8, ankle.y + 0.25, 2.15, 1.15, color);
  }
  c.translate(0, -bounce);
  if (mode === "pull") c.rotate(-0.12 + Math.sin(phase) * 0.035);
  oval(c, -8, -12, 10, 6.9, ink, -0.29);
  oval(c, 0, -15, 6.3, 6.4, ink);
  oval(c, 7, -23, 8.9, 9.1, ink, 0.15);
  stroke(c, "M5 -30 Q0 -39 -4 -35 M10 -30 Q11 -37 15 -35", ink, 1.8);
  oval(c, 11, -24, 1.45, 2.05, "#fff5d6");
  if (accessory === "scarf") {
    paint(c, "M-1 -19 Q4 -16 9 -18 L7 -15 L1 -16 L-6 -14 L-4 -17 L-10 -20 Z", "#48ad9c");
  } else if (accessory === "cap") {
    paint(c, "M-1 -27 Q0 -37 7 -35 Q12 -35 15 -29 L19 -28 Q19 -26 14 -26 Z", "#fbb933");
    stroke(c, "M1 -28 L16 -27", "#da8d23", 1.5);
  } else {
    paint(c, "M0 -29 L-4 -35 L3 -34 L5 -31 L11 -35 L12 -29 L6 -29 Z", "#ec6858");
  }
  if (mode === "pull" || mode === "carry") {
    stroke(c, "M2 -17 Q8 -13 17 -16 M4 -16 L16 -13", ink, 2.6);
  } else {
    stroke(c, `M2 -17 Q5 -13 ${8 - stride * 0.45} -13`, ink, 2.5);
  }
  c.restore();
}
