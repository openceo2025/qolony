export const ABORTED_TASK_MARKER_COLOR = "#8D99AE";

export function isAbortedTaskStatus(status: string | null | undefined): boolean {
  return status === "Abort";
}

export function isTerminalTaskStatus(status: string | null | undefined): boolean {
  return status === "Completion" || status === "Abort";
}

export function formatTaskStatus(status: string | null | undefined): string {
  if (status === "Abort") return "中止（Abort）";
  return status || "—";
}

export function getTaskMarkerColor(
  status: string,
  signal: string,
  endDate: string,
  todayIso: string
): string {
  if (status === "Abort") return ABORTED_TASK_MARKER_COLOR;
  const signalColorMap: Record<string, string> = {
    Blue: "#3A86FF",
    Green: "#2A9D52",
    Yellow: "#F59E0B",
    Red: "#E63946",
  };
  return signalColorMap[signal] ?? (status === "Completion" ? "#3A86FF" : endDate < todayIso ? "#E63946" : "#F59E0B");
}
