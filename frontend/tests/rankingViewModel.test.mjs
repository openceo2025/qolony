import assert from "node:assert/strict";
import test from "node:test";

import {
  buildRankingBaselineValue,
  buildRankingChartWindow,
  buildRankingHighlights,
  buildRankingTimeSeries,
  buildRankingTrendSeries,
  buildRankingWindowRange,
  formatRankingMetricValue,
  formatRankingHistoryLabel,
  formatRankingYearMonthLabel,
  rankingMetricUsesMinuteUnit,
  shiftRankingYearMonth,
} from "../src/lib/rankingViewModel.ts";

test("formatRankingMetricValue formats minutes and ratios appropriately", () => {
  assert.equal(formatRankingMetricValue("TeachingTime", 125.2), "125");
  assert.equal(formatRankingMetricValue("WorkSpeed", 83.6), "84");
});

test("buildRankingHighlights picks top published row and self row", () => {
  const rows = [
    { UserId: "u1", UserName: "A", Value: 2, Rank: 1, IsPublished: true, IsSelf: false },
    { UserId: "u2", UserName: "B", Value: 1, Rank: 3, IsPublished: false, IsSelf: true },
  ];

  const result = buildRankingHighlights(rows);

  assert.equal(result.topPublished?.UserId, "u1");
  assert.equal(result.selfRow?.UserId, "u2");
});

test("shiftRankingYearMonth moves to previous and next months", () => {
  assert.equal(shiftRankingYearMonth("2026-04", -1), "2026-03");
  assert.equal(shiftRankingYearMonth("2026-04", 1), "2026-05");
  assert.equal(shiftRankingYearMonth("2026-01", -1), "2025-12");
});

test("formatRankingYearMonthLabel renders a Japanese month label", () => {
  assert.equal(formatRankingYearMonthLabel("2026-04", "ja"), "2026年4月");
  assert.equal(formatRankingYearMonthLabel("2026-04", "en"), "2026-04");
});

test("rankingMetricUsesMinuteUnit distinguishes amount metrics from speed metrics", () => {
  assert.equal(rankingMetricUsesMinuteUnit("OutputVolume"), true);
  assert.equal(rankingMetricUsesMinuteUnit("TeachingTime"), true);
  assert.equal(rankingMetricUsesMinuteUnit("WorkSpeed"), false);
  assert.equal(rankingMetricUsesMinuteUnit("EducationEfficiency"), false);
});

test("formatRankingHistoryLabel switches between daily and monthly labels", () => {
  assert.equal(formatRankingHistoryLabel("2026-04-18", 1, "ja"), "4/18");
  assert.equal(formatRankingHistoryLabel("2026-04-01", 3, "ja"), "4月");
  assert.equal(formatRankingHistoryLabel("2026-04-01", 12, "en"), "2026-04");
});

test("buildRankingTrendSeries maps history points to chart labels and rounded values", () => {
  const result = buildRankingTrendSeries(
    [
      { Date: "2026-04-01", Value: 99.6 },
      { Date: "2026-04-02", Value: 120.2 },
    ],
    1,
    "ja"
  );

  assert.deepEqual(result.labels, ["4/1", "4/2"]);
  assert.deepEqual(result.values, [100, 120]);
});

test("buildRankingTimeSeries converts dates to epoch pairs", () => {
  const result = buildRankingTimeSeries([
    { Date: "2026-04-01", Value: 99.6 },
  ]);

  assert.equal(result.length, 1);
  assert.equal(result[0][1], 100);
});

test("buildRankingWindowRange spans the selected rolling period", () => {
  const monthly = buildRankingWindowRange("2026-04", 1);
  const quarter = buildRankingWindowRange("2026-04", 3);
  const formatLocalDate = (value) => {
    const date = new Date(value);
    return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
  };

  assert.equal(formatLocalDate(monthly.startMs), "2026-04-01");
  assert.equal(formatLocalDate(monthly.endMs), "2026-05-01");
  assert.equal(formatLocalDate(quarter.startMs), "2026-02-01");
  assert.equal(formatLocalDate(quarter.endMs), "2026-05-01");
});

test("buildRankingBaselineValue returns expected reference lines", () => {
  assert.equal(buildRankingBaselineValue("WorkSpeed", 3), 100);
  assert.equal(buildRankingBaselineValue("EducationEfficiency", 12), 100);
  assert.equal(buildRankingBaselineValue("OutputVolume", 1), 9600);
  assert.equal(buildRankingBaselineValue("TeachingTime", 6), 57600);
});

test("buildRankingChartWindow keeps five units visible when enough history exists", () => {
  assert.deepEqual(buildRankingChartWindow(24, 10, 1), {
    startIndex: 6,
    endIndex: 10,
    visibleCount: 5,
  });
  assert.deepEqual(buildRankingChartWindow(24, 10, 3), {
    startIndex: 0,
    endIndex: 14,
    visibleCount: 15,
  });
});

test("buildRankingChartWindow preserves full width near the beginning by extending to the right", () => {
  assert.deepEqual(buildRankingChartWindow(24, 3, 1), {
    startIndex: 0,
    endIndex: 4,
    visibleCount: 5,
  });
});
