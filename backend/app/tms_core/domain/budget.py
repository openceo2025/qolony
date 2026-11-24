from __future__ import annotations

from dataclasses import dataclass

from .rounding import round_half_up


@dataclass(frozen=True)
class DailyBudget:
    WorkMinutes: int
    RegularMinutes: int
    OvertimeMinutes: int
    FreeMinutes: int
    BaseAvailableMinutes: int
    EducationBudgetMinutes: int
    WorkBudgetMinutes: int



def calculate_daily_budget(
    work_minutes: int,
    free_time_rate: float,
    education_rate_monthly: float,
    regular_minutes_per_day: int = 480,
) -> DailyBudget:
    regular_minutes = min(max(work_minutes, 0), regular_minutes_per_day)
    overtime_minutes = max(0, work_minutes - regular_minutes_per_day)
    free_minutes = round_half_up(regular_minutes * free_time_rate)
    base_available_minutes = (regular_minutes - free_minutes) + overtime_minutes
    education_budget_minutes = round_half_up(base_available_minutes * education_rate_monthly)
    work_budget_minutes = max(0, base_available_minutes - education_budget_minutes)
    return DailyBudget(
        WorkMinutes=max(work_minutes, 0),
        RegularMinutes=regular_minutes,
        OvertimeMinutes=overtime_minutes,
        FreeMinutes=free_minutes,
        BaseAvailableMinutes=base_available_minutes,
        EducationBudgetMinutes=education_budget_minutes,
        WorkBudgetMinutes=work_budget_minutes,
    )
