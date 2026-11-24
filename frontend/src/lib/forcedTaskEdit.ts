import type { Task } from "../types";

/**
 * 競合パネルの「自分の変更で上書き」で、どのAPIを呼び直すかを決める。
 *
 * 楽観更新のpatchは画面上の見た目の差分なので、そのままでは送信できない。
 * patchの内容から、元になった操作を1つだけ特定する。特定できないものは上書き送信させない。
 */
export type ForcedTaskEditKind =
  | "complete"
  | "reopen"
  | "memo"
  | "schedule"
  | "actual_manhour"
  | "assigned_manhour"
  | "today_on"
  | "today_off";

export function resolveForcedTaskEditKind(patch: Partial<Task>): ForcedTaskEditKind | null {
  const fields = new Set(Object.keys(patch));

  if (patch.Status === "Completion") return "complete";
  if (patch.Status === "NotStart" || patch.Status === "DuringStart") return "reopen";
  if (fields.has("StartDate") && fields.has("EndDate")) return "schedule";
  if (fields.size === 1 && fields.has("Memo")) return "memo";
  if (fields.size === 1 && fields.has("ActualManHour")) return "actual_manhour";
  if (fields.size === 1 && fields.has("AssignedManHour")) return "assigned_manhour";
  if (fields.size === 1 && fields.has("TodayOverride")) {
    return patch.TodayOverride ? "today_on" : "today_off";
  }
  return null;
}
