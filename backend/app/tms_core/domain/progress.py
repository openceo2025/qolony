from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LeafProgress:
    AssignedManHour: int
    ProgressRate: float



def calculate_parent_progress_rate(leaves: list[LeafProgress]) -> float:
    denominator = sum(max(0, leaf.AssignedManHour) for leaf in leaves)
    if denominator <= 0:
        return 0.0
    numerator = sum(
        max(0, leaf.AssignedManHour) * max(0.0, min(100.0, leaf.ProgressRate)) / 100.0
        for leaf in leaves
    )
    return (numerator / denominator) * 100.0


def calculate_leaf_progress_rate(
    *,
    assigned_manhour: int,
    actual_manhour: int,
    status: str,
) -> float:
    if status == "Completion":
        return 100.0
    if status == "Abort":
        return 0.0

    assigned = max(0, assigned_manhour)
    actual = max(0, actual_manhour)
    if assigned <= 0:
        return 0.0

    return min(99.0, (actual / assigned) * 100.0)
