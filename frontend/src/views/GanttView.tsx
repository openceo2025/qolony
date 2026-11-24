import { useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Gantt, ViewMode } from "gantt-task-react";
import type { Task as GanttTask } from "gantt-task-react";
import "gantt-task-react/dist/index.css";
import { IquavisConflictPanel, IquavisSyncChip } from "../components/IquavisConflictPanel";
import { formatDisplayText } from "../lib/displayText";
import { formatEstimateBalance, getEstimateSignalClass, getEstimateSignalLabel } from "../lib/estimateSignal";
import { calculateGanttAutoScrollDelta } from "../lib/ganttAutoScroll";
import { useI18n } from "../lib/i18n";
import { findTimelineScrollLeftForDate } from "../lib/ganttTimeline";
import { formatGanttTaskRange } from "../lib/ganttViewModel";
import { applyGanttTaskDateChange, buildGanttSchedulePayload } from "../lib/ganttSchedule";
import {
  buildGanttTaskDepthMap,
  filterGanttTasksByCompletedDepth,
  parseCompletedTaskDepthCutoff,
  readCompletedTaskDepthCutoff,
  writeCompletedTaskDepthCutoff,
  type CompletedTaskDepthCutoff,
} from "../lib/ganttVisibility";
import { buildTree, isTodayOn, type WbsNode } from "../lib/taskTree";
import { isTerminalTaskStatus } from "../lib/taskStatus";
import { useUiStore } from "../store/uiStore";
import type { Task as DomainTask } from "../types";

interface GanttViewProps {
  tasks: DomainTask[];
  selectedUserId: string;
  selectedTaskId: string | null;
  onSelectTask: (taskId: string) => void;
  onToggleToday: (taskId: string, enabled: boolean) => void;
  onUpdateSchedule: (taskId: string, startDate: string, endDate: string) => Promise<boolean>;
}

interface ViewSwitcherProps {
  isChecked: boolean;
  completedTaskDepthCutoff: CompletedTaskDepthCutoff;
  completedTaskDepthOptions: number[];
  onViewListChange: (isChecked: boolean) => void;
  onViewModeChange: (viewMode: ViewMode) => void;
  onCompletedTaskDepthChange: (cutoff: CompletedTaskDepthCutoff) => void;
  onExpandAll: () => void;
  onCollapseAll: () => void;
}

type GanttTaskListHeaderProps = {
  headerHeight: number;
  rowWidth: string;
  fontFamily: string;
  fontSize: string;
};

type GanttTaskListTableProps = {
  rowHeight: number;
  rowWidth: string;
  fontFamily: string;
  fontSize: string;
  locale: string;
  tasks: GanttTask[];
  selectedTaskId: string;
  setSelectedTask: (taskId: string) => void;
  onExpanderClick: (task: GanttTask) => void;
};

const DAY_MS = 24 * 60 * 60 * 1000;
const GANTT_HORIZONTAL_SCROLL_CLASS = "_19jgW";
const GANTT_DATE_DRAG_CLASSES = new Set(["_KxSXS", "_3w_5u", "_31ERP", "_RRr13", "_2P2B1", "_1KJ6x", "_2RbVy", "_2pZMF"]);
const GANTT_AUTO_SCROLL_DATE_CLASSES = new Set(["_KxSXS", "_3w_5u", "_31ERP"]);
const MIN_GANTT_BODY_HEIGHT = 260;
const MAX_GANTT_BODY_HEIGHT = 430;
const DESKTOP_MIN_GANTT_BODY_HEIGHT = 420;
const DESKTOP_MAX_GANTT_BODY_HEIGHT = 720;
const DEFAULT_VIEWPORT_HEIGHT = 900;
const DESKTOP_LAYOUT_QUERY = "(min-width: 900px) and (orientation: landscape)";

type GanttPanState = {
  active: boolean;
  pointerId: number | null;
  startX: number;
  startScrollLeft: number;
  moved: boolean;
};

type GanttScrollSnapshot = {
  taskId: string;
  horizontal: number;
  chartVertical: number;
  contentVertical: number;
  windowVertical: number;
};

type GanttAutoScrollState = {
  active: boolean;
  dragging: boolean;
  startX: number;
  startY: number;
  clientX: number;
  clientY: number;
  frameId: number | null;
  svg: SVGSVGElement | null;
};

type TimelineHeaderCell = {
  key: string;
  label: string;
  width: number;
};

type TimelineHeaderData = {
  majorCells: TimelineHeaderCell[];
  minorCells: TimelineHeaderCell[];
  totalWidth: number;
};

type TimelineMinorCell = TimelineHeaderCell & {
  majorKey: string;
  majorLabel: string;
};

type GanttFixedTimeHeaderProps = {
  headerData: TimelineHeaderData;
  isChecked: boolean;
  listCellWidth: string;
  scrollLeft: number;
  taskLabel: string;
  assigneeLabel: string;
  todayLabel: string;
};

function hasClassInTree(element: Element | null, classNames: Set<string>): boolean {
  let current: Element | null = element;
  while (current) {
    for (const className of current.classList) {
      if (classNames.has(className)) {
        return true;
      }
    }
    current = current.parentElement;
  }
  return false;
}

function shouldStartGanttPan(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) {
    return false;
  }
  if (target.closest("button,input,select,textarea,a,.gantt-list-row,.gantt-list-expander,.gantt-list-cell-today")) {
    return false;
  }
  // バー本体と端ハンドルは日付編集ドラッグへ渡す。
  if (hasClassInTree(target, GANTT_DATE_DRAG_CLASSES)) {
    return false;
  }
  return !!target.closest("svg");
}

function shouldStartGanttDateAutoScroll(target: EventTarget | null): target is Element {
  if (!(target instanceof Element)) {
    return false;
  }
  if (target.matches("polygon._3w_5u")) {
    return false;
  }
  return hasClassInTree(target, GANTT_AUTO_SCROLL_DATE_CLASSES);
}

function dispatchScrollEvent(element: HTMLElement): void {
  element.dispatchEvent(new Event("scroll", { bubbles: true }));
}

