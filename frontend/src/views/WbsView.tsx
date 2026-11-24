import { useEffect, useMemo, useRef, useState } from "react";
import { Tree } from "react-arborist";
import type { TreeApi } from "react-arborist";
import { IquavisConflictPanel, IquavisSyncChip } from "../components/IquavisConflictPanel";
import type { Task, User } from "../types";
import type { WbsNode } from "../lib/taskTree";
import { formatDisplayText } from "../lib/displayText";
import { formatEstimateBalance, getEstimateSignalClass, getEstimateSignalLabel } from "../lib/estimateSignal";
import { useI18n } from "../lib/i18n";
import { isTodayOn } from "../lib/taskTree";
import { formatTaskStatus, getTaskMarkerColor, isAbortedTaskStatus, isTerminalTaskStatus } from "../lib/taskStatus";
import { filterWbsVisibleTasks } from "../lib/taskViewFilters";
import { useViewportSize } from "../lib/useViewportSize";
import {
  buildDescendantAssigneeMap,
  buildLeafTaskIds,
  buildOrderedChildren,
  buildWbsTreeFromOrder,
  moveVisibleNodeInTree,
  moveVisibleSiblingInOrder,
  wbsParentKey,
} from "../lib/wbsLocalOrder";
import { useUiStore } from "../store/uiStore";

interface WbsViewProps {
  tasks: Task[];
  selectedUserId: string;
  selectedTaskId: string | null;
  onSelectTask: (taskId: string) => void;
  onOpenDetail: (taskId: string) => void;
  onOpenSplit: (taskId: string) => void;
  onToggleToday: (taskId: string, enabled: boolean) => void;
  onSaveWbsSiblingOrder: (parentTaskId: string | null, orderedTaskIds: string[]) => void;
  isTopManager: boolean;
  onSetIquavisSyncExclusion: (taskIds: string[], excluded: boolean) => void;
  users: User[];
  onReassignSubtree: (rootTaskId: string, assigneeUserId: string) => void;
}

