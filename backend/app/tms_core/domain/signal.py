from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class TaskSignalInput:
    status: str
    due_date: date
    start_date: date
    progress_rate: float
    assigned_minutes: int
    actual_minutes: int
    person_count: int
    capacity_minutes_per_day: int
    crash_zone_pct: float
    today: date
    holiday_dates: set[date]
    started: bool


def count_workdays_inclusive(start_date: date, end_date: date, holiday_dates: set[date]) -> int:
    if end_date < start_date:
        return 0

    count = 0
    current = start_date
    while current <= end_date:
        if current.weekday() < 5 and current not in holiday_dates:
            count += 1
        current += timedelta(days=1)
    return count


def _capacity_signal(
    *,
    due_date: date,
    today: date,
    assigned_minutes: int,
    actual_minutes: int,
    person_count: int,
    capacity_minutes_per_day: int,
    crash_zone_pct: float,
    holiday_dates: set[date],
) -> str:
    remain_days = count_workdays_inclusive(today, due_date, holiday_dates)
    available = remain_days * max(1, capacity_minutes_per_day) * max(1, person_count)

    assigned = max(0, assigned_minutes)
    actual = max(0, actual_minutes)
    safe_multiplier = 1.0 + max(0.0, crash_zone_pct) / 100.0

    need_complete = max(0.0, assigned - actual)
    need_safe = max(0.0, assigned * safe_multiplier - actual)

    if available >= need_safe:
        return "Green"
    if available >= need_complete:
        return "Yellow"
    return "Red"


def calculate_task_signal(task: TaskSignalInput) -> str:
    if task.status == "Abort":
        return "Gray"
    if task.status == "Completion":
        return "Blue"
    if task.due_date < task.today:
        return "Red"

    assigned = max(0, task.assigned_minutes)
    actual = max(0, task.actual_minutes)
    safe_multiplier = 1.0 + max(0.0, task.crash_zone_pct) / 100.0

    if actual > assigned * safe_multiplier:
        return "Red"
    if assigned > 0 and actual >= assigned:
        return "Yellow"

    if not task.started:
        return _capacity_signal(
            due_date=task.due_date,
            today=task.today,
            assigned_minutes=assigned,
            actual_minutes=actual,
            person_count=task.person_count,
            capacity_minutes_per_day=task.capacity_minutes_per_day,
            crash_zone_pct=task.crash_zone_pct,
            holiday_dates=task.holiday_dates,
        )

    if task.progress_rate <= 0:
        return _capacity_signal(
            due_date=task.due_date,
            today=task.today,
            assigned_minutes=assigned,
            actual_minutes=actual,
            person_count=task.person_count,
            capacity_minutes_per_day=task.capacity_minutes_per_day,
            crash_zone_pct=task.crash_zone_pct,
            holiday_dates=task.holiday_dates,
        )

    elapsed_days = count_workdays_inclusive(task.start_date, task.today, task.holiday_dates)
    if elapsed_days <= 0:
        return _capacity_signal(
            due_date=task.due_date,
            today=task.today,
            assigned_minutes=assigned,
            actual_minutes=actual,
            person_count=task.person_count,
            capacity_minutes_per_day=task.capacity_minutes_per_day,
            crash_zone_pct=task.crash_zone_pct,
            holiday_dates=task.holiday_dates,
        )

    remain_days = count_workdays_inclusive(task.today, task.due_date, task.holiday_dates)
    speed = task.progress_rate / elapsed_days
    forecast = task.progress_rate + speed * remain_days

    if forecast >= (100.0 + task.crash_zone_pct):
        return "Green"
    if forecast >= 100.0:
        return "Yellow"
    return "Red"
