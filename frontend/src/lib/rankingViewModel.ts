import type { RankItem, RankingHistoryPoint, Rankings } from "../types";

export type RankingMetric = keyof Rankings;
export type RankingPeriodMonths = 1 | 3 | 6 | 12;

interface ParsedYearMonth {
  year: number;
  month: number;
}

function parseYearMonth(value: string): ParsedYearMonth | null {
  const match = /^(\d{4})-(\d{2})$/.exec(value.trim());
  if (!match) return null;

  const year = Number(match[1]);
  const month = Number(match[2]);
  if (!Number.isInteger(year) || !Number.isInteger(month) || month < 1 || month > 12) {
    return null;
  }
  return { year, month };
}

function currentYearMonth(): ParsedYearMonth {
  const now = new Date();
  return {
    year: now.getFullYear(),
    month: now.getMonth() + 1,
  };
}

function toYearMonth(value: ParsedYearMonth): string {
  return `${String(value.year).padStart(4, "0")}-${String(value.month).padStart(2, "0")}`;
}

export function formatRankingMetricValue(_metric: RankingMetric, value: number): string {
  return String(Math.round(value));
}

export function rankingMetricUsesMinuteUnit(metric: RankingMetric): boolean {
  return metric === "OutputVolume" || metric === "TeachingTime";
}

export function shiftRankingYearMonth(value: string, deltaMonths: number): string {
  const base = parseYearMonth(value) ?? currentYearMonth();
  const totalMonths = (base.year * 12) + (base.month - 1) + deltaMonths;
  const year = Math.floor(totalMonths / 12);
  const month = (totalMonths % 12) + 1;
  return toYearMonth({ year, month });
}

export function formatRankingYearMonthLabel(value: string, language: string): string {
  const parsed = parseYearMonth(value) ?? currentYearMonth();
  if (language === "ja") {
    return `${parsed.year}年${parsed.month}月`;
  }
  return toYearMonth(parsed);
}

export function buildRankingHighlights(rows: RankItem[]): {
  topPublished: RankItem | null;
  selfRow: RankItem | null;
} {
  return {
    topPublished: rows.find((row) => row.IsPublished) ?? null,
    selfRow: rows.find((row) => row.IsSelf) ?? null,
  };
}

function parseIsoDate(value: string): Date | null {
  if (!value) return null;
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  return parsed;
}

export function formatRankingHistoryLabel(
  value: string,
  periodMonths: RankingPeriodMonths,
  language: string
): string {
  const parsed = parseIsoDate(value);
  if (parsed === null) {
    return value;
  }
  const month = parsed.getMonth() + 1;
  const day = parsed.getDate();
  if (periodMonths === 1) {
    if (language === "ja") {
      return `${month}/${day}`;
    }
    return `${String(month).padStart(2, "0")}/${String(day).padStart(2, "0")}`;
  }
  if (language === "ja") {
    return `${month}月`;
  }
  return `${parsed.getFullYear()}-${String(month).padStart(2, "0")}`;
}

export function buildRankingTrendSeries(
  points: RankingHistoryPoint[] | undefined,
  periodMonths: RankingPeriodMonths,
  language: string
): { labels: string[]; values: number[] } {
  const safePoints = points ?? [];
  return {
    labels: safePoints.map((point) => formatRankingHistoryLabel(point.Date, periodMonths, language)),
    values: safePoints.map((point) => Math.round(point.Value)),
  };
}

export function buildRankingTimeSeries(points: RankingHistoryPoint[] | undefined): Array<[number, number]> {
  const safePoints = points ?? [];
  return safePoints
    .map((point) => {
      const parsed = parseIsoDate(point.Date);
      if (parsed === null) {
        return null;
      }
      return [parsed.getTime(), Math.round(point.Value)] as [number, number];
    })
    .filter((point): point is [number, number] => point !== null);
}

function nextMonthStart(parsed: ParsedYearMonth): ParsedYearMonth {
  const month = parsed.month === 12 ? 1 : parsed.month + 1;
  const year = parsed.month === 12 ? parsed.year + 1 : parsed.year;
  return { year, month };
}

export function buildRankingWindowRange(
  value: string,
  periodMonths: RankingPeriodMonths
): { startMs: number; endMs: number } {
  const anchor = parseYearMonth(value) ?? currentYearMonth();
  const totalMonths = (anchor.year * 12) + (anchor.month - 1);
  const startTotalMonths = totalMonths - (periodMonths - 1);
  const startYear = Math.floor(startTotalMonths / 12);
  const startMonth = (startTotalMonths % 12) + 1;
  const start = new Date(startYear, startMonth - 1, 1);
  const next = nextMonthStart(anchor);
  const end = new Date(next.year, next.month - 1, 1);
  return {
    startMs: start.getTime(),
    endMs: end.getTime(),
  };
}

export function buildRankingBaselineValue(
  metric: RankingMetric,
  periodMonths: RankingPeriodMonths
): number {
  if (metric === "WorkSpeed" || metric === "EducationEfficiency") {
    return 100;
  }
  return 160 * 60 * periodMonths;
}

export function buildRankingChartWindow(
  pointCount: number,
  selectedIndex: number,
  periodMonths: RankingPeriodMonths
): { startIndex: number; endIndex: number; visibleCount: number } {
  const safePointCount = Math.max(0, pointCount);
  if (safePointCount === 0) {
    return { startIndex: 0, endIndex: 0, visibleCount: periodMonths * 5 };
  }

  const safeSelectedIndex = Math.min(Math.max(selectedIndex, 0), safePointCount - 1);
  const visibleCount = Math.max(periodMonths * 5, 1);
  const maxStartIndex = Math.max(0, safePointCount - visibleCount);
  const startIndex = Math.min(Math.max(0, safeSelectedIndex - visibleCount + 1), maxStartIndex);
  const endIndex = Math.min(safePointCount - 1, startIndex + visibleCount - 1);
  return { startIndex, endIndex, visibleCount };
}
