from __future__ import annotations



def calculate_sabo(actual_work_minutes: int, work_budget_minutes: int) -> float:
    if work_budget_minutes <= 0:
        return 0.0
    return 1.0 - (actual_work_minutes / work_budget_minutes)



def calculate_thr(done_assigned_minutes: int, work_budget_minutes: int) -> float:
    if work_budget_minutes <= 0:
        return 0.0
    return done_assigned_minutes / work_budget_minutes



def calculate_actual_manhour_effective(actual_manhour: int, rework_minutes: int) -> int:
    return max(0, actual_manhour) + max(0, rework_minutes)



def calculate_exec_ratio(actual_effective_minutes: int, assigned_minutes: int) -> float:
    if assigned_minutes <= 0:
        return 0.0
    return actual_effective_minutes / assigned_minutes



def calculate_est_ratio(actual_effective_minutes: int, assigned_minutes: int) -> float:
    if assigned_minutes <= 0:
        return 0.0
    return actual_effective_minutes / assigned_minutes


def calculate_speed_score(
    output_minutes: int,
    effort_minutes: int,
    *,
    expected_effort_factor: float = 1.0,
) -> float:
    # 100 を「必要十分な速さ」として扱うため、比率を 100 倍して返す。
    if output_minutes <= 0 or effort_minutes <= 0 or expected_effort_factor <= 0:
        return 0.0
    return (float(output_minutes) / (float(effort_minutes) * expected_effort_factor)) * 100.0