function readViewportHeight(): number {
  return typeof window === "undefined" ? DEFAULT_VIEWPORT_HEIGHT : window.innerHeight;
}

function readIsDesktopLayout(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia(DESKTOP_LAYOUT_QUERY).matches
  );
}

function calculateGanttBodyHeight(viewportHeight: number, isDesktopLayout: boolean): number {
  if (isDesktopLayout) {
    return Math.max(
      DESKTOP_MIN_GANTT_BODY_HEIGHT,
      Math.min(DESKTOP_MAX_GANTT_BODY_HEIGHT, Math.floor(viewportHeight * 0.66))
    );
  }
  return Math.max(MIN_GANTT_BODY_HEIGHT, Math.min(MAX_GANTT_BODY_HEIGHT, Math.floor(viewportHeight * 0.44)));
}

function startOfDay(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate(), 0, 0, 0, 0);
}

function startOfWeek(date: Date): Date {
  const day = date.getDay();
  const diffToMonday = day === 0 ? -6 : 1 - day;
  const monday = startOfDay(date);
  monday.setDate(monday.getDate() + diffToMonday);
  return monday;
}

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1, 0, 0, 0, 0);
}

function addDays(date: Date, days: number): Date {
  const next = new Date(date);
  next.setDate(next.getDate() + days);
  return next;
}

function addMonths(date: Date, months: number): Date {
  const next = new Date(date);
  next.setMonth(next.getMonth() + months);
  return next;
}

function addHours(date: Date, hours: number): Date {
  const next = new Date(date);
  next.setHours(next.getHours() + hours);
  return next;
}

function formatYearMonth(date: Date, locale: string): string {
  if (locale === "ja-JP") {
    return `${date.getFullYear()}年${date.getMonth() + 1}月`;
  }
  return date.toLocaleDateString(locale, { year: "numeric", month: "short" });
}

function formatMonthOnly(date: Date, locale: string): string {
  if (locale === "ja-JP") {
    return `${date.getMonth() + 1}月`;
  }
  return date.toLocaleDateString(locale, { month: "short" });
}

function buildTimelineHeader(tasks: GanttTask[], view: ViewMode, columnWidth: number, locale: string): TimelineHeaderData {
  if (tasks.length === 0) {
    return { majorCells: [], minorCells: [], totalWidth: 0 };
  }
  const starts = tasks.map((task) => task.start.getTime()).filter(Number.isFinite);
  const ends = tasks.map((task) => task.end.getTime()).filter(Number.isFinite);
  if (starts.length === 0 || ends.length === 0) {
    return { majorCells: [], minorCells: [], totalWidth: 0 };
  }

  const minStart = new Date(Math.min(...starts));
  const maxEnd = new Date(Math.max(...ends));
  const minorCells: TimelineMinorCell[] = [];

  if (view === ViewMode.Month) {
    let cursor = addMonths(startOfMonth(minStart), -1);
    const limitBase = addMonths(maxEnd, 12);
    const limit = new Date(limitBase.getFullYear(), 0, 1, 0, 0, 0, 0);
    while (cursor <= limit) {
      const key = `${cursor.getFullYear()}-${cursor.getMonth()}`;
      minorCells.push({
        key,
        label: formatMonthOnly(cursor, locale),
        width: columnWidth,
        majorKey: `${cursor.getFullYear()}`,
        majorLabel: locale === "ja-JP" ? `${cursor.getFullYear()}年` : String(cursor.getFullYear()),
      });
      cursor = addMonths(cursor, 1);
    }
  } else if (view === ViewMode.Week) {
    let cursor = addDays(startOfWeek(minStart), -7);
    const limit = addMonths(startOfDay(maxEnd), 2);
    while (cursor <= limit) {
      const key = `${cursor.getFullYear()}-${cursor.getMonth()}-${cursor.getDate()}`;
      minorCells.push({
        key,
        label: `${cursor.getDate()}wk`,
        width: columnWidth,
        majorKey: `${cursor.getFullYear()}-${cursor.getMonth()}`,
        majorLabel: formatYearMonth(cursor, locale),
      });
      cursor = addDays(cursor, 7);
    }
  } else if (view === ViewMode.QuarterDay || view === ViewMode.HalfDay) {
    const hourStep = view === ViewMode.QuarterDay ? 6 : 12;
    const limitHours = view === ViewMode.QuarterDay ? 66 : 108;
    let cursor = addDays(startOfDay(minStart), -1);
    const limit = addHours(startOfDay(maxEnd), limitHours);
    while (cursor <= limit) {
      const key = `${cursor.getFullYear()}-${cursor.getMonth()}-${cursor.getDate()}-${cursor.getHours()}`;
      minorCells.push({
        key,
        label: cursor.getHours() === 0 ? String(cursor.getDate()) : `${cursor.getHours()}:00`,
        width: columnWidth,
        majorKey: `${cursor.getFullYear()}-${cursor.getMonth()}`,
        majorLabel: formatYearMonth(cursor, locale),
      });
      cursor = addHours(cursor, hourStep);
    }
  } else {
    let cursor = addDays(startOfDay(minStart), -1);
    const limit = addDays(startOfDay(maxEnd), 19);
    while (cursor <= limit) {
      const key = `${cursor.getFullYear()}-${cursor.getMonth()}-${cursor.getDate()}`;
      minorCells.push({
        key,
        label: String(cursor.getDate()),
        width: columnWidth,
        majorKey: `${cursor.getFullYear()}-${cursor.getMonth()}`,
        majorLabel: formatYearMonth(cursor, locale),
      });
      cursor = addDays(cursor, 1);
    }
  }

  const majorCells: TimelineHeaderCell[] = [];
  for (const cell of minorCells) {
    const lastCell = majorCells[majorCells.length - 1];
    if (lastCell && lastCell.key === cell.majorKey) {
      lastCell.width += cell.width;
    } else {
      majorCells.push({ key: cell.majorKey, label: cell.majorLabel, width: cell.width });
    }
  }

  return {
    majorCells,
    minorCells: minorCells.map(({ majorKey: _majorKey, majorLabel: _majorLabel, ...cell }) => cell),
    totalWidth: minorCells.reduce((total, cell) => total + cell.width, 0),
  };
}

