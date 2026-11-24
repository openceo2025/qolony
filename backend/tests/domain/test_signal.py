from datetime import date

from app.tms_core.domain.signal import (
    TaskSignalInput,
    calculate_task_signal,
    count_workdays_inclusive,
)


def _input(**kwargs) -> TaskSignalInput:
    base = TaskSignalInput(
        status="NotStart",
        due_date=date(2026, 1, 7),
        start_date=date(2026, 1, 1),
        progress_rate=0.0,
        assigned_minutes=480,
        actual_minutes=0,
        person_count=1,
        capacity_minutes_per_day=240,
        crash_zone_pct=20.0,
        today=date(2026, 1, 5),
        holiday_dates=set(),
        started=False,
    )
    merged = base.__dict__ | kwargs
    return TaskSignalInput(**merged)


def test_count_workdays_excludes_weekend_and_holidays() -> None:
    workdays = count_workdays_inclusive(
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 7),
        holiday_dates={date(2026, 1, 1)},
    )
    assert workdays == 4


def test_unstarted_capacity_based_signal_green_yellow_red() -> None:
    assert calculate_task_signal(_input(assigned_minutes=480)) == "Green"
    assert calculate_task_signal(_input(assigned_minutes=650)) == "Yellow"
    assert calculate_task_signal(_input(assigned_minutes=900)) == "Red"


def test_started_signal_uses_speed_forecast() -> None:
    green = calculate_task_signal(
        _input(
            status="DuringStart",
            started=True,
            progress_rate=60.0,
            start_date=date(2025, 12, 30),
            due_date=date(2026, 1, 9),
            today=date(2026, 1, 5),
        )
    )
    assert green == "Green"

    yellow = calculate_task_signal(
        _input(
            status="DuringStart",
            started=True,
            progress_rate=60.0,
            start_date=date(2025, 12, 31),
            due_date=date(2026, 1, 7),
            today=date(2026, 1, 5),
        )
    )
    assert yellow == "Yellow"

    red = calculate_task_signal(
        _input(
            status="DuringStart",
            started=True,
            progress_rate=30.0,
            start_date=date(2025, 12, 31),
            due_date=date(2026, 1, 7),
            today=date(2026, 1, 5),
        )
    )
    assert red == "Red"


def test_incomplete_overrun_and_overdue_rules() -> None:
    assert calculate_task_signal(
        _input(
            status="DuringStart",
            started=True,
            progress_rate=80.0,
            assigned_minutes=600,
            actual_minutes=600,
        )
    ) == "Yellow"

    assert calculate_task_signal(
        _input(
            status="DuringStart",
            started=True,
            progress_rate=80.0,
            assigned_minutes=600,
            actual_minutes=721,
        )
    ) == "Red"

    assert calculate_task_signal(
        _input(
            status="DuringStart",
            started=True,
            due_date=date(2026, 1, 4),
            today=date(2026, 1, 5),
        )
    ) == "Red"

    assert calculate_task_signal(
        _input(
            status="Completion",
            due_date=date(2026, 1, 4),
            today=date(2026, 1, 5),
        )
    ) == "Blue"


def test_abort_signal_is_neutral_gray() -> None:
    assert calculate_task_signal(
        _input(
            status="Abort",
            due_date=date(2026, 1, 4),
            today=date(2026, 1, 5),
            actual_minutes=999,
        )
    ) == "Gray"
