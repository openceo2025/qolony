import { useMemo } from "react";
import ReactECharts from "echarts-for-react";
import type { EChartsOption } from "echarts";
import { useI18n } from "../lib/i18n";
import {
  buildRankingBaselineValue,
  buildRankingChartWindow,
  buildRankingHighlights,
  formatRankingMetricValue,
  formatRankingYearMonthLabel,
  rankingMetricUsesMinuteUnit,
  shiftRankingYearMonth,
  type RankingMetric,
  type RankingPeriodMonths,
} from "../lib/rankingViewModel";
import type { RankingHistoryResponse, Rankings } from "../types";

interface RankingViewProps {
  rankings: Rankings | undefined;
  history: RankingHistoryResponse | undefined;
  isTopManager: boolean;
  yearMonth: string;
  periodMonths: RankingPeriodMonths;
  onChangeYearMonth: (value: string) => void;
  onChangePeriodMonths: (value: RankingPeriodMonths) => void;
}

interface MetricSection {
  metric: RankingMetric;
  label: string;
  description: string;
  selfTitle: string;
  trendTitle: string;
}

function buildMetricSections(language: string): MetricSection[] {
  if (language === "ja") {
    return [
      {
        metric: "OutputVolume",
        label: "成果量",
        description: "対象期間に完了したタスクの見積残高を、親子で二重計上しないよう合計します。",
        selfTitle: "あなたの成果量",
        trendTitle: "成果量の推移",
      },
      {
        metric: "WorkSpeed",
        label: "業務スピード",
        description: "成果量 ÷ 実績MH × 100 です。見積残高と実績は親子で二重計上しません。",
        selfTitle: "あなたの業務スピード",
        trendTitle: "業務スピードの推移",
      },
      {
        metric: "EducationEfficiency",
        label: "教育効率",
        description:
          "対象期間に完了したタスクのうち、教師つき OJT 実績が過半数を超えるタスクだけを対象に、成果量 ÷ 実績MH × 200 で算出します。100 が基準です。",
        selfTitle: "あなたの教育効率",
        trendTitle: "教育効率の推移",
      },
      {
        metric: "TeachingTime",
        label: "教育時間",
        description: "対象期間に完了したタスクで、教師として紐づいていた OJT 実績MHの合計です。",
        selfTitle: "あなたの教育時間",
        trendTitle: "教育時間の推移",
      },
    ];
  }

  return [
    {
      metric: "OutputVolume",
      label: "Output Volume",
      description: "Completed task estimate balance in the selected period, counted once across parent and child tasks.",
      selfTitle: "Your Output Volume",
      trendTitle: "Output Volume Trend",
    },
    {
      metric: "WorkSpeed",
      label: "Work Speed",
      description: "Output volume divided by actual MH, with parent and child estimates and actuals counted once.",
      selfTitle: "Your Work Speed",
      trendTitle: "Work Speed Trend",
    },
    {
      metric: "EducationEfficiency",
      label: "Education Efficiency",
      description:
        "For completed tasks where your teacher-linked OJT effort exceeds half of the task effort, output volume divided by actual MH and scaled so 100 is the baseline.",
      selfTitle: "Your Education Efficiency",
      trendTitle: "Education Efficiency Trend",
    },
    {
      metric: "TeachingTime",
      label: "Teaching Time",
      description: "Total OJT effort recorded while you were linked as the teacher on completed tasks.",
      selfTitle: "Your Teaching Time",
      trendTitle: "Teaching Time Trend",
    },
  ];
}

function buildPeriodOptions(language: string): Array<{ value: RankingPeriodMonths; label: string }> {
  if (language === "ja") {
    return [
      { value: 1, label: "1か月" },
      { value: 3, label: "3か月" },
      { value: 6, label: "半年" },
      { value: 12, label: "1年" },
    ];
  }
  return [
    { value: 1, label: "1 Month" },
    { value: 3, label: "3 Months" },
    { value: 6, label: "6 Months" },
    { value: 12, label: "1 Year" },
  ];
}

