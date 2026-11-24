from __future__ import annotations

from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class RankEntry:
    UserId: str
    UserName: str
    Value: float


@dataclass(frozen=True)
class PublishedRankEntry:
    UserId: str
    UserName: str
    Value: float
    Rank: int
    IsSelf: bool
    IsPublished: bool



def _with_rank(entries: list[RankEntry]) -> list[PublishedRankEntry]:
    sorted_entries = sorted(entries, key=lambda x: (-x.Value, x.UserName))
    result: list[PublishedRankEntry] = []
    rank = 0
    prev_value: float | None = None
    for idx, entry in enumerate(sorted_entries, start=1):
        if prev_value is None or entry.Value != prev_value:
            rank = idx
            prev_value = entry.Value
        result.append(
            PublishedRankEntry(
                UserId=entry.UserId,
                UserName=entry.UserName,
                Value=entry.Value,
                Rank=rank,
                IsSelf=False,
                IsPublished=False,
            )
        )
    return result



def apply_publication_rule(
    entries: list[RankEntry],
    self_user_id: str,
    publish_all: bool = False,
) -> list[PublishedRankEntry]:
    ranked = _with_rank(entries)
    if not ranked:
        return []

    threshold_index = ceil(len(ranked) / 2)
    published_user_ids: set[str] = set()
    group_start_index = 0
    while group_start_index < len(ranked):
        group_rank = ranked[group_start_index].Rank
        group_end_index = group_start_index
        while group_end_index + 1 < len(ranked) and ranked[group_end_index + 1].Rank == group_rank:
            group_end_index += 1

        if group_end_index + 1 <= threshold_index:
            published_user_ids.update(entry.UserId for entry in ranked[group_start_index : group_end_index + 1])

        group_start_index = group_end_index + 1

    if publish_all:
        return [
            PublishedRankEntry(
                UserId=entry.UserId,
                UserName=entry.UserName,
                Value=entry.Value,
                Rank=entry.Rank,
                IsSelf=entry.UserId == self_user_id,
                IsPublished=entry.UserId in published_user_ids,
            )
            for entry in ranked
        ]

    published: list[PublishedRankEntry] = []
    for entry in ranked:
        is_self = entry.UserId == self_user_id
        is_published = entry.UserId in published_user_ids or is_self
        if is_published:
            published.append(
                PublishedRankEntry(
                    UserId=entry.UserId,
                    UserName=entry.UserName,
                    Value=entry.Value,
                    Rank=entry.Rank,
                    IsSelf=is_self,
                    IsPublished=entry.UserId in published_user_ids,
                )
            )
    return published
