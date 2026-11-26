// 巣の中のカード（ゲームのステータス画面のような小窓）。数字はランキングと同じ数え方。
import { useEffect, useRef, useState } from "react";
import { ant, emblem } from "../colony/art";
import {
  ROOT_ROOM,
  canChangeRole,
  canDigBelow,
  canEditRoom,
  canManageRoom,
  roleAccessory,
  roleLabel,
  roleOrder,
  type NestLayout,
} from "../colony/nestModel";
import type { ColonyContributions, ColonyMonthValue, ColonyOrg, ColonyRole, User } from "../types";

export const lastOf = <T,>(list: T[] | undefined): T | undefined => (list ? list[list.length - 1] : undefined);
const mh = (minutes: number) => (minutes / 60).toLocaleString("ja-JP", { maximumFractionDigits: 1 });
const monthLabel = (month: string) => `${Number(month.slice(5))}月`;

export function AntPortrait({ role, size = 44 }: { role: ColonyRole; size?: number }) {
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
    ant(c, size * 0.42, size * 0.86, 1, 0.4, "idle", roleAccessory[role], size / 44);
  }, [role, size]);
  return <canvas ref={ref} style={{ width: size, height: size }} aria-hidden="true" />;
}

export function EmblemIcon({ name, size = 18 }: { name: string; size?: number }) {
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
    emblem(c, size / 2, size / 2, size / 2 - 0.5, name);
  }, [name, size]);
  return <canvas ref={ref} style={{ width: size, height: size }} aria-hidden="true" />;
}

/** 6か月の棒グラフ。line を渡すと折れ線（一人あたり）を重ねる。 */
export function MonthBars({
  months,
  values,
  line,
  unit,
  label,
}: {
  months: string[];
  values: number[];
  line?: number[];
  unit: string;
  label: string;
}) {
  const w = 270,
    h = 96,
    pad = 18;
  const top = Math.max(1, ...values, ...(line ?? []));
  const bw = (w - pad * 2) / Math.max(1, months.length);
  const y = (v: number) => h - 20 - (v / top) * (h - 34);
  return (
    <svg className="month-bars" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={label}>
      {values.map((v, i) => (
        <g key={months[i]}>
          <rect
            x={pad + i * bw + bw * 0.18}
            y={y(v)}
            width={bw * 0.64}
            height={Math.max(0, h - 20 - y(v))}
            rx="3"
            className={i === values.length - 1 ? "bar now" : "bar"}
          />
          <text x={pad + i * bw + bw / 2} y={h - 6} textAnchor="middle">
            {monthLabel(months[i])}
          </text>
          {i === values.length - 1 && (
            <text x={pad + i * bw + bw / 2} y={Math.max(10, y(v) - 4)} textAnchor="middle" className="value">
              {v.toLocaleString("ja-JP", { maximumFractionDigits: 1 })}
              {unit}
            </text>
          )}
        </g>
      ))}
      {line && line.length > 1 && (
        <polyline
          className="per-person"
          points={line.map((v, i) => `${pad + i * bw + bw / 2},${y(v)}`).join(" ")}
        />
      )}
    </svg>
  );
}

function Stat({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className="nest-stat">
      <small>{label}</small>
      <b>
        {value}
        {unit && <em>{unit}</em>}
      </b>
    </div>
  );
}

interface CardBase {
  org: ColonyOrg;
  layout: NestLayout;
  users: User[];
  viewer: string;
  contributions: ColonyContributions | undefined;
  busy: boolean;
  onClose: () => void;
  onSelectAnt: (id: string) => void;
}

