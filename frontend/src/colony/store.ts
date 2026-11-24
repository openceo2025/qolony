import { applyAction, initialWorld, populateDistricts } from "./model";
import type { Action, World } from "./model";

const key = "qolony.exterior.demo.v1";
function load(): World | null {
  const raw = localStorage.getItem(key);
  if (!raw) return null;
  const value: World = JSON.parse(raw);
  if (value.version !== 1 || !Array.isArray(value.foods))
    throw Error("保存済みのデモデータを読み込めません。");
  return value;
}
// タブ間の同時受注は、同じロック内で最新状態を読んでから判定する。
async function exclusive<T>(fn: () => T): Promise<T> {
  return navigator.locks ? navigator.locks.request(key, fn) : fn();
}
export async function openWorld(): Promise<World> {
  return exclusive(() => {
    const w = load();
    const world = w ? populateDistricts(w, Date.now()) : initialWorld(Date.now());
    if (world !== w) localStorage.setItem(key, JSON.stringify(world));
    return world;
  });
}
export async function commit(action: Action, actor: string) {
  return exclusive(() => {
    const now = Date.now(),
      saved = load();
    const w = applyAction(saved ? populateDistricts(saved, now) : initialWorld(now), action, actor, now);
    localStorage.setItem(key, JSON.stringify(w));
    return w;
  });
}
export function watchWorld(update: (world: World) => void) {
  const handle = (e: StorageEvent) => {
    if (e.key === key && e.newValue) {
      const w = load();
      if (w) update(w);
    }
  };
  window.addEventListener("storage", handle);
  return () => window.removeEventListener("storage", handle);
}
