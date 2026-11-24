from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import inf


@dataclass(frozen=True)
class ProgressSnapshot:
    RecordedAt: datetime
    ProgressRate: float


@dataclass(frozen=True)
class SpeedResult:
    VActual: float
    VCritical: float
    VSafe: float
    Signal: str



def calculate_v_actual(history: list[ProgressSnapshot]) -> float:
    if len(history) < 2:
        return 0.0
    ordered = sorted(history, key=lambda x: x.RecordedAt)
    before = ordered[-2]
    now = ordered[-1]
    seconds = (now.RecordedAt - before.RecordedAt).total_seconds()
    if seconds <= 0:
        return 0.0
    days = seconds / 86400.0
    return (now.ProgressRate - before.ProgressRate) / days



def calculate_v_critical(progress_now: float, remain_days: float) -> float:
    if remain_days <= 0:
        return 0.0 if progress_now >= 100 else inf
    return (100.0 - progress_now) / remain_days



def calculate_v_safe(progress_now: float, remain_days: float, crash_zone_pct: float) -> float:
    if remain_days <= 0:
        return 0.0 if progress_now >= 100 + crash_zone_pct else inf
    return (100.0 + crash_zone_pct - progress_now) / remain_days



def decide_signal(v_actual: float, v_critical: float, v_safe: float) -> str:
    if v_actual < v_critical:
        return "Red"
    if v_actual < v_safe:
        return "Yellow"
    return "Blue"



def calculate_speed_result(
    history: list[ProgressSnapshot],
    progress_now: float,
    remain_days: float,
    crash_zone_pct: float,
) -> SpeedResult:
    v_actual = calculate_v_actual(history)
    v_critical = calculate_v_critical(progress_now, remain_days)
    v_safe = calculate_v_safe(progress_now, remain_days, crash_zone_pct)
    return SpeedResult(
        VActual=v_actual,
        VCritical=v_critical,
        VSafe=v_safe,
        Signal=decide_signal(v_actual, v_critical, v_safe),
    )