export function AntCard({
  userId,
  focusRoom,
  onRole,
  onLeader,
  onLeave,
  ...base
}: CardBase & {
  userId: string;
  focusRoom: string | null;
  onRole: (role: ColonyRole) => void;
  onLeader: (roomId: string) => void;
  onLeave: (roomId: string) => void;
}) {
  const { org, layout, users, viewer, contributions } = base;
  const user = users.find((u) => u.Id === userId);
  const role = org.Roles[userId] ?? "member";
  const rooms = org.Teams.filter((team) => team.MemberIds.includes(userId));
  const person = contributions?.People.find((p) => p.UserId === userId);
  const nowValue: ColonyMonthValue | undefined = lastOf(person?.Months);
  const room = focusRoom ? layout.byId.get(focusRoom) : undefined;
  const inFocus = !!room && room.id !== ROOT_ROOM && room.members.includes(userId);
  return (
    <aside className="panel nest-card" aria-label="アリのステータス">
      <div className="nest-card-head">
        <AntPortrait role={role} size={52} />
        <div>
          <h2>{user?.Name ?? "（不明）"}</h2>
          <span className={"role-chip role-" + role}>{roleLabel[role]}</span>
        </div>
        <button type="button" className="tool-btn" aria-label="閉じる" onClick={base.onClose}>
          ×
        </button>
      </div>
      <div className="nest-chips">
        {rooms.length ? (
          rooms.map((team) => (
            <span key={team.Id} className="nest-chip">
              <EmblemIcon name={team.Emblem} size={16} />
              {team.Name}
              {team.LeaderUserId === userId && <em>主</em>}
            </span>
          ))
        ) : (
          <span className="nest-chip muted">まだどの部屋にも所属していません</span>
        )}
      </div>
      {person?.Visible && nowValue && contributions ? (
        <>
          <div className="nest-stats">
            <Stat label="今月運び込んだエサ" value={mh(nowValue.OutputMinutes)} unit="MH" />
            <Stat label="速さ" value={nowValue.WorkSpeed.toLocaleString("ja-JP", { maximumFractionDigits: 2 })} />
            <Stat label="教えた時間" value={String(nowValue.TeachingMinutes)} unit="分" />
          </div>
          <MonthBars
            label="過去6か月に運び込んだエサ"
            months={contributions.Months}
            values={person.Months.map((m) => m.OutputMinutes / 60)}
            unit="MH"
          />
        </>
      ) : (
        <p className="nest-note">
          {contributions ? "この人の数字は、本人と社長だけが見られます（ランキングと同じ公開の決まり）。" : "数字を読み込み中…"}
        </p>
      )}
      <div className="nest-section">
        <small>役職</small>
        <div className="role-picker">
          {roleOrder.map((next) => {
            const allowed =
              next !== role &&
              canChangeRole(org.ViewerRole, viewer, userId, role, next, rooms.map((t) => t.Id), org.Permissions);
            return (
              <button
                key={next}
                type="button"
                className={next === role ? "current" : ""}
                disabled={!allowed || base.busy}
                title={allowed || next === role ? roleLabel[next] : `${roleLabel[next]}（変えられるのは社長、課長は自分の部屋の係長・一般だけ）`}
                onClick={() => onRole(next)}
              >
                <AntPortrait role={next} size={34} />
                <span>{roleLabel[next]}</span>
                {!allowed && next !== role && <i aria-hidden="true">🔒</i>}
              </button>
            );
          })}
        </div>
      </div>
      {inFocus && room && (
        <div className="actions">
          {(role === "manager" || role === "chief") &&
            room.leader !== userId &&
            canManageRoom(org.ViewerRole, viewer, room.id, layout, org.Permissions) && (
              <button type="button" className="tool-btn" disabled={base.busy} onClick={() => onLeader(room.id)}>
                {room.name}の主にする
              </button>
            )}
          {room.leader !== userId && canEditRoom(org.Permissions, room.id) && (
            <button type="button" className="tool-btn" disabled={base.busy} onClick={() => onLeave(room.id)}>
              {room.name}から外す
            </button>
          )}
        </div>
      )}
    </aside>
  );
}