function clampProgress(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

function toDayStart(value: string): Date {
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) {
    return new Date();
  }
  return new Date(parsed.getFullYear(), parsed.getMonth(), parsed.getDate(), 0, 0, 0, 0);
}

function toDayEnd(isoDate: string): Date {
  const parsed = new Date(`${isoDate}T23:59:59`);
  if (!Number.isFinite(parsed.getTime())) {
    const fallback = new Date();
    return new Date(fallback.getFullYear(), fallback.getMonth(), fallback.getDate(), 23, 59, 59, 0);
  }
  return parsed;
}

function signalStyles(signal: string): GanttTask["styles"] {
  const key = signal.toLowerCase();
  if (key === "red") {
    return { progressColor: "#e63946", progressSelectedColor: "#c1121f" };
  }
  if (key === "yellow") {
    return { progressColor: "#ffbb54", progressSelectedColor: "#ff9e0d" };
  }
  if (key === "green") {
    return { progressColor: "#2a9d52", progressSelectedColor: "#1f7f3f" };
  }
  return { progressColor: "#3a86ff", progressSelectedColor: "#2162c2" };
}

function flattenTreeNodes(nodes: WbsNode[], output: DomainTask[]): void {
  for (const node of nodes) {
    output.push(node.task);
    if (node.children && node.children.length > 0) {
      flattenTreeNodes(node.children, output);
    }
  }
}

function orderTasksLikeWbs(tasks: DomainTask[]): DomainTask[] {
  const tree = buildTree(tasks);
  const ordered: DomainTask[] = [];
  flattenTreeNodes(tree, ordered);
  return ordered;
}

function toGanttTasks(tasks: DomainTask[], wbsOpenState: Record<string, boolean>): GanttTask[] {
  const childCount = new Map<string, number>();
  for (const task of tasks) {
    if (!task.ParentTaskId) continue;
    childCount.set(task.ParentTaskId, (childCount.get(task.ParentTaskId) ?? 0) + 1);
  }

  const orderedTasks = orderTasksLikeWbs(tasks);
  const taskById = new Map(orderedTasks.map((task) => [task.Id, task]));
  const siblingIdsByParent = new Map<string, string[]>();
  for (const task of orderedTasks) {
    if (!task.ParentTaskId) continue;
    const siblings = siblingIdsByParent.get(task.ParentTaskId) ?? [];
    siblings.push(task.Id);
    siblingIdsByParent.set(task.ParentTaskId, siblings);
  }

  const dependenciesByTaskId = new Map<string, string[]>();
  for (const [parentId, siblingIds] of siblingIdsByParent.entries()) {
    const parent = taskById.get(parentId);
    const mode = parent?.ChildDependencyMode === "Parallel" ? "Parallel" : "Serial";
    if (mode === "Serial") {
      for (let index = 1; index < siblingIds.length; index += 1) {
        const currentTaskId = siblingIds[index];
        const previousTaskId = siblingIds[index - 1];
        const deps = dependenciesByTaskId.get(currentTaskId) ?? [];
        deps.push(previousTaskId);
        dependenciesByTaskId.set(currentTaskId, deps);
      }
      continue;
    }

    for (const childTaskId of siblingIds) {
      const deps = dependenciesByTaskId.get(childTaskId) ?? [];
      deps.push(parentId);
      dependenciesByTaskId.set(childTaskId, deps);
    }
  }

  const isVisibleInWbs = (task: DomainTask): boolean => {
    let parentId = task.ParentTaskId;
    while (parentId) {
      if (wbsOpenState[parentId] !== true) {
        return false;
      }
      const parent = taskById.get(parentId);
      parentId = parent?.ParentTaskId ?? null;
    }
    return true;
  };

  const visibleOrderedTasks = orderedTasks.filter(isVisibleInWbs);
  const visibleTaskIds = new Set(visibleOrderedTasks.map((task) => task.Id));

  return visibleOrderedTasks.map((task) => {
    const start = toDayStart(task.StartDate);
    const rawEnd = toDayEnd(task.EndDate);
    const end = rawEnd.getTime() >= start.getTime() ? rawEnd : new Date(start.getTime() + DAY_MS);
    const hasChildren = (childCount.get(task.Id) ?? 0) > 0;
    const dependencies = (dependenciesByTaskId.get(task.Id) ?? []).filter(
      (dependencyTaskId) => dependencyTaskId !== task.Id && visibleTaskIds.has(dependencyTaskId)
    );

    return {
      start,
      end,
      name: formatDisplayText(task.Name),
      id: task.Id,
      type: hasChildren ? "project" : "task",
      progress: clampProgress(task.ProgressRate),
      project: task.ParentTaskId ?? undefined,
      dependencies: dependencies.length > 0 ? dependencies : undefined,
      hideChildren: hasChildren ? wbsOpenState[task.Id] !== true : false,
      isDisabled: isTerminalTaskStatus(task.Status),
      styles: signalStyles(task.Signal),
    } satisfies GanttTask;
  });
}

function TaskListHeaderCompact({ headerHeight, rowWidth, fontFamily, fontSize }: GanttTaskListHeaderProps): JSX.Element {
  const { t } = useI18n();

  return (
    <div className="gantt-list-header" style={{ height: headerHeight - 2, minWidth: rowWidth, fontFamily, fontSize }}>
      <span>{t("common.task", "Task")}</span>
      <span className="gantt-list-header-assignee">{t("common.assignee", "Assignee")}</span>
      <span className="gantt-list-header-today">{t("wbs.today_label", "today")}</span>
    </div>
  );
}

function EmptyTaskListHeader(): JSX.Element {
  return <div className="gantt-list-header-hidden" aria-hidden="true" />;
}

