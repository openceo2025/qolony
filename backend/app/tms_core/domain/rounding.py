from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def round_half_up(value: float) -> int:
    return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def round_minutes_to_30(minutes: float) -> int:
    if minutes <= 0:
        return 0
    return ((round_half_up(minutes) + 15) // 30) * 30


def seconds_to_rounded_minutes(seconds: int | float) -> int:
    if seconds <= 0:
        return 0
    return round_minutes_to_30(float(seconds) / 60.0)


def normalize_minutes_multiple_of_30(minutes: int) -> int:
    return round_minutes_to_30(float(minutes))