export function RoomCard({
  roomId,
  onRename,
  onEmblem,
  onDig,
  onFill,
  ...base
}: CardBase & {
  roomId: string;
  onRename: (name: string) => void;
  onEmblem: (emblem: string) => void;
  onDig: (parent: string) => void;
  onFill: () => void;
}) {
  const { org, layout, users, contributions } = base;
  const room = layout.byId.get(roomId);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(room?.name ?? "");
  const [confirmFill, setConfirmFill] = useState(false);
  useEffect(() => {
    setName(room?.name ?? "");
    setEditing(false);
    setConfirmFill(false);
  }, [roomId, room?.name]);
  if (!room) return null;
  const root = room.id === ROOT_ROOM;
  const stats = contributions?.Teams.find((t) => t.TeamId === roomId);
  const editable = !root && canEditRoom(org.Permissions, roomId);
  const manageable = !root && canManageRoom(org.ViewerRole, base.viewer, roomId, layout, org.Permissions);
  const hasChildren = layout.rooms.some((r) => r.parent === roomId);
  const leader = users.find((u) => u.Id === room.leader);
  const name_ = (id: string) => users.find((u) => u.Id === id)?.Name ?? "（不明）";
  const people = root ? users.filter((u) => u.IsActive) : room.members.map((id) => users.find((u) => u.Id === id)!).filter(Boolean);
  return (
    <aside className="panel nest-card" aria-label="部屋（チーム）">
      <div className="nest-card-head">
        <button
          type="button"
          className="emblem-btn"
          disabled={!editable || base.busy}
          title={editable ? "紋章を変える" : "紋章"}
          onClick={() => {
            const list = org.Emblems;
            onEmblem(list[(list.indexOf(room.emblem) + 1) % list.length]);
          }}
        >
          <EmblemIcon name={room.emblem} size={40} />
        </button>
        <div>
          {editing ? (
            <form
              className="inline-name"
              onSubmit={(e) => {
                e.preventDefault();
                if (name.trim()) onRename(name.trim());
                setEditing(false);
              }}
            >
              <input aria-label="部屋の名前" value={name} autoFocus onChange={(e) => setName(e.target.value)} />
            </form>
          ) : (
            <h2>
              {editable ? (
                <button type="button" className="name-btn" title="名前を変える" onClick={() => setEditing(true)}>
                  {room.name} ✎
                </button>
              ) : (
                room.name
              )}
            </h2>
          )}
          <small>
            {root ? "会社全体" : leader ? `主: ${leader.Name}` : "主: まだいない"} · {root ? people.length : room.members.length}人
          </small>
        </div>
        <button type="button" className="tool-btn" aria-label="閉じる" onClick={base.onClose}>
          ×
        </button>
      </div>
      {!root && stats && contributions && (
        <>
          <div className="nest-stats">
            <Stat label="今月の合計" value={mh(lastOf(stats.Months)?.OutputMinutes ?? 0)} unit="MH" />
            <Stat label="一人あたり" value={mh(lastOf(stats.PerPersonOutputMinutes) ?? 0)} unit="MH" />
            <Stat label="教えた時間" value={String(lastOf(stats.Months)?.TeachingMinutes ?? 0)} unit="分" />
          </div>
          <MonthBars
            label="チームが運び込んだエサ（棒=合計、線=一人あたり）"
            months={contributions.Months}
            values={stats.Months.map((m) => m.OutputMinutes / 60)}
            line={stats.PerPersonOutputMinutes.map((m) => m / 60)}
            unit="MH"
          />
          <p className="nest-legend">
            <span className="swatch bar" /> 合計 <span className="swatch line" /> 一人あたり
          </p>
        </>
      )}
      <ul className="nest-members">
        {people.map((u) => {
          const p = contributions?.People.find((item) => item.UserId === u.Id);
          const role = org.Roles[u.Id] ?? "member";
          return (
            <li key={u.Id}>
              <button type="button" onClick={() => base.onSelectAnt(u.Id)}>
                <AntPortrait role={role} size={28} />
                <span>
                  {u.Name}
                  {room.leader === u.Id && <em> 主</em>}
                  <small>{roleLabel[role]}</small>
                </span>
                <b>{p?.Visible ? `${mh(lastOf(p.Months)?.OutputMinutes ?? 0)}MH` : "非公開"}</b>
              </button>
            </li>
          );
        })}
        {!people.length && <li className="muted">まだ誰もいません。アリをつかんでこの部屋へ入れます。</li>}
      </ul>
      <div className="actions">
        <button
          type="button"
          className="tool-btn"
          disabled={!canDigBelow(org.Permissions, roomId) || base.busy}
          title={canDigBelow(org.Permissions, roomId) ? "この部屋の下に新しい部屋を掘る" : "掘れるのは社長と、この部屋を受け持つ課長です"}
          onClick={() => onDig(roomId)}
        >
          ⛏ この下に部屋を掘る
        </button>
        {manageable && !hasChildren && (
          <button
            type="button"
            className="tool-btn danger"
            disabled={base.busy}
            onClick={() => (confirmFill ? onFill() : setConfirmFill(true))}
          >
            {confirmFill ? "本当に埋める" : "部屋を埋める"}
          </button>
        )}
      </div>
      {root && <p className="nest-note">社長室です。ここから下の部屋がチームのつながりを表します。</p>}
      {!root && !editable && <p className="nest-note">所属を決められるのは、社長と、この部屋を受け持つ課長です。</p>}
      {room.leader && !users.some((u) => u.Id === room.leader) && <p className="nest-note">主: {name_(room.leader)}</p>}
    </aside>
  );
}

