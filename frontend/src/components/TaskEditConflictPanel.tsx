import {
  buildTaskEditConflictRows,
  formatTaskEditConflictGuidance,
  formatTaskEditConflictHeadline,
  type TaskEditConflict,
} from "../lib/taskEditConflict";

interface TaskEditConflictPanelProps {
  conflicts: readonly TaskEditConflict[];
  busyTaskIds: ReadonlySet<string>;
  onForceMine: (taskId: string) => void;
  onAcceptTheirs: (taskId: string) => void;
}

function formatDetectedAt(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleTimeString();
}

/**
 * 同時編集の競合をタスクごとに1枚ずつ出す。
 * 一括で片付けさせない。誰と何がぶつかっているかを読んでから決める操作なので、1件ずつ選ばせる。
 */
export function TaskEditConflictPanel({
  conflicts,
  busyTaskIds,
  onForceMine,
  onAcceptTheirs,
}: TaskEditConflictPanelProps): JSX.Element | null {
  if (conflicts.length === 0) return null;

  return (
    <div className="task-edit-conflict-stack" aria-live="assertive">
      {conflicts.map((conflict) => {
        const rows = buildTaskEditConflictRows(conflict);
        const busy = busyTaskIds.has(conflict.taskId);
        return (
          <section className="task-edit-conflict" key={conflict.taskId}>
            <div className="task-edit-conflict-header">
              <span className="task-edit-conflict-badge">編集が競合しました</span>
              <span className="task-edit-conflict-time">{formatDetectedAt(conflict.detectedAt)}</span>
            </div>
            <div className="task-edit-conflict-headline">{formatTaskEditConflictHeadline(conflict)}</div>
            <div className="task-edit-conflict-guidance">{formatTaskEditConflictGuidance(conflict)}</div>

            {rows.length > 0 ? (
              <div className="task-edit-conflict-grid" role="table" aria-label="編集競合の内容">
                <div className="task-edit-conflict-grid-head" role="row">
                  <span role="columnheader">項目</span>
                  <span role="columnheader">変更前</span>
                  <span role="columnheader">自分の変更</span>
                  <span role="columnheader">相手の現在値</span>
                </div>
                {rows.map((row) => (
                  <div
                    className={`task-edit-conflict-grid-row${row.contested ? " contested" : ""}`}
                    role="row"
                    key={row.field}
                  >
                    <span role="cell" className="task-edit-conflict-field">
                      {row.label}
                    </span>
                    <span role="cell" data-label="変更前">{row.base}</span>
                    <span role="cell" data-label="自分の変更" className="task-edit-conflict-mine">{row.mine}</span>
                    <span role="cell" data-label="相手の現在値" className="task-edit-conflict-theirs">{row.theirs}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="task-edit-conflict-guidance">
                変更内容の詳細を取得できませんでした。最新状態を確認してからやり直してください。
              </div>
            )}

            <div className="task-edit-conflict-actions">
              <button
                type="button"
                className="btn primary"
                disabled={busy}
                onClick={() => onForceMine(conflict.taskId)}
              >
                {busy ? "処理中..." : "自分の変更で上書き"}
              </button>
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => onAcceptTheirs(conflict.taskId)}
              >
                {busy ? "処理中..." : "相手の変更を採用"}
              </button>
            </div>
          </section>
        );
      })}
    </div>
  );
}
