import { createRoot } from "react-dom/client";
import { useEffect, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { AntMark, Icon } from "./Icon";
import type { IconName } from "./Icon";
import {
  deadline,
  detailOf,
  distance,
  hour,
  initialWorld,
  kinds,
  makeFood,
  memberCredit,
  nest,
  positionAt,
  progressAt,
  roles,
  sweets,
} from "./model";
import type { Accessory, Action, Food, Kind, Point, World } from "./model";
import { mountScene } from "./scene";
import { commit, openWorld, watchWorld } from "./store";
import "./style.css";

type Panel = "task" | "list" | "profile" | "place" | "zone" | "line" | "team" | "guide" | null;
type Tool =
  | { type: "place"; food: Food }
  | { type: "zone" | "line"; name: string; team: string | null; points: Point[] };
const yen = (n: number) => "¥" + n.toLocaleString("ja-JP");
const uid = () => crypto.randomUUID();
const iconNames: Record<Kind, string> = {
  sugar: "◇",
  bonbon: "◈",
  cookie: "●",
  chocolate: "▦",
  lollipop: "◎",
  crumb: "▪",
};
function ToolButton({
  icon,
  children,
  onClick,
  active = false,
  label,
  disabled = false,
}: {
  icon: IconName;
  children?: ReactNode;
  onClick: () => void;
  active?: boolean;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className={"tool-btn" + (active ? " active" : "")}
      onClick={onClick}
      aria-label={label}
      title={label}
      disabled={disabled}
    >
      <Icon name={icon} />
      {children}
    </button>
  );
}
function App() {
  const [world, setWorld] = useState<World>(() => initialWorld(Date.now()));
  const [ready, setReady] = useState(false),
    [actor, setActor] = useState(() => sessionStorage.getItem("qolony.demo.actor") ?? "mio");
  const role = roles.find((r) => r.id === actor) ?? roles[0];
  const [panel, setPanel] = useState<Panel>(null),
    [selected, setSelected] = useState<string | null>(null);
  const [tool, setTool] = useState<Tool | null>(null),
    [zoom, setZoom] = useState(100),
    [now, setNow] = useState(Date.now);
  const [toast, setToast] = useState(""),
    [problem, setProblem] = useState(""),
    [busy, setBusy] = useState(false);
  const canvas = useRef<HTMLCanvasElement>(null),
    annotationLayer = useRef<HTMLDivElement>(null),
    scene = useRef<ReturnType<typeof mountScene> | null>(null);
  const chosen = world.foods.find((f) => f.id === selected);
  const state = useRef({ world, selected, actor, draft: [] as Point[], tool: false });
  state.current = {
    world,
    selected,
    actor,
    draft: tool && tool.type !== "place" ? tool.points : [],
    tool: !!tool,
  };
  const pick = useRef<(p: Point, id: string | null) => void>(() => {});
  useEffect(() => {
    let alive = true;
    const unwatch = watchWorld((w) => {
      if (alive) setWorld(w);
    });
    openWorld()
      .then((w) => {
        if (alive) {
          setWorld(w);
          setReady(true);
        }
      })
      .catch((e) => setProblem(String(e)));
    return () => {
      alive = false;
      unwatch();
    };
  }, []);
  useEffect(() => {
    if (!canvas.current || !annotationLayer.current) return;
    const instance = mountScene(canvas.current, annotationLayer.current, () => state.current, {
      pick: (p, id) => pick.current(p, id),
      zoom: setZoom,
    });
    scene.current = instance;
    return () => instance.destroy();
  }, []);
  useEffect(() => {
    if (!ready) return;
    const percent = Number(new URLSearchParams(window.location.search).get("zoom"));
    if ([27, 100, 200].includes(percent)) scene.current?.viewpoint(percent / 100);
    else if (state.current.actor === "kei") scene.current?.viewpoint(0.27);
  }, [ready]);
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 420);
    return () => clearInterval(id);
  }, []);
  useEffect(() => {
    if (!toast) return;
    const id = window.setTimeout(() => setToast(""), 3200);
    return () => clearTimeout(id);
  }, [toast]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setTool(null);
        setPanel(null);
        setSelected(null);
        setProblem("");
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {
    if (selected && !chosen) {
      setSelected(null);
      if (panel === "task") setPanel(null);
    }
  }, [selected, chosen, panel]);
  function open(p: Panel) {
    setPanel(panel === p ? null : p);
    setTool(null);
    setProblem("");
    if (p !== "task") setSelected(null);
  }
  function close() {
    setPanel(null);
    setSelected(null);
    setProblem("");
  }
  async function act(action: Action, message: string) {
    if (!ready || busy) return false;
    setBusy(true);
    setProblem("");
    try {
      setWorld(await commit(action, actor));
      setToast(message);
      return true;
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(false);
    }
  }
  function select(id: string, focus = false) {
    setProblem("");
    setSelected(id);
    setPanel("task");
    const f = world.foods.find((it) => it.id === id);
    if (f && focus) scene.current?.focus(positionAt(f, Date.now()));
  }
  pick.current = (p, id) => {
    if (tool?.type === "place") {
      void act({ type: "place", food: { ...tool.food, ...p } }, "仕事を地面に置きました").then((ok) => {
        if (ok) {
          setTool(null);
          select(tool.food.id);
        }
      });
      return;
    }
    if (tool) {
      setTool({ ...tool, points: [...tool.points, p] });
      return;
    }
    if (id) select(id);
    else close();
  };
  function beginPlacement(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget),
      kind = data.get("kind") as Kind;
    const f = makeFood(
      uid(),
      String(data.get("name")).trim(),
      kind,
      { x: 0, y: 0 },
      Date.now() + Number(data.get("hours")) * hour,
    );
    if (!f.name) {
      setProblem("仕事名を入力してください。");
      return;
    }
    Object.assign(f, {
      requirement: String(data.get("requirement") ?? ""),
      sales: Number(data.get("sales")),
      profit: Number(data.get("profit")),
    });
    const detail = detailOf(f);
    f.detail = {
      ...detail,
      input: String(data.get("input") ?? "").trim() || detail.input,
      output: String(data.get("output") ?? "").trim() || detail.output,
      issue: String(data.get("issue") ?? "").trim() || detail.issue,
      estimateMH: Number(data.get("estimate")) || detail.estimateMH,
    };
    close();
    setTool({ type: "place", food: f });
  }
  function beginMark(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget),
      team = world.teams.find((t) => t.id === data.get("team")),
      isZone = panel === "zone";
    const name = isZone ? team?.name : String(data.get("name") ?? "").trim();
    if (!name) {
      setProblem("名前を入力してください。");
      return;
    }
    close();
    setTool({ type: isZone ? "zone" : "line", team: isZone ? team!.id : null, name, points: [] });
  }
  async function finishMark() {
    if (!tool || tool.type === "place") return;
    if (
      await act(
        { type: "mark", mark: { id: uid(), name: tool.name, team: tool.team, points: tool.points } },
        "地面に線を描きました",
      )
    )
      setTool(null);
  }
  async function makeTeam(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const d = new FormData(e.currentTarget);
    if (
      await act(
        {
          type: "team",
          team: {
            id: uid(),
            name: String(d.get("name")).trim(),
            parent: String(d.get("parent")) || null,
            members: d.getAll("member").map(String),
          },
        },
        "チームを作りました",
      )
    )
      setPanel("zone");
  }
  function changeActor(id: string) {
    sessionStorage.setItem("qolony.demo.actor", id);
    setActor(id);
    setTool(null);
    setPanel(null);
    setSelected(null);
    setProblem("");
    if (id === "kei") scene.current?.viewpoint(0.27);
    if (id === "yui") scene.current?.home();
  }
  const carrying = world.foods.filter((f) => f.since !== null).length;
  const titles: Record<Exclude<Panel, null>, string> = {
    task: chosen?.name ?? "仕事",
    list: "フィールドの仕事",
    profile: "操作するアリ",
    place: "仕事を置く",
    zone: "チームのエリア",
    line: "方針の線を描く",
    team: "チームを作る",
    guide: "フィールドの操作",
  };
  const closeButton = <ToolButton icon="close" label="閉じる" onClick={close} />;
  return (
    <main className="colony">
      <header className="colony-header">
        <div className="wordmark">
          <AntMark />
          QOLONY
        </div>
        <span className="header-divider" />
        <h1>巣の外</h1>
        <div className="org-stats" aria-label="組織の共通指標（デモ値）">
          <div>
            <span>売上</span>
            <strong>¥12,400,000</strong>
          </div>
          <div>
            <span>純利益</span>
            <strong>¥2,180,000</strong>
          </div>
        </div>
        <button className="profile-btn" onClick={() => open("profile")}>
          <span className="avatar">
            <AntMark />
          </span>
          <span>
            {role.name}
            <small>{role.role}</small>
          </span>
          <Icon name="down" size={14} />
        </button>
      </header>
      <section className="field" aria-label="コロニーフィールド">
        <canvas
          ref={canvas}
          tabIndex={0}
          aria-label="巣の外の地図。ホイールでズーム、ドラッグで移動。仕事は一覧からも選択できます。"
        />
        <div ref={annotationLayer} className="field-annotations" aria-label="地図上の情報" />
        <div className="top-left">
          <button className="summary" onClick={() => open("list")}>
            <Icon name="list" />
            <b>仕事一覧</b>
            <span>{world.foods.length}</span>
            <Icon name="down" size={13} />
          </button>
        </div>
        <div className="top-right">
          <button className="place-btn" onClick={() => open("place")} disabled={!ready}>
            <Icon name="plus" />
            仕事を置く
          </button>
          {role.max >= 4 && (
            <ToolButton icon="zone" onClick={() => open("zone")}>
              エリア
            </ToolButton>
          )}
          {role.max >= 5 && (
            <ToolButton icon="line" onClick={() => open("line")}>
              方針線
            </ToolButton>
          )}
        </div>
        <div className="bottom-left">
          <span className="field-stat">
            <i />
            運搬中 <b>{carrying}</b>
            <em />
            納品 <b>{world.delivered}</b>
          </span>
          <button
            className="guide-btn"
            title="フィールドの操作"
            onClick={() => open("guide")}
            aria-label="フィールドの操作"
          >
            ?
          </button>
        </div>
        <div className="map-nav">
          <ToolButton icon="target" label="巣に戻る" onClick={() => scene.current?.home()} />
          <span />
          <label className="viewpoint">
            <b>{zoom < 76 ? "社長目線" : zoom < 134 ? "課長目線" : "現場目線"}</b>
            <Icon name="down" size={12} />
            <select
              aria-label="視点と倍率"
              value=""
              onChange={(e) => {
                if (e.target.value) scene.current?.viewpoint(Number(e.target.value));
              }}
            >
              <option value="" disabled>
                視点を選択
              </option>
              <option value=".27">社長目線 · 27%</option>
              <option value="1">課長目線 · 100%</option>
              <option value="2">現場目線 · 200%</option>
            </select>
          </label>
          <ToolButton icon="minus" label="縮小" onClick={() => scene.current?.zoom(1 / 1.3)} />
          <output aria-label="ズーム倍率">{zoom}%</output>
          <ToolButton icon="plus" label="拡大" onClick={() => scene.current?.zoom(1.3)} />
        </div>
        {!panel && !tool && (
          <div className="map-hint">
            ドラッグで移動 <b>·</b> ホイールでズーム <b>·</b> お菓子で仕事を選択
          </div>
        )}
        {tool && (
          <div className="drawing">
            <Icon name="cursor" />
            <b>
              {tool.type === "place"
                ? "置く場所をクリック"
                : tool.type === "zone"
                  ? "境界の頂点を順にクリック"
                  : "線を通す点を順にクリック"}
            </b>
            {tool.type !== "place" && (
              <>
                <span>{tool.points.length} 点</span>
                <button
                  disabled={tool.points.length < (tool.type === "zone" ? 3 : 2) || busy}
                  onClick={() => void finishMark()}
                >
                  確定
                </button>
                <button
                  disabled={!tool.points.length}
                  onClick={() => setTool({ ...tool, points: tool.points.slice(0, -1) })}
                >
                  1点戻す
                </button>
              </>
            )}
            <button
              onClick={() => {
                setTool(null);
                setProblem("");
              }}
            >
              取消
            </button>
          </div>
        )}
        {(toast || problem) && (
          <div role={problem ? "alert" : "status"} className={"notice " + (problem ? "error" : "")}>
            <Icon name={problem ? "flag" : "check"} />
            {problem || toast}
          </div>
        )}
        {panel && (
          <aside className={"panel panel-" + panel} aria-label={titles[panel]}>
            <div className="panel-head">
              <div>
                {panel === "task" && chosen && (
                  <small>
                    {sweets[chosen.kind]} <span>/</span> 難易度 {chosen.weight}
                  </small>
                )}
                <h2>{titles[panel]}</h2>
              </div>
              {closeButton}
            </div>
            {panel === "task" && chosen && (
              <TaskInfo
                f={chosen}
                now={now}
                actor={actor}
                canHelp={role.max >= 3}
                busy={busy || !ready}
                world={world}
                run={async (type, msg) => {
                  const p = positionAt(chosen, Date.now());
                  const ok = await act({ type, id: chosen.id }, msg);
                  if (ok && type === "finish") scene.current?.frameDelivery(p);
                  if (ok && (type === "split" || type === "finish")) close();
                }}
              />
            )}
            {panel === "list" && (
              <div className="food-list">
                {world.foods.map((f) => (
                  <button key={f.id} onClick={() => select(f.id, true)}>
                    <span className={"food-symbol " + f.kind}>{iconNames[f.kind]}</span>
                    <span>
                      <b>{f.name}</b>
                      <small>
                        {sweets[f.kind]} · {f.since !== null ? "運搬中" : f.owner ? "一時停止" : "未着手"}
                      </small>
                    </span>
                    <Icon name="arrow" size={14} />
                  </button>
                ))}
                {!world.foods.length && <p>すべての仕事を巣に届けました。</p>}
              </div>
            )}
            {panel === "profile" && (
              <>
                <div className="role-list">
                  {roles.map((r) => (
                    <button
                      key={r.id}
                      className={r.id === actor ? "current" : ""}
                      onClick={() => changeActor(r.id)}
                    >
                      <b>{r.name}</b>
                      <span>{r.role}</span>
                      {r.id === actor && <Icon name="check" size={15} />}
                    </button>
                  ))}
                </div>
                <div className="profile-more">
                  <label>
                    自分の目印
                    <select
                      value={world.accessories[actor] ?? role.accessory}
                      onChange={(e) =>
                        void act(
                          { type: "accessory", value: e.target.value as Accessory },
                          "目印を変更しました",
                        )
                      }
                    >
                      <option value="scarf">ミントのスカーフ</option>
                      <option value="cap">山吹色の帽子</option>
                      <option value="bow">赤いリボン</option>
                    </select>
                  </label>
                  <p>
                    自分の貢献 <b>{memberCredit(world, actor, now)} pt</b>
                  </p>
                </div>
              </>
            )}
            {panel === "place" && (
              <form onSubmit={beginPlacement}>
                <label>
                  仕事名
                  <input name="name" required maxLength={48} autoFocus placeholder="例：見積書を確認する" />
                </label>
                <div className="form-row">
                  <label>
                    お菓子・難易度
                    <select name="kind">
                      {kinds.slice(0, role.max).map((k, i) => (
                        <option key={k} value={k}>
                          {i + 1} · {sweets[k]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    期限
                    <select name="hours" defaultValue="48">
                      {[4, 8, 24, 48, 72, 168].map((h) => (
                        <option key={h} value={h}>
                          {h < 24 ? h + "時間後" : h / 24 + "日後"}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <label>
                  要件
                  <textarea name="requirement" maxLength={240} rows={2} />
                </label>
                <details className="new-work-detail">
                  <summary>入出力・工数・課題</summary>
                  <label>
                    インプット
                    <input name="input" maxLength={60} />
                  </label>
                  <label>
                    アウトプット
                    <input name="output" maxLength={60} />
                  </label>
                  <label>
                    見積MH
                    <input type="number" name="estimate" min=".01" step=".01" placeholder="任意" />
                  </label>
                  <label>
                    課題
                    <input name="issue" maxLength={60} />
                  </label>
                </details>
                <div className="form-row">
                  <label>
                    予測売上（円）
                    <input type="number" name="sales" min="0" step="1" placeholder="任意" />
                  </label>
                  <label>
                    予測利益（円）
                    <input type="number" name="profit" step="1" placeholder="任意" />
                  </label>
                </div>
                <button className="primary">
                  地図で場所を選ぶ <Icon name="pin" />
                </button>
              </form>
            )}
            {(panel === "zone" || panel === "line") && (
              <form onSubmit={beginMark}>
                {panel === "zone" ? (
                  <>
                    <label>
                      適用するチーム
                      <select name="team">
                        {world.teams.map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.parent
                              ? (world.teams.find((p) => p.id === t.parent)?.name ?? "") + " ／ "
                              : ""}
                            {t.name}
                          </option>
                        ))}
                      </select>
                    </label>
                    <button type="button" className="secondary" onClick={() => setPanel("team")}>
                      <Icon name="plus" />
                      チームを作る
                    </button>
                  </>
                ) : (
                  <label>
                    線の名前
                    <input name="name" required maxLength={30} placeholder="例：新しい取り組み" />
                  </label>
                )}
                <button className="primary">
                  地面に描く <Icon name="cursor" />
                </button>
              </form>
            )}
            {panel === "team" && (
              <form onSubmit={makeTeam}>
                <label>
                  チーム名
                  <input name="name" required maxLength={30} />
                </label>
                <label>
                  上位チーム
                  <select name="parent">
                    <option value="">なし</option>
                    {world.teams.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.name}
                      </option>
                    ))}
                  </select>
                </label>
                <fieldset>
                  <legend>メンバー</legend>
                  {roles.map((r) => (
                    <label key={r.id}>
                      <input type="checkbox" name="member" value={r.id} />
                      {r.name}
                    </label>
                  ))}
                </fieldset>
                <button className="primary">チームを作る</button>
              </form>
            )}
            {panel === "guide" && (
              <div className="guide">
                <p>
                  <b>地面をドラッグ</b>して移動し、<b>ホイール</b>でポインターの場所にズームします。
                </p>
                <p>
                  右下の視点から27%・100%・200%にも移動できます。社長目線の円は、そのエリアに配置した仕事を見積MHで重み付けした進捗です。重なった場所の仕事は両方のエリアに含みます。
                </p>
                {world.delivered > (world.receipts?.length ?? 0) && (
                  <p>
                    旧版で納品した {world.delivered - (world.receipts?.length ?? 0)}{" "}
                    件は配置地点の記録がないため、エリア別の集計対象外です。
                  </p>
                )}
                <p>
                  現場目線では、読みたいお菓子やアリにポインターを合わせると、項目ごとの情報が引き出し線でつながります。
                </p>
                <p>
                  お菓子をクリックして仕事を選びます。運搬を始めた後、「完了して巣に落とす」で納品します。重い仕事は分割できます。
                </p>
                <p>
                  右上のアリで役職と目印を変更できます。同じブラウザで別のタブを開くと同じ巣で作業できます。
                </p>
                <small>デモデータ使用 · 地図上で矢印キー、＋／－、Homeも使用できます。</small>
              </div>
            )}
          </aside>
        )}
      </section>
    </main>
  );
}
type TaskCommand = "start" | "pause" | "finish" | "split" | "help";
function TaskInfo({
  f,
  now,
  actor,
  canHelp,
  busy,
  world,
  run,
}: {
  f: Food;
  now: number;
  actor: string;
  canHelp: boolean;
  busy: boolean;
  world: World;
  run: (type: TaskCommand, message: string) => Promise<void>;
}) {
  const d = deadline(f, now),
    mine = f.owner === actor,
    other = !!f.owner && !mine,
    active = f.since !== null;
  const p = progressAt(f, now),
    owner = roles.find((r) => r.id === f.owner),
    occupied = world.foods.some((task) => task.id !== f.id && task.owner === actor && task.since !== null);
  return (
    <div className="task-body">
      <div className="task-meta">
        <span>
          <Icon name="user" size={14} />
          {owner?.name ?? "担当未定"}
        </span>
        <span className={d.risk ? "risk" : ""}>
          <Icon name="clock" size={14} />
          {d.label}
        </span>
        {f.help && <span>応援中</span>}
      </div>
      {d.risk && <p className="risk-text">このペースでは期限に間に合わない見込みです。</p>}
      {f.requirement && <p className="requirement">{f.requirement}</p>}
      {(f.sales !== 0 || f.profit !== 0) && (
        <p className="requirement">
          予測売上 {yen(f.sales)}　予測利益 {yen(f.profit)}
        </p>
      )}
      <div className="progress-label">
        <span>運搬距離</span>
        <b>
          {Math.round(distance(f, nest) * p)} / {Math.round(distance(f, nest))} 歩
        </b>
      </div>
      <div className="track">
        <i style={{ width: p * 100 + "%" }} />
      </div>
      {!other && (!active || mine) && (
        <button
          className="primary"
          disabled={busy || (!active && occupied)}
          onClick={() =>
            void run(active ? "finish" : "start", active ? "仕事を巣に届けました" : "運搬を始めました")
          }
        >
          <Icon name={active ? "home" : "arrow"} />
          {active
            ? "完了して巣に落とす"
            : occupied
              ? "別の仕事を運搬中"
              : mine
                ? "運搬を再開"
                : "運搬を始める"}
        </button>
      )}
      <div className="actions">
        {mine && active && (
          <ToolButton
            icon="pause"
            disabled={busy}
            onClick={() => void run("pause", "運搬を一時停止しました")}
          >
            一時停止
          </ToolButton>
        )}
        {f.weight > 1 && !other && (
          <ToolButton
            icon="split"
            disabled={busy}
            onClick={() => void run("split", "仕事を2つに分割しました")}
          >
            分割する
          </ToolButton>
        )}
        {canHelp && !f.help && (
          <ToolButton
            icon="help"
            disabled={busy}
            onClick={() => void run("help", "応援のアリが加わりました")}
          >
            応援を呼ぶ
          </ToolButton>
        )}
      </div>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