function formatCategoryLabel(value: string, language: string): string {
  const date = new Date(`${value}T00:00:00`);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const year = date.getFullYear();
  const month = date.getMonth() + 1;
  if (language === "ja") {
    return `${year}/${month}`;
  }
  return `${year}-${String(month).padStart(2, "0")}`;
}

function buildMonthCategorySequence(startYearMonth: string, endYearMonth: string): string[] {
  const categories: string[] = [];
  let cursor = startYearMonth;
  while (cursor <= endYearMonth) {
    categories.push(`${cursor}-01`);
    const next = shiftRankingYearMonth(cursor, 1);
    if (next === cursor) {
      break;
    }
    cursor = next;
  }
  return categories;
}

function buildTrendChartOption(args: {
  metric: RankingMetric;
  history: RankingHistoryResponse | undefined;
  periodMonths: RankingPeriodMonths;
  yearMonth: string;
  language: string;
  isTopManager: boolean;
  unitSuffix: string;
}): EChartsOption {
  const seriesRows = args.history?.[args.metric] ?? [];
  const rawCategories = Array.from(
    new Set(seriesRows.flatMap((row) => row.Points.map((point) => point.Date)))
  ).sort();
  const paddedStartYearMonth = shiftRankingYearMonth(args.yearMonth, -((args.periodMonths * 5) - 1));
  const firstYearMonth = rawCategories[0]?.slice(0, 7) ?? paddedStartYearMonth;
  const lastYearMonth = rawCategories[rawCategories.length - 1]?.slice(0, 7) ?? args.yearMonth;
  const categories = buildMonthCategorySequence(
    firstYearMonth < paddedStartYearMonth ? firstYearMonth : paddedStartYearMonth,
    lastYearMonth > args.yearMonth ? lastYearMonth : args.yearMonth,
  );
  const selectedIndex = Math.max(0, categories.findIndex((value) => value.startsWith(args.yearMonth)));
  const defaultWindow = buildRankingChartWindow(categories.length, selectedIndex, args.periodMonths);
  const startIndex = defaultWindow.startIndex;
  const endIndex = defaultWindow.endIndex;
  const baselineValue = buildRankingBaselineValue(args.metric, args.periodMonths);
  const baselineLabel =
    args.metric === "WorkSpeed" || args.metric === "EducationEfficiency"
      ? (args.language === "ja" ? "基準速度" : "Baseline")
      : (args.language === "ja" ? "定時" : "Regular Hours");
  const referenceMonthLabel = args.language === "ja" ? "基準月" : "Anchor";

  const allValues = seriesRows.flatMap((row) => row.Points.map((point) => Math.round(point.Value)));
  const maxValue = Math.max(baselineValue, ...allValues, 0);
  const yMax = Math.max(1, Math.ceil(maxValue * 1.1));

  const series = seriesRows.map((row, index) => {
    const pointMap = new Map(row.Points.map((point) => [point.Date, Math.round(point.Value)]));
    return ({
    name: row.UserName,
    type: "line" as const,
    data: categories.map((category) => pointMap.get(category) ?? 0),
    smooth: false,
    showSymbol: row.IsSelf,
    symbolSize: row.IsSelf ? 7 : 5,
    connectNulls: true,
    lineStyle: {
      width: row.IsSelf ? 3 : 2,
      opacity: row.IsSelf ? 1 : 0.62,
    },
    emphasis: {
      focus: "series" as const,
    },
    z: row.IsSelf ? 3 : 2,
    markLine:
      index === 0 && categories[selectedIndex]
        ? {
            silent: true,
            symbol: ["none", "none"] as [string, string],
            label: { show: true },
            data: [
              {
                yAxis: baselineValue,
                label: {
                  formatter: baselineLabel,
                  position: "insideEndTop" as const,
                  padding: [2, 6],
                  borderRadius: 6,
                  backgroundColor: "rgba(255,255,255,0.92)",
                  color: "#92400e",
                },
                lineStyle: { color: "#b45309", width: 2, type: "dashed" as const },
              },
              {
                xAxis: categories[selectedIndex],
                label: {
                  formatter: referenceMonthLabel,
                  position: "insideStartTop" as const,
                  padding: [2, 6],
                  borderRadius: 6,
                  backgroundColor: "rgba(255,255,255,0.92)",
                  color: "#1d4ed8",
                },
                lineStyle: { color: "#2563eb", width: 2, type: "solid" as const },
              },
            ],
          }
        : undefined,
    });
  });

  return {
    animationDuration: 220,
    legend: args.isTopManager
      ? {
          top: 0,
          type: "scroll",
        }
      : undefined,
    grid: {
      left: 48,
      right: 28,
      top: args.isTopManager ? 68 : 44,
      bottom: 38,
      containLabel: true,
    },
    tooltip: {
      trigger: "axis",
      valueFormatter: (value) => `${Math.round(Number(value ?? 0))}${args.unitSuffix}`,
    },
    xAxis: {
      type: "category",
      data: categories,
      boundaryGap: false,
      axisTick: {
        alignWithLabel: true,
        interval: (index: number) => Math.abs(index - selectedIndex) % args.periodMonths === 0,
      },
      axisLabel: {
        interval: (index: number) => Math.abs(index - selectedIndex) % args.periodMonths === 0,
        formatter: (value: string) => formatCategoryLabel(value, args.language),
        margin: 10,
      },
    },
    yAxis: {
      type: "value",
      min: 0,
      max: yMax,
      axisLabel: {
        margin: 12,
      },
      splitLine: { lineStyle: { color: "#dfe9e2" } },
    },
    dataZoom: [
      {
        type: "inside",
        xAxisIndex: 0,
        filterMode: "none",
        moveOnMouseWheel: true,
        zoomOnMouseWheel: false,
        moveOnMouseMove: false,
        startValue: startIndex,
        endValue: endIndex,
      },
    ],
    series,
  };
}

