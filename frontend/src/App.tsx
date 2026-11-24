import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { lazy, Suspense } from "react";
import type { ColonyTaskDraft } from "./views/ColonyView";
import { ApiError, api, type TaskMutationResult } from "./api/client";
import { IquavisConflictProvider } from "./components/IquavisConflictPanel";
import { formatDisplayText } from "./lib/displayText";
import {
  buildIquavisConflictMap,
  isPendingIquavisSave,
  patchTaskConflictState,
} from "./lib/iquavisConflict";
import {
  canApplyIquavisAuthoritativeImport,
  formatIquavisAuthoritativeApplyStatus,
  formatIquavisAuthoritativeConfirmation,
  formatIquavisAuthoritativePreviewStatus,
} from "./lib/iquavisAuthoritativeImport";
import {
  canApplyIquavisForcedImport,
  formatForcedImportApplyStatus,
  formatForcedImportConfirmation,
  formatForcedImportPreviewSummary,
} from "./lib/iquavisForcedImport";
import { createTranslator, I18nProvider } from "./lib/i18n";
import { showMessageBox } from "./lib/messageBox";
import {
  applyPendingTaskOverlay,
  type PendingTaskOverlayEntry,
} from "./lib/pendingTaskOverlay";
import { TaskEditConflictPanel } from "./components/TaskEditConflictPanel";
import { resolveForcedTaskEditKind } from "./lib/forcedTaskEdit";
import {
  removeTaskEditConflict,
  upsertTaskEditConflict,
  type TaskEditConflict,
} from "./lib/taskEditConflict";
import { qk } from "./lib/queryKeys";
import { taskOrderRevision } from "./lib/taskOrderRevision";
import {
  enqueueTaskMutation,
  extractMutationWarnings,
  formatMutationFailure,
  rollbackOptimisticTaskInsertion,
  rollbackOptimisticTaskPatch,
} from "./lib/taskMutationFeedback";
import { executeSplitWithPopup } from "./lib/splitExecution";
import { canAccessAdminTab, canAccessTab } from "./lib/tabAccess";
import { applyTodayOrderToTasks, filterSyncVisibleTasks, moveVisibleIdWithinOrderedIds } from "./lib/taskViewFilters";
import {
  shouldRequireSelectionPassword,
} from "./lib/userSelectionAuth";
import {
  applyWbsSiblingOrderToTasks,
  getOrderedWbsSiblingIds,
  wbsParentKey,
} from "./lib/wbsLocalOrder";
import { useUiStore } from "./store/uiStore";
import { AdminView } from "./views/AdminView";
import { GanttView } from "./views/GanttView";
import { MembersView } from "./views/MembersView";
import { MyTaskView } from "./views/MyTaskView";
import { ProgressView } from "./views/ProgressView";
import { RankingView } from "./views/RankingView";
import { SplitView } from "./views/SplitView";
import { TaskDetailView } from "./views/TaskDetailView";
import { TodayView } from "./views/TodayView";
import { WbsView } from "./views/WbsView";
import type {
  BulkTaskFieldsResponse,
  IquavisAuthoritativeImportPreviewResponse,
  IquavisConflictDirection,
  IquavisForcedImportPreviewResponse,
  IquavisConflictRecord,
  TabKey,
  Task,
  User,
} from "./types";

const STORAGE_SELECTED_USER_ID_KEY = "qolony.selected_user_id";
const ColonyView = lazy(() => import("./views/ColonyView").then(module => ({ default: module.ColonyView })));
// 連打中の再取得をまとめる待ち時間。押し心地を落とさない範囲で短く保つ。
const TASK_REFRESH_DEBOUNCE_MS = 400;
const STORAGE_SELECTED_USER_NAME_KEY = "qolony.selected_user_name";

interface TodayOrderSavePayload {
  userId: string;
  orderedTaskIds: string[];
  expectedOrderRevision: string;
}

interface WbsOrderSavePayload {
  userId: string;
  parentTaskId: string | null;
  orderedTaskIds: string[];
  expectedOrderRevision: string;
}

interface OrderSaveState<TPayload> {
  inFlight: boolean;
  latest: TPayload | null;
}

