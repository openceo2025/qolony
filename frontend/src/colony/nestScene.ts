// 巣の中（地面の下の断面）を描くキャンバス。部屋 = チーム、トンネル = 組織のつながり。
// 情報の読み書きは ColonyView（React）側。ここは描画と、クリック・ドラッグ・ズームの受け付けだけ。
import { ant, emblem, emblemColor, oval, seeded, stone } from "./art";
import {
  NURSERY_ROOM,
  ROOT_ROOM,
  STORAGE_ROOM,
  along,
  pathBetween,
  pathLength,
  roleAccessory,
  roleOrder,
  standingSpot,
  type NestLayout,
  type NestPoint,
  type NestRoom,
} from "./nestModel";
import type { ColonyRole } from "../types";

export interface NestAnt {
  id: string;
  name: string;
  role: ColonyRole;
}

export interface NestSceneState {
  layout: NestLayout;
  ants: NestAnt[];
  viewer: string;
  /** 選んでいる部屋。所属するアリがここへ集まってくる。 */
  focus: string | null;
  selected: string | null;
  /** 光らせる部屋（掘れる部屋の候補など）。 */
  accept: Set<string> | null;
  roomOutput: Map<string, number>;
  companyOutput: number;
  teachingMinutes: number;
}

export interface NestCallbacks {
  room(id: string | null): void;
  ant(id: string): void;
  /** to が null = 部屋の外（土の上）へ落とした = from の部屋から外す。 */
  drop(antId: string, from: string | null, to: string | null): void;
  canDrop(antId: string, roomId: string): boolean;
  exit(): void;
  zoom(level: number): void;
}

interface Walker {
  p: NestPoint;
  room: string;
  target: string;
  spot: NestPoint;
  path: NestPoint[] | null;
  t0: number;
  dur: number;
  face: number;
  offset: number;
}

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const ease = (t: number) => (t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2);
const roomFill = "#f0d3a2";
const roomEdge = "#b47c4b";
const soilTop = "#b47d4c";
const soilDeep = "#7c4b2d";

function hashOf(text: string) {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return (h >>> 0) / 4294967296;
}

