import { ant, artSize, candy, makeSoil, nestBack, nestLip, seeded, stone, tuft } from "./art";
import {
  clamp,
  deadline,
  distance,
  districts,
  extent,
  fieldCrew,
  isRounded,
  nest,
  positionAt,
  roles,
} from "./model";
import { landscape } from "./landscape";
import type { Accessory, CrewAnt, Food, Point, World } from "./model";
import { mapAnnotations, roamingProfiles, type AntAnchor } from "./annotations";
import { drawSweet, drawSweetFar } from "./sweetArt";
import { carryPose } from "./liveModel";

/** これより引いて見ているときは、アリを点、お菓子を簡単な形で描く（会社規模で重くしないため）。 */
const farScale = 0.42;
const crewDoing: Record<CrewAnt["mode"], string> = {
  eat: "食べている（着手中）",
  wait: "次のかけらの前で待機",
  carry: "完了したかけらを運搬",
};

export interface SceneState {
  world: World;
  selected: string | null;
  actor: string;
  draft: Point[];
  tool: boolean;
}
interface Callbacks {
  pick(p: Point, food: string | null): void;
  zoom(z: number): void;
}
export const zoomLimit = { min: 0.25, max: 3.5 };
const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export function zoomAt(camera: Point, anchor: Point, oldZoom: number, newZoom: number): Point {
  const ratio = oldZoom / newZoom;
  return { x: anchor.x + (camera.x - anchor.x) * ratio, y: anchor.y + (camera.y - anchor.y) * ratio };
}
function chalk(
  c: CanvasRenderingContext2D,
  points: Point[],
  closed: boolean,
  color: string,
  smooth: boolean,
  mapAlpha = 0,
  mapScale = 1,
) {
  if (!points.length) return;
  c.save();
  c.lineCap = "round";
  c.lineJoin = "round";
  const path = new Path2D();
  if (smooth && closed) {
    const last = points[points.length - 1];
    path.moveTo((last.x + points[0].x) / 2, (last.y + points[0].y) / 2);
    points.forEach((p, i) => {
      const next = points[(i + 1) % points.length];
      path.quadraticCurveTo(p.x, p.y, (p.x + next.x) / 2, (p.y + next.y) / 2);
    });
  } else {
    path.moveTo(points[0].x, points[0].y);
    points.slice(1).forEach((p) => path.lineTo(p.x, p.y));
  }
  if (closed) path.closePath();
  if (closed && mapAlpha > 0) {
    c.fillStyle = color;
    c.globalAlpha = mapAlpha * 0.15;
    c.fill(path);
  }
  c.strokeStyle = color;
  c.lineWidth = 3.2 + (mapAlpha * 1.5) / mapScale;
  c.globalAlpha = 0.55 + mapAlpha * 0.25;
  c.stroke(path);
  c.setLineDash([1, 2, 4, 1, 2, 3]);
  c.lineWidth = 1.2;
  c.globalAlpha = 0.95;
  c.translate(0.7, -0.8);
  c.stroke(path);
  c.restore();
}
function pill(c: CanvasRenderingContext2D, txt: string, x: number, y: number, warning: boolean) {
  c.font = 'bold 10px "Yu Gothic UI",sans-serif';
  const w = c.measureText(txt).width + 16;
  c.fillStyle = warning ? "#863e29" : "#fff5dce8";
  c.beginPath();
  c.roundRect(x - w / 2, y - 13, w, 18, 9);
  c.fill();
  c.textAlign = "center";
  c.fillStyle = warning ? "#fff2d8" : "#593c30";
  c.fillText(txt, x, y);
}
const fixtures = [
  [-525, -260, 0.92, 1],
  [-484, -237, 0.55, 0],
  [518, 276, 1.05, 1],
  [578, 264, 0.9, 0],
  [-477, 345, 1, 0],
  [482, -308, 0.75, 0],
  [460, -292, 0.47, 1],
  [-675, -34, 0.9, 0],
  [740, 110, 0.9, 1],
  [-36, -290, 0.6, 1],
  [460, 72, 0.38, 1],
  [-400, 96, 0.44, 1],
  [149, 302, 0.55, 0],
  [-818, -526, 1, 1],
  [780, -500, 1.1, 1],
];
const stones = (() => {
  const r = seeded(420);
  return Array.from({ length: 210 }, () => ({
    x: (r() - 0.5) * 2800,
    y: (r() - 0.5) * 1780,
    s: 0.24 + r() * 0.68,
  }));
})();
const walkers = [
  [-88, 90, 1],
  [-275, -42, 0],
  [175, 94, 2],
  [-165, -145, 1],
  [365, 21, 0],
  [45, -197, 1],
  [489, 172, 1],
  [-538, 36, 0],
];
interface Patrol extends Point {
  id: string;
  radiusX: number;
  radiusY: number;
  angle: number;
  speed: number;
  scale: number;
  accessory: Accessory;
}
const patrols: Patrol[] = walkers.map(([x, y, scarf], i) => ({
  id: roamingProfiles[i],
  x,
  y,
  radiusX: 38,
  radiusY: 9,
  angle: i * 1.4,
  speed: 0.22 + i * 0.012,
  scale: 0.81 + (i % 3) * 0.08,
  accessory: scarf ? "scarf" : "cap",
}));
districts.forEach((d, i) => {
  fieldCrew
    .filter((m) => m.team === d.id)
    .slice(1)
    .forEach((member, j) => {
      patrols.push({
        id: member.id,
        x: d.x + d.radiusX * (j ? 0.35 : -0.36),
        y: d.y + d.radiusY * (j ? 0.14 : -0.2),
        radiusX: 58 + i * 4,
        radiusY: 27,
        speed: 0.21,
        angle: i * 0.84 + j * 2.7,
        scale: 1.06,
        accessory: j ? "scarf" : "cap",
      });
    });
});