export function StoreCard({ kind, ...base }: CardBase & { kind: "storage" | "nursery" }) {
  const { contributions, users, org } = base;
  const storage = kind === "storage";
  const company = contributions?.Company ?? [];
  const count = Math.max(1, contributions?.CompanyMemberCount ?? 1);
  const teachers = (contributions?.People ?? [])
    .filter((p) => p.Visible && (lastOf(p.Months)?.TeachingMinutes ?? 0) > 0)
    .sort((a, b) => (lastOf(b.Months)?.TeachingMinutes ?? 0) - (lastOf(a.Months)?.TeachingMinutes ?? 0));
  return (
    <aside className="panel nest-card" aria-label={storage ? "食料庫" : "育児室"}>
      <div className="nest-card-head">
        <EmblemIcon name={storage ? "storage" : "nursery"} size={40} />
        <div>
          <h2>{storage ? "食料庫" : "育児室"}</h2>
          <small>{storage ? "会社全体で運び込んだエサ" : "教えた時間。教育はコロニーの中心の仕事"}</small>
        </div>
        <button type="button" className="tool-btn" aria-label="閉じる" onClick={base.onClose}>
          ×
        </button>
      </div>
      {contributions ? (
        storage ? (
          <>
            <div className="nest-stats">
              <Stat label="今月の合計" value={mh(lastOf(company)?.OutputMinutes ?? 0)} unit="MH" />
              <Stat label="一人あたり" value={mh((lastOf(company)?.OutputMinutes ?? 0) / count)} unit="MH" />
              <Stat label="6か月の合計" value={mh(company.reduce((s, m) => s + m.OutputMinutes, 0))} unit="MH" />
            </div>
            <MonthBars
              label="会社全体で運び込んだエサ"
              months={contributions.Months}
              values={company.map((m) => m.OutputMinutes / 60)}
              line={company.map((m) => m.OutputMinutes / 60 / count)}
              unit="MH"
            />
          </>
        ) : (
          <>
            <div className="nest-stats">
              <Stat label="今月教えた時間" value={String(lastOf(company)?.TeachingMinutes ?? 0)} unit="分" />
              <Stat label="6か月の合計" value={String(company.reduce((s, m) => s + m.TeachingMinutes, 0))} unit="分" />
            </div>
            <MonthBars
              label="会社全体で教えた時間"
              months={contributions.Months}
              values={company.map((m) => m.TeachingMinutes)}
              unit="分"
            />
            <ul className="nest-members">
              {teachers.map((p) => {
                const u = users.find((item) => item.Id === p.UserId);
                const role = org.Roles[p.UserId] ?? "member";
                return (
                  <li key={p.UserId}>
                    <button type="button" onClick={() => base.onSelectAnt(p.UserId)}>
                      <AntPortrait role={role} size={28} />
                      <span>{u?.Name ?? "（不明）"}</span>
                      <b>{lastOf(p.Months)?.TeachingMinutes ?? 0}分</b>
                    </button>
                  </li>
                );
              })}
              {!teachers.length && <li className="muted">今月はまだ記録がありません（見られる人の分だけ表示）。</li>}
            </ul>
          </>
        )
      ) : (
        <p className="nest-note">数字を読み込み中…</p>
      )}
    </aside>
  );
}
