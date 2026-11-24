from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class RemainingTask:
    TaskId: str
    TaskName: str
    AssigneeUserId: str
    DueDate: date
    RemainingAssignedManHour: int


@dataclass(frozen=True)
class InfeasibilityCause:
    TaskId: str
    TaskName: str
    DueDate: date
    RemainingAssignedManHour: int


@dataclass(frozen=True)
class InfeasibilityResult:
    IsInfeasible: bool
    GapMinutes: int
    Causes: list[InfeasibilityCause]



def _sum_available_until(
    daily_budget: dict[date, int],
    target_date: date,
) -> int:
    return sum(minutes for d, minutes in daily_budget.items() if d <= target_date)



def detect_personal_infeasibility(
    tasks: list[RemainingTask],
    daily_budget: dict[date, int],
) -> InfeasibilityResult:
    if not tasks:
        return InfeasibilityResult(IsInfeasible=False, GapMinutes=0, Causes=[])

    sorted_tasks = sorted(tasks, key=lambda x: (x.DueDate, -x.RemainingAssignedManHour))
    running_required = 0
    max_gap = 0
    cause_cutoff: date | None = None

    for task in sorted_tasks:
        running_required += max(0, task.RemainingAssignedManHour)
        available = _sum_available_until(daily_budget, task.DueDate)
        gap = running_required - available
        if gap > max_gap:
            max_gap = gap
            cause_cutoff = task.DueDate

    if max_gap <= 0 or cause_cutoff is None:
        return InfeasibilityResult(IsInfeasible=False, GapMinutes=0, Causes=[])

    causes = [
        InfeasibilityCause(
            TaskId=t.TaskId,
            TaskName=t.TaskName,
            DueDate=t.DueDate,
            RemainingAssignedManHour=max(0, t.RemainingAssignedManHour),
        )
        for t in sorted_tasks
        if t.DueDate <= cause_cutoff
    ]
    causes = sorted(causes, key=lambda x: (x.DueDate, -x.RemainingAssignedManHour))

    return InfeasibilityResult(IsInfeasible=True, GapMinutes=max_gap, Causes=causes)



def detect_team_infeasibility(
    tasks: list[RemainingTask],
    team_daily_budget: dict[date, int],
) -> InfeasibilityResult:
    return detect_personal_infeasibility(tasks=tasks, daily_budget=team_daily_budget)
