import type {
  BulkTaskAssigneeResponse,
  BulkTaskFieldsResponse,
  InfeasibilityResponse,
  IquavisAuthoritativeImportApplyResponse,
  IquavisAuthoritativeImportPreviewResponse,
  IquavisForcedImportApplyResponse,
  IquavisForcedImportPreviewResponse,
  TaskEditConflictDetail,
  TaskSubtreeAssigneeResult,
  IquavisConflictDirection,
  IquavisConflictListResponse,
  IquavisConflictRefreshResponse,
  IquavisConflictResolveResponse,
  IquavisConflictHoldResponse,
  MetaResponse,
  MemberView,
  RankingHistoryResponse,
  Rankings,
  SyncStatus,
  Task,
  TaskProgressDashboard,
  TodaySummary,
} from "../types";

export class ApiError extends Error {
  status: number;
  /** 409のとき、相手の現在値と更新者。競合パネルの表示に使う。 */
  conflict?: TaskEditConflictDetail;

  constructor(message: string, status: number, conflict?: TaskEditConflictDetail) {
    super(message);
    this.status = status;
    this.conflict = conflict;
  }
}

export interface TaskMutationResult {
  status: string;
  revision: string | null;
  warnings: string[];
}

export interface OrderMutationResult {
  status: string;
  order_revision: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  const ct = res.headers.get("content-type") || "";
  const payload = ct.includes("application/json") ? await res.json() : await res.text();

  if (!res.ok) {
    const msg =
      payload && typeof payload === "object" && "detail" in payload
        ? String((payload as { detail: string }).detail)
        : `${res.status} ${res.statusText}`;
    const conflict =
      payload && typeof payload === "object" && "conflict" in payload
        ? ((payload as { conflict?: TaskEditConflictDetail }).conflict ?? undefined)
        : undefined;
    throw new ApiError(msg, res.status, conflict);
  }

