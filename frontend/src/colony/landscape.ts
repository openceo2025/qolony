import { seeded, stone, tuft } from "./art";
import { districts, type Point } from "./model";

type Ctx = CanvasRenderingContext2D;
type Motif = "flowers" | "leaf" | "pond" | "acorn" | "mushrooms" | "stones" | "clover" | "grass" | "twig";
type Prop = Point & { kind: Motif; scale: number; flip: number };
function shape(c: Ctx, d: string, color: string) {
  c.fillStyle = color;
  c.fill(new Path2D(d));
}
function line(c: Ctx, d: string, color: string, width: number) {
  c.strokeStyle = color;
  c.lineWidth = width;
  c.lineCap = "round";
  c.lineJoin = "round";
  c.stroke(new Path2D(d));
}
function oval(c: Ctx, x: number, y: number, rx: number, ry: number, color: string, tilt = 0) {
  c.beginPath();
  c.ellipse(x, y, rx, ry, tilt, 0, Math.PI * 2);
  c.fillStyle = color;
  c.fill();
}
function blossom(c: Ctx, x: number, y: number, s: number, petal: string) {
  c.save();
  c.translate(x, y);
  c.scale(s, s);
  line(c, "M0 0 Q6 17 -3 33", "#63783c", 3.8);
  shape(c, "M1 22 Q-15 23 -20 12 Q-5 11 1 22 M1 16 Q4 1 18 2 Q17 14 1 16", "#7d984e");
  for (let j = 0; j < 7; j++) {
    c.save();
    c.rotate((Math.PI * 2 * j) / 7);
    oval(c, 0, -7, 4.3, 8, petal);
    c.restore();
  }
  oval(c, 0, 0, 5.4, 5, "#e7a431");
  oval(c, -1, -1.5, 2.5, 1.5, "#ffd86a");
  c.restore();
}
function mushroom(c: Ctx, x: number, y: number, s: number) {
  c.save();
  c.translate(x, y);
  c.scale(s, s);
  oval(c, 3, 1, 21, 5, "#91693235");
  shape(c, "M-5 -25 Q-5 -12 -8 -2 Q-4 3 4 1 Q8 0 6 -3 L5 -25 Z", "#fff1ce");
  shape(c, "M2 -24 L6 -24 L6 -2 L2 0 Q4 -11 2 -24", "#d8bd90");
  shape(c, "M-23 -21 Q-17 -46 -1 -47 Q14 -46 24 -22 Q15 -14 -5 -15 Q-17 -16 -23 -21", "#c65b40");
  shape(c, "M-22 -22 Q-12 -44 0 -45 Q9 -44 16 -36 Q1 -37 -11 -24 Z", "#df7450");
  oval(c, -11, -28, 3.5, 2, "#fff0ce", -0.35);
  oval(c, 2, -37, 3, 2, "#ffedc9");
  oval(c, 12, -23, 4.1, 2, "#f0d9b4");
  c.restore();
}
function paintMotif(c: Ctx, kind: Motif) {
  switch (kind) {
    case "flowers":
      tuft(c, -12, 8, 0.73, 1);
      blossom(c, -19, -16, 0.8, "#fff4d6");
      blossom(c, 8, -34, 1.1, "#fff9dc");
      blossom(c, 27, -9, 0.67, "#f3ab8a");
      break;
    case "leaf":
      oval(c, 0, 2, 52, 12, "#98733630", -0.12);
      shape(c, "M-58 8 Q-34 -28 -1 -30 Q28 -32 51 -17 Q34 14 4 21 Q-32 27 -58 8", "#8c9b4f");
      shape(c, "M-56 8 Q-34 -25 0 -27 Q30 -29 50 -17 Q-2 -5 -56 8", "#a5b15d");
      line(c, "M-67 12 Q-8 -2 47 -16 M-27 2 L-18 -17 M-10 -2 L8 12 M9 -7 L19 -22", "#687e40", 2);
      break;
    case "pond":
      shape(
        c,
        "M-98 -2 Q-99 -31 -66 -39 Q-43 -49 -18 -40 Q5 -29 33 -35 Q68 -40 90 -16 Q108 0 85 20 Q59 39 28 38 Q7 36 -18 45 Q-53 54 -78 32 Q-98 20 -98 -2",
        "#d29750",
      );
      shape(
        c,
        "M-94 -5 Q-88 -34 -58 -36 Q-39 -42 -15 -34 Q8 -23 34 -30 Q65 -35 85 -13 Q102 2 78 18 Q50 32 28 33 Q6 31 -20 40 Q-53 46 -74 26 Q-90 17 -94 -5",
        "#73b7a1",
      );
      shape(
        c,
        "M-78 -9 Q-50 -34 -17 -24 Q8 -14 33 -23 Q58 -26 77 -12 Q43 -16 20 -5 Q-7 4 -34 -5 Q-51 -11 -78 -9",
        "#61a991",
      );
      line(c, "M-56 13 q12 4 23 0 M23 13 q12 -4 24 -3 M-3 -18 l15 1", "#c3e1ba", 2.1);
      for (const [x, y, s] of [
        [-60, -17, 1],
        [51, 21, 0.8],
        [36, 28, 0.55],
      ]) {
        c.save();
        c.translate(x, y);
        c.scale(s, s);
        shape(c, "M1 0 L13 1 C13 7 -7 9 -12 3 C-17 -6 6 -10 13 -3 Z", "#608b52");
        c.restore();
      }
      tuft(c, -67, 33, 0.75, 1);
      tuft(c, 82, -17, 0.64, 1);
      break;
    case "acorn":
      for (const [x, y, s, a] of [
        [-18, 5, 1, -0.5],
        [20, 0, 0.8, 0.35],
      ]) {
        c.save();
        c.translate(x, y);
        c.rotate(a);
        c.scale(s, s);
        shape(c, "M-13 -15 Q-17 1 -5 13 L1 19 L4 14 Q16 8 15 -15 Z", "#b3753c");
        shape(c, "M-11 -13 Q-10 2 -3 8 Q-9 2 -6 -12 Z", "#d59b55");
        shape(c, "M-17 -14 Q-17 -28 -3 -29 Q10 -30 16 -21 L18 -13 Q3 -8 -17 -14", "#745637");
        line(c, "M-1 -27 Q0 -32 5 -35", "#654c33", 3);
        line(c, "M-10 -18 l4 -2 M1 -16 l4 -2 M4 -23 l3 -1", "#a68151", 1.6);
        c.restore();
      }
      break;
    case "mushrooms":
      mushroom(c, -21, 1, 0.65);
      mushroom(c, 3, 1, 1.15);
      mushroom(c, 29, 9, 0.55);
      tuft(c, 14, 9, 0.32, 1);
      break;
    case "stones":
      for (const [x, y, s] of [
        [-11, -4, 3.3],
        [17, 6, 2.1],
        [-35, 8, 1.45],
      ]) {
        c.save();
        c.translate(x, y);
        c.scale(s, s);
        shape(c, "M-7 0 L-5 -5 L1 -8 L6 -5 L8 0 L4 2 L-3 2 Z", "#82887b");
        shape(c, "M-7 0 L-5 -5 L1 -8 L4 -5 L0 -1 Z", "#b0b19b");
        c.restore();
      }
      tuft(c, -17, 3, 0.55, 1);
      break;
    case "twig":
      oval(c, 0, 6, 65, 10, "#91743732", -0.13);
      shape(c, "M-51 -4 L43 -20 Q53 -18 55 -9 L52 -4 L-47 15 Q-55 11 -51 -4", "#9a6744");
      shape(c, "M1 -11 L14 -22 L18 -35 L22 -34 L21 -20 L13 -11 Z", "#966746");
      line(c, "M-43 -1 Q-11 -3 40 -16 M-33 6 L7 -3 M15 -4 L43 -10", "#ba8858", 2.4);
      oval(c, -50, 5, 8, 11, "#cf9f67", -0.28);
      oval(c, -50, 5, 4, 7, "#ad7a49", -0.28);
      tuft(c, 28, 0, 0.49, 1);
      break;
    case "clover":
      tuft(c, 0, 0, 1.08, 0);
      tuft(c, 24, 6, 0.52, 0);
      break;
    case "grass":
      tuft(c, 0, 0, 0.95, 1);
      stone(c, -14, 2, 0.7);
      break;
  }
}
const motifNames: Motif[] = [
  "flowers",
  "leaf",
  "pond",
  "acorn",
  "mushrooms",
  "stones",
  "clover",
  "grass",
  "twig",
];
const regions = districts.map((d, i) => {
  const random = seeded(809 + i * 119);
  const props: Prop[] = [];
  const landmark = motifNames.find((kind) => kind === d.landmark) ?? "clover";
  const add = (kind: Motif, x: number, y: number, scale: number) =>
    props.push({ kind, x, y, scale, flip: random() < 0.5 ? -1 : 1 });
  const direction = i % 2 ? -1 : 1;
  add(
    landmark,
    d.x + direction * d.radiusX * 0.72,
    d.y - d.radiusY * 0.88,
    landmark === "pond" ? 1.85 : 2.55,
  );
  for (let j = 0; j < 12; j++) {
    const angle = ((j + random() * 0.78) * Math.PI) / 6 + i * 1.12;
    const x = d.x + Math.cos(angle) * d.radiusX * (1.07 + random() * 0.13);
    const y = d.y + Math.sin(angle) * d.radiusY * (1.06 + random() * 0.15);
    const kind = j % 4 === 0 && landmark !== "pond" ? landmark : j % 3 === 1 ? "grass" : "clover";
    add(kind, x, y, 0.75 + random() * 1.25);
    if (j % 3 === 0) add("grass", x + 32 + random() * 38, y + 12 + random() * 24, 0.65 + random() * 0.55);
  }
  const trail = new Path2D();
  trail.moveTo(d.x * 0.21, d.y * 0.2);
  trail.bezierCurveTo(
    d.x * 0.47 - 90 * direction,
    d.y * 0.25,
    d.x * 0.64 + 75 * direction,
    d.y * 0.88,
    d.x,
    d.y,
  );
  return { district: d, trail, props };
});
const wayside: Prop[] = [
  { kind: "twig", x: -1775, y: 25, scale: 2.15, flip: 1 },
  { kind: "clover", x: -1845, y: 48, scale: 1.45, flip: -1 },
  { kind: "grass", x: -1665, y: 1, scale: 1.1, flip: 1 },
  { kind: "flowers", x: -1920, y: 87, scale: 0.88, flip: 1 },
  { kind: "leaf", x: 1740, y: 48, scale: 1.85, flip: -1 },
  { kind: "twig", x: 1865, y: 95, scale: 1.28, flip: -1 },
  { kind: "grass", x: 1798, y: 125, scale: 1.12, flip: 1 },
  { kind: "clover", x: 1682, y: 61, scale: 1.2, flip: -1 },
];
const foliage = [...regions.flatMap((region) => region.props), ...wayside].sort((a, b) => a.y - b.y);
// 草や水辺は静止画として再利用し、広域表示でも全ての輪郭を毎フレーム描き直さない。
export function landscape() {
  const sheets = new Map<Motif, HTMLCanvasElement>();
  for (const name of motifNames) {
    const sheet = document.createElement("canvas");
    sheet.width = 440;
    sheet.height = 272;
    const c = sheet.getContext("2d")!;
    c.scale(2, 2);
    c.translate(110, 76);
    paintMotif(c, name);
    sheets.set(name, sheet);
  }
  return (c: Ctx, inView: (x: number, y: number, pad?: number) => boolean, showTrails = true) => {
    c.save();
    c.lineCap = "round";
    for (const { district: d, trail } of regions) {
      if (!showTrails) break;
      const gradient = c.createLinearGradient(d.x * 0.21, d.y * 0.2, d.x, d.y);
      gradient.addColorStop(0, "#ffdc9e00");
      gradient.addColorStop(0.37, "#ffe2a554");
      gradient.addColorStop(1, "#ffdfa193");
      c.strokeStyle = gradient;
      c.lineWidth = 32;
      c.stroke(trail);
    }
    for (const p of foliage) {
      if (!inView(p.x, p.y, 114 * p.scale)) continue;
      c.save();
      c.translate(p.x, p.y);
      c.scale(p.scale * p.flip, p.scale);
      c.drawImage(sheets.get(p.kind)!, -110, -76, 220, 136);
      c.restore();
    }
    c.restore();
  };
}
