import { useCallback, useEffect, useMemo, useState } from "react";
import { Tree } from "react-arborist";
import ReactECharts from "echarts-for-react";
import type { ECharts, EChartsOption } from "echarts";
import { IquavisConflictPanel, IquavisSyncChip } from "../components/IquavisConflictPanel";
import { formatDisplayText } from "../lib/displayText";
import { useI18n } from "../lib/i18n";
import { buildTree } from "../lib/taskTree";
import { useViewportSize } from "../lib/useViewportSize";
import type { Task, TaskProgressDashboard } from "../types";

interface ProgressViewProps {
  tasks: Task[];
  selectedTaskId: string | null;
  onSelectTask: (taskId: string) => void;
  dashboard: TaskProgressDashboard | undefined;
}

type LinePoint = [number, number];
type ChartKind = "progress" | "speed";
type AxisRange = { min: number; max: number };

const DAY_MS = 24 * 60 * 60 * 1000;
const TRANSITION_RATIO = 0.1;
const MIN_TIME_SPAN_MS = 6 * 60 * 60 * 1000;
const MAX_TIME_SPAN_MS = DAY_MS * 3650;
const MIN_VALUE_SPAN = 1;
const MAX_VALUE_SPAN = 10 ** 9;
const RANGE_PADDING_RATIO = 0.08;
const WHEEL_PAN_RATIO = 1 / 600;
const WHEEL_ZOOM_SENSITIVITY = 0.0015;

function toDayMs(isoDate: string): number {
  return new Date(`${isoDate}T00:00:00`).getTime();
}

