import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { Icon } from "../colony/Icon";
import { candy } from "../colony/art";
import { buildProjectColony, colonyCanEdit } from "../colony/liveModel";
import { nest, positionAt } from "../colony/model";
import type { Kind, Point, WorldEvent } from "../colony/model";
import { ROOT_ROOM, NURSERY_ROOM, STORAGE_ROOM, canDigBelow, canEditRoom, layoutNest, roleLabel, sweetSizeLabel } from "../colony/nestModel";
import { mountNest, type NestSceneState } from "../colony/nestScene";
import { mountScene } from "../colony/scene";
import { formatDisplayText } from "../lib/displayText";
import { isTerminalTaskStatus } from "../lib/taskStatus";
import type { ActiveTimer, ColonyRole, MemberView, Project, Task, User } from "../types";
import { AntCard, RoomCard, StoreCard, lastOf } from "./ColonyNestCards";
import "../colony/style.css";
import "../colony/nest.css";

export interface ColonyTaskDraft {
  name: string;
  assignee: string;
  parentId: string | null;
  minutes: number;
  due: string;
  memo: string;
}

interface Props {
  tasks: Task[];
  users: User[];
  members: MemberView[];
  project: Project | undefined;
  user: User | undefined;
  timer: ActiveTimer | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onOpenDetail: (id: string) => void;
  onOpenSplit: (id: string) => void;
  onOpenToday: (id: string) => Promise<boolean>;
  onComplete: (id: string) => Promise<boolean>;
  onReopen: (id: string) => Promise<boolean>;
  onSaveMemo: (id: string, memo: string) => Promise<boolean>;
  onSaveEstimate: (id: string, minutes: number) => Promise<boolean>;
  onSaveDue: (id: string, date: string) => Promise<boolean>;
  onCreate: (draft: ColonyTaskDraft) => Promise<boolean>;
  onRefresh: () => void;
}

const statusName: Record<string, string> = {
  Completion: "完了",
  Abort: "中止",
  NotStart: "未着手",
  DuringStart: "進行中",
};
const sizeKinds: Kind[] = ["crumb", "sugar", "bonbon", "chocolate"];
const sizeMinutes = [60, 240, 480, 960];
const sizeNeed: ColonyRole[] = ["member", "chief", "manager", "president"];

function dateAfter(days: number) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function thisMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function SweetIcon({ kind, size = 46 }: { kind: Kind; size?: number }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    if (!canvas) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = size * dpr;
    canvas.height = size * dpr;
    const c = canvas.getContext("2d")!;
    c.setTransform(dpr, 0, 0, dpr, 0, 0);
    c.clearRect(0, 0, size, size);
    const scale = { crumb: 1.25, sugar: 0.95, bonbon: 0.42, chocolate: 0.3, cookie: 0.4, lollipop: 0.3 }[kind];
    c.translate(size / 2, size * 0.66);
    c.scale((scale * size) / 46, (scale * size) / 46);
    candy(c, kind, 0, true);
  }, [kind, size]);
  return <canvas ref={ref} style={{ width: size, height: size }} aria-hidden="true" />;
}

type NestPanel = { kind: "ant"; id: string } | { kind: "room"; id: string } | null;
type Overlay =
  | { kind: "drop"; area: string; size: number; screen: Point }
  | { kind: "chalk"; world: Point; screen: Point }
  | { kind: "erase"; policy: string; screen: Point }
  | { kind: "area" }
  | { kind: "assign"; owner: string; food: string; options: Task[] }
  | { kind: "dig"; parent: string }
  | null;