function formatLocalDate(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function currentSunday(): Date {
  const today = new Date();
  const sunday = new Date(today);
  sunday.setDate(today.getDate() - today.getDay());
  return sunday;
}

export default function App(): JSX.Element {
  const queryClient = useQueryClient();
  const [lastSyncStatusAt, setLastSyncStatusAt] = useState<string | null>(null);
  const [unlockedUserIds, setUnlockedUserIds] = useState<Record<string, true>>({});
  const [wbsTaskCacheDirty, setWbsTaskCacheDirty] = useState(false);
  const [refreshingIquavisConflicts, setRefreshingIquavisConflicts] = useState(false);
  const [iquavisAuthoritativeImportBusy, setIquavisAuthoritativeImportBusy] = useState(false);
  const [iquavisAuthoritativeImportPreview, setIquavisAuthoritativeImportPreview] =
    useState<IquavisAuthoritativeImportPreviewResponse | null>(null);
  const [iquavisForcedImportBusy, setIquavisForcedImportBusy] = useState(false);
  const [iquavisForcedImportPreview, setIquavisForcedImportPreview] =
    useState<IquavisForcedImportPreviewResponse | null>(null);
  const [resolvingIquavisTaskIds, setResolvingIquavisTaskIds] = useState<Set<string>>(new Set());
  const [iquavisConflictOverrides, setIquavisConflictOverrides] = useState<
    Map<string, IquavisConflictRecord | null>
  >(new Map());
  const resolvingIquavisTaskIdsRef = useRef<Set<string>>(new Set());
  const taskMutationQueuesRef = useRef<Map<string, Promise<boolean>>>(new Map());
  const taskRefreshTimerRef = useRef<number | null>(null);
  const [taskEditConflicts, setTaskEditConflicts] = useState<TaskEditConflict[]>([]);
  const [resolvingEditConflictIds, setResolvingEditConflictIds] = useState<Set<string>>(new Set());
  // サーバーへ保存中の楽観的変更。再取得がこれを消さないよう、読み出し時に被せ直す。
  const pendingTaskOverlayRef = useRef<Map<string, PendingTaskOverlayEntry>>(new Map());
  const [pendingTaskOverlayVersion, setPendingTaskOverlayVersion] = useState(0);
  const todayOrderSaveQueue = useMemo(() => new Map<string, OrderSaveState<TodayOrderSavePayload>>(), []);
  const wbsOrderSaveQueue = useMemo(() => new Map<string, OrderSaveState<WbsOrderSavePayload>>(), []);

  const {
    selectedUserId,
    selectedTaskId,
    currentTab,
    previousTab,
    rankingYearMonth,
    rankingPeriodMonths,
    splitTemplateIndex,
    statusMessage,
    statusIsError,
    setSelectedUserId,
    setSelectedTaskId,
    setCurrentTab,
    setPreviousTab,
    setRankingYearMonth,
    setRankingPeriodMonths,
    setSplitTemplateIndex,
    setStatus,
  } = useUiStore();

  const healthQuery = useQuery({
    queryKey: qk.health,
    queryFn: api.health,
    refetchInterval: 10_000,
    refetchIntervalInBackground: true,
    retry: false,
  });

  const metaQuery = useQuery({ queryKey: qk.meta, queryFn: api.meta });
  const syncStatusQuery = useQuery({
    queryKey: qk.syncStatus,
    queryFn: api.syncStatus,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
  });
  const iquavisConflictsQuery = useQuery({
    queryKey: qk.iquavisConflicts,
    queryFn: api.iquavisConflicts,
    refetchInterval: 5000,
    refetchIntervalInBackground: true,
  });
  // selectで被せると、再取得の結果がキャッシュへ入った後でも保存中の変更が生き残る。
  const selectTasksWithOverlay = useCallback(
    (data: Task[]) => applyPendingTaskOverlay(data, pendingTaskOverlayRef.current, "all") ?? data,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pendingTaskOverlayVersion]
  );
  const selectMyTasksWithOverlay = useCallback(
    (data: Task[]) => applyPendingTaskOverlay(data, pendingTaskOverlayRef.current, "my") ?? data,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pendingTaskOverlayVersion]
  );
  const selectTodayTasksWithOverlay = useCallback(
    (data: Task[]) =>
      applyPendingTaskOverlay(
        data,
        pendingTaskOverlayRef.current,
        "today",
        queryClient.getQueryData<Task[]>(qk.tasks)
      ) ?? data,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [pendingTaskOverlayVersion, queryClient]
  );

  const tasksQuery = useQuery({
    queryKey: qk.tasks,
    queryFn: api.tasks,
    select: selectTasksWithOverlay,
    refetchInterval: currentTab === "colony" ? 15_000 : false,
  });
  const memberDateRange = useMemo(() => {
    const from = currentSunday();
    const to = new Date(from);
    to.setDate(from.getDate() + 13);
    return { from: formatLocalDate(from), to: formatLocalDate(to) };
  }, []);
  const membersQuery = useQuery({
    queryKey: qk.members(memberDateRange.from, memberDateRange.to),
    queryFn: () => api.members(memberDateRange.from, memberDateRange.to),
  });

  const myTasksQuery = useQuery({
    queryKey: qk.myTasks(selectedUserId),
    queryFn: () => api.myTasks(selectedUserId),
    enabled: selectedUserId.length > 0,
    select: selectMyTasksWithOverlay,
  });

  const todayTasksQuery = useQuery({
    queryKey: qk.todayTasks(selectedUserId),
    queryFn: () => api.todayTasks(selectedUserId),
    enabled: selectedUserId.length > 0,
    select: selectTodayTasksWithOverlay,
  });

  const todaySummaryQuery = useQuery({
    queryKey: qk.todaySummary(selectedUserId),
    queryFn: () => api.todaySummary(selectedUserId),
    enabled: selectedUserId.length > 0,
    refetchInterval: currentTab === "today" ? 5000 : false,
  });

  const progressQuery = useQuery({
    queryKey: qk.taskProgress(selectedTaskId),
    queryFn: () => api.taskProgress(selectedTaskId ?? ""),
    enabled: Boolean(selectedTaskId),
  });

  const rankingsQuery = useQuery({
    queryKey: qk.rankings(selectedUserId, rankingYearMonth, rankingPeriodMonths),
    queryFn: () => api.rankings(selectedUserId, rankingYearMonth, rankingPeriodMonths),
    enabled: selectedUserId.length > 0,
  });
  const rankingHistoryQuery = useQuery({
    queryKey: qk.rankingHistory(selectedUserId, rankingYearMonth, rankingPeriodMonths),
    queryFn: () => api.rankingHistory(selectedUserId, rankingYearMonth, rankingPeriodMonths),
    enabled: selectedUserId.length > 0,
  });

  const infeasibilityQuery = useQuery({ queryKey: qk.infeasibility, queryFn: api.infeasibility });
  const reworkTasksQuery = useQuery({ queryKey: qk.reworkTasks, queryFn: api.reworkTasks });
  const i18n = useMemo(() => createTranslator(metaQuery.data?.ui), [metaQuery.data?.ui]);
  const t = i18n.t;
  const splitExecuteSuccessMessage = i18n.language === "ja" ? "分割を実行しました" : "Split executed";

  const selectUserWithPasswordGuard = async (nextUserId: string, users: User[]): Promise<boolean> => {
    if (!nextUserId) return false;
    const nextUser = users.find((user) => user.Id === nextUserId);
    if (!nextUser) return false;

    const needsPassword = shouldRequireSelectionPassword(nextUser);
    if (!needsPassword) {
      setUnlockedUserIds({});
      setSelectedUserId(nextUserId);
      setSelectedTaskId(null);
      return true;
    }

    const entered = window.prompt(t("app.auth.password_prompt", "{name} password:", { name: nextUser.Name }));
    if (entered === null) {
      setStatus(t("app.auth.password_entry_canceled", "Password entry canceled"), true);
      return false;
    }
    // 照合はサーバーで行う。パスワードは画面に持たない。
    let verified = false;
    try {
      verified = (await api.verifyPassword(nextUserId, entered)).ok;
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error), true);
      return false;
    }
    if (!verified) {
      setStatus(t("app.auth.password_mismatch", "Password mismatch"), true);
      return false;
    }

    setUnlockedUserIds({ [nextUserId]: true });
    setSelectedUserId(nextUserId);
    setSelectedTaskId(null);
    setStatus(t("app.auth.user_selected", "{name} selected", { name: nextUser.Name }), false);
    return true;
  };

  useEffect(() => {
    const firstError =
      metaQuery.error ??
      syncStatusQuery.error ??
      iquavisConflictsQuery.error ??
      membersQuery.error ??
      tasksQuery.error ??
      myTasksQuery.error ??
      todayTasksQuery.error ??
      todaySummaryQuery.error ??
      progressQuery.error ??
      rankingsQuery.error ??
      rankingHistoryQuery.error ??
      infeasibilityQuery.error ??
      reworkTasksQuery.error;
    if (!firstError) return;
    setStatus(firstError instanceof Error ? firstError.message : String(firstError), true);
  }, [
    infeasibilityQuery.error,
    iquavisConflictsQuery.error,
    metaQuery.error,
    membersQuery.error,
    myTasksQuery.error,
    progressQuery.error,
    rankingHistoryQuery.error,
    rankingsQuery.error,
    reworkTasksQuery.error,
    setStatus,
    syncStatusQuery.error,
    tasksQuery.error,
    todaySummaryQuery.error,
    todayTasksQuery.error,
  ]);

  useEffect(() => {
    const syncStatus = syncStatusQuery.data;
    if (!syncStatus?.message || !syncStatus.recorded_at) return;
    if (syncStatus.recorded_at === lastSyncStatusAt) return;
    setLastSyncStatusAt(syncStatus.recorded_at);
    setStatus(syncStatus.message, syncStatus.is_error);
    // バックグラウンド同期で変わったConflict/Pending状態も同時に取り込む。
    void queryClient.invalidateQueries({ queryKey: qk.tasks });
    void queryClient.invalidateQueries({ queryKey: qk.iquavisConflicts });
  }, [lastSyncStatusAt, queryClient, setStatus, syncStatusQuery.data]);

  useEffect(() => {
    const users = metaQuery.data?.users ?? [];
    if (selectedUserId || users.length === 0) return;

    let restoredUserId: string | null = null;
    try {
      const savedUserName = window.localStorage.getItem(STORAGE_SELECTED_USER_NAME_KEY);
      if (savedUserName) {
        const byName = users.find((user) => user.Name === savedUserName);
        if (byName) {
          restoredUserId = byName.Id;
        }
      }

      if (!restoredUserId) {
        const savedUserId = window.localStorage.getItem(STORAGE_SELECTED_USER_ID_KEY);
        if (savedUserId && users.some((user) => user.Id === savedUserId)) {
          restoredUserId = savedUserId;
        }
      }
    } catch {
      // localStorageが使えない環境では既定ユーザーを使う。
    }

    const targetUserId = restoredUserId ?? users[0].Id;
    void selectUserWithPasswordGuard(targetUserId, users).then((selected) => {
      if (selected) return;
      const fallback = users.find((user) => !user.HasPassword);
      if (fallback) {
        setSelectedUserId(fallback.Id);
        setSelectedTaskId(null);
      }
    });
  }, [selectedUserId, metaQuery.data?.users, setSelectedTaskId, setSelectedUserId, setStatus]);

  useEffect(() => {
    if (!selectedUserId) return;
    const users = metaQuery.data?.users ?? [];
    const selected = users.find((user) => user.Id === selectedUserId);
    if (!selected) return;
    try {
      window.localStorage.setItem(STORAGE_SELECTED_USER_ID_KEY, selected.Id);
      window.localStorage.setItem(STORAGE_SELECTED_USER_NAME_KEY, selected.Name);
    } catch {
      // localStorageが使えない環境では永続化を行わない。
    }
  }, [selectedUserId, metaQuery.data?.users]);

  useEffect(() => {
    setIquavisAuthoritativeImportPreview(null);
  }, [selectedUserId]);

  useEffect(() => {
    const tasks = tasksQuery.data ?? [];
    if (!selectedTaskId) return;
    if (!tasks.some((task) => task.Id === selectedTaskId)) {
      setSelectedTaskId(null);
    }
  }, [tasksQuery.data, selectedTaskId, setSelectedTaskId]);

  const invalidateTaskData = () => {
    void queryClient.invalidateQueries({ queryKey: qk.tasks });
    void queryClient.invalidateQueries({ queryKey: qk.iquavisConflicts });
    void queryClient.invalidateQueries({ queryKey: qk.myTasks(selectedUserId) });
    void queryClient.invalidateQueries({ queryKey: qk.todayTasks(selectedUserId) });
    void queryClient.invalidateQueries({ queryKey: qk.todaySummary(selectedUserId) });
    void queryClient.invalidateQueries({ queryKey: ["progress"] });
  };

  const bumpPendingTaskOverlay = useCallback(() => {
    // selectのメモ化を破棄し、保存中の変更を即座に反映し直す。
    setPendingTaskOverlayVersion((current) => current + 1);
  }, []);

  /**
   * 保存中の変更が全部片付いてから1回だけ再取得する。
   *
   * 保存のたびに再取得すると、まだ送信中の変更を含まないサーバー状態でキャッシュが置き換わり、
   * 完了させたタスクが画面へ戻ってしまう。連打中は再取得を先送りする。
   */
  const scheduleTaskDataRefresh = useCallback(() => {
    if (taskRefreshTimerRef.current !== null) {
      window.clearTimeout(taskRefreshTimerRef.current);
    }
    taskRefreshTimerRef.current = window.setTimeout(() => {
      taskRefreshTimerRef.current = null;
      if (taskMutationQueuesRef.current.size > 0) {
        // まだ送信中の変更がある。片付いてから改めて再取得する。
        scheduleTaskDataRefresh();
        return;
      }
      void invalidateAllRef.current().then(() => {
        // 再取得したサーバー状態が保存済みの変更を含むので、被せ物を外す。
        // 送信中のものだけは残し、後から押した変更が消えないようにする。
        let released = false;
        for (const taskId of [...pendingTaskOverlayRef.current.keys()]) {
          if (taskMutationQueuesRef.current.has(taskId)) continue;
          pendingTaskOverlayRef.current.delete(taskId);
          released = true;
        }
        if (released) bumpPendingTaskOverlay();
      });
    }, TASK_REFRESH_DEBOUNCE_MS);
  }, []);

  const invalidateAll = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: qk.tasks }),
      queryClient.invalidateQueries({ queryKey: qk.iquavisConflicts }),
      queryClient.invalidateQueries({ queryKey: qk.myTasks(selectedUserId) }),
      queryClient.invalidateQueries({ queryKey: qk.todayTasks(selectedUserId) }),
      queryClient.invalidateQueries({ queryKey: qk.todaySummary(selectedUserId) }),
      queryClient.invalidateQueries({ queryKey: qk.members(memberDateRange.from, memberDateRange.to) }),
      queryClient.invalidateQueries({ queryKey: ["progress"] }),
      queryClient.invalidateQueries({ queryKey: qk.rankings(selectedUserId, rankingYearMonth, rankingPeriodMonths) }),
      queryClient.invalidateQueries({
        queryKey: qk.rankingHistory(selectedUserId, rankingYearMonth, rankingPeriodMonths),
      }),
      queryClient.invalidateQueries({ queryKey: qk.infeasibility }),
      queryClient.invalidateQueries({ queryKey: qk.reworkTasks }),
    ]);
  };

  const invalidateAllRef = useRef(invalidateAll);
  invalidateAllRef.current = invalidateAll;

  useEffect(() => () => {
    if (taskRefreshTimerRef.current !== null) {
      window.clearTimeout(taskRefreshTimerRef.current);
      taskRefreshTimerRef.current = null;
    }
  }, []);

  const mutation = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => fn(),
  });

  const reloadPage = () => {
    window.location.reload();
  };

  const runAction = (
    fn: () => Promise<unknown>,
    feedbackMessage: string,
    after?: () => void,
    options?: { reload?: boolean; scope?: "all" | "tasks" }
  ) => {
    const shouldReload = options?.reload ?? false;
    const scope = options?.scope ?? "all";
    const refresh = () => {
      if (scope === "tasks") {
        invalidateTaskData();
      } else {
        void invalidateAll();
      }
    };
    mutation.mutate(fn, {
      onSuccess: (result) => {
        refresh();
        if (feedbackMessage) {
          setStatus(feedbackMessage, false);
        }
        after?.();
        const warnings = extractMutationWarnings(result);
        if (warnings.length > 0) {
          showMessageBox(warnings.join("\n"));
        }
        if (shouldReload) {
          reloadPage();
        }
      },
      onError: (error) => {
        refresh();
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
        if (shouldReload) {
          reloadPage();
        }
      },
    });
  };

  const tasks = tasksQuery.data ?? [];
  const iquavisConflictsByTaskId = useMemo(
    () => buildIquavisConflictMap(tasks, iquavisConflictsQuery.data, iquavisConflictOverrides),
    [iquavisConflictOverrides, iquavisConflictsQuery.data, tasks]
  );
  const syncVisibleTasks = useMemo(() => filterSyncVisibleTasks(tasks), [tasks]);
  const syncVisibleTaskIds = useMemo(() => new Set(syncVisibleTasks.map((task) => task.Id)), [syncVisibleTasks]);
  const todayTasks = todayTasksQuery.data ?? [];
  const syncVisibleTodayTasks = useMemo(
    () => todayTasks.filter((task) => syncVisibleTaskIds.has(task.Id) && !task.IQuavisSyncExcluded),
    [syncVisibleTaskIds, todayTasks]
  );
  const users = metaQuery.data?.users ?? [];

  const selectedTask = useMemo(
    () => tasks.find((task) => task.Id === selectedTaskId) ?? null,
    [tasks, selectedTaskId]
  );
  // todayビュー遷移時は未選択なら先頭タスクを自動選択する。
  useEffect(() => {
    if (currentTab !== "today") return;
    if (selectedTaskId) return;
    if (syncVisibleTodayTasks.length === 0) return;
    setSelectedTaskId(syncVisibleTodayTasks[0].Id);
  }, [currentTab, selectedTaskId, setSelectedTaskId, syncVisibleTodayTasks]);
  const isSelectedTaskLeaf = useMemo(() => {
    if (!selectedTask) return false;
    return !tasks.some((task) => task.ParentTaskId === selectedTask.Id);
  }, [tasks, selectedTask]);

  const taskById = useMemo(() => new Map(tasks.map((task) => [task.Id, task])), [tasks]);

  const selectedTaskMaxEndDate = useMemo(() => {
    if (!selectedTask) return null;
    if (selectedTask.ParentTaskId) {
      return taskById.get(selectedTask.ParentTaskId)?.EndDate ?? metaQuery.data?.project?.EndDate ?? null;
    }
    return metaQuery.data?.project?.EndDate ?? null;
  }, [selectedTask, taskById, metaQuery.data?.project?.EndDate]);

  const selectedUser = useMemo(
    () => metaQuery.data?.users.find((u) => u.Id === selectedUserId),
    [metaQuery.data?.users, selectedUserId]
  );
  const allTabs: Array<{ key: TabKey; label: string }> = useMemo(
    () => [
      { key: "wbs", label: t("app.tabs.wbs", "WBS") },
      { key: "gantt", label: t("app.tabs.gantt", "Gantt") },
      { key: "colony", label: t("app.tabs.colony", "Colony") },
      { key: "my", label: t("app.tabs.my", "My task") },
      { key: "today", label: t("app.tabs.today", "Today Tasks") },
      { key: "split", label: t("app.tabs.split", "Split") },
      { key: "detail", label: t("app.tabs.detail", "Task Detail") },
      { key: "progress", label: t("app.tabs.progress", "Progress") },
      { key: "members", label: t("app.tabs.members", "Members") },
      { key: "ranking", label: t("app.tabs.ranking", "Ranking") },
      { key: "admin", label: t("app.tabs.admin", "Admin") },
    ],
    [t]
  );

  const canAccessAdmin = useMemo(
    () => canAccessAdminTab(selectedUser, unlockedUserIds),
    [selectedUser, unlockedUserIds]
  );

  const tabs = useMemo(
    () => allTabs.filter((tab) => canAccessTab(tab.key, selectedUser, unlockedUserIds)),
    [allTabs, selectedUser, unlockedUserIds]
  );

  useEffect(() => {
    if (!canAccessAdmin && currentTab === "admin") {
      setCurrentTab("wbs");
    }
  }, [canAccessAdmin, currentTab, setCurrentTab]);

  useEffect(() => {
    if (currentTab === "wbs" || !wbsTaskCacheDirty) {
      return undefined;
    }

    const timerId = window.setTimeout(() => {
      setWbsTaskCacheDirty(false);
      void queryClient.invalidateQueries({ queryKey: qk.tasks });
    }, 800);

    return () => window.clearTimeout(timerId);
  }, [currentTab, queryClient, wbsTaskCacheDirty]);


  const canEditSelectedTask = useMemo(() => {
    if (!selectedTask || !selectedUser) return false;
    return selectedTask.AssigneeUserId === selectedUser.Id || !!selectedUser.IsTopManager;
  }, [selectedTask, selectedUser]);
  const isSelectedUserTopManager = !!selectedUser?.IsTopManager;

  const refreshIquavisConflicts = () => {
    if (!selectedUserId || !selectedUser?.IsActive || refreshingIquavisConflicts || iquavisAuthoritativeImportBusy) return;
    setRefreshingIquavisConflicts(true);
    setStatus("Excel差分同期を実行しています...", false);
    void api
      .refreshIquavisConflicts(selectedUserId)
      .then(async (result) => {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: qk.iquavisConflicts }),
          queryClient.invalidateQueries({ queryKey: qk.tasks }),
          queryClient.invalidateQueries({ queryKey: qk.myTasks(selectedUserId) }),
          queryClient.invalidateQueries({ queryKey: qk.todayTasks(selectedUserId) }),
          queryClient.invalidateQueries({ queryKey: qk.todaySummary(selectedUserId) }),
          queryClient.invalidateQueries({ queryKey: ["progress"] }),
        ]);
        setStatus(
          `Excel差分同期完了: 競合${result.conflict_count}件 / 保存待ち${result.pending_count}件 / QOLONY反映${result.remote_updates_applied}件 / Excel書出し${result.local_updates_written}件`,
          false
        );
      })
      .catch((error) => {
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
      })
      .finally(() => setRefreshingIquavisConflicts(false));
  };

  const previewIquavisAuthoritativeImport = (ignoredReviewItemKeys: string[]) => {
    if (
      !selectedUserId ||
      !selectedUser?.IsActive ||
      !selectedUser.IsTopManager ||
      iquavisAuthoritativeImportBusy ||
      refreshingIquavisConflicts
    ) {
      return;
    }

    setIquavisAuthoritativeImportBusy(true);
    setIquavisAuthoritativeImportPreview(null);
    setStatus("iQUAVIS→QOLONY 初期取込の事前確認を実行しています...", false);
    void api
      .previewIquavisAuthoritativeImport(selectedUserId, [], ignoredReviewItemKeys)
      .then((result) => {
        setIquavisAuthoritativeImportPreview(result);
        const ready = canApplyIquavisAuthoritativeImport(result);
        setStatus(formatIquavisAuthoritativePreviewStatus(result), !ready);

        if (!ready) {
          const blockers = result.blockers.length > 0 ? result.blockers : ["事前確認が適用可能になりませんでした。"];
          showMessageBox(["iQUAVIS初期取込は安全確認で停止しました。", ...blockers].join("\n"));
        } else if (result.warnings.length > 0) {
          showMessageBox(["事前確認は適用可能です。通常差分を含む確認事項があります。", ...result.warnings].join("\n"));
        }
      })
      .catch((error) => {
        setIquavisAuthoritativeImportPreview(null);
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
      })
      .finally(() => setIquavisAuthoritativeImportBusy(false));
  };

  const applyIquavisAuthoritativeImport = (ignoredReviewItemKeys: string[]) => {
    const preview = iquavisAuthoritativeImportPreview;
    if (
      !selectedUserId ||
      !selectedUser?.IsActive ||
      !selectedUser.IsTopManager ||
      iquavisAuthoritativeImportBusy ||
      refreshingIquavisConflicts ||
      !preview ||
      !canApplyIquavisAuthoritativeImport(preview)
    ) {
      return;
    }

    const confirmed = window.confirm(formatIquavisAuthoritativeConfirmation(preview));
    if (!confirmed) {
      setStatus("iQUAVIS→QOLONY 初期取込をキャンセルしました。", false);
      return;
    }

    setIquavisAuthoritativeImportBusy(true);
    setStatus("iQUAVIS最新値を優先してQOLONYへ安全に反映しています...", false);
    void api
      .applyIquavisAuthoritativeImport(
        selectedUserId,
        preview.workbook_fingerprint,
        [],
        ignoredReviewItemKeys
      )
      .then(async (result) => {
        await invalidateAll();
        setIquavisAuthoritativeImportPreview(null);
        const message = formatIquavisAuthoritativeApplyStatus(result);
        setStatus(message, false);
        showMessageBox(
          [
            message,
            ...(result.backup_path ? [`自動DBバックアップ: ${result.backup_path}`] : []),
            `派生進捗差異 ${result.derived_progress_mismatch_count}件`,
            ...result.warnings,
          ].join("\n")
        );
      })
      .catch((error) => {
        // ブックが変わった可能性を考慮し、失敗後は必ず事前確認からやり直す。
        setIquavisAuthoritativeImportPreview(null);
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
      })
      .finally(() => setIquavisAuthoritativeImportBusy(false));
  };

  const previewIquavisForcedImport = () => {
    if (
      !selectedUserId ||
      !selectedUser?.IsActive ||
      !selectedUser.IsTopManager ||
      iquavisForcedImportBusy ||
      iquavisAuthoritativeImportBusy ||
      refreshingIquavisConflicts
    ) {
      return;
    }

    setIquavisForcedImportBusy(true);
    setIquavisForcedImportPreview(null);
    setStatus("xlsm(iQUAVIS)→QOLONY 強制同期の事前確認を実行しています...", false);
    void api
      .previewIquavisForcedImport(selectedUserId)
      .then((result) => {
        setIquavisForcedImportPreview(result);
        setStatus(formatForcedImportPreviewSummary(result), false);
      })
      .catch((error) => {
        setIquavisForcedImportPreview(null);
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
      })
      .finally(() => setIquavisForcedImportBusy(false));
  };

  const applyIquavisForcedImport = () => {
    const preview = iquavisForcedImportPreview;
    if (
      !selectedUserId ||
      !selectedUser?.IsActive ||
      !selectedUser.IsTopManager ||
      iquavisForcedImportBusy ||
      iquavisAuthoritativeImportBusy ||
      refreshingIquavisConflicts ||
      !preview ||
      !canApplyIquavisForcedImport(preview)
    ) {
      return;
    }

    if (!window.confirm(formatForcedImportConfirmation(preview))) {
      setStatus("xlsm(iQUAVIS)→QOLONY 強制同期をキャンセルしました。", false);
      return;
    }

    setIquavisForcedImportBusy(true);
    setStatus("iQUAVISを正としてQOLONYへ取り込んでいます...", false);
    void api
      .applyIquavisForcedImport(selectedUserId, preview.workbook_fingerprint)
      .then(async (result) => {
        await invalidateAll();
        setIquavisForcedImportPreview(null);
        const message = formatForcedImportApplyStatus(result);
        setStatus(message, false);
        showMessageBox(
          [
            message,
            ...(result.backup_path ? [`自動DBバックアップ: ${result.backup_path}`] : []),
            ...(result.conflict_task_count > 0
              ? [`競合 ${result.conflict_task_count}件は各画面で採択してください。`]
              : []),
          ].join("\n")
        );
      })
      .catch((error) => {
        // ブックが変わった可能性を考慮し、失敗後は必ず事前確認からやり直す。
        setIquavisForcedImportPreview(null);
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
      })
      .finally(() => setIquavisForcedImportBusy(false));
  };

  const resolveIquavisConflict = (taskId: string, direction: IquavisConflictDirection) => {
    if (!selectedUserId || resolvingIquavisTaskIdsRef.current.has(taskId)) return;
    const targetTask = tasks.find((task) => task.Id === taskId);
    if (
      !targetTask ||
      !selectedUser?.IsActive ||
      (targetTask.AssigneeUserId !== selectedUser.Id && !selectedUser.IsTopManager)
    ) {
      return;
    }
    const conflict = iquavisConflictsByTaskId.get(taskId);
    if (!conflict || conflict.sync_state === "PendingIquavisSave" || !conflict.recorded_at) return;

    resolvingIquavisTaskIdsRef.current.add(taskId);
    setResolvingIquavisTaskIds((current) => new Set(current).add(taskId));

    const optimisticState = direction === "qolony_to_iquavis" ? "PendingIquavisSave" : "Linked";
    const pendingMessage =
      direction === "qolony_to_iquavis"
        ? "QOLONYの採用値をExcelへ書き出しました。ExcelからiQUAVISへ保存してください。"
        : "Excel側の値をQOLONYへ反映し、QOLONYの自動マージ結果をExcelへ書き出しました。ExcelからiQUAVISへ保存してください。";
    const optimisticMessage = direction === "qolony_to_iquavis" ? pendingMessage : null;
    const optimisticConflict =
      direction === "qolony_to_iquavis"
        ? { ...conflict, sync_state: optimisticState, message: optimisticMessage ?? "" }
        : null;
    setIquavisConflictOverrides((current) => new Map(current).set(taskId, optimisticConflict));

    const taskCacheKeys = [qk.tasks, qk.myTasks(selectedUserId), qk.todayTasks(selectedUserId)] as const;
    const snapshots = taskCacheKeys.map((queryKey) => ({
      queryKey,
      task: queryClient.getQueryData<Task[]>(queryKey)?.find((item) => item.Id === taskId),
    }));
    for (const { queryKey } of snapshots) {
      queryClient.setQueryData<Task[]>(queryKey, (current) =>
        patchTaskConflictState(current, taskId, optimisticState, optimisticMessage)
      );
    }

    void api
      .resolveIquavisConflict(taskId, {
        operator_user_id: selectedUserId,
        direction,
        expected_recorded_at: conflict.recorded_at,
      })
      .then(async (result) => {
        const pending = result.sync_state === "PendingIquavisSave";
        setIquavisConflictOverrides((current) =>
          new Map(current).set(
            taskId,
            pending
              ? {
                  ...conflict,
                  sync_state: "PendingIquavisSave",
                  message: pendingMessage,
                  recorded_at: result.recorded_at ?? conflict.recorded_at,
                }
              : null
          )
        );
        setStatus(
          pending ? pendingMessage : "Excel側の値をQOLONYへ反映しました。",
          false
        );
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: qk.iquavisConflicts }),
          queryClient.invalidateQueries({ queryKey: qk.tasks }),
          queryClient.invalidateQueries({ queryKey: qk.myTasks(selectedUserId) }),
          queryClient.invalidateQueries({ queryKey: qk.todayTasks(selectedUserId) }),
          queryClient.invalidateQueries({ queryKey: qk.todaySummary(selectedUserId) }),
          queryClient.invalidateQueries({ queryKey: ["progress"] }),
        ]);
        setIquavisConflictOverrides((current) => {
          const next = new Map(current);
          next.delete(taskId);
          return next;
        });
      })
      .catch((error) => {
        for (const snapshot of snapshots) {
          if (!snapshot.task) continue;
          queryClient.setQueryData<Task[]>(snapshot.queryKey, (current) =>
            patchTaskConflictState(
              current,
              taskId,
              snapshot.task?.IQuavisSyncState ?? "Conflict",
              snapshot.task?.IQuavisConflictMessage ?? null
            )
          );
        }
        setIquavisConflictOverrides((current) => {
          const next = new Map(current);
          next.delete(taskId);
          return next;
        });
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
        void queryClient.invalidateQueries({ queryKey: qk.iquavisConflicts });
        void queryClient.invalidateQueries({ queryKey: qk.tasks });
        void queryClient.invalidateQueries({ queryKey: qk.myTasks(selectedUserId) });
        void queryClient.invalidateQueries({ queryKey: qk.todayTasks(selectedUserId) });
      })
      .finally(() => {
        resolvingIquavisTaskIdsRef.current.delete(taskId);
        setResolvingIquavisTaskIds((current) => {
          const next = new Set(current);
          next.delete(taskId);
          return next;
        });
      });
  };

  const holdIquavisConflict = (taskId: string, hold: boolean) => {
    if (!selectedUserId || resolvingIquavisTaskIdsRef.current.has(taskId)) return;
    const targetTask = tasks.find((task) => task.Id === taskId);
    if (
      !targetTask ||
      !selectedUser?.IsActive ||
      (targetTask.AssigneeUserId !== selectedUser.Id && !selectedUser.IsTopManager)
    ) {
      return;
    }

    const conflict = iquavisConflictsByTaskId.get(taskId);
    if (!conflict || isPendingIquavisSave(conflict)) return;
    if (hold && conflict.sync_state !== "Conflict") return;
    if (!hold && conflict.sync_state !== "ConflictHold") return;

    resolvingIquavisTaskIdsRef.current.add(taskId);
    setResolvingIquavisTaskIds((current) => new Set(current).add(taskId));

    const optimisticConflict = hold
      ? {
          ...conflict,
          sync_state: "ConflictHold",
          message: conflict.message || "競合を保留しました",
          recorded_at: conflict.recorded_at,
        }
      : {
          ...conflict,
          sync_state: "Conflict",
          message: conflict.message,
          recorded_at: conflict.recorded_at,
        };
    setIquavisConflictOverrides((current) => new Map(current).set(taskId, optimisticConflict));

    const taskCacheKeys = [qk.tasks, qk.myTasks(selectedUserId), qk.todayTasks(selectedUserId)] as const;
    const snapshots = taskCacheKeys.map((queryKey) => ({
      queryKey,
      task: queryClient.getQueryData<Task[]>(queryKey)?.find((item) => item.Id === taskId),
    }));
    for (const { queryKey } of snapshots) {
      queryClient.setQueryData<Task[]>(queryKey, (current) =>
        patchTaskConflictState(current, taskId, hold ? "ConflictHold" : "Conflict", optimisticConflict.message)
      );
    }

    void api
      .holdIquavisConflict(taskId, {
        operator_user_id: selectedUserId,
        hold,
      })
      .then(async (result) => {
        setStatus(
          hold ? "iQUAVIS競合を保留しました。次回同期は保留対象を外すまで除外されます。" : "iQUAVIS競合の保留を解除しました。",
          false
        );
        if (result.sync_state === "Linked") {
          setIquavisConflictOverrides((current) => {
            const next = new Map(current);
            next.delete(taskId);
            return next;
          });
        } else {
          setIquavisConflictOverrides((current) =>
            new Map(current).set(taskId, {
              ...optimisticConflict,
              sync_state: result.sync_state,
              recorded_at: result.recorded_at ?? null,
              message: result.sync_state === "ConflictHold" ? (conflict.message || "競合を保留しました") : "",
            })
          );
        }

        for (const { queryKey } of snapshots) {
          queryClient.setQueryData<Task[]>(queryKey, (current) =>
            patchTaskConflictState(current, taskId, result.sync_state, result.sync_state === "ConflictHold" ? conflict.message : null)
          );
        }
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: qk.iquavisConflicts }),
          queryClient.invalidateQueries({ queryKey: qk.tasks }),
          queryClient.invalidateQueries({ queryKey: qk.myTasks(selectedUserId) }),
          queryClient.invalidateQueries({ queryKey: qk.todayTasks(selectedUserId) }),
          queryClient.invalidateQueries({ queryKey: qk.todaySummary(selectedUserId) }),
          queryClient.invalidateQueries({ queryKey: ["progress"] }),
        ]);
        setIquavisConflictOverrides((current) => {
          const next = new Map(current);
          if (result.sync_state === "Linked") {
            next.delete(taskId);
          } else {
            next.set(taskId, {
              ...conflict,
              sync_state: result.sync_state,
              recorded_at: result.recorded_at ?? conflict.recorded_at,
              message: result.sync_state === "ConflictHold" ? conflict.message || "競合を保留しました" : "",
            });
          }
          return next;
        });
      })
      .catch((error) => {
        for (const snapshot of snapshots) {
          if (!snapshot.task) continue;
          queryClient.setQueryData<Task[]>(snapshot.queryKey, (current) =>
            patchTaskConflictState(
              current,
              taskId,
              snapshot.task?.IQuavisSyncState ?? "Conflict",
              snapshot.task?.IQuavisConflictMessage ?? null
            )
          );
        }
        setIquavisConflictOverrides((current) => {
          const next = new Map(current);
          next.delete(taskId);
          return next;
        });
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
        void queryClient.invalidateQueries({ queryKey: qk.iquavisConflicts });
        void queryClient.invalidateQueries({ queryKey: qk.tasks });
        void queryClient.invalidateQueries({ queryKey: qk.myTasks(selectedUserId) });
        void queryClient.invalidateQueries({ queryKey: qk.todayTasks(selectedUserId) });
      })
      .finally(() => {
        resolvingIquavisTaskIdsRef.current.delete(taskId);
        setResolvingIquavisTaskIds((current) => {
          const next = new Set(current);
          next.delete(taskId);
          return next;
        });
      });
  };

  const goToTab = (tab: TabKey) => {
    setPreviousTab(currentTab);
    setCurrentTab(tab);
  };

  const openDetail = (taskId: string) => {
    setSelectedTaskId(taskId);
    goToTab("detail");
  };

  const openSplit = (taskId: string) => {
    setSelectedTaskId(taskId);
    goToTab("split");
  };

  const patchTaskInList = (taskList: Task[] | undefined, taskId: string, patch: Partial<Task>) =>
    taskList?.map((task) => (task.Id === taskId ? { ...task, ...patch } : task));

  const runOptimisticTaskAction = (
    taskId: string,
    patch: Partial<Task>,
    request: (expectedRevision: string | undefined) => Promise<TaskMutationResult>,
    successMessage: string,
    options?: {
      removeFromToday?: boolean;
      ensureInToday?: boolean;
      refreshAll?: boolean;
      // 競合パネルの「自分の変更で上書き」からの再送。リビジョン照合を外して先着を上書きする。
      force?: boolean;
    }
  ): Promise<boolean> => {
    const taskCacheKeys = [qk.tasks, qk.myTasks(selectedUserId), qk.todayTasks(selectedUserId)] as const;
    // 競合時に「自分の変更前の値」を見せるため、押した時点の姿を控える。
    const baseTask = queryClient.getQueryData<Task[]>(qk.tasks)?.find((task) => task.Id === taskId) ?? null;
    const snapshots = taskCacheKeys.map((queryKey) => ({
      queryKey,
      tasks: queryClient.getQueryData<Task[]>(queryKey),
    }));
    const applyOptimisticPatch = () => {
      for (const { queryKey } of snapshots) {
        queryClient.setQueryData<Task[]>(queryKey, (current) => {
          if (!current) return current;
          if (options?.removeFromToday && queryKey === taskCacheKeys[2]) {
            return current.filter((task) => task.Id !== taskId);
          }
          if (options?.ensureInToday && queryKey === taskCacheKeys[2] && !current.some((task) => task.Id === taskId)) {
            const source = queryClient.getQueryData<Task[]>(qk.tasks)?.find((task) => task.Id === taskId);
            return source ? [...current, source] : current;
          }
          return patchTaskInList(current, taskId, patch);
        });
      }
    };
    const rollbackOptimisticState = () => {
      for (const snapshot of snapshots) {
        queryClient.setQueryData<Task[]>(snapshot.queryKey, (current) => {
          if (!current) return current;
          const originalIndex = snapshot.tasks?.findIndex((task) => task.Id === taskId) ?? -1;
          const original = originalIndex >= 0 ? snapshot.tasks?.[originalIndex] : undefined;
          if (!original) {
            // Today追加に失敗し、元キャッシュに無かったタスクは通信断でも確実に取り除く。
            if (options?.ensureInToday && snapshot.queryKey === taskCacheKeys[2]) {
              return rollbackOptimisticTaskInsertion(current, taskId);
            }
            return current;
          }

          const currentIndex = current.findIndex((task) => task.Id === taskId);
          if (currentIndex < 0) {
            const insertAt = Math.min(originalIndex, current.length);
            return [...current.slice(0, insertAt), original, ...current.slice(insertAt)];
          }

          return current.map((task) => (
            task.Id === taskId ? rollbackOptimisticTaskPatch(task, original, patch) : task
          ));
        });
      }
    };
    // 保存が終わるまで、再取得の結果へこの変更を被せ直す。
    pendingTaskOverlayRef.current.set(taskId, {
      patch,
      removeFromToday: options?.removeFromToday,
      ensureInToday: options?.ensureInToday,
    });
    bumpPendingTaskOverlay();
    const releasePendingOverlay = () => {
      pendingTaskOverlayRef.current.delete(taskId);
      bumpPendingTaskOverlay();
    };
    applyOptimisticPatch();

    const execute = async (): Promise<boolean> => {
      // 先行リクエストの再取得で消えた後発の楽観表示を、送信直前に復元する。
      applyOptimisticPatch();
      const expectedRevision = options?.force
        ? undefined
        : queryClient.getQueryData<Task[]>(qk.tasks)?.find((task) => task.Id === taskId)?.Revision;
      try {
        const result = await request(expectedRevision);
        if (result.revision) {
          for (const { queryKey } of snapshots) {
            queryClient.setQueryData<Task[]>(queryKey, (current) =>
              patchTaskInList(current, taskId, { Revision: result.revision ?? expectedRevision ?? "" })
            );
          }
        }
        setStatus(successMessage, false);
        setTaskEditConflicts((current) => removeTaskEditConflict(current, taskId));
        const warnings = extractMutationWarnings(result);
        if (warnings.length > 0) showMessageBox(warnings.join("\n"));
        return true;
      } catch (error) {
        // 失敗した変更を再取得結果へ被せ直さないよう、先に外す。
        releasePendingOverlay();
        rollbackOptimisticState();
        // 競合時を含め、キャンセル通知を出す前にサーバーの最新状態へ揃える。
        await invalidateAll();
        if (error instanceof ApiError && error.status === 409 && error.conflict) {
          // 同時編集の衝突は握りつぶさない。誰と何がぶつかったかを出して、本人に選ばせる。
          const detail = error.conflict;
          setTaskEditConflicts((current) =>
            upsertTaskEditConflict(current, {
              taskId,
              taskName: detail.task_name ?? baseTask?.Name ?? taskId,
              base: baseTask,
              patch,
              detail,
              detectedAt: new Date().toISOString(),
            })
          );
          setStatus(error.message, true);
          return false;
        }
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
        return false;
      }
    };

    return enqueueTaskMutation(
      taskMutationQueuesRef.current,
      taskId,
      execute,
      () => {
        releasePendingOverlay();
        rollbackOptimisticState();
      },
      () => {
        // ここでは外さない。再取得がサーバー状態を持ってくるまで被せ続ける。
        scheduleTaskDataRefresh();
      }
    );
  };

  const onToggleToday = (taskId: string, enabled: boolean): Promise<boolean> => {
    if (!selectedUserId) return Promise.resolve(false);
    return runOptimisticTaskAction(
      taskId,
      { TodayOverride: enabled },
      (expectedRevision) => api.setTodayToggle(taskId, selectedUserId, enabled, expectedRevision),
      "today設定を更新しました",
      { removeFromToday: !enabled, ensureInToday: enabled }
    );
  };

  const onMoveTodayTask = (taskId: string, direction: -1 | 1) => {
    if (!selectedUserId) return;
    const userId = selectedUserId;
    const queryKey = qk.todayTasks(userId);
    const currentTodayTasks = queryClient.getQueryData<Task[]>(queryKey) ?? todayTasks;
    const orderedIds = currentTodayTasks.map((task) => task.Id);
    const visibleIds = currentTodayTasks
      .filter((task) => syncVisibleTaskIds.has(task.Id) && !task.IQuavisSyncExcluded)
      .map((task) => task.Id);
    const nextIds = moveVisibleIdWithinOrderedIds(orderedIds, visibleIds, taskId, direction);
    if (!nextIds) return;

    queryClient.setQueryData<Task[]>(queryKey, (currentTasks) =>
      currentTasks ? applyTodayOrderToTasks(currentTasks, nextIds) : currentTasks
    );
    sendTodayOrder(userId, {
      userId,
      orderedTaskIds: nextIds,
      expectedOrderRevision: taskOrderRevision(orderedIds),
    });
  };

  const sendTodayOrder = (key: string, payload: TodayOrderSavePayload) => {
    const state = todayOrderSaveQueue.get(key) ?? { inFlight: false, latest: null };
    if (state.inFlight) {
      state.latest = payload;
      todayOrderSaveQueue.set(key, state);
      return;
    }

    state.inFlight = true;
    state.latest = null;
    todayOrderSaveQueue.set(key, state);

    void api
      .reorderToday(payload.userId, payload.orderedTaskIds, payload.expectedOrderRevision)
      .then((result) => {
        const current = todayOrderSaveQueue.get(key);
        if (!current) return;
        const latest = current.latest;
        if (!latest) {
          todayOrderSaveQueue.delete(key);
          return;
        }
        current.inFlight = false;
        current.latest = null;
        sendTodayOrder(key, { ...latest, expectedOrderRevision: result.order_revision });
      })
      .catch((error) => {
        todayOrderSaveQueue.delete(key);
        void queryClient.invalidateQueries({ queryKey: qk.todayTasks(payload.userId) });
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
      });
  };

  const sendWbsSiblingOrder = (key: string, payload: WbsOrderSavePayload) => {
    const state = wbsOrderSaveQueue.get(key) ?? { inFlight: false, latest: null };

    if (state.inFlight) {
      state.latest = payload;
      wbsOrderSaveQueue.set(key, state);
      return;
    }

    state.inFlight = true;
    state.latest = null;
    wbsOrderSaveQueue.set(key, state);

    void api
      .reorderWbsSiblings(
        payload.userId,
        payload.parentTaskId,
        payload.orderedTaskIds,
        payload.expectedOrderRevision
      )
      .then((result) => {
        const current = wbsOrderSaveQueue.get(key);
        if (!current) return;
        const latest = current.latest;
        if (!latest) {
          wbsOrderSaveQueue.delete(key);
          return;
        }
        current.inFlight = false;
        current.latest = null;
        sendWbsSiblingOrder(key, { ...latest, expectedOrderRevision: result.order_revision });
      })
      .catch((error) => {
        wbsOrderSaveQueue.delete(key);
        invalidateTaskData();
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
      });
  };

  const onSaveWbsSiblingOrder = (parentTaskId: string | null, orderedTaskIds: string[]) => {
    if (!selectedUserId) return;
    const currentTasks = queryClient.getQueryData<Task[]>(qk.tasks) ?? tasks;
    const expectedOrderRevision = taskOrderRevision(getOrderedWbsSiblingIds(currentTasks, parentTaskId));
    queryClient.setQueryData<Task[]>(qk.tasks, (cachedTasks) =>
      cachedTasks ? applyWbsSiblingOrderToTasks(cachedTasks, parentTaskId, orderedTaskIds) : cachedTasks
    );
    setWbsTaskCacheDirty(true);
    sendWbsSiblingOrder(wbsParentKey(parentTaskId), {
      userId: selectedUserId,
      parentTaskId,
      orderedTaskIds,
      expectedOrderRevision,
    });
  };

  const onCommitTimerInterval = async (
    taskId: string,
    startedAtMs: number,
    endedAtMs: number,
    isOjt: boolean,
    teacherUserId: string | null
  ): Promise<boolean> => {
    if (!selectedUserId) return false;
    try {
      const result = await api.addTimerActual({
        user_id: selectedUserId,
        task_id: taskId,
        started_at_ms: startedAtMs,
        ended_at_ms: endedAtMs,
        is_ojt: isOjt,
        teacher_user_id: teacherUserId,
      });
      // 実績反映後のリビジョンを即座に反映し、Done送信を無関係な再取得で待たせない。
      if (result.revision) {
        const taskCacheKeys = [qk.tasks, qk.myTasks(selectedUserId), qk.todayTasks(selectedUserId)] as const;
        for (const queryKey of taskCacheKeys) {
          queryClient.setQueryData<Task[]>(queryKey, (current) =>
            patchTaskInList(current, taskId, { Revision: result.revision ?? "" })
          );
        }
      }
      void invalidateAll();
      return true;
    } catch (error) {
      void invalidateAll();
      setStatus(error instanceof Error ? error.message : String(error), true);
      showMessageBox(formatMutationFailure(error));
      return false;
    }
  };

  const onCommitTimerIntervalBeacon = (
    taskId: string,
    startedAtMs: number,
    endedAtMs: number,
    isOjt: boolean,
    teacherUserId: string | null
  ): void => {
    if (!selectedUserId) return;
    api.sendTimerActualBeacon({
      user_id: selectedUserId,
      task_id: taskId,
      started_at_ms: startedAtMs,
      ended_at_ms: endedAtMs,
      is_ojt: isOjt,
      teacher_user_id: teacherUserId,
    });
  };

  const onCompleteTask = async (taskId: string): Promise<boolean> => {
    if (!selectedUserId) return false;
    return runOptimisticTaskAction(
      taskId,
      { Status: "Completion", ProgressRate: 100 },
      (expectedRevision) => api.completeTask(taskId, selectedUserId, expectedRevision),
      "完了にしました",
      { removeFromToday: true, refreshAll: true }
    );
  };

  const saveTaskMemo = (taskId: string, memo: string) =>
    runOptimisticTaskAction(taskId, { Memo: memo },
      (revision) => api.setMemo(taskId, selectedUserId, memo, revision), "メモを保存しました");

  const saveTaskEstimate = (taskId: string, assignedManHour: number) =>
    runOptimisticTaskAction(taskId, { AssignedManHour: assignedManHour },
      (revision) => api.setAssignedManHour(taskId, selectedUserId, assignedManHour, revision), "見積MHを更新しました");

  const saveTaskDueDate = (taskId: string, endDate: string) => {
    const task = taskById.get(taskId);
    if (!task) return Promise.resolve(false);
    return runOptimisticTaskAction(taskId, { EndDate: endDate, StartDate: endDate < task.StartDate ? endDate : task.StartDate },
      (revision) => api.updateTaskAssigneeAndDueDate(taskId, {
        user_id: selectedUserId, assignee_user_id: task.AssigneeUserId, end_date: endDate, expected_revision: revision,
      }), "期日を更新しました");
  };

  const reopenTask = (taskId: string) =>
    runOptimisticTaskAction(taskId, { Status: "DuringStart", ProgressRate: 0 },
      (revision) => api.reopenTask(taskId, selectedUserId, revision), "完了解除しました", { refreshAll: true });

  const openColonyTaskInToday = async (taskId: string) => {
    if (!syncVisibleTodayTasks.some(task => task.Id === taskId) && !await onToggleToday(taskId, true)) return false;
    const state = useUiStore.getState();
    if (state.currentTab !== "colony" || state.selectedUserId !== selectedUserId) return false;
    setSelectedTaskId(taskId);
    goToTab("today");
    return true;
  };

  const createColonyTask = async (draft: ColonyTaskDraft) => {
    const parent = draft.parentId ? taskById.get(draft.parentId) : null;
    if (!selectedUser?.IsActive || !(draft.parentId
      ? parent && (parent.AssigneeUserId === selectedUserId || selectedUser.IsTopManager)
      : canAccessAdmin)) {
      setStatus("この階層にタスクを作成する権限がありません", true);
      return false;
    }
    try {
      const result = await api.createTask({
        name: draft.name, assignee_user_id: draft.assignee, creator_user_id: selectedUserId,
        assigned_manhour: draft.minutes, end_date: draft.due, parent_task_id: draft.parentId,
        planned_deliverables: [], is_rework: false, memo: draft.memo,
      });
      setStatus("タスクを作成しました", false);
      const warnings = extractMutationWarnings(result);
      if (warnings.length) showMessageBox(warnings.join("\n"));
      await invalidateAll();
      return true;
    } catch (error) {
      void invalidateAll();
      const message = formatMutationFailure(error);
      setStatus(message, true);
      showMessageBox(message);
      return false;
    }
  };

  /** 競合パネルからの再送に使う、リビジョン照合なしのリクエストを作る。 */
  const buildForcedTaskEditRequest = (
    patch: Partial<Task>,
    taskId: string,
    userId: string
  ): ((expectedRevision: string | undefined) => Promise<TaskMutationResult>) | null => {
    const kind = resolveForcedTaskEditKind(patch);
    switch (kind) {
      case "complete":
        return () => api.completeTask(taskId, userId, undefined);
      case "reopen":
        return () => api.reopenTask(taskId, userId, undefined);
      case "memo":
        return () => api.setMemo(taskId, userId, patch.Memo ?? "", undefined);
      case "schedule":
        return () =>
          api.updateTaskSchedule(taskId, {
            user_id: userId,
            start_date: patch.StartDate ?? "",
            end_date: patch.EndDate ?? "",
          });
      case "actual_manhour":
        return () => api.setActualManHour(taskId, userId, Number(patch.ActualManHour ?? 0), undefined);
      case "assigned_manhour":
        return () => api.setAssignedManHour(taskId, userId, Number(patch.AssignedManHour ?? 0), undefined);
      case "today_on":
        return () => api.setTodayToggle(taskId, userId, true, undefined);
      case "today_off":
        return () => api.setTodayToggle(taskId, userId, false, undefined);
      default:
        return null;
    }
  };

  /** 選択タスク配下の担当者をまとめて差し替える。 */
  const onReassignSubtreeAssignee = (rootTaskId: string, assigneeUserId: string) => {
    if (!selectedUserId) return;
    const root = tasks.find((task) => task.Id === rootTaskId);
    const assignee = users.find((user) => user.Id === assigneeUserId);
    if (!root || !assignee) return;
    if (
      !window.confirm(
        `「${root.Name}」とその配下すべての担当者を ${assignee.Name} に変更します。\n` +
          "完了・中止タスクは変更しません。\n\n実行しますか？"
      )
    ) {
      return;
    }
    void api
      .reassignTaskSubtreeAssignee(rootTaskId, selectedUserId, assigneeUserId)
      .then(async (result) => {
        await invalidateAll();
        const skipped: string[] = [];
        if (result.skipped_completed > 0) skipped.push(`完了 ${result.skipped_completed}件`);
        if (result.skipped_aborted > 0) skipped.push(`中止 ${result.skipped_aborted}件`);
        if (result.skipped_not_owned > 0) skipped.push(`自分の担当外 ${result.skipped_not_owned}件`);
        setStatus(
          `担当者を ${result.assignee_name} へ変更: ${result.changed}件` +
            (result.unchanged > 0 ? ` / 変更なし ${result.unchanged}件` : "") +
            (skipped.length > 0 ? ` / 対象外 ${skipped.join("・")}` : ""),
          false
        );
        if (!result.is_top_manager && result.skipped_not_owned > 0) {
          showMessageBox(
            `自分が担当ではないタスク ${result.skipped_not_owned}件は変更していません。
` +
              "他の人の担当分もまとめて変えるには、Top Managerに実行してもらってください。"
          );
        }
      })
      .catch((error) => {
        setStatus(error instanceof Error ? error.message : String(error), true);
        showMessageBox(formatMutationFailure(error));
      });
  };

  const setEditConflictBusy = (taskId: string, busy: boolean) => {
    setResolvingEditConflictIds((current) => {
      const next = new Set(current);
      if (busy) next.add(taskId);
      else next.delete(taskId);
      return next;
    });
  };

  /** 相談のうえ自分の変更を通すと決めたとき。リビジョン照合を外して送り直す。 */
  const onForceOwnTaskEdit = (taskId: string) => {
    const conflict = taskEditConflicts.find((item) => item.taskId === taskId);
    if (!conflict || !selectedUserId || resolvingEditConflictIds.has(taskId)) return;
    const request = buildForcedTaskEditRequest(conflict.patch, taskId, selectedUserId);
    if (!request) {
      setStatus("この変更は上書き送信に対応していません。画面を再読み込みしてやり直してください。", true);
      return;
    }
    setEditConflictBusy(taskId, true);
    void runOptimisticTaskAction(taskId, conflict.patch, request, "自分の変更で上書きしました", {
      force: true,
      removeFromToday: conflict.patch.Status === "Completion",
    }).finally(() => setEditConflictBusy(taskId, false));
  };

  /** 相手の変更を残すと決めたとき。自分の変更は捨て、サーバーの最新状態に合わせる。 */
  const onAcceptOtherTaskEdit = (taskId: string) => {
    if (resolvingEditConflictIds.has(taskId)) return;
    setEditConflictBusy(taskId, true);
    setTaskEditConflicts((current) => removeTaskEditConflict(current, taskId));
    void invalidateAll()
      .then(() => setStatus("相手の変更を採用しました。", false))
      .finally(() => setEditConflictBusy(taskId, false));
  };

  const onUpdateTaskSchedule = async (
    taskId: string,
    startDate: string,
    endDate: string
  ): Promise<boolean> => {
    if (!selectedUserId) return false;
    return runOptimisticTaskAction(
      taskId,
      { StartDate: startDate, EndDate: endDate },
      (expectedRevision) => api.updateTaskSchedule(taskId, {
        user_id: selectedUserId,
        start_date: startDate,
        end_date: endDate,
        expected_revision: expectedRevision,
      }),
      t("gantt.update_saved", "Schedule saved")
    );
  };

  const onFinalizeDailyActuals = async (force: boolean): Promise<void> => {
    try {
      const result = await api.finalizeDailyActuals(force);
      void invalidateAll();
      setStatus(
        result.executed
          ? t("admin.button_finalize_daily_actuals", "Finalize Daily Actuals")
          : t("admin.already_finalized", "Already finalized for today"),
        false
      );
    } catch (error) {
      void invalidateAll();
      setStatus(error instanceof Error ? error.message : String(error), true);
      showMessageBox(formatMutationFailure(error));
    }
  };

  const onBulkUpdateTaskFields = async (
    updates: Array<{
      task_id: string;
      expected_revision?: string;
      name: string;
      assigned_manhour: number;
      memo: string;
      end_date: string;
      assignee_user_id: string;
    }>,
    parentTaskId: string,
    expectedParentRevision: string
  ): Promise<BulkTaskFieldsResponse> => {
    if (!selectedUserId || updates.length === 0) {
      return { status: "ok", changed: 0, failed: 0, results: [] };
    }
    try {
      const result = await api.bulkUpdateTaskFields(
        selectedUserId,
        parentTaskId,
        expectedParentRevision,
        updates
      );
      await invalidateAll();
      setStatus(
        result.failed > 0
          ? `既存タスク変更: ${result.changed}件成功 / ${result.failed}件失敗`
          : `既存タスクを${result.changed}件更新しました`,
        result.failed > 0
      );
      return result;
    } catch (error) {
      await invalidateAll();
      setStatus(error instanceof Error ? error.message : String(error), true);
      throw error;
    }
  };

  const onUpdateMemberProfile = (
    memberId: string,
    payload: {
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
  ) => {
    if (!selectedUserId) return;
    runAction(
      () => api.updateMemberProfile(memberId, { operator_user_id: selectedUserId, ...payload }),
      "メンバー情報を更新しました",
      undefined,
      { reload: false }
    );
  };

  const onUpdateMemberIdentity = (
    memberId: string,
    payload: {
      nickname: string;
      is_active: boolean;
    }
  ) => {
    if (!selectedUserId) return;
    runAction(
      () => api.updateMemberIdentity(memberId, { operator_user_id: selectedUserId, ...payload }),
      "メンバー情報を更新しました",
      undefined,
      { reload: false }
    );
  };

  const onUpdateMemberDayConfig = (
    memberId: string,
    payload: {
      work_date: string;
      is_vacation: boolean;
      leave_type: string;
      commit_rate_override: number | null;
      note: string;
    }
  ) => {
    if (!selectedUserId) return;
    runAction(
      () => api.updateMemberDayConfig(memberId, { operator_user_id: selectedUserId, ...payload }),
      "日次メンバー設定を更新しました",
      undefined,
      { reload: false }
    );
  };

  const renderView = () => {
    if (!canAccessAdmin && currentTab === "admin") {
      return (
        <WbsView
          users={users}
          onReassignSubtree={onReassignSubtreeAssignee}
          tasks={tasks}
          selectedUserId={selectedUserId}
          selectedTaskId={selectedTaskId}
          onSelectTask={setSelectedTaskId}
          onOpenDetail={openDetail}
          onOpenSplit={openSplit}
          onToggleToday={onToggleToday}
          onSaveWbsSiblingOrder={onSaveWbsSiblingOrder}
          isTopManager={isSelectedUserTopManager}
          onSetIquavisSyncExclusion={(taskIds, excluded) =>
            runAction(
              () => api.setIquavisSyncExclusion(selectedUserId, taskIds, excluded),
              excluded ? "選択タスクをiQUAVIS同期対象外にしました" : "選択タスクをiQUAVIS同期対象に戻しました",
              undefined,
              { scope: "tasks" }
            )
          }
        />
      );
    }

    if (currentTab === "wbs") {
      return (
        <WbsView
          users={users}
          onReassignSubtree={onReassignSubtreeAssignee}
          tasks={tasks}
          selectedUserId={selectedUserId}
          selectedTaskId={selectedTaskId}
          onSelectTask={setSelectedTaskId}
          onOpenDetail={openDetail}
          onOpenSplit={openSplit}
          onToggleToday={onToggleToday}
          onSaveWbsSiblingOrder={onSaveWbsSiblingOrder}
          isTopManager={isSelectedUserTopManager}
          onSetIquavisSyncExclusion={(taskIds, excluded) =>
            runAction(
              () => api.setIquavisSyncExclusion(selectedUserId, taskIds, excluded),
              excluded ? "選択タスクをiQUAVIS同期対象外にしました" : "選択タスクをiQUAVIS同期対象に戻しました",
              undefined,
              { scope: "tasks" }
            )
          }
        />
      );
    }

    if (currentTab === "gantt") {
      return (
        <GanttView
          tasks={syncVisibleTasks}
          selectedUserId={selectedUserId}
          selectedTaskId={selectedTaskId}
          onSelectTask={setSelectedTaskId}
          onToggleToday={onToggleToday}
          onUpdateSchedule={onUpdateTaskSchedule}
        />
      );
    }

    if (currentTab === "colony") {
      return (
        <Suspense fallback={<div className="empty">巣の外を読み込み中…</div>}>
          <ColonyView
            tasks={tasks}
            users={users}
            members={membersQuery.data ?? []}
            project={metaQuery.data?.project}
            user={selectedUser}
            timer={todaySummaryQuery.data?.ActiveTimer ?? null}
            selectedId={selectedTaskId}
            onSelect={setSelectedTaskId}
            onOpenDetail={openDetail}
            onOpenSplit={openSplit}
            onOpenToday={openColonyTaskInToday}
            onComplete={onCompleteTask}
            onReopen={reopenTask}
            onSaveMemo={saveTaskMemo}
            onSaveEstimate={saveTaskEstimate}
            onSaveDue={saveTaskDueDate}
            onCreate={createColonyTask}
            onRefresh={() => void invalidateAll()}
          />
        </Suspense>
      );
    }

    if (currentTab === "my") {
      return (
        <MyTaskView
          allTasks={syncVisibleTasks}
          selectedUserId={selectedUserId}
          selectedTaskId={selectedTaskId}
          onSelectTask={setSelectedTaskId}
          onOpenDetail={openDetail}
          onOpenSplit={openSplit}
          onToggleToday={onToggleToday}
        />
      );
    }

    if (currentTab === "today") {
      return (
        <TodayView
          selectedTaskId={selectedTaskId}
          selectedTask={selectedTask}
          todayTasks={syncVisibleTodayTasks}
          users={users}
          currentUserId={selectedUserId}
          onSelectTask={setSelectedTaskId}
          onOpenDetail={openDetail}
          onOpenSplit={openSplit}
          onToggleToday={onToggleToday}
          onMoveTask={onMoveTodayTask}
          onCommitTimerInterval={onCommitTimerInterval}
          onCommitTimerIntervalBeacon={onCommitTimerIntervalBeacon}
          onCompleteTask={onCompleteTask}
        />
      );
    }

    if (currentTab === "split") {
      return (
        <SplitView
          parentTask={selectedTask}
          allTasks={syncVisibleTasks}
          users={users}
          canEdit={canEditSelectedTask}
          currentUserId={selectedUserId}
          isCurrentUserTopManager={isSelectedUserTopManager}
          templates={metaQuery.data?.split_templates ?? []}
          templateIndex={splitTemplateIndex}
          setTemplateIndex={setSplitTemplateIndex}
          onExecute={async (children, parentChildDependencyMode, expectedParentRevision) => {
            if (!selectedTask || !selectedUserId) return { saved: false, conflict: false };
            return executeSplitWithPopup({
              execute: () =>
                api.splitTask(
                  selectedTask.Id,
                  selectedUserId,
                  children,
                  parentChildDependencyMode,
                  expectedParentRevision
                ),
              invalidateAll,
              showResult: showMessageBox,
              setCurrentTab,
              previousTab,
              successMessage: splitExecuteSuccessMessage,
              conflictRecoveryMessage: t(
                "split.conflict_draft_cancelled",
                "The stale draft was cancelled. Review the latest values before editing again."
              ),
            });
          }}
          onUpdateExistingRows={onBulkUpdateTaskFields}
          showExecutionMessage={showMessageBox}
        />
      );
    }

    if (currentTab === "detail") {
      return (
        <TaskDetailView
          task={selectedTask}
          allTasks={syncVisibleTasks}
          users={users}
          maxEndDate={selectedTaskMaxEndDate}
          canEdit={canEditSelectedTask}
          isLeafTask={isSelectedTaskLeaf}
          onSaveMemo={saveTaskMemo}
          onUpdateAssignedManHour={saveTaskEstimate}
          onUpdateAssignee={(taskId, assigneeUserId) => {
            const assigneeName = users.find((user) => user.Id === assigneeUserId)?.Name ?? "";
            runOptimisticTaskAction(
              taskId,
              { AssigneeUserId: assigneeUserId, AssigneeName: assigneeName },
              (expectedRevision) =>
                api.updateTaskAssignee(taskId, {
                  user_id: selectedUserId,
                  assignee_user_id: assigneeUserId,
                  expected_revision: expectedRevision,
                }),
              "担当者を更新しました"
            );
          }}
          onUpdateChildDependencyMode={(taskId, childDependencyMode) =>
            runOptimisticTaskAction(
              taskId,
              { ChildDependencyMode: childDependencyMode },
              (expectedRevision) =>
                api.updateTaskChildDependencyMode(taskId, {
                  user_id: selectedUserId,
                  child_dependency_mode: childDependencyMode,
                  expected_revision: expectedRevision,
                }),
              "依存モードを更新しました"
            )
          }
          onUpdateDueDate={saveTaskDueDate}
          onUpdateParent={(taskId, parentTaskId) =>
            runOptimisticTaskAction(
              taskId,
              { ParentTaskId: parentTaskId },
              (expectedRevision) =>
                api.updateTaskParent(taskId, {
                  user_id: selectedUserId,
                  parent_task_id: parentTaskId,
                  expected_revision: expectedRevision,
                }),
              "親タスクを更新しました"
            )
          }
          onAddOutput={(taskId, title, url) => {
            const outputs = taskById.get(taskId)?.OutputDeliverables ?? [];
            runOptimisticTaskAction(
              taskId,
              { OutputDeliverables: [...outputs, { title, url }] },
              (expectedRevision) => api.addOutput(taskId, selectedUserId, title, url, expectedRevision),
              "成果物を追加しました"
            );
          }}
          onRemoveOutput={(taskId, index) => {
            const outputs = taskById.get(taskId)?.OutputDeliverables ?? [];
            runOptimisticTaskAction(
              taskId,
              { OutputDeliverables: outputs.filter((_, outputIndex) => outputIndex !== index) },
              (expectedRevision) => api.removeOutput(taskId, selectedUserId, index, expectedRevision),
              "成果物を削除しました"
            );
          }}
          onComplete={(taskId) => {
            void onCompleteTask(taskId);
          }}
          onReopen={reopenTask}
          onOpenSplit={openSplit}
        />
      );
    }

    if (currentTab === "members") {
      return (
        <MembersView
          members={membersQuery.data ?? []}
          selectedUserId={selectedUserId}
          isTopManager={isSelectedUserTopManager}
          onUpdateProfile={onUpdateMemberProfile}
          onUpdateIdentity={onUpdateMemberIdentity}
          onUpdateDayConfig={onUpdateMemberDayConfig}
        />
      );
    }

    if (currentTab === "progress") {
      return (
        <ProgressView
          tasks={syncVisibleTasks}
          selectedTaskId={selectedTaskId}
          onSelectTask={setSelectedTaskId}
          dashboard={progressQuery.data}
        />
      );
    }

    if (currentTab === "ranking") {
      return (
        <RankingView
          rankings={rankingsQuery.data}
          history={rankingHistoryQuery.data}
          isTopManager={Boolean(selectedUser?.IsTopManager)}
          yearMonth={rankingYearMonth}
          periodMonths={rankingPeriodMonths}
          onChangeYearMonth={setRankingYearMonth}
          onChangePeriodMonths={setRankingPeriodMonths}
        />
      );
    }

    return (
      <AdminView
        users={metaQuery.data?.users ?? []}
        topManagerId={metaQuery.data?.top_manager_id ?? ""}
        selectedUserId={selectedUserId}
        crashZonePct={metaQuery.data?.project?.CrashZonePct ?? 20}
        infeasibility={infeasibilityQuery.data}
        reworkTasks={reworkTasksQuery.data ?? []}
        allTasks={tasks}
        rankingYearMonth={rankingYearMonth}
        iquavisAuthoritativeImportPreview={iquavisAuthoritativeImportPreview}
        iquavisAuthoritativeImportBusy={iquavisAuthoritativeImportBusy}
        onPreviewIquavisAuthoritativeImport={previewIquavisAuthoritativeImport}
        onApplyIquavisAuthoritativeImport={applyIquavisAuthoritativeImport}
        iquavisForcedImportPreview={iquavisForcedImportPreview}
        iquavisForcedImportBusy={iquavisForcedImportBusy}
        onPreviewIquavisForcedImport={previewIquavisForcedImport}
        onApplyIquavisForcedImport={applyIquavisForcedImport}
        onSetEducationRate={({ userId, yearMonth, rate }) =>
          runAction(() => api.setEducationRate(userId, yearMonth, rate), "教育比率を更新しました")
        }
        onSetCrashZone={(value) => runAction(() => api.setCrashZone(value), "CrashZoneを更新しました")}
        onSetReworkLink={({ reworkTaskId, preventedByTaskId }) =>
          runAction(() => api.setReworkLink(reworkTaskId, preventedByTaskId), "手戻り紐付けを更新しました")
        }
        onFinalizeDailyActuals={onFinalizeDailyActuals}
        onCreateRootTask={({ name, assigneeUserId, assignedManhour, endDate, memo }) =>
          runAction(
            () =>
              api.createTask({
                name,
                assignee_user_id: assigneeUserId,
                creator_user_id: selectedUserId,
                assigned_manhour: assignedManhour,
                end_date: endDate,
                parent_task_id: null,
                planned_deliverables: [],
                is_rework: false,
                memo,
              }),
            "ルートタスクを作成しました"
          )
        }
        onUpdateRootTaskAssignedManhour={({ taskId, assignedManhour }) =>
          runOptimisticTaskAction(
            taskId,
            { AssignedManHour: assignedManhour },
            (expectedRevision) => api.setAssignedManHour(taskId, selectedUserId, assignedManhour, expectedRevision),
            "トップ見積MHを更新しました"
          )
        }
      />
    );
  };

  const loading = metaQuery.isLoading || tasksQuery.isLoading || membersQuery.isLoading;
  const isServerDown = healthQuery.isError;

  return (
    <I18nProvider value={i18n}>
      <IquavisConflictProvider
        value={{
          conflictsByTaskId: iquavisConflictsByTaskId,
          resolvingTaskIds: resolvingIquavisTaskIds,
          canResolveTask: (task) =>
            Boolean(
              selectedUser?.IsActive &&
              (task.AssigneeUserId === selectedUser.Id || selectedUser.IsTopManager)
            ),
          onResolve: resolveIquavisConflict,
          onHold: (taskId, hold) => holdIquavisConflict(taskId, hold),
        }}
      >
        <div className={currentTab === "colony" ? "app-shell app-shell-colony" : "app-shell"}>
          <header className="header">
            <div className="brand">QOLONY</div>
            <div className="header-user">
              {metaQuery.data?.iquavis?.available !== false ? (
                <button
                  type="button"
                  className="header-sync-button"
                  disabled={
                    isServerDown ||
                    !selectedUser?.IsActive ||
                    refreshingIquavisConflicts ||
                    iquavisAuthoritativeImportBusy
                  }
                  onClick={refreshIquavisConflicts}
                >
                  {refreshingIquavisConflicts ? "差分同期中..." : "Excel差分同期"}
                </button>
              ) : null}
              <select
                value={selectedUserId}
                disabled={isServerDown || iquavisAuthoritativeImportBusy}
                onChange={(e) => {
                  const users = metaQuery.data?.users ?? [];
                  void selectUserWithPasswordGuard(e.currentTarget.value, users);
                }}
              >
                {users.map((user) => (
                  <option key={user.Id} value={user.Id}>
                    {user.Name}
                  </option>
                ))}
              </select>
            </div>
          </header>

          <nav className="tab-row">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                className={`tab-btn${tab.key === currentTab ? " active" : ""}`}
                type="button"
                onClick={() => goToTab(tab.key)}
                disabled={isServerDown}
              >
                {tab.label}
              </button>
            ))}
          </nav>

          <main className="content">
            {/* 同時編集の競合はどのタブにいても見えないと困るので、内容の手前に出す。 */}
            <TaskEditConflictPanel
              conflicts={taskEditConflicts}
              busyTaskIds={resolvingEditConflictIds}
              onForceMine={onForceOwnTaskEdit}
              onAcceptTheirs={onAcceptOtherTaskEdit}
            />
            {loading ? (
              <div className="empty">loading...</div>
            ) : (
              renderView()
            )}
          </main>

          <footer className={`status${statusIsError ? " error" : ""}`}>
            {statusMessage}
            {selectedUser && currentTab !== "wbs" && currentTab !== "my" && currentTab !== "today" ? (
              <span className="status-meta"> / {t("app.footer.user", "user")}: {selectedUser.Name}</span>
            ) : null}
            {selectedTask && (currentTab === "today" || currentTab === "detail" || currentTab === "split") ? (
              <span className="status-meta"> / {t("app.footer.task", "task")}: {formatDisplayText(selectedTask.Name)}</span>
            ) : null}
          </footer>
        </div>
        {isServerDown ? (
          <div className="server-down-overlay" role="alert" aria-live="assertive">
            <div className="server-down-modal">{t("app.server_down", "Server is not running")}</div>
          </div>
        ) : null}
      </IquavisConflictProvider>
    </I18nProvider>
  );
}