export function mountNest(canvas: HTMLCanvasElement, read: () => NestSceneState, cb: NestCallbacks) {
  const c = canvas.getContext("2d")!;
  let width = 0,
    height = 0,
    dpr = 1,
    base = 1,
    z = 1,
    target = 1,
    cam = { x: 0, y: 260 },
    goal = { ...cam },
    frame = 0,
    last = 0,
    active = false,
    lastZoom = -1,
    exitPull = 0;
  let pointer: NestPoint | null = null;
  let hoverAnt: string | null = null,
    hoverRoom: string | null = null;
  let drag:
    | { id: number; start: NestPoint; last: NestPoint; moved: boolean; ant: string | null; from: string | null }
    | null = null;
  const walkers = new Map<string, Walker>();
  const pebbles = (() => {
    const r = seeded(77);
    return Array.from({ length: 420 }, () => ({ x: (r() - 0.5) * 6000, y: r() * 3600, s: 0.3 + r() * 0.8 }));
  })();

  const toWorld = (p: NestPoint): NestPoint => ({
    x: cam.x + (p.x - width / 2) / (base * z),
    y: cam.y + (p.y - height / 2) / (base * z),
  });
  const local = (e: MouseEvent): NestPoint => {
    const r = canvas.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  };
  function limits() {
    const { extent } = read().layout;
    return { minX: extent.minX - 200, maxX: extent.maxX + 200, minY: -160, maxY: extent.maxY + 100 };
  }
  function bounds(p: NestPoint): NestPoint {
    const l = limits();
    return { x: clamp(p.x, l.minX, l.maxX), y: clamp(p.y, l.minY, l.maxY) };
  }
  function fitZoom() {
    const l = limits();
    // 下の道具欄（約110px）に隠れないよう、その分を除いた高さに収める。
    return clamp(Math.min(width / (base * (l.maxX - l.minX)), (height - 120) / (base * (l.maxY - l.minY))), 0.18, 1);
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

  function roomAt(p: NestPoint, teamsOnly = false): NestRoom | null {
    const { layout } = read();
    const list: NestRoom[] = teamsOnly
      ? layout.rooms.filter((room) => room.id !== ROOT_ROOM)
      : [...layout.rooms, layout.storage, layout.nursery];
    for (const room of list) {
      const dx = (p.x - room.x) / (room.w / 2 + 10),
        dy = (p.y - room.y) / (room.h / 2 + 34);
      if (dx * dx + dy * dy < 1) return room;
    }
    return null;
  }
  function antAt(p: NestPoint): string | null {
    let best: string | null = null,
      bestD = 22;
    for (const [id, w] of walkers) {
      const d = Math.hypot(p.x - w.p.x, p.y - (w.p.y - 14));
      if (d < bestD) {
        best = id;
        bestD = d;
      }
    }
    return best;
  }

  const wheel = (e: WheelEvent) => {
    e.preventDefault();
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? height : 1;
    const factor = Math.exp(-clamp(e.deltaY * unit, -420, 420) * 0.00165);
    const min = Math.min(0.3, fitZoom());
    // 一番引いた状態で、入口の近くからさらに引くと地上へ戻る。
    if (factor < 1 && target <= min + 0.001 && cam.y < 420) {
      exitPull += 1 - factor;
      if (exitPull > 0.35) {
        exitPull = 0;
        cb.exit();
      }
      return;
    }
    exitPull = 0;
    const next = clamp(target * factor, min, 2.6);
    const anchor = toWorld(local(e));
    const ratio = z / next;
    goal = bounds({ x: anchor.x + (cam.x - anchor.x) * ratio, y: anchor.y + (cam.y - anchor.y) * ratio });
    target = next;
  };
  const down = (e: PointerEvent) => {
    if (e.button !== 0) return;
    const p = local(e);
    const w = toWorld(p);
    canvas.focus({ preventScroll: true });
    const antId = antAt(w);
    drag = { id: e.pointerId, start: p, last: p, moved: false, ant: antId, from: antId ? (walkers.get(antId)?.room ?? null) : null };
    canvas.setPointerCapture(e.pointerId);
  };
  const move = (e: PointerEvent) => {
    const p = local(e);
    pointer = toWorld(p);
    if (drag?.id === e.pointerId) {
      if (Math.hypot(p.x - drag.start.x, p.y - drag.start.y) > 5) drag.moved = true;
      if (drag.moved && !drag.ant) {
        cam = bounds({ x: cam.x - (p.x - drag.last.x) / (z * base), y: cam.y - (p.y - drag.last.y) / (z * base) });
        goal = { ...cam };
        target = z;
        canvas.style.cursor = "grabbing";
      }
      if (drag.moved && drag.ant) canvas.style.cursor = "grabbing";
      drag.last = p;
      return;
    }
    hoverAnt = antAt(pointer);
    hoverRoom = hoverAnt ? null : (roomAt(pointer)?.id ?? null);
    canvas.style.cursor = hoverAnt ? "grab" : hoverRoom ? "pointer" : "default";
  };
  const up = (e: PointerEvent) => {
    if (drag?.id !== e.pointerId) return;
    const current = drag;
    drag = null;
    if (canvas.hasPointerCapture(e.pointerId)) canvas.releasePointerCapture(e.pointerId);
    canvas.style.cursor = "default";
    const w = toWorld(local(e));
    if (current.ant && current.moved) {
      const room = roomAt(w, true);
      const walker = walkers.get(current.ant);
      if (walker) {
        // 手を離した場所からトンネルを通って戻る（または新しい部屋へ入る）。
        walker.p = { ...w };
        walker.path = null;
        walker.target = "";
      }
      if (room && room.id !== current.from) cb.drop(current.ant, current.from, room.id);
      else if (!room && current.from && current.from !== ROOT_ROOM) cb.drop(current.ant, current.from, null);
      return;
    }
    if (current.moved) return;
    if (current.ant) {
      cb.ant(current.ant);
      return;
    }
    const room = roomAt(w);
    cb.room(room?.id ?? null);
  };
  const cancel = () => {
    drag = null;
  };
  const leave = () => {
    pointer = null;
    hoverAnt = null;
    hoverRoom = null;
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

  function desiredRooms(state: NestSceneState) {
    const { layout, focus, ants } = state;
    const focusRoom = focus ? layout.byId.get(focus) : undefined;
    const gather = focusRoom && focusRoom.id !== ROOT_ROOM && !!focusRoom.members.length;
    const where = new Map<string, string>();
    for (const a of ants) {
      const home = layout.homes.get(a.id) ?? ROOT_ROOM;
      where.set(a.id, gather && focusRoom!.members.includes(a.id) ? focusRoom!.id : home);
    }
    const occupants = new Map<string, NestAnt[]>();
    for (const a of ants) {
      const room = where.get(a.id)!;
      occupants.set(room, [...(occupants.get(room) ?? []), a]);
    }
    const spots = new Map<string, NestPoint>();
    for (const [roomId, list] of occupants) {
      const room = layout.byId.get(roomId);
      if (!room) continue;
      list.sort((a, b) => roleOrder.indexOf(a.role) - roleOrder.indexOf(b.role) || a.name.localeCompare(b.name, "ja"));
      list.forEach((a, i) => spots.set(a.id, standingSpot(room, i, list.length)));
    }
    return { where, spots };
  }

  function stepWalkers(state: NestSceneState, now: number) {
    const { where, spots } = desiredRooms(state);
    const alive = new Set(state.ants.map((a) => a.id));
    for (const id of walkers.keys()) if (!alive.has(id)) walkers.delete(id);
    for (const a of state.ants) {
      const room = where.get(a.id)!;
      const spot = spots.get(a.id)!;
      let w = walkers.get(a.id);
      if (!w) {
        w = { p: { ...spot }, room, target: room, spot, path: null, t0: now, dur: 1, face: 1, offset: hashOf(a.id) };
        walkers.set(a.id, w);
        continue;
      }
      if (drag?.ant === a.id && drag.moved) continue;
      if (w.target !== room) {
        // 別の部屋へ。トンネルをたどる。投げ落とされた直後は、いまいる場所から最寄りの部屋を起点にする。
        const startRoom = w.target === "" ? (roomAt(w.p, false)?.id ?? w.room) : w.room;
        const route = [{ ...w.p }, ...pathBetween(state.layout, startRoom, room).slice(1, -1), spot];
        w.path = route;
        w.t0 = now;
        w.dur = clamp(pathLength(route) / 420, 0.5, 4.2) * 1000;
        w.target = room;
        w.spot = spot;
      } else if (Math.hypot(w.spot.x - spot.x, w.spot.y - spot.y) > 1 && !w.path) {
        w.path = [{ ...w.p }, spot];
        w.t0 = now;
        w.dur = 600;
        w.spot = spot;
      }
      if (w.path) {
        const k = clamp((now - w.t0) / w.dur, 0, 1);
        const next = along(w.path, ease(k));
        if (Math.abs(next.x - w.p.x) > 0.2) w.face = next.x > w.p.x ? 1 : -1;
        w.p = next;
        if (k >= 1) {
          w.path = null;
          w.room = w.target;
          w.p = { ...w.spot };
        }
      }
    }
  }

  function drawRoom(room: NestRoom, far: boolean, glow: boolean, chosen: boolean, output: number) {
    c.save();
    // 部屋の空洞。床は平らに、天井は丸く。
    c.beginPath();
    c.ellipse(room.x, room.y, room.w / 2, room.h / 2, 0, 0, Math.PI * 2);
    c.fillStyle = roomEdge;
    c.fill();
    c.beginPath();
    c.ellipse(room.x, room.y + 3, room.w / 2 - 7, room.h / 2 - 6, 0, 0, Math.PI * 2);
    c.fillStyle = roomFill;
    c.fill();
    c.beginPath();
    c.ellipse(room.x, room.y + room.h / 2 - 12, room.w / 2 - 26, 9, 0, 0, Math.PI * 2);
    c.fillStyle = "#e2bd85";
    c.fill();
    if (glow || chosen) {
      c.lineWidth = chosen ? 5 : 4;
      c.strokeStyle = chosen ? "#2f7d6f" : "#f8f1c8";
      c.setLineDash(chosen ? [] : [10, 7]);
      c.beginPath();
      c.ellipse(room.x, room.y, room.w / 2 + 6, room.h / 2 + 6, 0, 0, Math.PI * 2);
      c.stroke();
      c.setLineDash([]);
    }
    if (far) {
      // 遠くからは扉を閉じた部屋に見せ、人数だけ出す。
      c.fillStyle = "#8a5a35";
      c.beginPath();
      c.roundRect(room.x - 22, room.y - 30, 44, 56, [22, 22, 4, 4]);
      c.fill();
      c.fillStyle = "#fff4dc";
      c.font = "bold 30px 'Yu Gothic UI',sans-serif";
      c.textAlign = "center";
      c.fillText(String(room.members.length), room.x, room.y + 10);
    }
    // 食べ物の山（今月運び込んだ量）。部屋の右隅に積もる。
    if (output > 0 && room.id !== STORAGE_ROOM && room.id !== NURSERY_ROOM) pile(room.x + room.w / 2 - 52, room.y + room.h / 2 - 14, output, 24);
    // 看板
    const signY = room.y - room.h / 2 - 26;
    c.font = "bold 17px 'Yu Gothic UI',sans-serif";
    const label = room.name;
    const textW = Math.min(260, c.measureText(label).width);
    const boardW = textW + 58;
    c.fillStyle = "#6f4527";
    c.fillRect(room.x - 3, signY + 12, 6, 18);
    c.fillStyle = "#a8743f";
    c.beginPath();
    c.roundRect(room.x - boardW / 2, signY - 16, boardW, 32, 6);
    c.fill();
    c.strokeStyle = "#7d522c";
    c.lineWidth = 2;
    c.stroke();
    emblem(c, room.x - boardW / 2 + 18, signY, 11, room.emblem);
    c.fillStyle = "#fff6e2";
    c.textAlign = "left";
    c.fillText(label, room.x - boardW / 2 + 34, signY + 6, 260);
    c.restore();
  }

  function pile(x: number, y: number, minutes: number, cap: number) {
    const count = clamp(Math.round(Math.sqrt(minutes / 60) * 2.2), 1, cap);
    const r = seeded(Math.round(minutes) + 3);
    let k = 0;
    for (let row = 0; k < count; row++) {
      const inRow = Math.max(1, 6 - row);
      for (let i = 0; i < inRow && k < count; i++, k++) {
        const px = x + (i - (inRow - 1) / 2) * 9 + (r() - 0.5) * 3;
        const py = y - row * 6.5;
        c.fillStyle = r() < 0.3 ? "#f6e6c2" : r() < 0.6 ? "#9a5a36" : "#7a4127";
        c.save();
        c.translate(px, py);
        c.rotate((r() - 0.5) * 0.8);
        c.fillRect(-4, -3, 8, 6);
        c.restore();
      }
    }
  }

  function drawTunnel(points: NestPoint[], widthPx: number) {
    if (points.length < 2) return;
    c.save();
    c.lineCap = "round";
    c.lineJoin = "round";
    c.beginPath();
    c.moveTo(points[0].x, points[0].y);
    for (const p of points.slice(1)) c.lineTo(p.x, p.y);
    c.strokeStyle = roomEdge;
    c.lineWidth = widthPx + 12;
    c.stroke();
    c.strokeStyle = "#e7c391";
    c.lineWidth = widthPx;
    c.stroke();
    c.restore();
  }

  function render(time: number) {
    if (!active) return;
    const dt = last ? Math.min(70, time - last) : 16;
    last = time;
    const blend = 1 - Math.exp(-dt / 90);
    z = 1 / lerp(1 / z, 1 / target, blend);
    cam = { x: lerp(cam.x, goal.x, blend), y: lerp(cam.y, goal.y, blend) };
    const level = Math.round(z * 100);
    if (level !== lastZoom) {
      lastZoom = level;
      cb.zoom(level);
    }
    const state = read();
    const { layout } = state;
    const now = performance.now();
    const t = time * 0.001;
    stepWalkers(state, now);
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.fillStyle = soilDeep;
    c.fillRect(0, 0, width, height);
    c.translate(width / 2, height / 2);
    c.scale(base * z, base * z);
    c.translate(-cam.x, -cam.y);
    const halfW = width / (2 * base * z),
      halfH = height / (2 * base * z);
    const left = cam.x - halfW,
      top = cam.y - halfH;
    // 地上（入口のある地面）と、深くなるほど暗くなる土。
    const g = c.createLinearGradient(0, 0, 0, Math.max(800, layout.extent.maxY));
    g.addColorStop(0, soilTop);
    g.addColorStop(1, soilDeep);
    c.fillStyle = g;
    c.fillRect(left, Math.max(top, 0), halfW * 2, halfH * 2 + Math.abs(top));
    if (top < 0) {
      c.fillStyle = "#efb350";
      c.fillRect(left, top, halfW * 2, -top);
      c.fillStyle = "#6f9a4a";
      for (let x = Math.floor(left / 26) * 26; x < left + halfW * 2; x += 26) {
        const h = 8 + hashOf(String(x)) * 10;
        c.beginPath();
        c.moveTo(x, 0);
        c.quadraticCurveTo(x + 4, -h, x + 2, -h - 4);
        c.quadraticCurveTo(x + 8, -h + 2, x + 10, 0);
        c.fill();
      }
      c.fillStyle = "#8d5a33";
      c.fillRect(left, -4, halfW * 2, 8);
    }
    c.strokeStyle = "#00000014";
    c.lineWidth = 3;
    for (let depth = 120; depth < layout.extent.maxY + 400; depth += 210) {
      if (depth < top - 20 || depth > top + halfH * 2 + 20) continue;
      c.beginPath();
      for (let x = left - 40; x < left + halfW * 2 + 40; x += 60) {
        const y = depth + Math.sin(x * 0.004 + depth) * 12;
        if (x === left - 40) c.moveTo(x, y);
        else c.lineTo(x, y);
      }
      c.stroke();
    }
    for (const p of pebbles)
      if (p.y > 10 && Math.abs(p.x - cam.x) < halfW + 20 && Math.abs(p.y - cam.y) < halfH + 20) stone(c, p.x, p.y, p.s);

    const far = base * z < 0.42;
    // 入口から社長室への縦穴と、食料庫・育児室への横穴。
    drawTunnel(layout.shaft, 50);
    drawTunnel([{ x: 0, y: layout.storage.y }, { x: layout.storage.x, y: layout.storage.y }], 40);
    drawTunnel([{ x: 0, y: layout.nursery.y }, { x: layout.nursery.x, y: layout.nursery.y }], 40);
    for (const tunnel of layout.tunnels) drawTunnel(tunnel.points, 40);
    // 入口の穴
    oval(c, 0, -2, 46, 12, "#3d2416");
    oval(c, 0, -5, 38, 8, "#24150d");

    for (const room of layout.rooms) {
      if (Math.abs(room.x - cam.x) > halfW + room.w || Math.abs(room.y - cam.y) > halfH + room.h + 60) continue;
      const draggingAnt = drag?.ant && drag.moved ? drag.ant : null;
      const glow = draggingAnt
        ? room.id !== ROOT_ROOM && cb.canDrop(draggingAnt, room.id)
        : !!state.accept?.has(room.id) || hoverRoom === room.id;
      drawRoom(room, far, glow, state.focus === room.id, state.roomOutput.get(room.id) ?? 0);
    }
    // 食料庫: 会社全体で運び込んだエサが積もる。
    drawRoom(layout.storage, false, hoverRoom === STORAGE_ROOM, state.focus === STORAGE_ROOM, 0);
    pile(layout.storage.x - 30, layout.storage.y + layout.storage.h / 2 - 14, state.companyOutput, 60);
    // 育児室: 教えた時間だけ、小さなアリが育っている。
    drawRoom(layout.nursery, false, hoverRoom === NURSERY_ROOM, state.focus === NURSERY_ROOM, 0);
    const young = clamp(Math.round(Math.sqrt(state.teachingMinutes / 30)), 0, 8);
    for (let i = 0; i < young; i++) {
      const x = layout.nursery.x - 70 + i * 20;
      const y = layout.nursery.y + layout.nursery.h / 2 - 16;
      ant(c, x, y, i % 2 ? 1 : -1, t * 3 + i, "idle", "helmet-white", 0.55);
    }

    // アリ
    const draggedId = drag?.ant && drag.moved ? drag.ant : null;
    const sorted = [...state.ants].sort((a, b) => (walkers.get(a.id)?.p.y ?? 0) - (walkers.get(b.id)?.p.y ?? 0));
    for (const a of sorted) {
      const w = walkers.get(a.id);
      if (!w || a.id === draggedId) continue;
      if (Math.abs(w.p.x - cam.x) > halfW + 40 || Math.abs(w.p.y - cam.y) > halfH + 40) continue;
      if (far) {
        c.fillStyle = a.id === state.viewer ? "#1f8a7d" : "#3d1f30";
        c.fillRect(w.p.x - 5, w.p.y - 10, 10, 10);
        continue;
      }
      const walking = !!w.path;
      const sway = (t * 0.25 + w.offset) % 1;
      const mode = walking ? "walk" : sway < 0.1 ? "walk" : "idle";
      const phase = walking ? t * 11 + w.offset * 30 : t * 2.6 + w.offset * 9;
      if (state.selected === a.id || a.id === state.viewer) {
        c.strokeStyle = state.selected === a.id ? "#2f7d6f" : "#347e7288";
        c.lineWidth = 2;
        c.beginPath();
        c.ellipse(w.p.x, w.p.y + 2, 18, 5, 0, 0, Math.PI * 2);
        c.stroke();
      }
      ant(c, w.p.x, w.p.y, walking ? w.face : (w.offset > 0.5 ? 1 : -1) * (sway > 0.5 && sway < 0.55 ? -1 : 1), phase, mode, roleAccessory[a.role], 1.05);
      if (z * base > 0.75 || state.selected === a.id || hoverAnt === a.id) {
        c.font = `${state.selected === a.id ? "bold " : ""}12px 'Yu Gothic UI',sans-serif`;
        c.textAlign = "center";
        c.fillStyle = "#3c2418";
        c.fillText(a.name, w.p.x, w.p.y + 16);
      }
      const room = layout.byId.get(w.room);
      if (room?.leader === a.id && !walking) {
        // 部屋の主には小さな旗
        c.fillStyle = "#5a3a22";
        c.fillRect(w.p.x - 15, w.p.y - 44, 1.6, 16);
        c.fillStyle = emblemColor[room.emblem] ?? "#7b6a55";
        c.beginPath();
        c.moveTo(w.p.x - 13.4, w.p.y - 44);
        c.lineTo(w.p.x - 3, w.p.y - 40.5);
        c.lineTo(w.p.x - 13.4, w.p.y - 37);
        c.fill();
      }
    }
    if (draggedId && pointer) {
      const a = state.ants.find((item) => item.id === draggedId);
      if (a) {
        c.save();
        c.globalAlpha = 0.25;
        oval(c, pointer.x, pointer.y + 14, 16, 4, "#000");
        c.restore();
        ant(c, pointer.x, pointer.y, 1, t * 16, "walk", roleAccessory[a.role], 1.15);
      }
    }
    frame = requestAnimationFrame(render);
  }

  return {
    setActive(on: boolean) {
      if (on === active) return;
      active = on;
      last = 0;
      cancelAnimationFrame(frame);
      if (on) frame = requestAnimationFrame(render);
    },
    /** 入口から潜る。カメラを地面の高さから社長室まで下ろす。 */
    enter() {
      resize();
      walkers.clear();
      const root = read().layout.byId.get(ROOT_ROOM)!;
      cam = { x: 0, y: -60 };
      z = 1.4;
      target = Math.min(1, Math.max(0.42, fitZoom() * 1.6));
      goal = bounds({ x: 0, y: root.y - 40 });
    },
    focusRoom(id: string) {
      const room = read().layout.byId.get(id);
      if (!room) return;
      target = clamp(Math.min(width / (base * (room.w + 260)), height / (base * (room.h + 300))), 0.5, 1.6);
      goal = bounds({ x: room.x, y: room.y - 10 });
    },
    overview() {
      const l = limits();
      target = fitZoom();
      // 道具欄の分だけ中心を下げ、いちばん深い部屋が道具欄の上に来るようにする。
      goal = bounds({ x: (l.minX + l.maxX) / 2, y: (l.minY + l.maxY) / 2 + 60 / (base * target) });
    },
    zoom(factor: number) {
      target = clamp(target * factor, Math.min(0.3, fitZoom()), 2.6);
    },
    destroy() {
      active = false;
      cancelAnimationFrame(frame);
      ro.disconnect();
      canvas.removeEventListener("wheel", wheel);
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointercancel", cancel);
      canvas.removeEventListener("pointerleave", leave);
    },
  };
}