function formatDay(value: number): string {
  const d = new Date(value);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${m}/${day}`;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function panRange(range: AxisRange, offset: number): AxisRange {
  return { min: range.min + offset, max: range.max + offset };
}

function zoomRange(
  range: AxisRange,
  zoomFactor: number,
  anchorRatio: number,
  minSpan: number,
  maxSpan: number
): AxisRange {
  const currentSpan = Math.max(minSpan, range.max - range.min);
  const nextSpan = clamp(currentSpan * zoomFactor, minSpan, maxSpan);
  const ratio = clamp(anchorRatio, 0, 1);
  const anchor = range.min + currentSpan * ratio;
  const min = anchor - nextSpan * ratio;
  const max = anchor + nextSpan * (1 - ratio);
  return { min, max };
}

function normalizeRange(min: number, max: number, minimumSpan: number): AxisRange {
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    const now = Date.now();
    return { min: now - DAY_MS, max: now + DAY_MS };
  }
  if (max <= min) {
    return { min, max: min + minimumSpan };
  }
  return { min, max };
}

function buildTimeRange(values: number[]): AxisRange {
  const finiteValues = values.filter((v) => Number.isFinite(v));
  if (finiteValues.length === 0) {
    return { min: Date.now() - DAY_MS, max: Date.now() + DAY_MS };
  }
  const min = Math.min(...finiteValues);
  const max = Math.max(...finiteValues);
  const normalized = normalizeRange(min, max, DAY_MS);
  const span = normalized.max - normalized.min;
  const padding = Math.max(DAY_MS * 0.5, span * RANGE_PADDING_RATIO);
  return { min: normalized.min - padding, max: normalized.max + padding };
}

function buildValueRange(values: number[], floor = 0): AxisRange {
  const finiteValues = values.filter((v) => Number.isFinite(v));
  const source = finiteValues.length > 0 ? finiteValues : [floor, floor + MIN_VALUE_SPAN];
  const min = Math.min(...source, floor);
  const max = Math.max(...source, floor + MIN_VALUE_SPAN);
  const normalized = normalizeRange(min, max, MIN_VALUE_SPAN);
  const span = normalized.max - normalized.min;
  const padding = Math.max(1, span * RANGE_PADDING_RATIO);
  const rangedMin = Math.max(floor, normalized.min - padding);
  const rangedMax = Math.max(rangedMin + MIN_VALUE_SPAN, normalized.max + padding);
  return { min: rangedMin, max: rangedMax };
}

function zoomValueRange(range: AxisRange, zoomFactor: number, anchorRatio: number, floor = 0): AxisRange {
  const zoomed = zoomRange(range, zoomFactor, anchorRatio, MIN_VALUE_SPAN, MAX_VALUE_SPAN);
  if (zoomed.min >= floor) return zoomed;
  const shift = floor - zoomed.min;
  return { min: floor, max: zoomed.max + shift };
}

function buildPseudoStepSmoothSeries(items: Array<{ date: string; value: number }>): LinePoint[] {
  if (items.length === 0) {
    return [[Date.now(), 0]];
  }

  const sorted = [...items].sort((a, b) => toDayMs(a.date) - toDayMs(b.date));
  const edge = DAY_MS * TRANSITION_RATIO;
  const points: LinePoint[] = [];

  for (let i = 0; i < sorted.length; i += 1) {
    const dayStart = toDayMs(sorted[i].date);
    const value = sorted[i].value;
    const dayCenter = dayStart + DAY_MS * 0.5;
    const dayEndMinus = dayStart + DAY_MS * (1 - TRANSITION_RATIO);

    if (i === 0) {
      const base = value > 0 ? 0 : value;
      points.push([dayStart - edge, base]);
      points.push([dayStart + edge, value]);
    } else {
      const prevValue = sorted[i - 1].value;
      points.push([dayStart - edge, prevValue]);
      points.push([dayStart + edge, value]);
    }

    points.push([dayCenter, value]);
    if (i === sorted.length - 1) {
      points.push([dayEndMinus, value]);
    }
  }

  points.sort((a, b) => a[0] - b[0]);

  const deduped: LinePoint[] = [];
  for (const point of points) {
    const last = deduped[deduped.length - 1];
    if (last && last[0] === point[0]) {
      deduped[deduped.length - 1] = point;
      continue;
    }
    deduped.push(point);
  }

  return deduped;
}

function extendSeriesToDate(series: LinePoint[], targetMs: number | null, enabled: boolean): LinePoint[] {
  if (!enabled || targetMs === null || series.length === 0) {
    return series;
  }
  const last = series[series.length - 1];
  if (last[0] >= targetMs) {
    return series;
  }
  return [...series, [targetMs, last[1]]];
}

function signalClass(signal: string): "red" | "yellow" | "blue" | "green" {
  const value = signal.toLowerCase();
  if (value === "red" || value === "yellow" || value === "blue" || value === "green") {
    return value;
  }
  return "blue";
}

export function ProgressView({
  tasks,
  selectedTaskId,
  onSelectTask,
  dashboard,
}: ProgressViewProps): JSX.Element {
  const { t } = useI18n();
  const [lockX, setLockX] = useState(true);
  const [lockY, setLockY] = useState(false);
  const [xViewRange, setXViewRange] = useState<AxisRange | null>(null);
  const [speedYViewRange, setSpeedYViewRange] = useState<AxisRange | null>(null);
  const [progressMhYViewRange, setProgressMhYViewRange] = useState<AxisRange | null>(null);
  const [progressChart, setProgressChart] = useState<ECharts | null>(null);
  const [speedChart, setSpeedChart] = useState<ECharts | null>(null);

  const size = useViewportSize(360, 320);
  const treeData = useMemo(() => buildTree(tasks), [tasks]);

  useEffect(() => {
    if (selectedTaskId || tasks.length === 0) return;
    onSelectTask(tasks[0].Id);
  }, [selectedTaskId, tasks, onSelectTask]);

  const selectedTask = useMemo(
    () => tasks.find((task) => task.Id === selectedTaskId) ?? null,
    [tasks, selectedTaskId]
  );
  const isParentTask = useMemo(
    () => (selectedTask ? tasks.some((task) => task.ParentTaskId === selectedTask.Id) : false),
    [selectedTask, tasks]
  );
  const startDateMs = selectedTask ? toDayMs(selectedTask.StartDate) : null;
  const dueDateMs = selectedTask ? toDayMs(selectedTask.EndDate) : null;
  const todayMs = useMemo(() => {
    const now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  }, []);
  const referenceDateMarkLine = useMemo(() => {
    const data: Array<{
      xAxis: number;
      label: { show: boolean; formatter: string; color: string; fontWeight: number };
      lineStyle: { color: string; width: number; type: "solid" };
    }> = [
      {
        xAxis: todayMs,
        label: {
          show: true,
          formatter: t("progress_view.marker_today", "Today"),
          color: "#2563eb",
          fontWeight: 700,
        },
        lineStyle: {
          color: "#2563eb",
          width: 2,
          type: "solid",
        },
      },
    ];

    if (dueDateMs !== null) {
      data.push({
        xAxis: dueDateMs,
        label: {
          show: true,
          formatter: t("progress_view.marker_due", "Due"),
          color: "#dc2626",
          fontWeight: 700,
        },
        lineStyle: {
          color: "#dc2626",
          width: 2,
          type: "solid",
        },
      });
    }

    return {
      symbol: ["none", "none"] as [string, string],
      data,
    };
  }, [dueDateMs, t, todayMs]);

  const progressPoints = dashboard?.ProgressPoints ?? [];
  const speedPoints = dashboard?.SpeedPoints ?? [];
  const assignedTotalMinutes = Math.max(0, dashboard?.AssignedTotalMinutes ?? selectedTask?.AssignedManHour ?? 0);
  const progressActualSeries = useMemo(
    () =>
      buildPseudoStepSmoothSeries(
        progressPoints.map((point) => ({ date: point.Date, value: point.ProgressRate }))
      ),
    [progressPoints]
  );
  const progressPlanSeries = useMemo(
    () =>
      buildPseudoStepSmoothSeries(
        progressPoints.map((point) => ({ date: point.Date, value: point.PlannedProgressRate }))
      ),
    [progressPoints]
  );
  const progressActualMhSeries = useMemo(
    () =>
      buildPseudoStepSmoothSeries(
        progressPoints.map((point) => ({ date: point.Date, value: point.ActualCumMinutes }))
      ),
    [progressPoints]
  );
  const progressActualSeriesForChart = useMemo(
    () => extendSeriesToDate(progressActualSeries, dueDateMs, isParentTask),
    [dueDateMs, isParentTask, progressActualSeries]
  );
  const progressPlanSeriesForChart = useMemo(
    () => extendSeriesToDate(progressPlanSeries, dueDateMs, isParentTask),
    [dueDateMs, isParentTask, progressPlanSeries]
  );
  const progressActualMhSeriesForChart = useMemo(
    () => extendSeriesToDate(progressActualMhSeries, dueDateMs, isParentTask),
    [dueDateMs, isParentTask, progressActualMhSeries]
  );
  const speedActualSeries = useMemo(
    () =>
      buildPseudoStepSmoothSeries(
        speedPoints.map((point) => ({ date: point.Date, value: point.VActual }))
      ),
    [speedPoints]
  );
  const speedAverageSeries = useMemo(
    () =>
      buildPseudoStepSmoothSeries(
        speedPoints.map((point) => ({ date: point.Date, value: point.VAverage }))
      ),
    [speedPoints]
  );
  const speedCriticalSeries = useMemo(
    () =>
      buildPseudoStepSmoothSeries(
        speedPoints.map((point) => ({ date: point.Date, value: point.VCritical }))
      ),
    [speedPoints]
  );
  const speedSafeSeries = useMemo(
    () =>
      buildPseudoStepSmoothSeries(
        speedPoints.map((point) => ({ date: point.Date, value: point.VSafe }))
      ),
    [speedPoints]
  );

  const fitXRange = useMemo(() => {
    const values: number[] = [];
    for (const [ms] of progressActualSeriesForChart) values.push(ms);
    for (const [ms] of progressPlanSeriesForChart) values.push(ms);
    for (const [ms] of progressActualMhSeriesForChart) values.push(ms);
    for (const [ms] of speedActualSeries) values.push(ms);
    for (const [ms] of speedAverageSeries) values.push(ms);
    for (const [ms] of speedCriticalSeries) values.push(ms);
    for (const [ms] of speedSafeSeries) values.push(ms);
    if (startDateMs !== null) values.push(startDateMs);
    if (dueDateMs !== null) values.push(dueDateMs);
    values.push(todayMs);
    return buildTimeRange(values);
  }, [
    startDateMs,
    dueDateMs,
    todayMs,
    progressActualMhSeriesForChart,
    progressActualSeriesForChart,
    progressPlanSeriesForChart,
    speedActualSeries,
    speedAverageSeries,
    speedCriticalSeries,
    speedSafeSeries,
  ]);

  const fitProgressMhYRange = useMemo(
    () => buildValueRange([...progressActualMhSeriesForChart.map((point) => point[1]), assignedTotalMinutes], 0),
    [assignedTotalMinutes, progressActualMhSeriesForChart]
  );

  const fitSpeedYRange = useMemo(
    () =>
      buildValueRange(
        [
          ...speedActualSeries.map((point) => point[1]),
          ...speedAverageSeries.map((point) => point[1]),
          ...speedCriticalSeries.map((point) => point[1]),
          ...speedSafeSeries.map((point) => point[1]),
        ],
        0
      ),
    [speedActualSeries, speedAverageSeries, speedCriticalSeries, speedSafeSeries]
  );

  useEffect(() => {
    setXViewRange((current) => {
      if (current === null || !lockX) return fitXRange;
      return current;
    });
  }, [fitXRange, lockX]);

  useEffect(() => {
    setProgressMhYViewRange((current) => {
      if (current === null || !lockY) return fitProgressMhYRange;
      return current;
    });
    setSpeedYViewRange((current) => {
      if (current === null || !lockY) return fitSpeedYRange;
      return current;
    });
  }, [fitProgressMhYRange, fitSpeedYRange, lockY]);

  const resetViewport = useCallback(() => {
    setXViewRange(fitXRange);
    setProgressMhYViewRange(fitProgressMhYRange);
    setSpeedYViewRange(fitSpeedYRange);
  }, [fitProgressMhYRange, fitSpeedYRange, fitXRange]);

  const handleChartWheel = useCallback(
    (event: WheelEvent, chartKind: ChartKind) => {
      event.preventDefault();
      const target = event.currentTarget as HTMLElement | null;
      if (!target) return;
      const rect = target.getBoundingClientRect();
      const width = Math.max(rect.width, 1);
      const height = Math.max(rect.height, 1);
      const wheelDelta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;

      if (event.ctrlKey && event.shiftKey) {
        const yRatioFromTop = clamp((event.clientY - rect.top) / height, 0, 1);
        const anchorRatio = 1 - yRatioFromTop;
        const zoomFactor = Math.exp(event.deltaY * WHEEL_ZOOM_SENSITIVITY);

        if (chartKind === "progress") {
          setProgressMhYViewRange((current) =>
            zoomValueRange(current ?? fitProgressMhYRange, zoomFactor, anchorRatio, 0)
          );
        } else {
          setSpeedYViewRange((current) =>
            zoomValueRange(current ?? fitSpeedYRange, zoomFactor, anchorRatio, 0)
          );
        }
        return;
      }

      if (event.ctrlKey) {
        const xRatio = clamp((event.clientX - rect.left) / width, 0, 1);
        const zoomFactor = Math.exp(event.deltaY * WHEEL_ZOOM_SENSITIVITY);
        setXViewRange((current) =>
          zoomRange(current ?? fitXRange, zoomFactor, xRatio, MIN_TIME_SPAN_MS, MAX_TIME_SPAN_MS)
        );
        return;
      }

      setXViewRange((current) => {
        const base = current ?? fitXRange;
        const span = Math.max(MIN_TIME_SPAN_MS, base.max - base.min);
        const offset = span * wheelDelta * WHEEL_PAN_RATIO;
        return panRange(base, offset);
      });
    },
    [fitProgressMhYRange, fitSpeedYRange, fitXRange]
  );

  useEffect(() => {
    if (!progressChart) return;
    const dom = progressChart.getDom();
    const wheelListener = (event: WheelEvent) => handleChartWheel(event, "progress");
    const doubleClickListener = () => resetViewport();
    dom.addEventListener("wheel", wheelListener, { passive: false });
    dom.addEventListener("dblclick", doubleClickListener);
    return () => {
      dom.removeEventListener("wheel", wheelListener);
      dom.removeEventListener("dblclick", doubleClickListener);
    };
  }, [handleChartWheel, progressChart, resetViewport]);

  useEffect(() => {
    if (!speedChart) return;
    const dom = speedChart.getDom();
    const wheelListener = (event: WheelEvent) => handleChartWheel(event, "speed");
    const doubleClickListener = () => resetViewport();
    dom.addEventListener("wheel", wheelListener, { passive: false });
    dom.addEventListener("dblclick", doubleClickListener);
    return () => {
      dom.removeEventListener("wheel", wheelListener);
      dom.removeEventListener("dblclick", doubleClickListener);
    };
  }, [handleChartWheel, resetViewport, speedChart]);

  const progressOption = useMemo<EChartsOption>(
    () => ({
      animationDuration: 220,
      tooltip: { trigger: "axis" },
      legend: { top: 0 },
      xAxis: {
        type: "time",
        min: xViewRange?.min,
        max: xViewRange?.max,
        axisLabel: {
          formatter: (value: number) => formatDay(value),
        },
      },
      yAxis: [
        { type: "value", min: 0, max: 100, name: t("progress_view.axis_progress_pct", "Progress (%)") },
        {
          type: "value",
          min: progressMhYViewRange?.min ?? 0,
          max: progressMhYViewRange?.max,
          name: t("progress_view.axis_actual_mh_min", "Actual MH (min)"),
        },
      ],
      series: [
        {
          name: t("progress_view.legend_complete_task", "Completion Forecast"),
          type: "line",
          data: progressActualSeriesForChart,
          smooth: true,
          areaStyle: { opacity: 0.18 },
          showSymbol: false,
          markLine: referenceDateMarkLine,
        },
        {
          name: t("progress_view.legend_plan", "Plan"),
          type: "line",
          data: progressPlanSeriesForChart,
          smooth: true,
          lineStyle: { type: "dashed" },
          showSymbol: false,
        },
        {
          name: t("progress_view.legend_actual_mh", "Actual MH"),
          type: "line",
          data: progressActualMhSeriesForChart,
          smooth: true,
          showSymbol: false,
          yAxisIndex: 1,
          lineStyle: { width: 2 },
        },
      ],
    }),
    [
      referenceDateMarkLine,
      progressActualMhSeriesForChart,
      progressActualSeriesForChart,
      progressMhYViewRange,
      progressPlanSeriesForChart,
      t,
      xViewRange,
    ]
  );

  const speedOption = useMemo<EChartsOption>(
    () => ({
      animationDuration: 220,
      tooltip: { trigger: "axis" },
      legend: { top: 0 },
      xAxis: {
        type: "time",
        min: xViewRange?.min,
        max: xViewRange?.max,
        axisLabel: {
          formatter: (value: number) => formatDay(value),
        },
      },
      yAxis: {
        type: "value",
        name: t("progress_view.axis_speed_min_day", "min/day"),
        min: speedYViewRange?.min,
        max: speedYViewRange?.max,
      },
      series: [
        {
          name: t("progress_view.legend_actual_speed", "Actual Speed"),
          type: "line",
          data: speedActualSeries,
          smooth: true,
          showSymbol: false,
          markLine: referenceDateMarkLine,
        },
        {
          name: t("progress_view.legend_average_speed", "Average Speed"),
          type: "line",
          data: speedAverageSeries,
          smooth: true,
          lineStyle: { type: "solid", width: 2 },
          showSymbol: false,
        },
        {
          name: t("progress_view.legend_critical_speed", "Critical Speed"),
          type: "line",
          data: speedCriticalSeries,
          smooth: true,
          lineStyle: { type: "dashed" },
          showSymbol: false,
        },
        {
          name: t("progress_view.legend_safe_speed", "Safe Speed"),
          type: "line",
          data: speedSafeSeries,
          smooth: true,
          lineStyle: { type: "dashed" },
          showSymbol: false,
        },
      ],
    }),
    [referenceDateMarkLine, speedActualSeries, speedAverageSeries, speedCriticalSeries, speedSafeSeries, speedYViewRange, t, xViewRange]
  );

  const latestSpeed = speedPoints.length > 0 ? speedPoints[speedPoints.length - 1] : null;
  const selectedSignal = dashboard?.Signal ?? selectedTask?.Signal ?? "Blue";

  return (
    <div className="view-stack">
      <div className="panel">
        <div className="panel-title">{t("progress_view.task_tree_title", "Target Task (WBS)")}</div>
        <div className="progress-wbs-strip">
          <Tree
            data={treeData}
            width={size.width}
            height={170}
            rowHeight={32}
            selection={selectedTaskId ?? undefined}
            selectionFollowsFocus
            onFocus={(node) => {
              if (node.id !== selectedTaskId) {
                onSelectTask(node.id);
              }
            }}
          >
            {({ node, style }) => (
              <div style={style}>
                <div
                  className={`progress-wbs-row${selectedTaskId === node.id ? " active" : ""}`}
                  onClick={() => onSelectTask(node.id)}
                >
                  <span className="progress-wbs-name">{formatDisplayText(node.data.task.Name)}</span>
                  <IquavisSyncChip task={node.data.task} />
                  <span className="progress-wbs-rate">{node.data.task.ProgressRate.toFixed(1)}%</span>
                </div>
              </div>
            )}
          </Tree>
        </div>
      </div>

      <IquavisConflictPanel task={selectedTask} />

      {selectedTask ? (
        <div className={`signal-card ${signalClass(selectedSignal)}`}>
          <div className="panel-title">
            {t("progress_view.signal", "Signal")}: {selectedSignal}
          </div>
          <div className="subtext">
            {t("progress_view.task", "Task")}: {formatDisplayText(selectedTask.Name)}
          </div>
          <div className="subtext">
            {t("progress_view.progress", "Progress")}: {selectedTask.ProgressRate.toFixed(1)}%
          </div>
          <div className="subtext">
            {t("progress_view.speed_summary", "V_actual: {actual} / V_average: {average} / V_critical: {critical} / V_safe: {safe}", {
              actual: (latestSpeed?.VActual ?? 0).toFixed(2),
              average: (latestSpeed?.VAverage ?? 0).toFixed(2),
              critical: (latestSpeed?.VCritical ?? 0).toFixed(2),
              safe: (latestSpeed?.VSafe ?? 0).toFixed(2),
            })}
          </div>
        </div>
      ) : (
        <div className="empty">{t("progress_view.select_task", "Please select a task")}</div>
      )}

      <div className="chart-wrap progress-chart-card">
        <div className="panel-title">{t("progress_view.chart_controls", "Chart Controls")}</div>
        <div className="progress-chart-toolbar">
          <button type="button" className={`btn${lockX ? " primary" : ""}`} onClick={() => setLockX((v) => !v)}>
            {t("progress_view.lock_x", "Lock X")}
          </button>
          <button type="button" className={`btn${lockY ? " primary" : ""}`} onClick={() => setLockY((v) => !v)}>
            {t("progress_view.lock_y", "Lock Y")}
          </button>
          <button type="button" className="btn" onClick={resetViewport}>
            {t("progress_view.fit", "Fit")}
          </button>
          <span className="subtext">
            {t("progress_view.wheel_hint", "Wheel: pan X / Ctrl+Wheel: zoom X / Shift+Ctrl+Wheel: zoom Y / Double click: reset")}
          </span>
        </div>
      </div>

      <div className="chart-wrap progress-chart-card">
        <div className="panel-title">{t("progress_view.progress_chart", "Progress")}</div>
        <div className="progress-chart-body">
          <ReactECharts
            option={progressOption}
            notMerge
            style={{ width: "100%", height: 240 }}
            onChartReady={setProgressChart}
          />
        </div>
      </div>

      <div className="chart-wrap progress-chart-card">
        <div className="panel-title">{t("progress_view.speed_chart", "Speed")}</div>
        <div className="progress-chart-body">
          <ReactECharts
            option={speedOption}
            notMerge
            style={{ width: "100%", height: 240 }}
            onChartReady={setSpeedChart}
          />
        </div>
      </div>
    </div>
  );
}




