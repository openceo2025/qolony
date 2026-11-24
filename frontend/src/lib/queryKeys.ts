export const qk = {
  health: ["health"] as const,
  meta: ["meta"] as const,
  syncStatus: ["syncStatus"] as const,
  iquavisConflicts: ["iquavisConflicts"] as const,
  members: (dateFrom: string, dateTo: string) => ["members", dateFrom, dateTo] as const,
  tasks: ["tasks"] as const,
  myTasks: (userId: string) => ["myTasks", userId] as const,
  todayTasks: (userId: string) => ["todayTasks", userId] as const,
  todaySummary: (userId: string) => ["todaySummary", userId] as const,
  taskProgress: (taskId: string | null) => ["progress", "task", taskId] as const,
  rankings: (userId: string, yearMonth: string, periodMonths: number) =>
    ["rankings", userId, yearMonth, periodMonths] as const,
  rankingHistory: (userId: string, yearMonth: string, periodMonths: number) =>
    ["rankings", "history", userId, yearMonth, periodMonths] as const,
  infeasibility: ["infeasibility"] as const,
  reworkTasks: ["reworkTasks"] as const,
};