  return payload as T;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  meta: () => request<MetaResponse>("/api/meta"),
  verifyPassword: (userId: string, password: string) =>
    request<{ ok: boolean }>("/api/auth/verify", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, password }),
    }),
  syncStatus: () => request<SyncStatus>("/api/sync/status"),
  iquavisConflicts: () => request<IquavisConflictListResponse>("/api/admin/iquavis-conflicts"),
  members: (dateFrom: string, dateTo: string) =>
    request<MemberView[]>(
      `/api/members?date_from=${encodeURIComponent(dateFrom)}&date_to=${encodeURIComponent(dateTo)}`
    ),
  updateMemberProfile: (
    userId: string,
    payload: {
      operator_user_id: string;
      default_commit_rate: number;
      condition_icon: string;
      formation_role: string;
      formation_position: string;
      formation_x: number;
      formation_y: number;
      display_order: number;
      title_tags: string[];
      special_abilities: string[];
    }
  ) =>
    request<{ status: string }>(`/api/members/${userId}/profile`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateMemberIdentity: (
    userId: string,
    payload: {
      operator_user_id: string;
      nickname: string;
      is_active: boolean;
    }
  ) =>
    request<{ status: string }>(`/api/members/${userId}/identity`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateMemberDayConfig: (
    userId: string,
    payload: {
      operator_user_id: string;
      work_date: string;
      is_vacation: boolean;
      leave_type: string;
      commit_rate_override: number | null;
      note: string;
    }
  ) =>
    request<{ status: string }>(`/api/members/${userId}/day-config`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  tasks: () => request<Task[]>("/api/tasks"),
  task: (taskId: string) => request<Task>(`/api/tasks/${taskId}`),
  createTask: (payload: {
    name: string;
    assignee_user_id: string;
    creator_user_id: string;
    assigned_manhour: number;
    end_date: string;
    parent_task_id: string | null;
    planned_deliverables: Array<{ title: string; url: string }>;
    is_rework: boolean;
    memo: string;
  }) =>
    request<{ status: string }>("/api/tasks/create", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  myTasks: (userId: string) => request<Task[]>(`/api/tasks/my?user_id=${encodeURIComponent(userId)}`),
  todayTasks: (userId: string) =>
    request<Task[]>(`/api/tasks/today?user_id=${encodeURIComponent(userId)}`),
  setTodayToggle: (taskId: string, userId: string, enabled: boolean, expectedRevision?: string) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/today`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, enabled, expected_revision: expectedRevision }),
    }),
  reorderToday: (userId: string, orderedTaskIds: string[], expectedOrderRevision: string) =>
    request<OrderMutationResult>("/api/tasks/today/reorder", {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        ordered_task_ids: orderedTaskIds,
        expected_order_revision: expectedOrderRevision,
      }),
    }),
  moveWbsTask: (taskId: string, userId: string, direction: -1 | 1, expectedOrderRevision: string) =>
    request<OrderMutationResult>(`/api/tasks/${taskId}/wbs/move`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, direction, expected_order_revision: expectedOrderRevision }),
    }),
  reorderWbs: (userId: string, orderedTaskIds: string[], expectedOrderRevision: string) =>
    request<OrderMutationResult>("/api/tasks/wbs/reorder", {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        ordered_task_ids: orderedTaskIds,
        expected_order_revision: expectedOrderRevision,
      }),
    }),
  reorderWbsSiblings: (
    userId: string,
    parentTaskId: string | null,
    orderedTaskIds: string[],
    expectedOrderRevision: string
  ) =>
    request<OrderMutationResult>("/api/tasks/wbs/reorder-siblings", {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        parent_task_id: parentTaskId,
        ordered_task_ids: orderedTaskIds,
        expected_order_revision: expectedOrderRevision,
      }),
    }),
  setMemo: (taskId: string, userId: string, memo: string, expectedRevision?: string) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/memo`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, memo, expected_revision: expectedRevision }),
    }),
  setActualManHour: (taskId: string, userId: string, actualManhour: number, expectedRevision?: string) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/actual-manhour`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, actual_manhour: actualManhour, expected_revision: expectedRevision }),
    }),
  setAssignedManHour: (taskId: string, userId: string, assignedManhour: number, expectedRevision?: string) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/assigned-manhour`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, assigned_manhour: assignedManhour, expected_revision: expectedRevision }),
    }),
  addOutput: (taskId: string, userId: string, title: string, url: string, expectedRevision?: string) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/outputs`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, title, url, expected_revision: expectedRevision }),
    }),
  removeOutput: (taskId: string, userId: string, index: number, expectedRevision?: string) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/outputs/${index}?user_id=${encodeURIComponent(userId)}${expectedRevision ? `&expected_revision=${encodeURIComponent(expectedRevision)}` : ""}`, {
      method: "DELETE",
    }),
  completeTask: (taskId: string, userId: string, expectedRevision?: string) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/complete`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, expected_revision: expectedRevision }),
    }),
  reopenTask: (taskId: string, userId: string, expectedRevision?: string) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/reopen`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, expected_revision: expectedRevision }),
    }),
  splitTask: (
    taskId: string,
    userId: string,
    children: Array<{
      ExistingTaskId?: string;
      ExpectedRevision?: string;
      Name: string;
      AssignedManHour: number;
      ActualManHour: number;
      EndDate: string;
      Memo: string;
      AssigneeUserId: string;
    }>,
    parentChildDependencyMode?: string,
    expectedRevision?: string
  ) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/split`, {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        children,
        parent_child_dependency_mode: parentChildDependencyMode ?? null,
        expected_revision: expectedRevision,
      }),
    }),
  updateTaskChildDependencyMode: (
    taskId: string,
    payload: { user_id: string; child_dependency_mode: string; expected_revision?: string }
  ) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/child-dependency-mode`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),  updateTaskAssignee: (
    taskId: string,
    payload: { user_id: string; assignee_user_id: string; expected_revision?: string }
  ) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/assignee`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateTaskAssigneeAndDueDate: (
    taskId: string,
    payload: { user_id: string; assignee_user_id: string; end_date: string; expected_revision?: string }
  ) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/assignment`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateTaskSchedule: (
    taskId: string,
    payload: { user_id: string; start_date: string; end_date: string; expected_revision?: string }
  ) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/schedule`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateTaskParent: (
    taskId: string,
    payload: { user_id: string; parent_task_id: string | null; expected_revision?: string }
  ) =>
    request<TaskMutationResult>(`/api/tasks/${taskId}/parent`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  bulkUpdateTaskAssignees: (
    userId: string,
    updates: Array<{ task_id: string; assignee_user_id: string; expected_revision?: string }>
  ) =>
    request<BulkTaskAssigneeResponse>("/api/tasks/assignees/bulk", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, updates }),
    }),
  bulkUpdateTaskFields: (
    userId: string,
    parentTaskId: string,
    expectedParentRevision: string,
    updates: Array<{
      task_id: string;
      expected_revision?: string;
      name: string;
      assigned_manhour: number;
      memo: string;
      end_date: string;
      assignee_user_id: string;
    }>
  ) =>
    request<BulkTaskFieldsResponse>("/api/tasks/fields/bulk", {
      method: "POST",
      body: JSON.stringify({
        user_id: userId,
        parent_task_id: parentTaskId,
        expected_parent_revision: expectedParentRevision,
        updates,
      }),
    }),
  addTimerActual: (payload: {
    user_id: string;
    task_id: string;
    started_at_ms: number;
    ended_at_ms: number;
    is_ojt: boolean;
    teacher_user_id: string | null;
  }) => request<TaskMutationResult>("/api/timers/add-actual", { method: "POST", body: JSON.stringify(payload) }),
  sendTimerActualBeacon: (payload: {
    user_id: string;
    task_id: string;
    started_at_ms: number;
    ended_at_ms: number;
    is_ojt: boolean;
    teacher_user_id: string | null;
  }): boolean => {
    const body = JSON.stringify(payload);
    if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function") {
      const blob = new Blob([body], { type: "application/json" });
      return navigator.sendBeacon("/api/timers/add-actual", blob);
    }
    void fetch("/api/timers/add-actual", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    });
    return true;
  },
  todaySummary: (userId: string) =>
    request<TodaySummary>(`/api/today/summary?user_id=${encodeURIComponent(userId)}`),
  taskProgress: (taskId: string) =>
    request<TaskProgressDashboard>(`/api/progress/task-dashboard?task_id=${encodeURIComponent(taskId)}`),
  rankings: (userId: string, yearMonth: string, periodMonths: number) =>
    request<Rankings>(
      `/api/rankings?user_id=${encodeURIComponent(userId)}&year_month=${encodeURIComponent(yearMonth)}&period_months=${encodeURIComponent(String(periodMonths))}`
    ),
  rankingHistory: (userId: string, yearMonth: string, periodMonths: number) =>
    request<RankingHistoryResponse>(
      `/api/rankings/history?user_id=${encodeURIComponent(userId)}&year_month=${encodeURIComponent(yearMonth)}&period_months=${encodeURIComponent(String(periodMonths))}`
    ),
  infeasibility: () => request<InfeasibilityResponse>("/api/admin/infeasibility"),
  reworkTasks: () => request<Task[]>("/api/admin/rework-tasks"),
  setEducationRate: (userId: string, yearMonth: string, rate: number) =>
    request<{ status: string }>("/api/admin/education-rate", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, year_month: yearMonth, rate }),
    }),
  setCrashZone: (value: number) =>
    request<{ status: string }>("/api/admin/crash-zone", {
      method: "POST",
      body: JSON.stringify({ value }),
    }),
  setReworkLink: (reworkTaskId: string, preventedByTaskId: string) =>
    request<{ status: string }>("/api/admin/rework-link", {
      method: "POST",
      body: JSON.stringify({ rework_task_id: reworkTaskId, prevented_by_task_id: preventedByTaskId }),
    }),
  finalizeDailyActuals: (force = false) =>
    request<{ status: string; executed: boolean }>("/api/admin/daily-actual-finalize", {
      method: "POST",
      body: JSON.stringify({ force }),
    }),
  setIquavisSyncExclusion: (operatorUserId: string, rootTaskIds: string[], excluded: boolean) =>
    request<{ status: string; affected_count: number }>("/api/admin/iquavis-sync-exclusion", {
      method: "POST",
      body: JSON.stringify({ operator_user_id: operatorUserId, root_task_ids: rootTaskIds, excluded }),
    }),
  previewIquavisAuthoritativeImport: (
    operatorUserId: string,
    ignoredIquavisIds?: string[],
    ignoredReviewItemKeys?: string[]
  ) =>
    request<IquavisAuthoritativeImportPreviewResponse>("/api/admin/iquavis-authoritative-import/preview", {
      method: "POST",
      body: JSON.stringify({
        operator_user_id: operatorUserId,
        ...(ignoredIquavisIds?.length ? { ignored_iquavis_ids: ignoredIquavisIds } : {}),
        ...(ignoredReviewItemKeys?.length ? { ignored_review_item_keys: ignoredReviewItemKeys } : {}),
      }),
    }),
  applyIquavisAuthoritativeImport: (
    operatorUserId: string,
    expectedWorkbookFingerprint: string,
    ignoredIquavisIds?: string[],
    ignoredReviewItemKeys?: string[]
  ) =>
    request<IquavisAuthoritativeImportApplyResponse>("/api/admin/iquavis-authoritative-import/apply", {
      method: "POST",
      body: JSON.stringify({
        operator_user_id: operatorUserId,
        expected_workbook_fingerprint: expectedWorkbookFingerprint,
        confirmation: "IQUAVIS_TO_QOLONY",
        ...(ignoredIquavisIds?.length ? { ignored_iquavis_ids: ignoredIquavisIds } : {}),
        ...(ignoredReviewItemKeys?.length ? { ignored_review_item_keys: ignoredReviewItemKeys } : {}),
      }),
    }),
  reassignTaskSubtreeAssignee: (taskId: string, operatorUserId: string, assigneeUserId: string) =>
    request<TaskSubtreeAssigneeResult>(`/api/tasks/${taskId}/subtree-assignee`, {
      method: "POST",
      body: JSON.stringify({ operator_user_id: operatorUserId, assignee_user_id: assigneeUserId }),
    }),
  previewIquavisForcedImport: (operatorUserId: string) =>
    request<IquavisForcedImportPreviewResponse>("/api/admin/iquavis-forced-import/preview", {
      method: "POST",
      body: JSON.stringify({ operator_user_id: operatorUserId }),
    }),
  applyIquavisForcedImport: (operatorUserId: string, expectedWorkbookFingerprint: string) =>
    request<IquavisForcedImportApplyResponse>("/api/admin/iquavis-forced-import/apply", {
      method: "POST",
      body: JSON.stringify({
        operator_user_id: operatorUserId,
        expected_workbook_fingerprint: expectedWorkbookFingerprint,
        confirmation: "IQUAVIS_FORCE_TO_QOLONY",
      }),
    }),
  refreshIquavisConflicts: (operatorUserId: string) =>
    request<IquavisConflictRefreshResponse>("/api/admin/iquavis-conflicts/refresh", {
      method: "POST",
      body: JSON.stringify({ operator_user_id: operatorUserId }),
    }),
  resolveIquavisConflict: (
    taskId: string,
    payload: {
      operator_user_id: string;
      direction: IquavisConflictDirection;
      expected_recorded_at: string;
    }
  ) =>
    request<IquavisConflictResolveResponse>(`/api/admin/iquavis-conflicts/${encodeURIComponent(taskId)}/resolve`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  holdIquavisConflict: (
    taskId: string,
    payload: {
      operator_user_id: string;
      hold: boolean;
    }
  ) =>
    request<IquavisConflictHoldResponse>(`/api/admin/iquavis-conflicts/${encodeURIComponent(taskId)}/hold`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};






