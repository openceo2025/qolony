from __future__ import annotations

import io
from datetime import datetime
from math import isfinite

from app.tms_core.domain.speed import calculate_v_critical, calculate_v_safe
from app.tms_core.services.core import ProgressDashboard

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt

    HAS_MATPLOTLIB = True
except Exception:
    HAS_MATPLOTLIB = False


def _compute_speed_series(
    dashboard: ProgressDashboard,
) -> tuple[list[datetime], list[float], list[float], list[float]]:
    history = dashboard.History
    points: list[datetime] = []
    v_actuals: list[float] = []
    v_criticals: list[float] = []
    v_safes: list[float] = []

    for index in range(1, len(history)):
        prev = history[index - 1]
        now = history[index]
        elapsed_days = (now.RecordedAt - prev.RecordedAt).total_seconds() / 86400.0
        if elapsed_days <= 0:
            continue

        progress_now = now.ProgressRate
        remain_days = max(0.0, float((dashboard.ProjectEndDate - now.RecordedAt.date()).days))
        v_actual = (progress_now - prev.ProgressRate) / elapsed_days
        v_critical = calculate_v_critical(progress_now=progress_now, remain_days=remain_days)
        v_safe = calculate_v_safe(
            progress_now=progress_now,
            remain_days=remain_days,
            crash_zone_pct=dashboard.CrashZonePct,
        )
        if not isfinite(v_critical):
            v_critical = 0.0
        if not isfinite(v_safe):
            v_safe = 0.0

        points.append(now.RecordedAt)
        v_actuals.append(v_actual)
        v_criticals.append(v_critical)
        v_safes.append(v_safe)

    if not points and history:
        points = [history[-1].RecordedAt]
        v_actuals = [dashboard.Speed.VActual if isfinite(dashboard.Speed.VActual) else 0.0]
        v_criticals = [dashboard.Speed.VCritical if isfinite(dashboard.Speed.VCritical) else 0.0]
        v_safes = [dashboard.Speed.VSafe if isfinite(dashboard.Speed.VSafe) else 0.0]

    return points, v_actuals, v_criticals, v_safes


def render_progress_chart_png(dashboard: ProgressDashboard, mode: str) -> bytes:
    if not HAS_MATPLOTLIB:
        return b""

    fig, ax = plt.subplots(figsize=(9.0, 4.0), dpi=150)
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#F9FFFC")
    ax.grid(True, linestyle="--", linewidth=0.6, alpha=0.4, color="#8AA29E")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
    ax.tick_params(axis="x", rotation=0, labelsize=9)
    ax.tick_params(axis="y", labelsize=9)

    if mode == "progress":
        x = [item.RecordedAt for item in dashboard.History]
        y = [item.ProgressRate for item in dashboard.History]
        if not x:
            x = [datetime.now()]
            y = [0.0]
        ax.plot(
            x,
            y,
            color="#0EA5A4",
            linewidth=2.4,
            marker="o",
            markersize=3.5,
            label="ProgressRate(%)",
        )
        ax.fill_between(x, y, [0.0] * len(y), color="#A7F3D0", alpha=0.25)
        ax.set_ylabel("Progress (%)", fontsize=10)
        ax.set_ylim(0, max(100.0, max(y) + 8.0))
        ax.set_title("Progress Rate Graph", fontsize=12, loc="left", color="#1F2937")
        ax.legend(loc="upper left", fontsize=9, frameon=False)
    else:
        x, v_actuals, v_criticals, v_safes = _compute_speed_series(dashboard)
        if not x:
            x = [datetime.now()]
            v_actuals = [0.0]
            v_criticals = [0.0]
            v_safes = [0.0]

        ax.plot(
            x,
            v_actuals,
            color="#2563EB",
            linewidth=2.4,
            marker="o",
            markersize=3.5,
            label="V_actual",
        )
        ax.plot(
            x,
            v_criticals,
            color="#DC2626",
            linewidth=1.8,
            linestyle="--",
            label="V_critical",
        )
        ax.plot(
            x,
            v_safes,
            color="#059669",
            linewidth=1.8,
            linestyle="--",
            label="V_safe",
        )
        ax.axhline(0.0, color="#9CA3AF", linewidth=0.8)

        all_values = [*v_actuals, *v_criticals, *v_safes]
        lo = min(all_values)
        hi = max(all_values)
        if abs(hi - lo) < 0.1:
            lo -= 1.0
            hi += 1.0
        padding = max(0.5, (hi - lo) * 0.12)
        ax.set_ylim(lo - padding, hi + padding)

        ax.set_ylabel("Speed (%/day)", fontsize=10)
        ax.set_title("Speed Graph", fontsize=12, loc="left", color="#1F2937")
        ax.legend(loc="upper left", fontsize=9, frameon=False)

    ax.set_xlabel("Time", fontsize=10)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf.read()