interface RankingMonthNavigatorProps {
  yearMonth: string;
  yearMonthLabel: string;
  onChangeYearMonth: (value: string) => void;
  basisMonthLabel: string;
}

function RankingMonthNavigator({
  yearMonth,
  yearMonthLabel,
  onChangeYearMonth,
  basisMonthLabel,
}: RankingMonthNavigatorProps): JSX.Element {
  return (
    <div className="ranking-month-nav" aria-label="ranking-month-navigation">
      <span className="ranking-month-nav-title">{basisMonthLabel}</span>
      <button
        className="btn"
        type="button"
        aria-label="previous ranking month"
        onClick={() => onChangeYearMonth(shiftRankingYearMonth(yearMonth, -1))}
      >
        {"<"}
      </button>
      <div className="ranking-month-label">{yearMonthLabel}</div>
      <button
        className="btn"
        type="button"
        aria-label="next ranking month"
        onClick={() => onChangeYearMonth(shiftRankingYearMonth(yearMonth, 1))}
      >
        {">"}
      </button>
    </div>
  );
}

export function RankingView({
  rankings,
  history,
  isTopManager,
  yearMonth,
  periodMonths,
  onChangeYearMonth,
  onChangePeriodMonths,
}: RankingViewProps): JSX.Element {
  const { language, t } = useI18n();
  const yearMonthLabel = formatRankingYearMonthLabel(yearMonth, language);
  const sections = useMemo(() => buildMetricSections(language), [language]);
  const periodOptions = useMemo(() => buildPeriodOptions(language), [language]);
  const emptyMessage = t("ranking.empty", "No ranking data for this period");
  const basisMonthLabel = t("ranking.anchor_month_label", "基準月");

  const describeVisibility = (isPublished: boolean): string =>
    isPublished ? t("ranking.visibility_published", "Published") : t("ranking.visibility_hidden", "Hidden");

  return (
    <div className="view-stack">
      <div className="panel">
        <div className="panel-title">{t("ranking.title", "Ranking")}</div>
        <div className="toolbar-row">
          <RankingMonthNavigator
            yearMonth={yearMonth}
            yearMonthLabel={yearMonthLabel}
            onChangeYearMonth={onChangeYearMonth}
            basisMonthLabel={basisMonthLabel}
          />
          <label className="ranking-period-filter">
            <span>{t("ranking.period_label", "集計期間")}</span>
            <select
              aria-label="ranking-period-months"
              value={String(periodMonths)}
              onChange={(e) => onChangePeriodMonths(Number(e.currentTarget.value) as RankingPeriodMonths)}
            >
              {periodOptions.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
        </div>
        <div className="subtext">
          {isTopManager
            ? t("ranking.admin_visibility", "Admin view shows all members.")
            : t("ranking.publication_rule", "Only the top 50% are visible to everyone. You always see your own value and rank.")}
        </div>
      </div>

      {sections.map((section) => {
        const rows = rankings?.[section.metric] ?? [];
        const { selfRow } = buildRankingHighlights(rows);
        const usesMinutes = rankingMetricUsesMinuteUnit(section.metric);
        const unitSuffix = usesMinutes ? t("common.minutes", "min") : "";
        const trendSeries = history?.[section.metric] ?? [];
        const trendOption = buildTrendChartOption({
          metric: section.metric,
          history,
          periodMonths,
          yearMonth,
          language,
          isTopManager,
          unitSuffix,
        });

        return (
          <section className="panel" key={section.metric}>
            <div className="panel-title">{section.label}</div>
            <div className="subtext">{section.description}</div>
            <div className="ranking-section-toolbar">
              <RankingMonthNavigator
                yearMonth={yearMonth}
                yearMonthLabel={yearMonthLabel}
                onChangeYearMonth={onChangeYearMonth}
                basisMonthLabel={basisMonthLabel}
              />
            </div>

            <div className="ranking-summary-grid">
              <section className="panel ranking-self-card">
                <div className="panel-title">{section.selfTitle}</div>
                {selfRow ? (
                  <>
                    <div className="ranking-spotlight-rank">No.{selfRow.Rank}</div>
                    <div className="ranking-spotlight-name">
                      {selfRow.UserName}
                      <span className="ranking-self-chip">YOU</span>
                    </div>
                    <div className="ranking-spotlight-value">
                      {formatRankingMetricValue(section.metric, selfRow.Value)}
                      {unitSuffix}
                    </div>
                    {isTopManager ? (
                      <div className="subtext">{describeVisibility(selfRow.IsPublished)}</div>
                    ) : null}
                  </>
                ) : (
                  <div className="subtext">{emptyMessage}</div>
                )}
              </section>

              <section className="panel ranking-trend-card">
                <div className="panel-title">{section.trendTitle}</div>
                {trendSeries.length > 0 ? (
                  <ReactECharts option={trendOption} notMerge style={{ width: "100%", height: 220 }} />
                ) : (
                  <div className="empty ranking-trend-empty">{emptyMessage}</div>
                )}
              </section>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("ranking.column_rank", "Rank")}</th>
                    <th>{t("ranking.column_name", "Name")}</th>
                    <th>{section.label}</th>
                    {isTopManager ? <th>{t("ranking.column_visibility", "Visibility")}</th> : null}
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 ? (
                    <tr>
                      <td colSpan={isTopManager ? 4 : 3}>
                        <div className="empty">{emptyMessage}</div>
                      </td>
                    </tr>
                  ) : (
                    rows.map((row) => (
                      <tr key={`${section.metric}-${row.UserId}-${row.Rank}`} className={row.IsSelf ? "ranking-self-row" : ""}>
                        <td>{`No.${row.Rank}`}</td>
                        <td>
                          {row.UserName}
                          {row.IsSelf ? <span className="ranking-self-chip">YOU</span> : null}
                        </td>
                        <td>
                          {formatRankingMetricValue(section.metric, row.Value)}
                          {unitSuffix}
                        </td>
                        {isTopManager ? <td>{describeVisibility(row.IsPublished)}</td> : null}
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
    </div>
  );
}
