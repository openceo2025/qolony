import { createRoot } from "react-dom/client";
import { useMemo, useState } from "react";
import { ColonyView } from "../views/ColonyView";
import { fakeUsers, makeFakeTasks } from "./fakeTasks";
import type { Task, User } from "../types";

// 開発用の確認ページ（field-lab.html）。架空タスクで巣の外を表示する。本番ビルドには含めない。
// ?n=11000 で件数、?done=0.7 で完了率を変えられる。

const params = new URLSearchParams(location.search);
const count = Number(params.get("n") ?? 300);
const doneRatio = Number(params.get("done") ?? 0.6);

const users = fakeUsers().map(
  (u, i) =>
    ({
      Id: u.Id,
      Name: u.Name,
      IsActive: true,
      IsTopManager: i === 0,
    }) as unknown as User,
);

function Lab() {
  const [tasks, setTasks] = useState<Task[]>(() => makeFakeTasks(count, 7, doneRatio));
  const [selected, setSelected] = useState<string | null>(null);
  const patch = (id: string, fields: Partial<Task>) => {
    setTasks((rows) => rows.map((t) => (t.Id === id ? { ...t, ...fields } : t)));
    return Promise.resolve(true);
  };
  const project = useMemo(() => undefined, []);
  return (
    <div style={{ position: "fixed", inset: 0, display: "flex", flexDirection: "column" }}>
      <ColonyView
        tasks={tasks}
        users={users}
        members={[]}
        project={project}
        user={users[0]}
        timer={null}
        selectedId={selected}
        onSelect={setSelected}
        onOpenDetail={() => undefined}
        onOpenSplit={() => undefined}
        onOpenToday={() => Promise.resolve(true)}
        onComplete={(id) => patch(id, { Status: "Completion", ProgressRate: 100 })}
        onReopen={(id) => patch(id, { Status: "NotStart", ProgressRate: 0 })}
        onSaveMemo={(id, memo) => patch(id, { Memo: memo })}
        onSaveEstimate={(id, minutes) => patch(id, { AssignedManHour: minutes })}
        onSaveDue={(id, date) => patch(id, { EndDate: date })}
        onCreate={() => Promise.resolve(false)}
        onRefresh={() => undefined}
      />
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<Lab />);
