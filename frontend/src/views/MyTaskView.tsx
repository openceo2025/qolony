import { useEffect, useMemo, useRef } from "react";
import { Tree } from "react-arborist";
import type { TreeApi } from "react-arborist";
import { IquavisConflictPanel, IquavisSyncChip } from "../components/IquavisConflictPanel";
import type { Task } from "../types";
import type { WbsNode } from "../lib/taskTree";
import { formatDisplayText } from "../lib/displayText";
import { formatEstimateBalance, getEstimateSignalClass, getEstimateSignalLabel } from "../lib/estimateSignal";
import { buildChildMap, compareTask, hasDescendantAssignee, isLeafTask, isTodayOn } from "../lib/taskTree";
import { formatTaskStatus, getTaskMarkerColor, isAbortedTaskStatus, isTerminalTaskStatus } from "../lib/taskStatus";
import { useI18n } from "../lib/i18n";
import { useViewportSize } from "../lib/useViewportSize";
import { useUiStore } from "../store/uiStore";

interface MyTaskViewProps {
  allTasks: Task[];
  selectedUserId: string;
  selectedTaskId: string | null;
  onSelectTask: (taskId: string) => void;
  onOpenDetail: (taskId: string) => void;
  onOpenSplit: (taskId: string) => void;
  onToggleToday: (taskId: string, enabled: boolean) => void;
}

const ROOT_ID = "__root__";

function buildMyTaskTree(allTasks: Task[], selectedUserId: string, showCompleted: boolean): WbsNode[] {
  const taskById = new Map(allTasks.map((task) => [task.Id, task]));
  const includeIds = new Set<string>();

  // 自分担当タスクと、その直系親を辿って表示対象を確定する。
  for (const task of allTasks) {
    if (task.AssigneeUserId !== selectedUserId) continue;

    let cursor: Task | undefined = task;
    while (cursor) {
      includeIds.add(cursor.Id);
      cursor = cursor.ParentTaskId ? taskById.get(cursor.ParentTaskId) : undefined;
    }
  }

  if (includeIds.size === 0) return [];

  const childrenByParent = new Map<string, Task[]>();
  for (const task of allTasks) {
    if (!includeIds.has(task.Id)) continue;
    const key = task.ParentTaskId ?? ROOT_ID;
    const list = childrenByParent.get(key) ?? [];
    list.push(task);
    childrenByParent.set(key, list);
  }

  for (const [key, list] of childrenByParent) {
    childrenByParent.set(key, [...list].sort(compareTask));
  }

  const build = (parentId: string): WbsNode[] => {
    const tasksForParent = childrenByParent.get(parentId) ?? [];
    const nodes: WbsNode[] = [];

    for (const task of tasksForParent) {
      const children = build(task.Id);
      const isVisible = showCompleted || !isTerminalTaskStatus(task.Status);

      if (isVisible) {
        nodes.push({
          id: task.Id,
          name: task.Name,
          task,
          children: children.length > 0 ? children : undefined,
        });
        continue;
      }

      // 完了・中止タスクを非表示にしているときは、子を1段繰り上げて表示する。
      nodes.push(...children);
    }

    return nodes;
  };

  return build(ROOT_ID);
}

export function MyTaskView({
  allTasks,
  selectedUserId,
  selectedTaskId,
  onSelectTask,
  onOpenDetail,
  onOpenSplit,
  onToggleToday,
}: MyTaskViewProps): JSX.Element {
  const { t } = useI18n();
  const myTaskShowCompleted = useUiStore((state) => state.myTaskShowCompleted);
  const setMyTaskShowCompleted = useUiStore((state) => state.setMyTaskShowCompleted);

  const treeData = useMemo(
    () => buildMyTaskTree(allTasks, selectedUserId, myTaskShowCompleted),
    [allTasks, myTaskShowCompleted, selectedUserId]
  );
  const childMap = useMemo(() => buildChildMap(allTasks), [allTasks]);
  const selectedTask = useMemo(
    () => allTasks.find((task) => task.Id === selectedTaskId) ?? null,
    [allTasks, selectedTaskId]
  );
  const size = useViewportSize(360, 320);
  const treeRef = useRef<TreeApi<WbsNode> | undefined>(undefined);

  const handleOpenAll = () => {
    const tree = treeRef.current;
    if (!tree) return;
    tree.openAll();
  };

  const handleCloseAll = () => {
    const tree = treeRef.current;
    if (!tree) return;
    tree.closeAll();
  };

  useEffect(() => {
    if (treeData.length === 0) return;
    // MyTask は遷移のたびに全展開で開始する。
    const timerId = window.setTimeout(() => {
      treeRef.current?.openAll();
    }, 0);
    return () => {
      window.clearTimeout(timerId);
    };
  }, [treeData]);

  return (
    <div className="view-stack">
      <div className="view-hint-row wbs-hint-row">
        <div className="view-hint">{t("my.hint", "Click: active / Double click: Details / Right click: Split")}</div>
        <div className="view-hint-actions wbs-hint-actions">
          <label className="view-filter-toggle">
            <input
              type="checkbox"
              checked={myTaskShowCompleted}
              onChange={(event) => setMyTaskShowCompleted(event.currentTarget.checked)}
            />
            <span>{t("my.show_terminal", "完了・中止タスクを表示")}</span>
          </label>
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

      <IquavisConflictPanel task={selectedTask} />

      {treeData.length === 0 ? <div className="empty">{t("my.empty", "No tasks to display")}</div> : null}

      <div className="wbs-tree-wrap">
        <Tree
          ref={treeRef}
          data={treeData}
          width="100%"
          height={size.height}
          rowHeight={72}
          openByDefault
          selection={selectedTaskId ?? undefined}
          selectionFollowsFocus
          onFocus={(node) => {
            if (node.id !== selectedTaskId) {
              onSelectTask(node.id);
            }
          }}
          overscanCount={8}
          children={({ node, style, dragHandle }) => {
            const task = node.data.task as Task;
            const own = task.AssigneeUserId === selectedUserId;
            const descendantOwn = hasDescendantAssignee(task.Id, selectedUserId, childMap);
            const muted = !own && !descendantOwn;
            const active = selectedTaskId === task.Id;
            const leaf = isLeafTask(task.Id, allTasks);
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
                      <span>{t("my.today_label", "today")}</span>
                    </label>
                  ) : null}
                </div>
              </div>
            );
          }}
        />
      </div>
    </div>
  );
}