export function mountScene(
  canvas: HTMLCanvasElement,
  layer: HTMLDivElement,
  read: () => SceneState,
  cb: Callbacks,
) {
  const c = canvas.getContext("2d")!;
  const annotations = mapAnnotations(layer);
  const soil = makeSoil(c);
  const drawLandscape = landscape();
  const stations: Record<string, Point> = {
    mio: { x: -215, y: 129 },
    ren: { x: 190, y: 145 },
    yui: { x: 341, y: -83 },
    kei: { x: -387, y: -119 },
  };
  for (const member of fieldCrew.filter((member) => member.carrier)) {
    const district = districts.find((d) => d.id === member.team)!;
    stations[member.id] = { x: district.x, y: district.y + 18 };
  }
  const workers: Record<string, Point> = {};
  const phases: Record<string, number> = {};
  function stepPhase(id: string, travel: number, dt: number) {
    // 一歩の長さで位相を進めるので、ゆっくり運ぶときと立ち止まったときに空回りしない。
    phases[id] = ((phases[id] ?? 0) + Math.min(travel * 0.67, dt * 0.015)) % (Math.PI * 2);
    return phases[id];
  }
  let width = 0,
    height = 0,
    dpr = 1,
    base = 1,
    cam = { x: 0, y: 0 },
    goal = { ...cam },
    z = 1,
    target = 1,
    frame = 0,
    last = 0,
    hover: string | null = null;
  let drag: { id: number; start: Point; last: Point; moved: boolean } | null = null;
  let pointer: Point | null = null,
    lastZoom = -1;
  let screenAim: Point | null = null;
  const toWorld = (p: Point): Point => ({
    x: cam.x + (p.x - width / 2) / (base * z),
    y: cam.y + (p.y - height / 2) / (base * z),
  });
  const local = (e: MouseEvent): Point => {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  function bounds(p: Point): Point {
    const size = read().world.project?.extent ?? extent;
    return { x: clamp(p.x, -size.x, size.x), y: clamp(p.y, -size.y, size.y) };
  }
  function overview() {
    const size = read().world.project?.extent;
    return size && width && height
      ? Math.min(0.27, width / (2 * base * size.x), height / (2 * base * size.y))
      : 0.27;
  }
  function resize() {
    const r = canvas.getBoundingClientRect();
    width = r.width;
    height = r.height;
    dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    base = Math.max(0.45, Math.min(width / 1190, height / 665));
  }
  function hit(p: Point, now: number) {
    const visible = read()
      .world.foods.map((f) => ({ f, p: positionAt(f, now) }))
      .sort((a, b) => b.p.y - a.p.y);
    for (const { f, p: pos } of visible) {
      const d = f.size ?? artSize[f.kind] * (f.piece ? 0.68 : 1);
      const y = pos.y - (f.kind === "lollipop" ? 38 : f.kind === "sugar" && f.since !== null ? 22 : 12);
      if (Math.hypot((p.x - pos.x) / (d + 9), (p.y - y) / (d * 0.75 + 10)) < 1) return f.id;
    }
    return null;
  }
  function setZoom(factor: number, p?: Point) {
    const next = clamp(target * factor, Math.min(zoomLimit.min, overview()), zoomLimit.max);
    const offset = p ?? { x: width / 2, y: height / 2 };
    const anchor = toWorld(offset);
    // 有理補間した視点でホイール位置のワールド座標を保つ。
    goal = bounds(zoomAt(cam, anchor, z, next));
    target = next;
  }
  const wheel = (e: WheelEvent) => {
    e.preventDefault();
    screenAim = local(e);
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? height : 1;
    setZoom(Math.exp(-clamp(e.deltaY * unit, -420, 420) * 0.00165), local(e));
  };
  const down = (e: PointerEvent) => {
    if (e.button !== 0 && e.button !== 1) return;
    const p = local(e);
    canvas.focus({ preventScroll: true });
    drag = { id: e.pointerId, start: p, last: p, moved: false };
    canvas.setPointerCapture(e.pointerId);
  };
  const move = (e: PointerEvent) => {
    const p = local(e);
    screenAim = p;
    pointer = toWorld(p);
    if (drag?.id === e.pointerId) {
      if (distance(p, drag.start) > 4) drag.moved = true;
      if (drag.moved) {
        cam = bounds({
          x: cam.x - (p.x - drag.last.x) / (z * base),
          y: cam.y - (p.y - drag.last.y) / (z * base),
        });
        goal = { ...cam };
        target = z;
        canvas.style.cursor = "grabbing";
      }
      drag.last = p;
    } else {
      hover = hit(pointer, Date.now());
      canvas.style.cursor = read().tool ? "crosshair" : hover ? "pointer" : "grab";
    }
  };
  const up = (e: PointerEvent) => {
    if (drag?.id !== e.pointerId) return;
    const click = !drag.moved && e.button === 0;
    drag = null;
    if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    canvas.style.cursor = read().tool ? "crosshair" : "grab";
    if (click) {
      const p = toWorld(local(e));
      cb.pick(p, hit(p, Date.now()));
    }
  };
  const cancel = () => {
    drag = null;
    canvas.style.cursor = "grab";
  };
  const leave = () => {
    pointer = null;
    hover = null;
    screenAim = null;
  };
  const home = () => {
    target = 1;
    goal = { x: 0, y: 0 };
    screenAim = null;
  };
  const key = (e: KeyboardEvent) => {
    const step = 90 / (target * base);
    switch (e.key) {
      case "+":
      case "=":
        setZoom(1.3);
        break;
      case "-":
        setZoom(1 / 1.3);
        break;
      case "Home":
        home();
        break;
      case "ArrowLeft":
        goal = bounds({ ...goal, x: goal.x - step });
        break;
      case "ArrowRight":
        goal = bounds({ ...goal, x: goal.x + step });
        break;
      case "ArrowUp":
        goal = bounds({ ...goal, y: goal.y - step });
        break;
      case "ArrowDown":
        goal = bounds({ ...goal, y: goal.y + step });
        break;
      default:
        return;
    }
    e.preventDefault();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(canvas);
  resize();
  canvas.addEventListener("wheel", wheel, { passive: false });
  canvas.addEventListener("pointerdown", down);
  canvas.addEventListener("pointermove", move);
  canvas.addEventListener("pointerup", up);
  canvas.addEventListener("pointercancel", cancel);
  canvas.addEventListener("pointerleave", leave);
  canvas.addEventListener("keydown", key);
  function render(time: number) {
    const dt = last ? Math.min(70, time - last) : 16;
    last = time;
    const blend = 1 - Math.exp(-dt / 76);
    const inv = lerp(1 / z, 1 / target, blend);
    z = 1 / inv;
    cam = { x: lerp(cam.x, goal.x, blend), y: lerp(cam.y, goal.y, blend) };
    const level = Math.round(z * 100);
    if (level !== lastZoom) {
      lastZoom = level;
      cb.zoom(level);
    }
    const { world, selected, actor, draft, tool } = read(),
      now = Date.now(),
      t = time * 0.001;
    const ants: AntAnchor[] = [];
    const acc = (id: string): Accessory =>
      world.accessories[id] ?? roles.find((r) => r.id === id)?.accessory ?? "scarf";
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    const g = c.createLinearGradient(0, 0, width * 0.75, height);
    g.addColorStop(0, "#f6c25e");
    g.addColorStop(0.52, "#efb350");
    g.addColorStop(1, "#e9a64b");
    c.fillStyle = g;
    c.fillRect(0, 0, width, height);
    c.translate(width / 2, height / 2);
    c.scale(base * z, base * z);
    c.translate(-cam.x, -cam.y);
    const marginX = width / (2 * base * z),
      marginY = height / (2 * base * z);
    const inView = (x: number, y: number, pad = 110) =>
      Math.abs(x - cam.x) < marginX + pad && Math.abs(y - cam.y) < marginY + pad;
    if (soil) {
      c.fillStyle = soil;
      c.fillRect(cam.x - marginX, cam.y - marginY, marginX * 2, marginY * 2);
    }
    drawLandscape(c, inView, !world.project);
    world.marks.forEach((m, i) => {
      const macro = clamp((0.76 - z) / 0.2, 0, 1);
      const color = m.team
        ? i % 2
          ? macro > 0.5
            ? "#348d81"
            : "#c6efd0"
          : macro > 0.5
            ? "#ab673a"
            : "#fff0cf"
        : "#95724b";
      chalk(c, m.points, !!m.team, m.color && macro > 0.12 ? m.color : color, isRounded(m), macro, base * z);
      if (z >= 0.76 && (!m.team || z < 0.86)) {
        const p = m.points.reduce((v, p) => (p.y < v.y ? p : v), m.points[0]);
        c.save();
        c.textAlign = "left";
        c.font = '600 14px "Yu Gothic UI",sans-serif';
        c.fillStyle = "#715d3b";
        c.fillText(m.name, p.x + 15, p.y + 22);
        c.restore();
      }
    });
    for (const [x, y, s, v] of fixtures) if (inView(x, y)) tuft(c, x, y, s, v);
    for (const k of stones) if (inView(k.x, k.y)) stone(c, k.x, k.y, k.s);
    c.save();
    c.translate(nest.x, nest.y);
    nestBack(c);
    c.restore();
    for (const e of world.events) {
      const age = (now - e.at) / 1000;
      if (e.type !== "drop" || age < 0 || age > 1.55) continue;
      const a = clamp(age / 0.97, 0, 1),
        ease = a * a * (3 - 2 * a),
        sink = clamp((age - 0.9) / 0.42, 0, 1);
      c.save();
      c.translate(
        lerp(e.x, nest.x, ease),
        lerp(e.y, nest.y - 4, ease) - Math.sin(a * Math.PI) * 16 + sink * 8,
      );
      c.scale(1 - 0.94 * sink, 1 - 0.94 * sink);
      c.rotate(0.24 * ease + 0.42 * sink);
      c.globalAlpha = 1 - sink;
      candy(c, e.kind, e.piece, false);
      c.restore();
      const face = e.x < 0 ? 1 : -1,
        carryX = lerp(e.x - face * 22, nest.x - face * 78, ease),
        carryY = lerp(e.y + 3, nest.y + 14, ease);
      workers[e.actor] = { x: carryX, y: carryY };
      ants.push({ id: e.actor, profile: e.actor, p: { x: carryX, y: carryY - 22 } });
      ant(c, carryX, carryY, face, t * 10, a < 1 ? "carry" : "idle", acc(e.actor), 1.07);
      if (age > 0.76 && age < 1.48) {
        const wave = (age - 0.76) / 0.72;
        c.save();
        c.globalAlpha = 1 - wave;
        for (let j = 0; j < 9; j++) {
          const angle = j * 0.698;
          stone(
            c,
            Math.cos(angle) * (24 + wave * 51),
            nest.y + Math.sin(angle) * (6 + wave * 20) - Math.sin(wave * Math.PI) * 11,
            0.49,
          );
        }
        c.restore();
      }
    }
    c.save();
    c.translate(nest.x, nest.y);
    nestLip(c);
    c.restore();
    for (const patrol of patrols) {
      if (world.project) break;
      if (!inView(patrol.x, patrol.y, patrol.radiusX + 60)) continue;
      const angle = t * patrol.speed + patrol.angle;
      const x = patrol.x + patrol.radiusX * Math.sin(angle),
        y = patrol.y + patrol.radiusY * Math.cos(angle);
      const travel =
        dt *
        0.001 *
        patrol.speed *
        Math.hypot(patrol.radiusX * Math.cos(angle), patrol.radiusY * Math.sin(angle));
      const phase = stepPhase(patrol.id, travel, dt);
      ants.push({ id: patrol.id, profile: patrol.id, p: { x, y: y - 21 } });
      ant(
        c,
        x,
        y,
        Math.cos(angle) > 0 ? 1 : -1,
        phase + patrol.angle,
        "walk",
        patrol.accessory,
        patrol.scale,
      );
    }
    function worker(id: string, target: Point, face: number, action: "carry" | "pull" | "idle") {
      const w = workers[id] ?? { ...target };
      workers[id] = w;
      const gap = distance(w, target),
        step = Math.min(1, (dt * 0.33) / Math.max(0.01, gap)),
        dx = target.x - w.x;
      w.x = lerp(w.x, target.x, step);
      w.y = lerp(w.y, target.y, step);
      const phase = stepPhase(id, gap * step, dt);
      if (!inView(w.x, w.y)) return;
      ants.push({ id, profile: id, p: { x: w.x, y: w.y - 22 } });
      if (id === actor && z > 0.65) {
        c.save();
        c.strokeStyle = "#347e72";
        c.lineWidth = 1.8;
        c.beginPath();
        c.ellipse(w.x, w.y + 2, 17, 4.6, 0, 0, Math.PI * 2);
        c.stroke();
        c.restore();
        if (z > 1.7) {
          c.save();
          c.textAlign = "center";
          c.font = 'bold 9px "Yu Gothic UI",sans-serif';
          c.fillStyle = "#316859";
          c.fillText("あなた", w.x, w.y + 16);
          c.restore();
        }
      }
      ant(
        c,
        w.x,
        w.y,
        gap > 9 ? (dx >= 0 ? 1 : -1) : face,
        gap > 9 || action === "carry" ? phase : t * 3,
        gap > 9 ? "walk" : action,
        acc(id),
        1.07,
      );
    }
    const far = base * z < farScale;
    const crew = world.project?.crew;
    if (crew) {
      // 運び手の通り道。お菓子ごとに巣までの点線を1本だけ引く。
      const routes = new Map<string, Point>();
      for (const a of crew) if (a.mode === "carry" && !routes.has(a.food)) routes.set(a.food, a.point);
      if (routes.size) {
        c.save();
        c.strokeStyle = "#7a4a2a38";
        c.lineWidth = Math.max(2, 1.4 / (base * z));
        c.setLineDash([3 / (base * z) + 2, 7 / (base * z) + 4]);
        c.lineCap = "round";
        c.beginPath();
        for (const p of routes.values()) {
          c.moveTo(nest.x, nest.y);
          c.lineTo(p.x, p.y);
        }
        c.stroke();
        c.restore();
      }
    }
    const occupied = new Set(world.foods.filter((f) => f.since !== null).map((f) => f.owner));
    const visibleMembers = world.project
      ? []
      : roles.map(({ id }) => ({ id, point: stations[id] }));
    for (const r of visibleMembers) {
      if (
        occupied.has(r.id) ||
        world.events.some((e) => e.type === "drop" && e.actor === r.id && now - e.at < 1550)
      )
        continue;
      const paused = world.foods.find((f) => f.owner === r.id),
        where = paused && !world.project ? positionAt(paused, now) : null;
      worker(r.id, where ? { x: where.x - 38, y: where.y + 7 } : r.point, r.id === "yui" ? -1 : 1, "idle");
    }
    const positions = world.foods.map((f) => ({ f, p: positionAt(f, now) })).sort((a, b) => a.p.y - b.p.y);
    for (const { f, p } of positions) {
      const active = f.since !== null,
        chosen = selected === f.id,
        over = hover === f.id && !tool;
      const face = p.x < 0 ? 1 : -1,
        size = f.size ?? artSize[f.kind] * (f.piece ? 0.65 : 1),
        light = f.kind === "sugar";
      const pullX = p.x + face * (size * 0.82 + 34);
      if (active && f.owner)
        worker(
          f.owner,
          { x: light ? p.x - face * 22 : pullX, y: p.y + (light ? 4 : 8) },
          light ? face : -face,
          light ? "carry" : "pull",
        );
      if (!inView(p.x, p.y)) continue;
      const split = world.events.find(
        (e) => e.type === "split" && (f.id === e.id.slice(0, -6) + "-a" || f.id === e.id.slice(0, -6) + "-b"),
      );
      const a = split ? clamp((now - split.at) / 550, 0, 1) : 1,
        side = f.id.endsWith("-a") ? -1 : 1;
      c.save();
      c.translate(p.x + side * (-27 * (1 - a) + Math.sin(a * Math.PI) * 9), p.y - Math.sin(a * Math.PI) * 7);
      if (chosen || over) {
        c.save();
        c.setLineDash([5, 5]);
        c.strokeStyle = chosen ? "#704034" : "#fff2ce";
        c.lineWidth = 2;
        c.beginPath();
        c.ellipse(0, 7, size + 12, size * 0.35 + 7, 0, 0, Math.PI * 2);
        c.stroke();
        c.restore();
      }
      const attached = active && f.owner && distance(workers[f.owner], p) < size + 54;
      if (attached && !light) {
        c.save();
        c.strokeStyle = "#785338";
        c.lineWidth = 2.8;
        c.beginPath();
        c.moveTo(face * size * 0.69, -7);
        c.quadraticCurveTo(face * size, -3, face * (size * 0.82 + 18), -7);
        c.stroke();
        c.restore();
      }
      if (f.help) {
        const helperX = -face * (size * 0.75 + 13);
        ant(c, helperX, 8, face, t * 4, "pull", "cap", 0.95);
        ants.push({ id: "help-" + f.id, profile: "helper", p: { x: p.x + helperX, y: p.y - 12 } });
      }
      if (split && a < 1) {
        ant(
          c,
          side * (size * 0.8 + 12 + Math.sin(a * Math.PI) * 9),
          11,
          -side,
          t * 8,
          "pull",
          side < 0 ? "cap" : "scarf",
          1.02,
        );
      }
      c.save();
      if (attached && light) {
        c.translate(0, -12 - Math.abs(Math.cos(phases[f.owner!] ?? 0)) * 0.55);
      }
      if (active) c.rotate(Math.sin(t * 5) * 0.018);
      if (f.bits) (far ? drawSweetFar : drawSweet)(c, f, t);
      else candy(c, f.kind, f.piece, !(attached && light));
      c.restore();
      const d = deadline(f, now),
        show = chosen || over || z > 1.48,
        bar = show || z > 0.8;
      if (bar) {
        const y = f.kind === "lollipop" ? -113 * (f.piece ? 0.72 : 1) : f.kind === "chocolate" ? -69 : -54;
        if (show || d.risk) pill(c, (d.risk ? "!  " : "") + d.label, 0, y, d.risk);
        c.fillStyle = "#f9e5b7";
        c.beginPath();
        c.roundRect(-21, y + 8, 42, 3, 1.5);
        c.fill();
        c.fillStyle = d.risk ? "#bb5637" : "#4d9774";
        c.fillRect(-21, y + 8, 42 * d.fraction, 3);
      }
      if (show && z < 1.4) {
        c.textAlign = "center";
        c.font = 'bold 11px "Yu Gothic UI",sans-serif';
        c.fillStyle = "#53352d";
        c.fillText(f.name, 0, 29);
      }
      c.restore();
    }
    if (crew) {
      const dots: Point[] = [],
        mine: Point[] = [];
      for (const a of crew) {
        let p = a.point,
          face: number = a.face,
          mode: "walk" | "carry" | "pull" | "idle" = "pull",
          phase = t * 7 + a.offset * 40,
          load = false;
        if (a.mode === "carry") {
          const pose = carryPose(a, { x: nest.x, y: nest.y + 6 }, t + a.offset * 30);
          p = pose.p;
          face = pose.face;
          mode = pose.mode;
          load = pose.load;
          phase = t * 11 + a.offset * 40;
        } else if (a.mode === "wait") {
          // 待っているアリも止めない。体を揺らし、ときどき足踏みして向きを変える。
          const u = (t * 0.22 + a.offset) % 1;
          mode = u < 0.16 ? "walk" : "idle";
          phase = u < 0.16 ? t * 12 : t * 2.4 + a.offset * 9;
          if (u >= 0.08 && u < 0.16) face = -face;
        }
        if (!inView(p.x, p.y, 40)) continue;
        if (far) {
          (a.owner === actor ? mine : dots).push(p);
          continue;
        }
        ants.push({ id: a.id, profile: a.owner, p: { x: p.x, y: p.y - 22 }, doing: crewDoing[a.mode] });
        if (a.owner === actor && z > 0.65) {
          c.save();
          c.strokeStyle = "#347e72";
          c.lineWidth = 1.8;
          c.beginPath();
          c.ellipse(p.x, p.y + 2, 17, 4.6, 0, 0, Math.PI * 2);
          c.stroke();
          c.restore();
        }
        ant(c, p.x, p.y, face, phase, mode, a.owner === actor ? "scarf" : "cap", 0.95);
        if (load) {
          // 運び去るかけら
          c.fillStyle = "#96503a";
          c.fillRect(p.x + face * 15 - 4, p.y - 22, 8, 6);
        } else if (a.mode === "eat" && (t * 1.3 + a.offset) % 1 < 0.3) {
          // かじり取った欠片が飛ぶ
          const k = ((t * 1.3 + a.offset) % 1) / 0.3;
          c.fillStyle = "#87452f";
          c.fillRect(p.x + face * (18 + k * 10), p.y - 14 - Math.sin(k * Math.PI) * 8, 2.4, 2.4);
        }
      }
      if (far) {
        // 遠景では1匹ずつ描かず、点をまとめて塗る。行列は流れる点の列に見える。
        const s = 3.2 / (base * z);
        c.fillStyle = "#3d1f30";
        c.beginPath();
        for (const p of dots) c.rect(p.x - s / 2, p.y - s, s, s);
        c.fill();
        c.fillStyle = "#1f8a7d";
        c.beginPath();
        for (const p of mine) c.rect(p.x - s / 2, p.y - s, s, s);
        c.fill();
      }
    }
    for (const e of world.events) {
      const a = (now - e.at) / 750;
      if (e.type !== "split" || a < 0 || a >= 1) continue;
      c.save();
      c.globalAlpha = 1 - a;
      for (let j = 0; j < 8; j++) {
        const theta = j * 0.78;
        c.save();
        c.translate(
          e.x + Math.cos(theta) * a * 47,
          e.y - 16 + Math.sin(theta) * a * 19 - Math.sin(a * Math.PI) * 22,
        );
        c.rotate(theta + a * 3);
        c.fillStyle = j % 2 ? "#88462b" : "#f7ce8c";
        c.fillRect(-2, -2, 4, 4);
        c.restore();
      }
      c.restore();
    }
    if (tool && pointer) {
      chalk(c, [...draft, pointer], false, "#603f32", false);
      for (const p of [...draft, pointer]) {
        c.beginPath();
        c.arc(p.x, p.y, 4 / z, 0, Math.PI * 2);
        c.fillStyle = "#fff9e8";
        c.fill();
        c.strokeStyle = "#75513b";
        c.lineWidth = 1.5 / z;
        c.stroke();
      }
    }
    const project = (p: Point) => ({
      x: width / 2 + (p.x - cam.x) * base * z,
      y: height / 2 + (p.y - cam.y) * base * z,
    });
    annotations.render({
      world,
      now,
      zoom: tool && z > 1 ? 1 : z,
      width,
      height,
      project,
      ants,
      foods: positions,
      selected,
      aim: screenAim ?? { x: width / 2, y: height / 2 },
    });
    frame = requestAnimationFrame(render);
  }
  frame = requestAnimationFrame(render);
  if (import.meta.env.DEV) {
    // 開発時だけ、重さの確認用にコンソールから1フレームの描画時間を測れるようにする。
    (window as unknown as { __colonyMeasure?: (zoom: number, frames?: number) => number[] }).__colonyMeasure = (
      level,
      frames = 30,
    ) => {
      target = level;
      const costs: number[] = [];
      for (let i = 0; i < frames; i++) {
        cancelAnimationFrame(frame);
        const started = performance.now();
        render(started + i * 16);
        costs.push(performance.now() - started);
      }
      return costs;
    };
  }
  return {
    zoom: setZoom,
    home,
    viewpoint(level: number) {
      screenAim = null;
      target = level === 0.27 ? overview() : level;
      if (level <= 1) {
        goal = { x: 0, y: 0 };
        return;
      }
      const { world, actor, selected } = read();
      const food =
        world.foods.find((f) => f.id === selected) ??
        world.foods.find((f) => f.owner === actor) ??
        world.foods.find((f) => f.kind === "sugar") ??
        world.foods[0];
      goal = bounds(food ? positionAt(food, Date.now()) : (workers[actor] ?? nest));
    },
    frameDelivery(p: Point) {
      const visible = (q: Point) =>
        Math.abs(q.x - cam.x) < width / (2 * base * z) - 60 &&
        Math.abs(q.y - cam.y) < height / (2 * base * z) - 55;
      if (visible(p) && visible(nest)) return;
      target = clamp(
        Math.min(
          z,
          width / (base * (Math.abs(p.x - nest.x) + 210)),
          height / (base * (Math.abs(p.y - nest.y) + 175)),
        ),
        Math.min(zoomLimit.min, overview()),
        zoomLimit.max,
      );
      goal = bounds({ x: (p.x + nest.x) / 2, y: (p.y + nest.y) / 2 });
    },
    focus(p: Point) {
      target = Math.max(target, 1.45);
      goal = bounds({ ...p });
    },
    destroy() {
      cancelAnimationFrame(frame);
      annotations.clear();
      ro.disconnect();
      canvas.removeEventListener("wheel", wheel);
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointercancel", cancel);
      canvas.removeEventListener("pointerleave", leave);
      canvas.removeEventListener("keydown", key);
    },
  };
}