export function WbsView({
  tasks,
  selectedUserId,
  selectedTaskId,
  onSelectTask,
  onOpenDetail,
  onOpenSplit,
  onToggleToday,
  onSaveWbsSiblingOrder,
  isTopManager,
  onSetIquavisSyncExclusion,
  users,
  onReassignSubtree,
}: WbsViewProps): JSX.Element {
  const { t } = useI18n();
  const wbsOpenState = useUiStore((state) => state.wbsOpenState);
  const setWbsOpenState = useUiStore((state) => state.setWbsOpenState);
  const [showCompletedTasks, setShowCompletedTasks] = useState(false);
  const [showSyncExcludedTasks, setShowSyncExcludedTasks] = useState(false);
  const [selectedSyncTaskIds, setSelectedSyncTaskIds] = useState<Set<string>>(new Set());
  const [subtreeAssigneeUserId, setSubtreeAssigneeUserId] = useState("");
  const taskById = useMemo(() => new Map(tasks.map((task) => [task.Id, task])), [tasks]);
  const orderedChildrenByParentRef = useRef<Map<string, string[]>>(new Map());
  const lastTasksRef = useRef<Task[] | null>(null);
  const saveOrderTimerRefs = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const visibleTasks = useMemo(
    () => filterWbsVisibleTasks(tasks, showCompletedTasks, showSyncExcludedTasks),
    [showCompletedTasks, showSyncExcludedTasks, tasks]
  );
  const visibleTaskIds = useMemo(() => new Set(visibleTasks.map((task) => task.Id)), [visibleTasks]);
  const [treeData, setTreeData] = useState<WbsNode[]>([]);
  const leafTaskIds = useMemo(() => buildLeafTaskIds(tasks), [tasks]);
  const descendantAssigneeById = useMemo(
    () => buildDescendantAssigneeMap(tasks, selectedUserId),
    [selectedUserId, tasks]
  );
  const size = useViewportSize(360, 320);
  const treeRef = useRef<TreeApi<WbsNode> | undefined>(undefined);
  const initializedRef = useRef(false);
  const checkedSyncTaskIds = useMemo(
    () => [...selectedSyncTaskIds].filter((taskId) => visibleTaskIds.has(taskId)),
    [selectedSyncTaskIds, visibleTaskIds]
  );

  const scheduleWbsOrderSave = (parentTaskId: string | null, orderedTaskIds: string[]) => {
    const key = wbsParentKey(parentTaskId);
    const existingTimer = saveOrderTimerRefs.current.get(key);
    if (existingTimer) {
      clearTimeout(existingTimer);
    }
    const timer = setTimeout(() => {
      saveOrderTimerRefs.current.delete(key);
      onSaveWbsSiblingOrder(parentTaskId, orderedTaskIds);
    }, 150);
    saveOrderTimerRefs.current.set(key, timer);
  };

  const moveLocalWbsTask = (taskId: string, direction: -1 | 1) => {
    const target = taskById.get(taskId);
    if (!target) return;

    const moveResult = moveVisibleSiblingInOrder(
      orderedChildrenByParentRef.current,
      target.ParentTaskId,
      taskId,
      direction,
      visibleTaskIds
    );
    if (!moveResult) return;

    orderedChildrenByParentRef.current = moveResult.nextOrderedChildrenByParent;
    setTreeData((current) => {
      const nextTree = moveVisibleNodeInTree(current, target.ParentTaskId, taskId, direction);
      return nextTree ?? buildWbsTreeFromOrder(taskById, moveResult.nextOrderedChildrenByParent, visibleTaskIds);
    });
    scheduleWbsOrderSave(target.ParentTaskId, moveResult.orderedSiblingIds);
  };

  useEffect(() => {
    if (lastTasksRef.current !== tasks) {
      orderedChildrenByParentRef.current = buildOrderedChildren(tasks);
      lastTasksRef.current = tasks;
    }
    setTreeData(buildWbsTreeFromOrder(taskById, orderedChildrenByParentRef.current, visibleTaskIds));
  }, [taskById, tasks, visibleTaskIds]);

  useEffect(
    () => () => {
      for (const timer of saveOrderTimerRefs.current.values()) {
        clearTimeout(timer);
      }
      saveOrderTimerRefs.current.clear();
    },
    []
  );

  useEffect(() => {
    setSelectedSyncTaskIds((current) => {
      const next = new Set([...current].filter((taskId) => visibleTaskIds.has(taskId)));
      return next.size === current.size ? current : next;
    });
  }, [visibleTaskIds]);

  const toggleSyncSelection = (taskId: string, checked: boolean) => {
    setSelectedSyncTaskIds((current) => {
      const next = new Set(current);
      if (checked) {
        next.add(taskId);
      } else {
        next.delete(taskId);
      }
      return next;
    });
  };

  const executeSyncExclusion = (excluded: boolean) => {
    if (checkedSyncTaskIds.length === 0) return;
    onSetIquavisSyncExclusion(checkedSyncTaskIds, excluded);
    setSelectedSyncTaskIds(new Set());
  };

  const handleOpenAll = () => {
    const tree = treeRef.current;
    if (!tree) return;
    tree.openAll();
    setWbsOpenState(tree.openState);
  };

  const handleCloseAll = () => {
    const tree = treeRef.current;
    if (!tree) return;
    tree.closeAll();
    setWbsOpenState({});
  };

  useEffect(() => {
    const tree = treeRef.current;
    if (!tree || initializedRef.current) return;
    initializedRef.current = true;

    const openIds = Object.keys(wbsOpenState).filter((id) => wbsOpenState[id]);
    if (openIds.length === 0) {
      tree.closeAll();
      return;
    }

    tree.closeAll();
    for (const id of openIds) {
      tree.open(id);
    }
  }, [wbsOpenState]);

  return (
    <div className="view-stack">
      <div className="view-hint-row wbs-hint-row">
        <div className="view-hint">{t("wbs.hint", "Click: active / Double click: Details / Right click: Split")}</div>
        <div className="view-hint-actions wbs-hint-actions">
          <label className="view-filter-toggle">
            <input
              type="checkbox"
              checked={showCompletedTasks}
              onChange={(event) => setShowCompletedTasks(event.currentTarget.checked)}
            />
            <span>{t("wbs.show_terminal", "完了・中止タスクを表示")}</span>
          </label>
          {/* 選択タスク配下の担当者をまとめて差し替える。1件ずつ直すと現実的な時間で終わらない。 */}
          <span className="wbs-subtree-assign">
            <select
              value={subtreeAssigneeUserId}
              onChange={(event) => setSubtreeAssigneeUserId(event.currentTarget.value)}
              aria-label={t("wbs.subtree_assignee", "配下タスクの担当者")}
            >
              <option value="">{t("wbs.subtree_assignee", "配下タスクの担当者")}</option>
              {users.map((user) => (
                <option key={user.Id} value={user.Id}>
                  {formatDisplayText(user.Name)}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn small"
              disabled={!selectedTaskId || !subtreeAssigneeUserId}
              onClick={() => {
                if (!selectedTaskId || !subtreeAssigneeUserId) return;
                onReassignSubtree(selectedTaskId, subtreeAssigneeUserId);
              }}
            >
              {t("wbs.assign_subtree", "選択タスク以下を一括担当変更")}
            </button>
          </span>
          {isTopManager ? (
            <>
              <label className="view-filter-toggle">
                <input
                  type="checkbox"
                  checked={showSyncExcludedTasks}
                  onChange={(event) => setShowSyncExcludedTasks(event.currentTarget.checked)}
                />
                <span>{t("wbs.show_sync_excluded", "同期対象外を表示")}</span>
              </label>
              <button
                type="button"
                className="btn small danger"
                disabled={checkedSyncTaskIds.length === 0}
                onClick={() => executeSyncExclusion(true)}
              >
                {t("wbs.exclude_selected_from_sync", "同期対象外にする")}
              </button>
              {showSyncExcludedTasks ? (
                <button
                  type="button"
                  className="btn small"
                  disabled={checkedSyncTaskIds.length === 0}
                  onClick={() => executeSyncExclusion(false)}
                >
                  {t("wbs.restore_selected_sync", "同期対象に戻す")}
                </button>
              ) : null}
            </>
          ) : null}
          <button
            type="button"
            className="hint-icon-btn"
            onClick={handleOpenAll}
            title={t("common.expand_all", "Expand All")}
            aria-label={t("common.expand_all", "Expand All")}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 6h7" />
              <path d="M3 12h5" />
              <path d="M3 18h9" />
              <path d="M17 9v8" />
              <path d="M13 13h8" />
            </svg>
          </button>
          <button
            type="button"
            className="hint-icon-btn"
            onClick={handleCloseAll}
            title={t("common.collapse_all", "Collapse All")}
            aria-label={t("common.collapse_all", "Collapse All")}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M3 6h7" />
              <path d="M3 12h5" />
              <path d="M3 18h9" />
              <path d="M13 13h8" />
            </svg>
          </button>
        </div>
      </div>
      <IquavisConflictPanel task={selectedTaskId ? taskById.get(selectedTaskId) : null} />
      <div className="wbs-tree-wrap">
        <Tree
          ref={treeRef}
          data={treeData}
          width="100%"
          height={size.height}
          rowHeight={72}
          openByDefault={false}
          initialOpenState={wbsOpenState}
          selection={selectedTaskId ?? undefined}
          selectionFollowsFocus
          onToggle={() => {
            const tree = treeRef.current;
            if (!tree) return;
            setWbsOpenState(tree.openState);
          }}
          onFocus={(node) => {
            if (node.id !== selectedTaskId) {
              onSelectTask(node.id);
            }
          }}
          overscanCount={8}
          children={({ node, style, dragHandle }) => {
            const task = node.data.task as Task;
            const own = task.AssigneeUserId === selectedUserId;
            const descendantOwn = descendantAssigneeById.get(task.Id) ?? false;
            const muted = !own && !descendantOwn;
            const active = selectedTaskId === task.Id;
            const leaf = leafTaskIds.has(task.Id);
            const markerColor = getTaskMarkerColor(
              task.Status,
              task.Signal,
              task.EndDate,
              new Date().toISOString().slice(0, 10)
            );
            const estimateSignalLabel = getEstimateSignalLabel(task);

            return (
              <div style={style} ref={dragHandle}>
                <div
                  className={`wbs-row${active ? " active" : ""}${own ? " own" : ""}${muted ? " muted" : ""}`}
                  onClick={() => onSelectTask(task.Id)}
                  onDoubleClick={(event) => {
                    event.preventDefault();
                    event.stopPropagation();
                    onOpenDetail(task.Id);
                  }}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    if (!isTerminalTaskStatus(task.Status)) onOpenSplit(task.Id);
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <div className={`wbs-arrow${node.children && node.children.length > 0 ? "" : " empty"}`}>
                    {node.children && node.children.length > 0 ? (
                      <button
                        type="button"
                        className="icon-btn"
                        onClick={(event) => {
                          event.stopPropagation();
                          node.toggle();
                        }}
                      >
                        {node.isOpen ? "▾" : "▸"}
                      </button>
                    ) : (
                      ""
                    )}
                  </div>
                  <div className="wbs-marker" style={{ backgroundColor: markerColor }} />
                  <div className="wbs-main">
                    <div className="wbs-name">
                      <span>{formatDisplayText(task.Name)}</span>
                      <IquavisSyncChip task={task} />
                      {estimateSignalLabel ? (
                        <span
                          className={`estimate-signal-chip ${getEstimateSignalClass(task)}`}
                          title={task.EstimateSignalMessage ?? undefined}
                        >
                          {estimateSignalLabel}
                        </span>
                      ) : null}
                    </div>
                    <div className="wbs-meta">
                      {t("task_card.meta", "{assignee} | Due:{dueDate} | Estimate:{assigned} min Actual:{actual} min | {progress}%", {
                        assignee: task.AssigneeName,
                        dueDate: task.EndDate,
                        assigned: task.AssignedManHour,
                        actual: task.ActualManHour,
                        progress: task.ProgressRate.toFixed(1),
                      })}
                      {isAbortedTaskStatus(task.Status) ? <> | {formatTaskStatus(task.Status)}</> : null}
                      {" | "}
                      {formatEstimateBalance(task)}
                    </div>
                  </div>
                  {own && leaf && !isTerminalTaskStatus(task.Status) ? (
                    <label className="wbs-today" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={isTodayOn(task)}
                        onChange={(e) => onToggleToday(task.Id, e.currentTarget.checked)}
                      />
                      <span>{t("wbs.today_label", "today")}</span>
                    </label>
                  ) : null}
                  {isTopManager ? (
                    <label className="wbs-sync-toggle" onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selectedSyncTaskIds.has(task.Id)}
                        onChange={(event) => toggleSyncSelection(task.Id, event.currentTarget.checked)}
                      />
                      <span>{t("wbs.sync_select_label", "除外選択")}</span>
                    </label>
                  ) : null}
                  <div className="task-card-order" onClick={(e) => e.stopPropagation()}>
                    <button type="button" onClick={() => moveLocalWbsTask(task.Id, -1)}>
                      ↑
                    </button>
                    <button type="button" onClick={() => moveLocalWbsTask(task.Id, 1)}>
                      ↓
                    </button>
                  </div>
                </div>
              </div>
            );
          }}
        />
      </div>
    </div>
  );
}