export function ColonyView(props: Props) {
  const { tasks, users, members, project, user, timer, selectedId } = props;
  const queryClient = useQueryClient();
  const [showClosed, setShowClosed] = useState(false);
  const [panel, setPanel] = useState<"list" | "task" | null>(selectedId ? "task" : null);
  const [search, setSearch] = useState("");
  const [zoom, setZoom] = useState(100);
  const [pending, setPending] = useState(false);
  const [delivery, setDelivery] = useState<WorldEvent | null>(null);
  const [mode, setMode] = useState<"outside" | "diving" | "inside" | "surfacing">("outside");
  const [focusRoom, setFocusRoom] = useState<string | null>(null);
  const [nestPanel, setNestPanel] = useState<NestPanel>(null);
  const [tool, setTool] = useState<"chalk" | "dig" | null>(null);
  const [overlay, setOverlay] = useState<Overlay>(null);
  const [carry, setCarry] = useState<{ size: number; x: number; y: number } | null>(null);
  const [toast, setToast] = useState<{ text: string; error?: boolean } | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const nestCanvas = useRef<HTMLCanvasElement>(null);
  const fieldRef = useRef<HTMLDivElement>(null);
  const layer = useRef<HTMLDivElement>(null);
  const scene = useRef<ReturnType<typeof mountScene>>();
  const nestScene = useRef<ReturnType<typeof mountNest>>();
  const visibleTasks = useMemo(() => tasks.filter((task) => !task.IQuavisSyncExcluded), [tasks]);
  const selected = visibleTasks.find((task) => task.Id === selectedId);
  const actor = user?.Id ?? "";
  const orgQuery = useQuery({
    queryKey: ["colony-org", actor],
    queryFn: () => api.colonyOrg(actor),
    enabled: !!actor,
  });
  const org = orgQuery.data;
  const month = thisMonth();
  const contributionsQuery = useQuery({
    queryKey: ["colony-contributions", actor, month],
    queryFn: () => api.colonyContributions(actor, month),
    enabled: !!actor && mode !== "outside",
  });
  const contributions = contributionsQuery.data;
  const model = useMemo(
    () => buildProjectColony(tasks, users, members, actor, timer, showClosed),
    [tasks, users, members, actor, timer, showClosed],
  );
  const { world, positions, parentIds, foodOf } = model;
  const displayWorld = useMemo(
    () => ({
      ...world,
      events: delivery ? [delivery] : [],
      foods: delivery ? world.foods.filter((f) => f.id !== delivery.id) : world.foods,
    }),
    [world, delivery],
  );
  const activeUsers = useMemo(() => users.filter((u) => u.IsActive), [users]);
  const emblems = useMemo(() => {
    const map = new Map<string, string[]>();
    for (const team of org?.Teams ?? []) for (const id of team.MemberIds) map.set(id, [...(map.get(id) ?? []), team.Emblem]);
    return map;
  }, [org]);
  const idle = useMemo(() => {
    const busy = new Set((world.project?.crew ?? []).filter((a) => a.mode !== "carry").map((a) => a.owner));
    return activeUsers.filter((u) => !busy.has(u.Id)).map((u) => u.Id);
  }, [world, activeUsers]);
  const roles = org?.Roles;
  const viewerRole: ColonyRole = org?.ViewerRole ?? (user?.IsTopManager ? "president" : "member");
  const permissions = org?.Permissions;
  const layout = useMemo(
    () =>
      layoutNest(
        org?.Teams ?? [],
        activeUsers.map((u) => u.Id),
        activeUsers.filter((u) => org?.Roles[u.Id] === "president").map((u) => u.Id),
      ),
    [org, activeUsers],
  );
  const nestState = useMemo<NestSceneState>(() => {
    const roomOutput = new Map<string, number>();
    for (const team of contributions?.Teams ?? []) roomOutput.set(team.TeamId, lastOf(team.Months)?.OutputMinutes ?? 0);
    return {
      layout,
      ants: activeUsers.map((u) => ({ id: u.Id, name: u.Name, role: org?.Roles[u.Id] ?? "member" })),
      viewer: actor,
      focus: focusRoom,
      selected: nestPanel?.kind === "ant" ? nestPanel.id : null,
      accept:
        tool === "dig" && permissions
          ? new Set(layout.rooms.filter((r) => canDigBelow(permissions, r.id)).map((r) => r.id))
          : null,
      roomOutput,
      companyOutput: (contributions?.Company ?? []).reduce((s, m) => s + m.OutputMinutes, 0),
      teachingMinutes: lastOf(contributions?.Company)?.TeachingMinutes ?? 0,
    };
  }, [layout, activeUsers, org, actor, focusRoom, nestPanel, tool, permissions, contributions]);
  const nestStateRef = useRef(nestState);
  nestStateRef.current = nestState;
  const sceneState = useRef({
    world: displayWorld,
    selected: selectedId,
    actor,
    tool: false,
    draft: [] as Point[],
    roles,
    emblems,
    policies: org?.Policies,
    idle,
  });
  sceneState.current = {
    ...sceneState.current,
    selected: selectedId,
    actor,
    world: displayWorld,
    tool: tool === "chalk",
    roles,
    emblems,
    policies: org?.Policies,
    idle,
  };
  const handlers = useRef<{
    pick: (p: Point, id: string | null) => void;
    enterNest: () => void;
    canDragAnt: (owner: string) => boolean;
    dropAnt: (owner: string, food: string) => void;
    nestRoom: (id: string | null) => void;
    nestAnt: (id: string) => void;
    nestDrop: (antId: string, from: string | null, to: string | null) => void;
    nestCanDrop: (antId: string, room: string) => boolean;
    exitNest: () => void;
  }>();

  function say(text: string, error = false) {
    setToast({ text, error });
  }
  useEffect(() => {
    if (!toast) return;
    const timeout = window.setTimeout(() => setToast(null), toast.error ? 5200 : 2600);
    return () => window.clearTimeout(timeout);
  }, [toast]);
  async function act(run: () => Promise<unknown>, done: string, refreshTasks = false) {
    if (pending) return false;
    setPending(true);
    try {
      await run();
      say(done);
      await queryClient.invalidateQueries({ queryKey: ["colony-org"] });
      void queryClient.invalidateQueries({ queryKey: ["colony-contributions"] });
      if (refreshTasks) props.onRefresh();
      return true;
    } catch (error) {
      say(error instanceof Error ? error.message : String(error), true);
      return false;
    } finally {
      setPending(false);
    }
  }
  const nameOf = (id: string) => users.find((u) => u.Id === id)?.Name ?? "（不明）";
  /** 地面に浮かべる入力欄が画面の外へはみ出さないようにする。 */
  function keepInside(p: Point, h: number): Point {
    const rect = canvas.current?.getBoundingClientRect();
    if (!rect) return p;
    const half = 145;
    return {
      x: Math.max(half + 8, Math.min(rect.width - half - 8, p.x)),
      y: Math.max(8, Math.min(rect.height - h - 24, p.y)),
    };
  }

  handlers.current = {
    pick(p, id) {
      if (tool === "chalk") {
        const policy = scene.current?.policyAt(p);
        const screen = scene.current?.screenAt(p) ?? { x: 0, y: 0 };
        setOverlay(
          policy
            ? { kind: "erase", policy, screen: keepInside(screen, 90) }
            : { kind: "chalk", world: p, screen: keepInside(screen, 150) },
        );
        return;
      }
      props.onSelect(id);
      setPanel(id ? "task" : null);
    },
    enterNest() {
      if (mode !== "outside") return;
      setPanel(null);
      setOverlay(null);
      setTool(null);
      setMode("diving");
      scene.current?.focus({ x: nest.x, y: nest.y });
      scene.current?.zoom(2.4);
      window.setTimeout(() => {
        nestScene.current?.enter();
        nestScene.current?.setActive(true);
        scene.current?.setActive(false);
        setMode("inside");
      }, 520);
    },
    canDragAnt(owner) {
      return !!user?.IsActive && (!!permissions?.AssignOthers || owner === actor);
    },
    dropAnt(owner, food) {
      const options = visibleTasks.filter(
        (task) =>
          foodOf.get(task.Id) === food &&
          !parentIds.has(task.Id) &&
          !isTerminalTaskStatus(task.Status) &&
          task.AssigneeUserId !== owner &&
          (permissions?.AssignOthers || task.Status === "NotStart"),
      );
      if (!options.length) {
        say(`${nameOf(owner)}に割り振れる残りのかけらがありません`, true);
        return;
      }
      if (options.length === 1) {
        void assign(owner, options[0]);
        return;
      }
      setOverlay({ kind: "assign", owner, food, options });
    },
    nestRoom(id) {
      if (tool === "dig") {
        if (id && permissions && canDigBelow(permissions, id)) setOverlay({ kind: "dig", parent: id });
        else if (id) say("ここには掘れません。掘れるのは社長と、その部屋を受け持つ課長です", true);
        return;
      }
      setFocusRoom(id);
      setNestPanel(id ? { kind: "room", id } : null);
      if (id) nestScene.current?.focusRoom(id);
    },
    nestAnt(id) {
      setNestPanel({ kind: "ant", id });
    },
    nestDrop(antId, from, to) {
      if (!permissions) return;
      if (to) {
        const room = layout.byId.get(to);
        if (!room) return;
        if (room.members.includes(antId)) return;
        if (!canEditRoom(permissions, to)) {
          say(`${room.name}の所属を決められるのは、社長と、この部屋を受け持つ課長です`, true);
          return;
        }
        void act(() => api.colonyJoin(actor, to, antId), `${nameOf(antId)}を${room.name}に所属させました`);
        return;
      }
      if (!from) return;
      const room = layout.byId.get(from);
      if (!room || !room.members.includes(antId)) return;
      if (!canEditRoom(permissions, from)) {
        say(`${room.name}から外せるのは、社長と、この部屋を受け持つ課長です`, true);
        return;
      }
      void act(() => api.colonyLeave(actor, from, antId), `${nameOf(antId)}を${room.name}から外しました`);
    },
    nestCanDrop(antId, room) {
      const target = layout.byId.get(room);
      return !!permissions && !!target && !target.members.includes(antId) && canEditRoom(permissions, room);
    },
    exitNest() {
      if (mode !== "inside") return;
      setNestPanel(null);
      setFocusRoom(null);
      setOverlay(null);
      setTool(null);
      setMode("surfacing");
      scene.current?.setActive(true);
      scene.current?.leaveNest();
      window.setTimeout(() => {
        nestScene.current?.setActive(false);
        setMode("outside");
      }, 420);
    },
  };

  async function assign(owner: string, task: Task) {
    setOverlay(null);
    await act(
      () => api.colonyAssign(actor, task.Id, owner, task.Revision),
      `「${formatDisplayText(task.Name)}」を${nameOf(owner)}に割り振りました`,
      true,
    );
  }

  useEffect(() => {
    const instance = mountScene(canvas.current!, layer.current!, () => sceneState.current, {
      pick: (point, id) => handlers.current?.pick(point, id),
      zoom: setZoom,
      enterNest: () => handlers.current?.enterNest(),
      canDragAnt: (owner) => handlers.current?.canDragAnt(owner) ?? false,
      dropAnt: (owner, food) => handlers.current?.dropAnt(owner, food),
    });
    scene.current = instance;
    instance.viewpoint(0.27);
    const inside = mountNest(nestCanvas.current!, () => nestStateRef.current, {
      room: (id) => handlers.current?.nestRoom(id),
      ant: (id) => handlers.current?.nestAnt(id),
      drop: (antId, from, to) => handlers.current?.nestDrop(antId, from, to),
      canDrop: (antId, room) => handlers.current?.nestCanDrop(antId, room) ?? false,
      exit: () => handlers.current?.exitNest(),
      zoom: () => undefined,
    });
    nestScene.current = inside;
    return () => {
      instance.destroy();
      inside.destroy();
    };
  }, []);
  useEffect(() => {
    if (!delivery) return;
    const timeout = window.setTimeout(() => setDelivery(null), 1700);
    return () => window.clearTimeout(timeout);
  }, [delivery]);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setPanel(null);
      setOverlay(null);
      setTool(null);
      setNestPanel(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  // 手持ちのお菓子をつかんで、地面へ落とす。
  useEffect(() => {
    if (!carry) return;
    const moveCarry = (e: PointerEvent) => setCarry((c) => (c ? { ...c, x: e.clientX, y: e.clientY } : c));
    const release = (e: PointerEvent) => {
      const size = carry.size;
      setCarry(null);
      const rect = canvas.current?.getBoundingClientRect();
      if (!rect || e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) return;
      const screen = { x: e.clientX - rect.left, y: e.clientY - rect.top };
      const point = scene.current?.worldAt(screen);
      const area = point ? scene.current?.areaAt(point) : null;
      if (!area) {
        say("エリア（枠で囲まれた区画）の中に落としてください", true);
        return;
      }
      setOverlay({ kind: "drop", area, size, screen: keepInside(screen, 300) });
    };
    window.addEventListener("pointermove", moveCarry);
    window.addEventListener("pointerup", release, { once: true });
    return () => {
      window.removeEventListener("pointermove", moveCarry);
      window.removeEventListener("pointerup", release);
    };
  }, [carry?.size]);

  function focus(task: Task) {
    props.onSelect(task.Id);
    setPanel("task");
    const position = positions.get(task.Id);
    if (position) scene.current?.focus(position);
  }
  function complete(task: Task) {
    // 完了は楽観更新。保存の往復を待たずに、かけらを巣へ運ぶ動きを始める（CLAUDE.md 5.7: ボタンを塞がない）。
    const food = world.foods.find((f) => f.id === (foodOf.get(task.Id) ?? task.Id));
    void props.onComplete(task.Id);
    if (food) {
      const point = positionAt(food, Date.now());
      scene.current?.frameDelivery(point);
      setDelivery({
        ...point,
        id: task.Id,
        type: "drop",
        at: Date.now(),
        kind: food.kind,
        piece: food.bits ? 2 : 0,
        actor,
      });
    }
    setPanel(null);
  }
  async function submit(action: () => Promise<boolean>) {
    if (pending) return false;
    setPending(true);
    try {
      return await action();
    } finally {
      setPending(false);
    }
  }
  const needle = search.trim().toLocaleLowerCase();
  const listed = visibleTasks.filter(
    (task) =>
      (showClosed || !isTerminalTaskStatus(task.Status)) &&
      [task.Name, task.AssigneeName ?? ""].some((value) => value.toLocaleLowerCase().includes(needle)),
  );
  const parent = tasks.find((task) => task.Id === selected?.ParentTaskId);
  const inside = mode === "inside" || mode === "diving";
  const maxSize = permissions?.MaxSweetSize ?? (user?.IsTopManager ? 3 : 0);
  const viewLabel = zoom < 76 ? "社長目線" : zoom < 134 ? "課長目線" : "現場目線";
  const cardBase = org
    ? {
        org,
        layout,
        users,
        viewer: actor,
        contributions,
        busy: pending,
        onClose: () => {
          setNestPanel(null);
          setFocusRoom(null);
        },
        onSelectAnt: (id: string) => setNestPanel({ kind: "ant", id }),
      }
    : null;

  return (
    <section className={"colony colony-integrated mode-" + mode} aria-label="巣">
      <div className="field" ref={fieldRef}>
        <canvas
          ref={canvas}
          className="outside-canvas"
          tabIndex={0}
          aria-label="巣の外。ドラッグで移動、ホイールで拡大縮小、巣の入口をクリックすると巣の中へ"
        />
        <div ref={layer} className="field-annotations" aria-label="タスクと進捗の情報" />
        <canvas
          ref={nestCanvas}
          className="nest-canvas"
          tabIndex={-1}
          aria-label="巣の中。部屋をクリックするとメンバーが集まる。アリをつかんで部屋へ入れる"
        />
        <div className="dive-curtain" aria-hidden="true" />

        {!inside && (
          <>
            <div className="top-left">
              <button type="button" className="summary" onClick={() => setPanel(panel === "list" ? null : "list")}>
                <Icon name="list" /> タスク一覧 <span>{listed.length}</span>
              </button>
            </div>
            <div className="top-right">
              <button type="button" className="tool-btn" onClick={props.onRefresh} aria-label="タスクを再取得">
                更新
              </button>
            </div>
            <div className="bottom-left">
              <span className="field-stat">
                未完了{" "}
                <b>{visibleTasks.filter((t) => !isTerminalTaskStatus(t.Status) && !parentIds.has(t.Id)).length}</b>
                <em />
                完了 <b>{world.delivered}</b>
              </span>
            </div>
            <div className="hotbar" role="toolbar" aria-label="手持ちと道具">
              <span className="hotbar-label">手持ち</span>
              {sizeKinds.map((kind, size) => {
                const locked = size > maxSize || !user?.IsActive;
                return (
                  <button
                    key={kind}
                    type="button"
                    className={"slot" + (locked ? " locked" : "")}
                    disabled={locked}
                    title={
                      locked
                        ? `${sweetSizeLabel[size]}を置けるのは${roleLabel[sizeNeed[size]]}以上です`
                        : `${sweetSizeLabel[size]}をつかんでエリアへ落とす`
                    }
                    onPointerDown={(e: ReactPointerEvent) => {
                      if (locked) return;
                      e.preventDefault();
                      setOverlay(null);
                      setCarry({ size, x: e.clientX, y: e.clientY });
                    }}
                  >
                    <SweetIcon kind={kind} size={40} />
                    <small>{sweetSizeLabel[size]}</small>
                    {locked && <i aria-hidden="true">🔒</i>}
                  </button>
                );
              })}
              <span className="hotbar-sep" />
              <button
                type="button"
                className={"slot tool" + (tool === "chalk" ? " on" : "") + (permissions?.WritePolicy ? "" : " locked")}
                disabled={!permissions?.WritePolicy}
                title={permissions?.WritePolicy ? "チョークで方針を書く（地面をクリック）" : "チョークを使えるのは社長だけです"}
                onClick={() => setTool(tool === "chalk" ? null : "chalk")}
              >
                <span className="chalk-stick" aria-hidden="true" />
                <small>チョーク</small>
                {!permissions?.WritePolicy && <i aria-hidden="true">🔒</i>}
              </button>
              <button
                type="button"
                className={"slot tool" + (permissions?.CreateArea ? "" : " locked")}
                disabled={!permissions?.CreateArea}
                title={permissions?.CreateArea ? "新しいエリア（業務の区分け）を作る" : "エリアを作れるのは課長以上です"}
                onClick={() => setOverlay({ kind: "area" })}
              >
                <Icon name="zone" size={26} />
                <small>エリア</small>
                {!permissions?.CreateArea && <i aria-hidden="true">🔒</i>}
              </button>
              <button type="button" className="slot tool nest-go" onClick={() => handlers.current?.enterNest()}>
                <span className="hole" aria-hidden="true" />
                <small>巣に入る</small>
              </button>
            </div>
            <div className="map-nav">
              <button
                type="button"
                className="tool-btn"
                title="全体表示"
                aria-label="全体表示"
                onClick={() => scene.current?.viewpoint(0.27)}
              >
                <Icon name="target" />
              </button>
              <label className="viewpoint">
                <b>{viewLabel}</b>
                <Icon name="down" size={12} />
                <select
                  aria-label="視点と倍率"
                  value=""
                  onChange={(e) => scene.current?.viewpoint(Number(e.currentTarget.value))}
                >
                  <option value="" disabled>
                    視点を選択
                  </option>
                  <option value=".27">社長目線 · 全体</option>
                  <option value="1">課長目線 · 100%</option>
                  <option value="2">現場目線 · 200%</option>
                </select>
              </label>
              <button type="button" className="tool-btn" aria-label="縮小" onClick={() => scene.current?.zoom(1 / 1.3)}>
                <Icon name="minus" />
              </button>
              <output aria-label="ズーム倍率">{zoom}%</output>
              <button type="button" className="tool-btn" aria-label="拡大" onClick={() => scene.current?.zoom(1.3)}>
                <Icon name="plus" />
              </button>
            </div>
            {!panel && !overlay && (
              <div className="map-hint">
                {tool === "chalk"
                  ? "地面をクリックして方針を書く · 書いた字をクリックすると消せる"
                  : "お菓子をクリックして編集 · アリをつかんでお菓子へ運ぶと割り振り · 巣の入口をクリックで巣の中へ"}
              </div>
            )}
            {panel && (
              <aside className={"panel panel-" + panel} aria-label={panel === "list" ? "タスク一覧" : "タスク編集"}>
                <div className="panel-head">
                  <h2>{panel === "list" ? "タスク一覧" : formatDisplayText(selected?.Name) || "タスク"}</h2>
                  <button type="button" className="tool-btn" aria-label="閉じる" onClick={() => setPanel(null)}>
                    <Icon name="close" />
                  </button>
                </div>
                {panel === "list" && (
                  <div className="food-list">
                    <label>
                      検索
                      <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="タスク名または担当者" />
                    </label>
                    <label className="colony-check">
                      <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> 完了・中止を表示
                    </label>
                    {listed.map((task) => (
                      <button type="button" key={task.Id} onClick={() => focus(task)}>
                        <span>
                          <b>{formatDisplayText(task.Name)}</b>
                          <small>
                            {task.AssigneeName || "担当未定"} · {statusName[task.Status] ?? task.Status}
                            {parentIds.has(task.Id) ? " · 親タスク" : ""}
                          </small>
                        </span>
                        <Icon name="arrow" size={14} />
                      </button>
                    ))}
                    {!listed.length && <p>該当するタスクがありません。</p>}
                  </div>
                )}
                {panel === "task" && selected && (
                  <TaskPanel
                    key={selected.Id}
                    task={selected}
                    parent={parent}
                    leaf={!parentIds.has(selected.Id)}
                    canEdit={colonyCanEdit(selected, user)}
                    pending={pending}
                    maxDue={parent?.EndDate || project?.EndDate}
                    onMemo={(memo) => submit(() => props.onSaveMemo(selected.Id, memo))}
                    onEstimate={(minutes) => submit(() => props.onSaveEstimate(selected.Id, minutes))}
                    onDue={(date) => submit(() => props.onSaveDue(selected.Id, date))}
                    onDetail={() => props.onOpenDetail(selected.Id)}
                    onSplit={() => props.onOpenSplit(selected.Id)}
                    onComplete={() => void complete(selected)}
                    onReopen={() => void submit(() => props.onReopen(selected.Id))}
                    onToday={
                      selected.AssigneeUserId === actor && user?.IsActive
                        ? () => void submit(() => props.onOpenToday(selected.Id))
                        : undefined
                    }
                  />
                )}
              </aside>
            )}
          </>
        )}

        {inside && (
          <>
            <div className="top-left nest-title">
              <span className="nest-badge">巣の中</span>
              <small>{org ? `${roleLabel[viewerRole]}として見ています` : "読み込み中…"}</small>
            </div>
            <div className="hotbar nest-hotbar" role="toolbar" aria-label="巣の中の道具">
              <button type="button" className="slot tool" onClick={() => handlers.current?.exitNest()}>
                <Icon name="home" size={24} />
                <small>地上へ</small>
              </button>
              <button
                type="button"
                className={"slot tool" + (tool === "dig" ? " on" : "") + (permissions && (permissions.EditAllTeams || permissions.EditableTeamIds.length) ? "" : " locked")}
                disabled={!permissions || !(permissions.EditAllTeams || permissions.EditableTeamIds.length)}
                title="部屋を掘る（掘り始める部屋をクリック）。掘れるのは社長と、部屋を受け持つ課長です"
                onClick={() => setTool(tool === "dig" ? null : "dig")}
              >
                <span className="pick" aria-hidden="true">⛏</span>
                <small>部屋を掘る</small>
              </button>
              <button type="button" className="slot tool" onClick={() => nestScene.current?.overview()}>
                <Icon name="layers" size={24} />
                <small>全体</small>
              </button>
              <button
                type="button"
                className="slot tool"
                onClick={() => {
                  setFocusRoom(STORAGE_ROOM);
                  setNestPanel({ kind: "room", id: STORAGE_ROOM });
                  nestScene.current?.focusRoom(STORAGE_ROOM);
                }}
              >
                <span className="pile-icon" aria-hidden="true" />
                <small>食料庫</small>
              </button>
            </div>
            {!nestPanel && !overlay && (
              <div className="map-hint nest-hint">
                {tool === "dig"
                  ? "掘り始める部屋をクリック（光っている部屋に掘れます）"
                  : "部屋をクリックするとメンバーが集まる · アリをつかんで部屋へ入れる / 外へ出すと所属を外す · 引いていくと地上へ"}
              </div>
            )}
            {cardBase && nestPanel?.kind === "ant" && (
              <AntCard
                {...cardBase}
                userId={nestPanel.id}
                focusRoom={focusRoom}
                onRole={(role) =>
                  void act(() => api.colonySetRole(actor, nestPanel.id, role), `${nameOf(nestPanel.id)}を${roleLabel[role]}にしました`)
                }
                onLeader={(roomId) =>
                  void act(
                    () => api.colonyUpdateRoom(actor, roomId, { leader_user_id: nestPanel.id }),
                    `${nameOf(nestPanel.id)}を${layout.byId.get(roomId)?.name ?? "部屋"}の主にしました`,
                  )
                }
                onLeave={(roomId) =>
                  void act(
                    () => api.colonyLeave(actor, roomId, nestPanel.id),
                    `${nameOf(nestPanel.id)}を${layout.byId.get(roomId)?.name ?? "部屋"}から外しました`,
                  )
                }
              />
            )}
            {cardBase && nestPanel?.kind === "room" && (nestPanel.id === STORAGE_ROOM || nestPanel.id === NURSERY_ROOM) && (
              <StoreCard {...cardBase} kind={nestPanel.id === STORAGE_ROOM ? "storage" : "nursery"} />
            )}
            {cardBase && nestPanel?.kind === "room" && nestPanel.id !== STORAGE_ROOM && nestPanel.id !== NURSERY_ROOM && (
              <RoomCard
                {...cardBase}
                roomId={nestPanel.id}
                onRename={(name) => void act(() => api.colonyUpdateRoom(actor, nestPanel.id, { name }), "部屋の名前を変えました")}
                onEmblem={(emblemName) =>
                  void act(() => api.colonyUpdateRoom(actor, nestPanel.id, { emblem: emblemName }), "紋章を変えました")
                }
                onDig={(parentId) => setOverlay({ kind: "dig", parent: parentId })}
                onFill={() =>
                  void act(() => api.colonyFillRoom(actor, nestPanel.id), "部屋を埋めました").then((ok) => {
                    if (ok) {
                      setNestPanel(null);
                      setFocusRoom(null);
                    }
                  })
                }
              />
            )}
          </>
        )}

        {overlay?.kind === "drop" && (
          <DropForm
            screen={overlay.screen}
            size={overlay.size}
            areaName={formatDisplayText(tasks.find((t) => t.Id === overlay.area)?.Name) || "エリア"}
            busy={pending}
            onCancel={() => setOverlay(null)}
            onSave={(name, minutes, due) =>
              void act(
                () =>
                  api.colonyDropSweet({
                    user_id: actor,
                    area_task_id: overlay.area,
                    name,
                    size: overlay.size,
                    assigned_manhour: minutes,
                    end_date: due,
                  }),
                `${sweetSizeLabel[overlay.size]}「${name}」を置きました`,
                true,
              ).then((ok) => ok && setOverlay(null))
            }
          />
        )}
        {overlay?.kind === "chalk" && (
          <FloatingForm
            screen={overlay.screen}
            label="チョークで書く方針"
            placeholder="例: お客様の時間を大切に"
            busy={pending}
            onCancel={() => setOverlay(null)}
            onSave={(text) =>
              void act(
                () =>
                  api.colonyWritePolicy(actor, {
                    kind: "text",
                    text,
                    x: overlay.world.x,
                    y: overlay.world.y,
                    size: 1.6,
                    points: [],
                  }),
                "地面に方針を書きました",
              ).then((ok) => ok && setOverlay(null))
            }
          />
        )}
        {overlay?.kind === "erase" && (
          <div className="floating-form" style={{ left: overlay.screen.x, top: overlay.screen.y } as CSSProperties}>
            <p>この方針を消しますか？</p>
            <div className="actions">
              <button
                type="button"
                className="tool-btn danger"
                disabled={pending}
                onClick={() =>
                  void act(() => api.colonyErasePolicy(actor, overlay.policy), "方針を消しました").then(
                    (ok) => ok && setOverlay(null),
                  )
                }
              >
                消す
              </button>
              <button type="button" className="tool-btn" onClick={() => setOverlay(null)}>
                やめる
              </button>
            </div>
          </div>
        )}
        {overlay?.kind === "area" && (
          <AreaForm
            busy={pending}
            onCancel={() => setOverlay(null)}
            onSave={(name, due) =>
              void act(() => api.colonyCreateArea(actor, name, due), `エリア「${name}」を作りました`, true).then(
                (ok) => ok && setOverlay(null),
              )
            }
          />
        )}
        {overlay?.kind === "assign" && (
          <div className="center-card" role="dialog" aria-label="割り振るかけらを選ぶ">
            <h3>{nameOf(overlay.owner)}にどのかけらを任せますか？</h3>
            <ul>
              {overlay.options.slice(0, 30).map((task) => (
                <li key={task.Id}>
                  <button type="button" disabled={pending} onClick={() => void assign(overlay.owner, task)}>
                    <b>{formatDisplayText(task.Name)}</b>
                    <small>
                      いま: {task.AssigneeName || "担当未定"} · {statusName[task.Status] ?? task.Status}
                    </small>
                  </button>
                </li>
              ))}
            </ul>
            <button type="button" className="tool-btn" onClick={() => setOverlay(null)}>
              やめる
            </button>
          </div>
        )}
        {overlay?.kind === "dig" && (
          <DigForm
            parentName={layout.byId.get(overlay.parent)?.name ?? "部屋"}
            busy={pending}
            onCancel={() => setOverlay(null)}
            onSave={(name) =>
              void act(
                () => api.colonyDigRoom(actor, overlay.parent === ROOT_ROOM ? null : overlay.parent, name),
                `「${name}」の部屋を掘りました`,
              ).then((ok) => {
                if (ok) {
                  setOverlay(null);
                  setTool(null);
                }
              })
            }
          />
        )}
        {carry && (
          <div className="carry-ghost" style={{ left: carry.x, top: carry.y } as CSSProperties} aria-hidden="true">
            <SweetIcon kind={sizeKinds[carry.size]} size={64} />
          </div>
        )}
        {toast && (
          <div className={"colony-toast" + (toast.error ? " error" : "")} role="status">
            {toast.text}
          </div>
        )}
      </div>
    </section>
  );
}

function FloatingForm({
  screen,
  label,
  placeholder,
  busy,
  onSave,
  onCancel,
}: {
  screen: Point;
  label: string;
  placeholder: string;
  busy: boolean;
  onSave: (text: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState("");
  return (
    <form
      className="floating-form"
      style={{ left: screen.x, top: screen.y } as CSSProperties}
      onSubmit={(e) => {
        e.preventDefault();
        if (text.trim()) onSave(text.trim());
      }}
    >
      <label>
        {label}
        <input autoFocus value={text} placeholder={placeholder} onChange={(e) => setText(e.target.value)} />
      </label>
      <div className="actions">
        <button className="primary" disabled={busy || !text.trim()}>
          書く
        </button>
        <button type="button" className="tool-btn" onClick={onCancel}>
          やめる
        </button>
      </div>
    </form>
  );
}

function DropForm({
  screen,
  size,
  areaName,
  busy,
  onSave,
  onCancel,
}: {
  screen: Point;
  size: number;
  areaName: string;
  busy: boolean;
  onSave: (name: string, minutes: number, due: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [hours, setHours] = useState(String(sizeMinutes[size] / 60));
  const [due, setDue] = useState(dateAfter(size >= 2 ? 21 : 7));
  return (
    <form
      className="floating-form drop-form"
      style={{ left: screen.x, top: screen.y } as CSSProperties}
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) onSave(name.trim(), Math.max(30, Math.round(Number(hours) * 60)), due);
      }}
    >
      <div className="dropped" aria-hidden="true">
        <SweetIcon kind={sizeKinds[size]} size={48} />
      </div>
      <p>
        <b>{areaName}</b> に{sweetSizeLabel[size]}を置く
      </p>
      <label>
        仕事の名前
        <input autoFocus required value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <div className="form-row">
        <label>
          見積MH
          <input type="number" min="0.5" step="0.5" value={hours} onChange={(e) => setHours(e.target.value)} />
        </label>
        <label>
          期日
          <input type="date" required value={due} onChange={(e) => setDue(e.target.value)} />
        </label>
      </div>
      <div className="actions">
        <button className="primary" disabled={busy || !name.trim()}>
          置く
        </button>
        <button type="button" className="tool-btn" onClick={onCancel}>
          やめる
        </button>
      </div>
    </form>
  );
}

function AreaForm({
  busy,
  onSave,
  onCancel,
}: {
  busy: boolean;
  onSave: (name: string, due: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [due, setDue] = useState(dateAfter(90));
  return (
    <form
      className="center-card"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) onSave(name.trim(), due);
      }}
    >
      <h3>新しいエリアを作る</h3>
      <p className="nest-note">エリアは業務のざっくりとした区分けです。地面に枠で囲まれた区画として現れます。</p>
      <label>
        エリアの名前
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label>
        期日
        <input type="date" required value={due} onChange={(e) => setDue(e.target.value)} />
      </label>
      <div className="actions">
        <button className="primary" disabled={busy || !name.trim()}>
          作る
        </button>
        <button type="button" className="tool-btn" onClick={onCancel}>
          やめる
        </button>
      </div>
    </form>
  );
}

function DigForm({
  parentName,
  busy,
  onSave,
  onCancel,
}: {
  parentName: string;
  busy: boolean;
  onSave: (name: string) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  return (
    <form
      className="center-card"
      onSubmit={(e) => {
        e.preventDefault();
        if (name.trim()) onSave(name.trim());
      }}
    >
      <h3>⛏ {parentName}の下に部屋を掘る</h3>
      <label>
        新しい部屋（チーム）の名前
        <input autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="例: 企画二係" />
      </label>
      <div className="actions">
        <button className="primary" disabled={busy || !name.trim()}>
          掘る
        </button>
        <button type="button" className="tool-btn" onClick={onCancel}>
          やめる
        </button>
      </div>
    </form>
  );
}

function TaskPanel({
  task,
  parent,
  leaf,
  canEdit,
  pending,
  maxDue,
  onMemo,
  onEstimate,
  onDue,
  onDetail,
  onSplit,
  onComplete,
  onReopen,
  onToday,
}: {
  task: Task;
  parent: Task | undefined;
  leaf: boolean;
  canEdit: boolean;
  pending: boolean;
  maxDue: string | undefined;
  onMemo: (memo: string) => Promise<boolean>;
  onEstimate: (minutes: number) => Promise<boolean>;
  onDue: (date: string) => Promise<boolean>;
  onDetail: () => void;
  onSplit: () => void;
  onComplete: () => void;
  onReopen: () => void;
  onToday: (() => void) | undefined;
}) {
  const [memo, setMemo] = useState(task.Memo ?? "");
  const [estimate, setEstimate] = useState(String(task.AssignedManHour / 60));
  const [due, setDue] = useState(task.EndDate);
  useEffect(() => setMemo(task.Memo ?? ""), [task.Memo]);
  useEffect(() => setEstimate(String(task.AssignedManHour / 60)), [task.AssignedManHour]);
  useEffect(() => setDue(task.EndDate), [task.EndDate]);
  const terminal = isTerminalTaskStatus(task.Status);
  return (
    <div className="task-body">
      <div className="task-meta">
        <span>{task.AssigneeName || "担当未定"}</span>
        <span>{statusName[task.Status] ?? task.Status}</span>
        <span>{Math.round(task.ProgressRate)}%</span>
      </div>
      <p className="requirement">
        {parent && <>{formatDisplayText(parent.Name)} 配下 · </>}期日 {task.EndDate} · 実績{" "}
        {(task.ActualManHour / 60).toLocaleString("ja-JP", { maximumFractionDigits: 2 })} MH
      </p>
      {canEdit && !terminal && (
        <div className="colony-edit-fields">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void onMemo(memo);
            }}
          >
            <label>
              メモ
              <textarea aria-label="メモ" rows={2} value={memo} onChange={(e) => setMemo(e.target.value)} />
            </label>
            <button className="tool-btn" disabled={pending || memo === (task.Memo ?? "")}>
              メモを保存
            </button>
          </form>
          <div className="form-row">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void onEstimate(Math.round(Number(estimate) * 60));
              }}
            >
              <label>
                見積MH
                <input
                  type="number"
                  min="0"
                  step="0.5"
                  required
                  value={estimate}
                  onChange={(e) => setEstimate(e.target.value)}
                />
              </label>
              <button className="tool-btn" disabled={pending || Number(estimate) * 60 === task.AssignedManHour}>
                見積を保存
              </button>
            </form>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void onDue(due);
              }}
            >
              <label>
                期日
                <input type="date" required max={maxDue || undefined} value={due} onChange={(e) => setDue(e.target.value)} />
              </label>
              <button className="tool-btn" disabled={pending || !due || due === task.EndDate}>
                期日を保存
              </button>
            </form>
          </div>
        </div>
      )}
      {(terminal || !canEdit) && task.Memo && <p className="requirement">{formatDisplayText(task.Memo)}</p>}
      <div className="actions">
        <button type="button" className="tool-btn" onClick={onDetail}>
          タスク詳細
        </button>
        {canEdit && !terminal && (
          <button type="button" className="tool-btn" disabled={pending} onClick={onSplit}>
            分割
          </button>
        )}
        {leaf && onToday && !terminal && (
          <button type="button" className="tool-btn" disabled={pending} onClick={onToday}>
            Todayで作業
          </button>
        )}
      </div>
      {canEdit && !terminal && (
        <button type="button" className="primary" disabled={pending} onClick={onComplete}>
          {leaf ? "完了して巣へ届ける" : "親タスクを完了"}
        </button>
      )}
      {canEdit && task.Status === "Completion" && (
        <button type="button" className="primary" disabled={pending} onClick={onReopen}>
          完了を解除
        </button>
      )}
    </div>
  );
}
