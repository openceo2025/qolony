import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Icon } from "../colony/Icon";
import { buildProjectColony, colonyCanEdit } from "../colony/liveModel";
import { positionAt } from "../colony/model";
import type { Point, WorldEvent } from "../colony/model";
import { mountScene } from "../colony/scene";
import { formatDisplayText } from "../lib/displayText";
import { isTerminalTaskStatus } from "../lib/taskStatus";
import type { ActiveTimer, MemberView, Project, Task, User } from "../types";
import "../colony/style.css";

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

export function ColonyView(props: Props) {
  const { tasks, users, members, project, user, timer, selectedId } = props;
  const [showClosed, setShowClosed] = useState(false);
  const [panel, setPanel] = useState<"list" | "task" | "create" | null>(selectedId ? "task" : null);
  const [search, setSearch] = useState("");
  const [zoom, setZoom] = useState(100);
  const [pending, setPending] = useState(false);
  const [delivery, setDelivery] = useState<WorldEvent | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const layer = useRef<HTMLDivElement>(null);
  const scene = useRef<ReturnType<typeof mountScene>>();
  const visibleTasks = useMemo(() => tasks.filter((task) => !task.IQuavisSyncExcluded), [tasks]);
  const selected = visibleTasks.find((task) => task.Id === selectedId);
  const actor = user?.Id ?? "";
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
  const sceneState = useRef({
    world: displayWorld,
    selected: selectedId,
    actor,
    tool: false,
    draft: [] as Point[],
  });
  sceneState.current = { ...sceneState.current, selected: selectedId, actor, world: displayWorld };
  const select = useRef<(id: string | null) => void>();
  select.current = (id) => {
    props.onSelect(id);
    setPanel(id ? "task" : null);
  };
  useEffect(() => {
    const instance = mountScene(canvas.current!, layer.current!, () => sceneState.current, {
      pick: (_point, id) => select.current?.(id),
      zoom: setZoom,
    });
    scene.current = instance;
    instance.viewpoint(0.27);
    return () => instance.destroy();
  }, []);
  useEffect(() => {
    if (!delivery) return;
    const timeout = window.setTimeout(() => setDelivery(null), 1700);
    return () => window.clearTimeout(timeout);
  }, [delivery]);
  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPanel(null);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, []);

  async function submit(action: () => Promise<boolean>) {
    if (pending) return false;
    setPending(true);
    try {
      return await action();
    } finally {
      setPending(false);
    }
  }
  function focus(task: Task) {
    select.current?.(task.Id);
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
  const createParents = visibleTasks.filter(
    (task) => colonyCanEdit(task, user) && !isTerminalTaskStatus(task.Status),
  );
  const canCreate = Boolean(user?.IsActive && (user.IsTopManager || createParents.length));
  const needle = search.trim().toLocaleLowerCase();
  const listed = visibleTasks.filter(
    (task) =>
      (showClosed || !isTerminalTaskStatus(task.Status)) &&
      [task.Name, task.AssigneeName ?? ""].some((value) => value.toLocaleLowerCase().includes(needle)),
  );
  const parent = tasks.find((task) => task.Id === selected?.ParentTaskId);

  return (
    <section className="colony colony-integrated" aria-label="巣の外">
      <div className="field">
        <canvas ref={canvas} tabIndex={0} aria-label="タスクの地図。ドラッグで移動、ホイールで拡大縮小" />
        <div ref={layer} className="field-annotations" aria-label="タスクと進捗の情報" />
        <div className="top-left">
          <button
            type="button"
            className="summary"
            onClick={() => setPanel(panel === "list" ? null : "list")}
          >
            <Icon name="list" /> タスク一覧 <span>{listed.length}</span>
          </button>
        </div>
        <div className="top-right">
          <button type="button" className="tool-btn" onClick={props.onRefresh} aria-label="タスクを再取得">
            更新
          </button>
          {canCreate && (
            <button type="button" className="place-btn" onClick={() => setPanel("create")}>
              <Icon name="plus" /> タスクを追加
            </button>
          )}
        </div>
        <div className="bottom-left">
          <span className="field-stat">
            未完了{" "}
            <b>
              {visibleTasks.filter((t) => !isTerminalTaskStatus(t.Status) && !parentIds.has(t.Id)).length}
            </b>
            <em />
            完了 <b>{world.delivered}</b>
          </span>
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
            <b>{zoom < 76 ? "全体" : zoom < 134 ? "エリア" : "詳細"}</b>
            <Icon name="down" size={12} />
            <select
              aria-label="視点と倍率"
              value=""
              onChange={(e) => scene.current?.viewpoint(Number(e.currentTarget.value))}
            >
              <option value="" disabled>
                視点を選択
              </option>
              <option value=".27">全体表示</option>
              <option value="1">エリア · 100%</option>
              <option value="2">詳細 · 200%</option>
            </select>
          </label>
          <button
            type="button"
            className="tool-btn"
            aria-label="縮小"
            onClick={() => scene.current?.zoom(1 / 1.3)}
          >
            <Icon name="minus" />
          </button>
          <output aria-label="ズーム倍率">{zoom}%</output>
          <button
            type="button"
            className="tool-btn"
            aria-label="拡大"
            onClick={() => scene.current?.zoom(1.3)}
          >
            <Icon name="plus" />
          </button>
        </div>
        {!panel && (
          <div className="map-hint">
            {visibleTasks.length
              ? "WBSのルートごとに表示 · お菓子をクリックして編集"
              : "表示できるタスクがありません"}
          </div>
        )}
        {panel && (
          <aside
            className={"panel panel-" + panel}
            aria-label={panel === "create" ? "タスクを追加" : panel === "list" ? "タスク一覧" : "タスク編集"}
          >
            <div className="panel-head">
              <h2>
                {panel === "create"
                  ? "タスクを追加"
                  : panel === "list"
                    ? "タスク一覧"
                    : formatDisplayText(selected?.Name) || "タスク"}
              </h2>
              <button type="button" className="tool-btn" aria-label="閉じる" onClick={() => setPanel(null)}>
                <Icon name="close" />
              </button>
            </div>
            {panel === "list" && (
              <div className="food-list">
                <label>
                  検索
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="タスク名または担当者"
                  />
                </label>
                <label className="colony-check">
                  <input
                    type="checkbox"
                    checked={showClosed}
                    onChange={(e) => setShowClosed(e.target.checked)}
                  />{" "}
                  完了・中止を表示
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
            {panel === "create" && canCreate && user && (
              <CreatePanel
                users={users}
                user={user}
                parents={createParents}
                project={project}
                initialParent={selectedId}
                pending={pending}
                onCreate={async (draft) => {
                  if (await submit(() => props.onCreate(draft))) setPanel(null);
                }}
              />
            )}
          </aside>
        )}
      </div>
    </section>
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
              <button
                className="tool-btn"
                disabled={pending || Number(estimate) * 60 === task.AssignedManHour}
              >
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
                <input
                  type="date"
                  required
                  max={maxDue || undefined}
                  value={due}
                  onChange={(e) => setDue(e.target.value)}
                />
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

function CreatePanel({
  users,
  user,
  parents,
  project,
  initialParent,
  pending,
  onCreate,
}: {
  users: User[];
  user: User;
  parents: Task[];
  project: Project | undefined;
  initialParent: string | null;
  pending: boolean;
  onCreate: (draft: ColonyTaskDraft) => Promise<void>;
}) {
  const [parentId, setParentId] = useState(
    parents.some((p) => p.Id === initialParent)
      ? (initialParent ?? "")
      : user.IsTopManager
        ? ""
        : (parents[0]?.Id ?? ""),
  );
  const maxDue = parents.find((parent) => parent.Id === parentId)?.EndDate || project?.EndDate;
  function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const name = String(data.get("name")).trim();
    if (!name) return;
    void onCreate({
      name,
      parentId: parentId || null,
      assignee: String(data.get("assignee")),
      minutes: Math.round(Number(data.get("estimate")) * 60),
      due: String(data.get("due")),
      memo: String(data.get("memo")),
    });
  }
  return (
    <form onSubmit={save}>
      <label>
        タスク名
        <input name="name" required autoFocus />
      </label>
      <label>
        親タスク
        <select name="parent" aria-label="親タスク" value={parentId} onChange={(event) => setParentId(event.target.value)}>
          {user.IsTopManager && <option value="">ルートタスク</option>}
          {parents.map((task) => (
            <option value={task.Id} key={task.Id}>
              {formatDisplayText(task.Name)}
            </option>
          ))}
        </select>
      </label>
      <label>
        担当者
        <select name="assignee" aria-label="担当者" defaultValue={user.Id}>
          {users
            .filter((u) => u.IsActive)
            .map((u) => (
              <option key={u.Id} value={u.Id}>
                {u.Name}
              </option>
            ))}
        </select>
      </label>
      <div className="form-row">
        <label>
          見積MH
          <input name="estimate" type="number" min=".5" step=".5" defaultValue="1" required />
        </label>
        <label>
          期日
          <input name="due" type="date" max={maxDue || undefined} required />
        </label>
      </div>
      <label>
        メモ
        <textarea name="memo" rows={2} />
      </label>
      <button className="primary" disabled={pending}>
        作成
      </button>
    </form>
  );
}
