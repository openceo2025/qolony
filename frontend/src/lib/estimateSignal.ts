import type { Task } from "../types";

type EstimateSignalTask = Pick<
  Task,
  "AssignedManHour" | "ChildAssignedManHour" | "EstimateBalanceManHour" | "EstimateSignal" | "EstimateSignalMessage"
>;

export function getEstimateSignalLabel(task: Pick<EstimateSignalTask, "EstimateSignal">): string | null {
  if (task.EstimateSignal === "Strong") return "見積必須";
  if (task.EstimateSignal === "Medium") return "依頼見積なし";
  if (task.EstimateSignal === "Weak") return "見積未配分";
  return null;
}

export function getEstimateSignalClass(task: Pick<EstimateSignalTask, "EstimateSignal">): string {
  if (task.EstimateSignal === "Strong") return "strong";
  if (task.EstimateSignal === "Medium") return "medium";
  if (task.EstimateSignal === "Weak") return "weak";
  return "none";
}

export function formatEstimateBalance(task: Pick<EstimateSignalTask, "EstimateBalanceManHour">): string {
  const balance = Number.isFinite(task.EstimateBalanceManHour) ? task.EstimateBalanceManHour : 0;
  if (balance > 0) return `見積残:+${balance}分`;
  if (balance < 0) return `見積負債:${balance}分`;
  return "見積残:0分";
}

export function formatChildEstimate(task: Pick<EstimateSignalTask, "ChildAssignedManHour">): string {
  const childAssigned = Number.isFinite(task.ChildAssignedManHour) ? task.ChildAssignedManHour : 0;
  return `子配分:${childAssigned}分`;
}