function ViewSwitcher({
  isChecked,
  completedTaskDepthCutoff,
  completedTaskDepthOptions,
  onViewListChange,
  onViewModeChange,
  onCompletedTaskDepthChange,
  onExpandAll,
  onCollapseAll,
}: ViewSwitcherProps): JSX.Element {
  const { t } = useI18n();

  return (
    <div className="gantt-view-switcher">
      <button type="button" className="btn" onClick={() => onViewModeChange(ViewMode.QuarterDay)}>
        {t("gantt.quarter_day", "Quarter of Day")}
      </button>
      <button type="button" className="btn" onClick={() => onViewModeChange(ViewMode.HalfDay)}>
        {t("gantt.half_day", "Half of Day")}
      </button>
      <button type="button" className="btn" onClick={() => onViewModeChange(ViewMode.Day)}>
        {t("gantt.day", "Day")}
      </button>
      <button type="button" className="btn" onClick={() => onViewModeChange(ViewMode.Week)}>
        {t("gantt.week", "Week")}
      </button>
      <button type="button" className="btn" onClick={() => onViewModeChange(ViewMode.Month)}>
        {t("gantt.month", "Month")}
      </button>
      <button type="button" className="btn" onClick={onExpandAll}>
        {t("common.expand_all", "Expand All")}
      </button>
      <button type="button" className="btn" onClick={onCollapseAll}>
        {t("common.collapse_all", "Collapse All")}
      </button>

      <label className="gantt-view-list-switch">
        <input type="checkbox" checked={isChecked} onChange={() => onViewListChange(!isChecked)} />
        <span>{t("gantt.show_task_list", "Show Task List")}</span>
      </label>
      <label className="gantt-completed-depth-control">
        <span>{t("gantt.completed_task_visibility", "Completed tasks")}</span>
        <select
          value={completedTaskDepthCutoff === null ? "all" : String(completedTaskDepthCutoff)}
          onChange={(event) => onCompletedTaskDepthChange(parseCompletedTaskDepthCutoff(event.currentTarget.value))}
        >
          <option value="all">{t("gantt.completed_task_show_all", "Show all")}</option>
          {completedTaskDepthOptions.map((depth) => (
            <option key={depth} value={depth}>
              {t("gantt.completed_task_hide_from_depth", "Hide completed from level {level}", { level: depth + 1 })}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}

function GanttFixedTimeHeader({
  headerData,
  isChecked,
  listCellWidth,
  scrollLeft,
  taskLabel,
  assigneeLabel,
  todayLabel,
}: GanttFixedTimeHeaderProps): JSX.Element | null {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [viewportWidth, setViewportWidth] = useState(0);

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) {
      return undefined;
    }

    const measure = (): void => {
      setViewportWidth(viewport.clientWidth);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  if (headerData.minorCells.length === 0) {
    return null;
  }

  let majorOffset = 0;
  const majorCells = headerData.majorCells.map((cell) => {
    const start = majorOffset;
    majorOffset += cell.width;
    const visibleStart = Math.max(0, scrollLeft - start);
    const visibleEnd = Math.min(cell.width, scrollLeft + viewportWidth - start);
    const visibleWidth = Math.max(0, visibleEnd - visibleStart);
    const labelCenter = visibleWidth > 0 ? visibleStart + visibleWidth / 2 : cell.width / 2;
    return { ...cell, labelCenter, visibleWidth };
  });

  return (
    <div className="gantt-fixed-time-header">
      <div
        className={`gantt-fixed-list-header${isChecked ? "" : " hidden"}`}
        style={{ width: isChecked ? listCellWidth : "0px" }}
      >
        {isChecked ? (
          <>
            <span>{taskLabel}</span>
            <span className="gantt-list-header-assignee">{assigneeLabel}</span>
            <span className="gantt-list-header-today">{todayLabel}</span>
          </>
        ) : null}
      </div>
      <div className="gantt-fixed-time-viewport" ref={viewportRef}>
        <div
          className="gantt-fixed-time-track"
          style={{ width: headerData.totalWidth, transform: `translateX(${-scrollLeft}px)` }}
        >
          <div className="gantt-fixed-time-row major">
            {majorCells.map((cell) => (
              <div key={cell.key} className="gantt-fixed-time-cell major-cell" style={{ width: cell.width }}>
                <span
                  className="gantt-fixed-major-label"
                  style={{
                    left: cell.labelCenter,
                    maxWidth: Math.max(44, cell.visibleWidth || cell.width),
                  }}
                >
                  {cell.label}
                </span>
              </div>
            ))}
          </div>
          <div className="gantt-fixed-time-row minor">
            {headerData.minorCells.map((cell) => (
              <div key={cell.key} className="gantt-fixed-time-cell minor-cell" style={{ width: cell.width }}>
                {cell.label}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export function GanttView({
  tasks,
  selectedUserId,
  selectedTaskId,
  onSelectTask,
  onToggleToday,
  onUpdateSchedule,
}: GanttViewProps): JSX.Element {
  const { t, language } = useI18n();
  const [view, setView] = useState<ViewMode>(ViewMode.Day);
  const [isChecked, setIsChecked] = useState(true);
  const [completedTaskDepthCutoff, setCompletedTaskDepthCutoff] = useState<CompletedTaskDepthCutoff>(() =>
    readCompletedTaskDepthCutoff(selectedUserId)
  );
  const [timelineScrollLeft, setTimelineScrollLeft] = useState(0);
  const [isDesktopLayout, setIsDesktopLayout] = useState(() => readIsDesktopLayout());
  const [ganttBodyHeight, setGanttBodyHeight] = useState(() =>
    calculateGanttBodyHeight(readViewportHeight(), readIsDesktopLayout())
  );
  const chartWrapRef = useRef<HTMLDivElement | null>(null);
  const pendingScrollRestoreRef = useRef<GanttScrollSnapshot | null>(null);
  const centerTodayOnNextRenderRef = useRef(true);
  const panStateRef = useRef<GanttPanState>({
    active: false,
    pointerId: null,
    startX: 0,
    startScrollLeft: 0,
    moved: false,
  });
  const autoScrollStateRef = useRef<GanttAutoScrollState>({
    active: false,
    dragging: false,
    startX: 0,
    startY: 0,
    clientX: 0,
    clientY: 0,
    frameId: null,
    svg: null,
  });
  const wbsOpenState = useUiStore((state) => state.wbsOpenState);
  const setWbsOpenState = useUiStore((state) => state.setWbsOpenState);
  const visibleDomainTasks = useMemo(
    () => filterGanttTasksByCompletedDepth(tasks, completedTaskDepthCutoff),
    [completedTaskDepthCutoff, tasks]
  );
  const mappedTasks = useMemo(
    () => toGanttTasks(visibleDomainTasks, wbsOpenState),
    [visibleDomainTasks, wbsOpenState]
  );
  const [ganttTasks, setGanttTasks] = useState<GanttTask[]>(mappedTasks);

  const taskById = useMemo(() => new Map(tasks.map((task) => [task.Id, task])), [tasks]);
  const selectedDomainTask = selectedTaskId ? taskById.get(selectedTaskId) ?? null : null;
  const taskDepthById = useMemo(() => buildGanttTaskDepthMap(tasks), [tasks]);
  const completedTaskDepthOptions = useMemo(() => {
    let maximumDepth = completedTaskDepthCutoff ?? 0;
    for (const depth of taskDepthById.values()) {
      maximumDepth = Math.max(maximumDepth, depth);
    }
    return Array.from({ length: maximumDepth + 1 }, (_unused, depth) => depth);
  }, [completedTaskDepthCutoff, taskDepthById]);
  const ganttLocale = language === "ja" ? "ja-JP" : "en-US";

  const childCount = useMemo(() => {
    const map = new Map<string, number>();
    for (const task of visibleDomainTasks) {
      if (!task.ParentTaskId) continue;
      map.set(task.ParentTaskId, (map.get(task.ParentTaskId) ?? 0) + 1);
    }
    return map;
  }, [visibleDomainTasks]);

  const handleExpandAll = () => {
    const nextOpenState: Record<string, boolean> = {};
    for (const taskId of childCount.keys()) {
      nextOpenState[taskId] = true;
    }
    setWbsOpenState(nextOpenState);
  };

  const handleCollapseAll = () => {
    setWbsOpenState({});
  };

  const handleCompletedTaskDepthChange = (cutoff: CompletedTaskDepthCutoff): void => {
    setCompletedTaskDepthCutoff(cutoff);
    writeCompletedTaskDepthCutoff(selectedUserId, cutoff);
  };

  useEffect(() => {
    setCompletedTaskDepthCutoff(readCompletedTaskDepthCutoff(selectedUserId));
  }, [selectedUserId]);

  useEffect(() => {
    setGanttTasks(mappedTasks);
    const pendingSnapshot = pendingScrollRestoreRef.current;
    if (pendingSnapshot) {
      restoreGanttScrollSnapshot(pendingSnapshot);
      onSelectTask(pendingSnapshot.taskId);
    }
  }, [mappedTasks]);

  useEffect(() => {
    const handleResize = (): void => {
      const nextDesktopLayout = readIsDesktopLayout();
      setIsDesktopLayout(nextDesktopLayout);
      setGanttBodyHeight(calculateGanttBodyHeight(readViewportHeight(), nextDesktopLayout));
    };
    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    const wrap = chartWrapRef.current;
    if (!wrap) {
      return undefined;
    }
    const keepBrowserZoom = (event: WheelEvent): void => {
      if (event.ctrlKey) {
        event.stopPropagation();
      }
    };
    wrap.addEventListener("wheel", keepBrowserZoom, true);
    return () => wrap.removeEventListener("wheel", keepBrowserZoom, true);
  }, []);

  useEffect(() => {
    const wrap = chartWrapRef.current;
    if (!wrap) {
      return undefined;
    }
    const state = autoScrollStateRef.current;

    const stopAutoScroll = (): void => {
      state.active = false;
      state.dragging = false;
      state.svg = null;
      if (state.frameId !== null) {
        window.cancelAnimationFrame(state.frameId);
        state.frameId = null;
      }
    };

    const runAutoScrollFrame = (): void => {
      state.frameId = null;
      if (!state.active || !state.dragging) {
        return;
      }
      const scrollElement = wrap.querySelector<HTMLElement>(`.${GANTT_HORIZONTAL_SCROLL_CLASS}`) ?? wrap;
      const viewport = scrollElement.getBoundingClientRect();
      const delta = calculateGanttAutoScrollDelta(state.clientX, viewport.left, viewport.right);
      if (delta === 0) {
        return;
      }

      const maximumScrollLeft = Math.max(0, scrollElement.scrollWidth - scrollElement.clientWidth);
      const nextScrollLeft = Math.max(0, Math.min(maximumScrollLeft, scrollElement.scrollLeft + delta));
      if (nextScrollLeft === scrollElement.scrollLeft) {
        return;
      }

      scrollElement.scrollLeft = nextScrollLeft;
      setTimelineScrollLeft(nextScrollLeft);
      dispatchScrollEvent(scrollElement);
      state.frameId = window.requestAnimationFrame(runAutoScrollFrame);

      if (state.svg?.isConnected) {
        state.svg.dispatchEvent(
          new MouseEvent("mousemove", {
            bubbles: true,
            buttons: 1,
            clientX: state.clientX,
            clientY: state.clientY,
          })
        );
      }
    };

    const ensureAutoScrollFrame = (): void => {
      if (state.active && state.dragging && state.frameId === null) {
        state.frameId = window.requestAnimationFrame(runAutoScrollFrame);
      }
    };

    const handleMouseDown = (event: MouseEvent): void => {
      if (event.button !== 0 || !shouldStartGanttDateAutoScroll(event.target)) {
        return;
      }
      const svg = event.target.closest("svg");
      if (!(svg instanceof SVGSVGElement)) {
        return;
      }
      stopAutoScroll();
      state.active = true;
      state.startX = event.clientX;
      state.startY = event.clientY;
      state.clientX = event.clientX;
      state.clientY = event.clientY;
      state.svg = svg;
    };

    const handleMouseMove = (event: MouseEvent): void => {
      if (!state.active) {
        return;
      }
      if (event.buttons === 0) {
        stopAutoScroll();
        return;
      }
      state.clientX = event.clientX;
      state.clientY = event.clientY;
      if (Math.hypot(event.clientX - state.startX, event.clientY - state.startY) > 3) {
        state.dragging = true;
      }
      ensureAutoScrollFrame();
    };

    wrap.addEventListener("mousedown", handleMouseDown, true);
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", stopAutoScroll);
    window.addEventListener("blur", stopAutoScroll);
    return () => {
      stopAutoScroll();
      wrap.removeEventListener("mousedown", handleMouseDown, true);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", stopAutoScroll);
      window.removeEventListener("blur", stopAutoScroll);
    };
  }, []);

  let columnWidth = isDesktopLayout ? 72 : 60;
  if (view === ViewMode.Month) {
    columnWidth = isDesktopLayout ? 340 : 300;
  } else if (view === ViewMode.Week) {
    columnWidth = isDesktopLayout ? 280 : 250;
  }
  const listCellWidth = isChecked ? (isDesktopLayout ? "460px" : "320px") : "";
  const ganttRangeLabel = useMemo(() => {
    if (ganttTasks.length === 0) {
      return "";
    }
    const starts = ganttTasks.map((task) => task.start.getTime()).filter(Number.isFinite);
    const ends = ganttTasks.map((task) => task.end.getTime()).filter(Number.isFinite);
    if (starts.length === 0 || ends.length === 0) {
      return "";
    }
    const start = new Date(Math.min(...starts));
    const end = new Date(Math.max(...ends));
    return `${start.toLocaleDateString(ganttLocale)} - ${end.toLocaleDateString(ganttLocale)}`;
  }, [ganttLocale, ganttTasks]);
  const timelineHeader = useMemo(
    () => buildTimelineHeader(ganttTasks, view, columnWidth, ganttLocale),
    [columnWidth, ganttLocale, ganttTasks, view]
  );

  useEffect(() => {
    let detachScrollListener: (() => void) | null = null;
    const timerId = window.setTimeout(() => {
      const scrollElement = getHorizontalScrollElement();
      if (!scrollElement) {
        return;
      }
      const updateTimelineScroll = (): void => {
        setTimelineScrollLeft(scrollElement.scrollLeft);
      };
      updateTimelineScroll();
      scrollElement.addEventListener("scroll", updateTimelineScroll);
      detachScrollListener = () => scrollElement.removeEventListener("scroll", updateTimelineScroll);
    }, 0);

    return () => {
      window.clearTimeout(timerId);
      detachScrollListener?.();
    };
  }, [columnWidth, ganttTasks.length, isChecked, listCellWidth, view]);

  const handleTaskChange = async (task: GanttTask): Promise<boolean> => {
    const { taskId, startDate, endDate } = buildGanttSchedulePayload(task);
    const scrollSnapshot = captureGanttScrollSnapshot(taskId);
    pendingScrollRestoreRef.current = scrollSnapshot;
    onSelectTask(taskId);

    const saved = await onUpdateSchedule(taskId, startDate, endDate);
    if (!saved) {
      restoreGanttScrollSnapshot(scrollSnapshot);
      return false;
    }
    setGanttTasks((currentTasks) => applyGanttTaskDateChange(currentTasks, task));
    restoreGanttScrollSnapshot(scrollSnapshot);
    onSelectTask(taskId);
    return true;
  };

  const handleTaskDelete = (): boolean => false;

  const handleProgressChange = async (): Promise<boolean> => {
    return false;
  };

  const getHorizontalScrollElement = (): HTMLElement | null => {
    const wrap = chartWrapRef.current;
    if (!wrap) {
      return null;
    }
    return wrap.querySelector<HTMLElement>(`.${GANTT_HORIZONTAL_SCROLL_CLASS}`) ?? wrap;
  };

  const setHorizontalScrollLeft = (nextScrollLeft: number): void => {
    const scrollElement = getHorizontalScrollElement();
    if (!scrollElement) {
      return;
    }
    const maxScrollLeft = Math.max(0, scrollElement.scrollWidth - scrollElement.clientWidth);
    const boundedScrollLeft = Math.max(0, Math.min(maxScrollLeft, nextScrollLeft));
    if (scrollElement.scrollLeft === boundedScrollLeft) {
      return;
    }
    scrollElement.scrollLeft = boundedScrollLeft;
    setTimelineScrollLeft(boundedScrollLeft);
    dispatchScrollEvent(scrollElement);
  };

  const centerTimelineToday = (retryCount = 0): void => {
    const scrollElement = getHorizontalScrollElement();
    const viewportWidth = scrollElement?.clientWidth ?? 0;
    const todayScrollLeft = findTimelineScrollLeftForDate(timelineHeader.minorCells, view, new Date(), viewportWidth);
    if (scrollElement && todayScrollLeft !== null) {
      setHorizontalScrollLeft(todayScrollLeft);
      return;
    }
    if (retryCount >= 6) {
      return;
    }
    window.requestAnimationFrame(() => centerTimelineToday(retryCount + 1));
  };

  const handleTimelineShift = (direction: -1 | 1): void => {
    const scrollElement = getHorizontalScrollElement();
    const viewportWidth = scrollElement?.clientWidth ?? 0;
    const shiftWidth = Math.max(columnWidth, Math.floor(viewportWidth * 0.72));
    setHorizontalScrollLeft((scrollElement?.scrollLeft ?? timelineScrollLeft) + direction * shiftWidth);
  };

  const handleTimelineToday = (): void => {
    centerTimelineToday();
  };

  const handleViewModeChange = (nextView: ViewMode): void => {
    centerTodayOnNextRenderRef.current = true;
    setView(nextView);
  };

  useEffect(() => {
    if (!centerTodayOnNextRenderRef.current || timelineHeader.minorCells.length === 0) {
      return undefined;
    }
    centerTodayOnNextRenderRef.current = false;
    const timerId = window.setTimeout(() => centerTimelineToday(), 0);
    return () => window.clearTimeout(timerId);
  }, [ganttTasks.length, isChecked, listCellWidth, timelineHeader, view]);

  const getContentScrollElement = (): HTMLElement | null => {
    return chartWrapRef.current?.closest<HTMLElement>(".content") ?? null;
  };

  const captureGanttScrollSnapshot = (taskId: string): GanttScrollSnapshot => {
    const horizontalScrollElement = getHorizontalScrollElement();
    const contentScrollElement = getContentScrollElement();
    return {
      taskId,
      horizontal: horizontalScrollElement?.scrollLeft ?? timelineScrollLeft,
      chartVertical: chartWrapRef.current?.scrollTop ?? 0,
      contentVertical: contentScrollElement?.scrollTop ?? 0,
      windowVertical: typeof window === "undefined" ? 0 : window.scrollY,
    };
  };

  const restoreGanttScrollSnapshot = (snapshot: GanttScrollSnapshot): void => {
    pendingScrollRestoreRef.current = snapshot;
    const applyRestore = (): void => {
      setHorizontalScrollLeft(snapshot.horizontal);
      setTimelineScrollLeft(snapshot.horizontal);
      if (chartWrapRef.current) {
        chartWrapRef.current.scrollTop = snapshot.chartVertical;
      }
      const contentScrollElement = getContentScrollElement();
      if (contentScrollElement) {
        contentScrollElement.scrollTop = snapshot.contentVertical;
      }
      if (typeof window !== "undefined") {
        window.scrollTo(window.scrollX, snapshot.windowVertical);
      }
    };

    window.requestAnimationFrame(() => {
      applyRestore();
      window.requestAnimationFrame(() => {
        applyRestore();
      });
    });
    window.setTimeout(() => {
      if (pendingScrollRestoreRef.current?.taskId === snapshot.taskId) {
        pendingScrollRestoreRef.current = null;
      }
    }, 1200);
  };

  const stopChartPan = (pointerId?: number): void => {
    const wrap = chartWrapRef.current;
    if (wrap && pointerId !== undefined) {
      try {
        wrap.releasePointerCapture(pointerId);
      } catch {
        // 既にキャプチャが解除されている場合は終了処理だけ続ける。
      }
    }
    panStateRef.current = {
      active: false,
      pointerId: null,
      startX: 0,
      startScrollLeft: 0,
      moved: false,
    };
    wrap?.classList.remove("panning");
  };

  const handleChartPointerDown = (event: ReactPointerEvent<HTMLDivElement>): void => {
    if (event.button !== 0 || !shouldStartGanttPan(event.target)) {
      return;
    }
    const scrollElement = getHorizontalScrollElement();
    const wrap = chartWrapRef.current;
    if (!scrollElement || !wrap) {
      return;
    }
    panStateRef.current = {
      active: true,
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: scrollElement.scrollLeft,
      moved: false,
    };
    wrap.setPointerCapture(event.pointerId);
    wrap.classList.add("panning");
  };

  const handleChartPointerMove = (event: ReactPointerEvent<HTMLDivElement>): void => {
    const panState = panStateRef.current;
    if (!panState.active || panState.pointerId !== event.pointerId) {
      return;
    }
    const deltaX = event.clientX - panState.startX;
    if (Math.abs(deltaX) > 3) {
      panState.moved = true;
    }
    setHorizontalScrollLeft(panState.startScrollLeft - deltaX);
    if (panState.moved) {
      event.preventDefault();
      event.stopPropagation();
    }
  };

  const handleChartPointerEnd = (event: ReactPointerEvent<HTMLDivElement>): void => {
    const panState = panStateRef.current;
    if (!panState.active || panState.pointerId !== event.pointerId) {
      return;
    }
    stopChartPan(event.pointerId);
  };

  const handleDblClick = (task: GanttTask) => {
    onSelectTask(String(task.id));
  };

  const handleSelect = (task: GanttTask, isSelected: boolean) => {
    if (isSelected) {
      onSelectTask(String(task.id));
    }
  };

  const handleExpanderClick = (task: GanttTask) => {
    setGanttTasks((currentTasks) => {
      const nextTasks = currentTasks.map((item) => (item.id === task.id ? task : item));
      const nextOpenState: Record<string, boolean> = {};
      for (const item of nextTasks) {
        const taskId = String(item.id);
        if ((childCount.get(taskId) ?? 0) === 0) continue;
        nextOpenState[taskId] = item.hideChildren !== true;
      }
      setWbsOpenState(nextOpenState);
      return nextTasks;
    });
  };

  const TaskListTableWithToday = ({
    rowHeight,
    rowWidth,
    fontFamily,
    fontSize,
    tasks: visibleTasks,
    selectedTaskId: selectedInList,
    setSelectedTask,
    onExpanderClick,
  }: GanttTaskListTableProps): JSX.Element => {
    return (
      <div className="gantt-list-table" style={{ fontFamily, fontSize }}>
        {visibleTasks.map((task) => {
          const taskId = String(task.id);
          const domainTask = taskById.get(taskId);
          const hasChildren = (childCount.get(taskId) ?? 0) > 0;
          const isTodayTarget = !!domainTask && !hasChildren && !isTerminalTaskStatus(domainTask.Status);
          const canToggleToday = isTodayTarget && domainTask.AssigneeUserId === selectedUserId;
          const showReadOnlyToday = isTodayTarget && domainTask.AssigneeUserId !== selectedUserId;
          const todayChecked = domainTask ? isTodayOn(domainTask) : false;
          const assigneeName = domainTask?.AssigneeName?.trim() || "-";
          const taskDepth = domainTask ? taskDepthById.get(domainTask.Id) ?? 0 : 0;
          const rangeText = domainTask ? formatGanttTaskRange(domainTask) : "-";
          const estimateSignalLabel = domainTask ? getEstimateSignalLabel(domainTask) : null;
          const estimateBalanceText = domainTask ? formatEstimateBalance(domainTask) : "";
          const expanderSymbol = task.hideChildren === false ? "▾" : task.hideChildren === true ? "▸" : "";

          return (
            <div
              key={`${task.id}-row`}
              className={`gantt-list-row${selectedInList === task.id ? " selected" : ""}`}
              style={{ height: rowHeight, minWidth: rowWidth, maxWidth: rowWidth }}
              onClick={() => setSelectedTask(taskId)}
            >
              <div className="gantt-list-cell-name" title={task.name}>
                <div className="gantt-list-name-wrap">
                  <span className="gantt-list-indent" style={{ width: taskDepth * 16 }} aria-hidden="true" />
                  <button
                    type="button"
                    className={`gantt-list-expander${expanderSymbol ? "" : " empty"}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      onExpanderClick(task);
                    }}
                  >
                    {expanderSymbol}
                  </button>
                  <div className="gantt-list-name-block">
                    <div className="gantt-list-name-top">
                      <span className="gantt-list-task-name">{task.name}</span>
                      {domainTask ? <IquavisSyncChip task={domainTask} /> : null}
                      {domainTask && estimateSignalLabel ? (
                        <span
                          className={`estimate-signal-chip ${getEstimateSignalClass(domainTask)}`}
                          title={domainTask.EstimateSignalMessage ?? undefined}
                        >
                          {estimateSignalLabel}
                        </span>
                      ) : null}
                    </div>
                    <div className="gantt-list-task-meta">
                      {rangeText}
                      {estimateBalanceText ? ` | ${estimateBalanceText}` : ""}
                    </div>
                  </div>
                </div>
              </div>
              <div className="gantt-list-cell-assignee" title={assigneeName}>
                {assigneeName}
              </div>
              <div
                className="gantt-list-cell-today"
                onClick={(event) => {
                  event.stopPropagation();
                }}
              >
                {canToggleToday ? (
                  <input
                    type="checkbox"
                    checked={todayChecked}
                    onChange={(event) => onToggleToday(taskId, event.currentTarget.checked)}
                  />
                ) : showReadOnlyToday ? (
                  <input
                    type="checkbox"
                    checked={todayChecked}
                    disabled
                    className="gantt-list-cell-today-readonly"
                    aria-label={`readonly-today-${taskId}`}
                  />
                ) : (
                  <span className="gantt-list-cell-empty">-</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="view-stack">
      <div className="panel gantt-controls-panel">
        <div className="panel-title">{t("gantt.title", "Gantt")}</div>
        <ViewSwitcher
          onViewModeChange={handleViewModeChange}
          onViewListChange={setIsChecked}
          onCompletedTaskDepthChange={handleCompletedTaskDepthChange}
          onExpandAll={handleExpandAll}
          onCollapseAll={handleCollapseAll}
          isChecked={isChecked}
          completedTaskDepthCutoff={completedTaskDepthCutoff}
          completedTaskDepthOptions={completedTaskDepthOptions}
        />
        <div className="subtext">{t("gantt.update_hint", "Drag to update start and end dates directly. Changes are saved without navigation.")}</div>
        <div className="subtext">{t("gantt.pan_hint", "Drag blank chart space to pan horizontally. Shift+wheel also scrolls sideways.")}</div>
        <div className="subtext">{t("gantt.sync_hint", "Paired tasks are queued for iQUAVIS sync after save.")}</div>
      </div>

      <IquavisConflictPanel task={selectedDomainTask} />

      <div
        ref={chartWrapRef}
        className="chart-wrap gantt-pan-surface"
        onPointerDown={handleChartPointerDown}
        onPointerMove={handleChartPointerMove}
        onPointerUp={handleChartPointerEnd}
        onPointerCancel={handleChartPointerEnd}
        onLostPointerCapture={handleChartPointerEnd}
      >
        <div className="panel-title">{t("gantt.title", "Gantt")}</div>
        {ganttRangeLabel ? <div className="gantt-visible-range">{ganttRangeLabel}</div> : null}
        <div className="gantt-timeline-nav">
          <div className="gantt-timeline-nav-spacer" style={{ width: isChecked ? listCellWidth : "0px" }} />
          <div className="gantt-timeline-nav-controls">
            <button
              type="button"
              className="gantt-timeline-nav-button arrow"
              onClick={() => handleTimelineShift(-1)}
              aria-label={t("gantt.previous_range", "Previous range")}
            >
              ←
            </button>
            <button type="button" className="gantt-timeline-nav-button today" onClick={handleTimelineToday}>
              {t("gantt.today", "今日")}
            </button>
            <button
              type="button"
              className="gantt-timeline-nav-button arrow"
              onClick={() => handleTimelineShift(1)}
              aria-label={t("gantt.next_range", "Next range")}
            >
              →
            </button>
          </div>
        </div>
        <GanttFixedTimeHeader
          headerData={timelineHeader}
          isChecked={isChecked}
          listCellWidth={listCellWidth}
          scrollLeft={timelineScrollLeft}
          taskLabel={t("common.task", "Task")}
          assigneeLabel={t("common.assignee", "Assignee")}
          todayLabel={t("wbs.today_label", "today")}
        />
        {ganttTasks.length > 0 ? (
          <Gantt
            tasks={ganttTasks}
            viewMode={view}
            onDateChange={handleTaskChange}
            onDelete={handleTaskDelete}
            onProgressChange={handleProgressChange}
            onDoubleClick={handleDblClick}
            onSelect={handleSelect}
            onExpanderClick={handleExpanderClick}
            locale={ganttLocale}
            listCellWidth={listCellWidth}
            TaskListHeader={EmptyTaskListHeader}
            TaskListTable={TaskListTableWithToday}
            columnWidth={columnWidth}
            headerHeight={0}
            ganttHeight={ganttBodyHeight}
            rowHeight={46}
            barCornerRadius={4}
            handleWidth={8}
            todayColor="#dff5ff"
          />
        ) : (
          <div className="gantt-empty-state">{t("gantt.no_visible_tasks", "No tasks match the current display settings.")}</div>
        )}
      </div>
    </div>
  );
}
