import type { Task } from "../types";

/**
 * サーバーへ保存中の楽観的変更。
 *
 * 楽観更新をキャッシュへ書くだけでは足りない。先に投げたリクエストの完了で走る再取得は、
 * その時点のサーバー状態（=あとから押した変更をまだ含まない）でキャッシュを丸ごと置き換えるため、
 * 消したはずのタスクが復活する。保存中の変更をここに持ち、再取得のたびに上へ被せ直す。
 */
export interface PendingTaskOverlayEntry {
  patch: Partial<Task>;
  removeFromToday?: boolean;
  ensureInToday?: boolean;
}

export type PendingTaskOverlay = ReadonlyMap<string, PendingTaskOverlayEntry>;

/** overlay対象のリスト種別。todayだけ在籍そのものを変える。 */
export type TaskListKind = "all" | "my" | "today";

function patchTask(task: Task, patch: Partial<Task>): Task {
  let changed = false;
  for (const [field, value] of Object.entries(patch) as Array<[keyof Task, unknown]>) {
    if (!Object.is(task[field], value)) {
      changed = true;
      break;
    }
  }
  return changed ? { ...task, ...patch } : task;
}

/**
 * 保存中の変更をサーバー由来のリストへ被せ直す。
 * 変化が無いときは同じ参照を返し、無駄な再描画と再帰的な更新を避ける。
 */
export function applyPendingTaskOverlay(
  tasks: Task[] | undefined,
  overlay: PendingTaskOverlay,
  kind: TaskListKind,
  allTasks?: Task[]
): Task[] | undefined {
  if (!tasks || overlay.size === 0) return tasks;

  let next = tasks;
  let changed = false;

  for (const [taskId, entry] of overlay) {
    if (kind === "today" && entry.removeFromToday) {
      if (next.some((task) => task.Id === taskId)) {
        next = next.filter((task) => task.Id !== taskId);
        changed = true;
      }
      continue;
    }
    if (kind === "today" && entry.ensureInToday && !next.some((task) => task.Id === taskId)) {
      const source = allTasks?.find((task) => task.Id === taskId);
      if (source) {
        next = [...next, patchTask(source, entry.patch)];
        changed = true;
      }
      continue;
    }

    const index = next.findIndex((task) => task.Id === taskId);
    if (index < 0) continue;
    const patched = patchTask(next[index], entry.patch);
    if (patched !== next[index]) {
      if (!changed) next = [...next];
      next[index] = patched;
      changed = true;
    }
  }

  return changed ? next : tasks;
}

/** 保存中の変更が1件も無いか。全部片付いてから再取得したいときに使う。 */
export function isPendingTaskOverlayEmpty(overlay: PendingTaskOverlay): boolean {
  return overlay.size === 0;
}
