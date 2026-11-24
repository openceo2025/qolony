from app.tms_core.domain.progress import (
    LeafProgress,
    calculate_leaf_progress_rate,
    calculate_parent_progress_rate,
)


def test_calculate_leaf_progress_rate_completion_is_100() -> None:
    assert calculate_leaf_progress_rate(
        assigned_manhour=300,
        actual_manhour=120,
        status="Completion",
    ) == 100.0


def test_calculate_leaf_progress_rate_abort_is_zero_even_with_actuals() -> None:
    assert calculate_leaf_progress_rate(
        assigned_manhour=300,
        actual_manhour=240,
        status="Abort",
    ) == 0.0


def test_calculate_leaf_progress_rate_partial_uses_actual_ratio_and_caps_99() -> None:
    assert calculate_leaf_progress_rate(
        assigned_manhour=300,
        actual_manhour=150,
        status="DuringStart",
    ) == 50.0
    assert calculate_leaf_progress_rate(
        assigned_manhour=300,
        actual_manhour=900,
        status="DuringStart",
    ) == 99.0


def test_calculate_parent_progress_rate_weighted_average() -> None:
    progress = calculate_parent_progress_rate(
        [
            LeafProgress(AssignedManHour=300, ProgressRate=100.0),
            LeafProgress(AssignedManHour=300, ProgressRate=50.0),
            LeafProgress(AssignedManHour=600, ProgressRate=0.0),
        ]
    )
    assert round(progress, 4) == 37.5
