"""巣（組織）の役職と権限の判定。DB に触れない純粋関数だけを置く。

2026-11-26 社長決定:
- 役職は 社長・課長・係長・一般 の4段。上の役職は下の役職の権限を全部持つ。
- 置けるエサの規模: 一般=かけら1つ（分解しない仕事）、係長=角砂糖、課長=包みキャンディー、社長=板チョコ。
- 社長はチョーク（方針）を書く。方針は見えるだけで、何にも効かせてはいけない。
- 課長はチーム編成（部屋を掘る・所属を決める）。範囲は自分が主の部屋から下。
- 係長は采配（誰が何をやるか）。一般は自分で引き受けるだけ。
- 部屋（チーム）の主は課長か係長。所属は掛け持ちできる。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

ROLES: tuple[str, ...] = ("member", "chief", "manager", "president")
ROLE_LABELS: dict[str, str] = {
    "president": "社長",
    "manager": "課長",
    "chief": "係長",
    "member": "一般",
}
# お菓子の大きさ（分解できる階層の数）。0 = かけら、1 = 角砂糖、2 = 包みキャンディー、3 = 板チョコ。
SWEET_SIZES: tuple[int, ...] = (0, 1, 2, 3)
SWEET_LABELS: dict[int, str] = {0: "かけら", 1: "角砂糖", 2: "包みキャンディー", 3: "板チョコ"}
LEADER_ROLES: frozenset[str] = frozenset({"manager", "chief"})
EMBLEMS: tuple[str, ...] = ("leaf", "acorn", "flower", "mushroom", "drop", "star", "shell", "clover")


class ColonyPermissionError(ValueError):
    """役職が足りない操作。API では 403 として返す。"""


def normalize_role(role: str | None) -> str:
    value = (role or "").strip().lower()
    return value if value in ROLES else "member"


def role_rank(role: str | None) -> int:
    return ROLES.index(normalize_role(role))


def max_sweet_size(role: str | None) -> int:
    """その役職が置けるいちばん大きなお菓子。役職の段がそのまま大きさの上限になる。"""
    return role_rank(role)


def can_place_sweet(role: str | None, size: int) -> bool:
    return size in SWEET_SIZES and size <= max_sweet_size(role)


def can_write_policy(role: str | None) -> bool:
    return normalize_role(role) == "president"


def can_create_area(role: str | None) -> bool:
    return role_rank(role) >= role_rank("manager")


def can_assign(role: str | None, *, actor_id: str, assignee_id: str, task_not_started: bool) -> bool:
    """係長以上は誰にでも割り振れる。一般は未着手の仕事を自分で引き受けるだけ。"""
    if role_rank(role) >= role_rank("chief"):
        return True
    return actor_id == assignee_id and task_not_started


@dataclass(frozen=True)
class TeamNode:
    Id: str
    ParentTeamId: str | None
    LeaderUserId: str | None


def led_subtree(actor_id: str, teams: Iterable[TeamNode]) -> set[str]:
    """actor が主の部屋と、その下の部屋すべて。"""
    team_list = list(teams)
    children: dict[str | None, list[str]] = {}
    for team in team_list:
        children.setdefault(team.ParentTeamId, []).append(team.Id)
    result: set[str] = set()
    stack = [team.Id for team in team_list if team.LeaderUserId == actor_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def strictly_below_led(actor_id: str, teams: Iterable[TeamNode]) -> set[str]:
    """actor が主の部屋より下だけ。自分が主の部屋そのものの主替え・埋め戻しは社長が決める。"""
    team_list = list(teams)
    led = {team.Id for team in team_list if team.LeaderUserId == actor_id}
    return led_subtree(actor_id, team_list) - led


def can_edit_team_members(role: str | None, actor_id: str, team_id: str, teams: Iterable[TeamNode]) -> bool:
    if normalize_role(role) == "president":
        return True
    if normalize_role(role) != "manager":
        return False
    return team_id in led_subtree(actor_id, teams)


def can_dig_room(role: str | None, actor_id: str, parent_team_id: str | None, teams: Iterable[TeamNode]) -> bool:
    """社長は社長室の真下にも掘れる。課長は自分が主の部屋から下にだけ掘れる。"""
    if normalize_role(role) == "president":
        return True
    if normalize_role(role) != "manager" or parent_team_id is None:
        return False
    return parent_team_id in led_subtree(actor_id, teams)


def can_rename_team(role: str | None, actor_id: str, team_id: str, teams: Iterable[TeamNode]) -> bool:
    return can_edit_team_members(role, actor_id, team_id, teams)


def can_change_leader_or_remove(role: str | None, actor_id: str, team_id: str, teams: Iterable[TeamNode]) -> bool:
    if normalize_role(role) == "president":
        return True
    if normalize_role(role) != "manager":
        return False
    return team_id in strictly_below_led(actor_id, teams)


def can_set_role(
    actor_role: str | None,
    actor_id: str,
    *,
    target_id: str,
    target_role: str | None,
    new_role: str,
    target_team_ids: Iterable[str],
    teams: Iterable[TeamNode],
) -> bool:
    """社長は誰の役職でも変えられる。課長は自分の部屋から下の人を係長・一般にだけできる。"""
    if normalize_role(new_role) != new_role:
        return False
    if normalize_role(actor_role) == "president":
        return True
    if normalize_role(actor_role) != "manager" or target_id == actor_id:
        return False
    if new_role not in ("chief", "member") or normalize_role(target_role) not in ("chief", "member"):
        return False
    scope = led_subtree(actor_id, teams)
    return any(team_id in scope for team_id in target_team_ids)


def is_ancestor(team_id: str, maybe_descendant_id: str | None, teams: Iterable[TeamNode]) -> bool:
    """部屋の付け替えで輪ができないかを調べる。"""
    parents = {team.Id: team.ParentTeamId for team in teams}
    current = maybe_descendant_id
    guard = 0
    while current is not None and guard < 256:
        if current == team_id:
            return True
        current = parents.get(current)
        guard += 1
    return False
