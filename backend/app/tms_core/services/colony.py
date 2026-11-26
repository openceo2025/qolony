"""巣（組織）のサービス。役職・部屋（チーム）・所属・方針・貢献の集計をまとめる。

権限の判定は domain/colony.py の純粋関数で行い、DB への読み書きは repository に閉じる。
タスクそのものの作成・担当変更は既存の TmsService を通し、ここでは役職による許可だけを足す。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.tms_core.db.repository import TmsRepository
from app.tms_core.domain.colony import (
    EMBLEMS,
    LEADER_ROLES,
    ROLE_LABELS,
    SWEET_LABELS,
    ColonyPermissionError,
    TeamNode,
    can_assign,
    can_change_leader_or_remove,
    can_create_area,
    can_dig_room,
    can_edit_team_members,
    can_place_sweet,
    can_rename_team,
    can_set_role,
    can_write_policy,
    is_ancestor,
    led_subtree,
    max_sweet_size,
    normalize_role,
)
from app.tms_core.domain.metrics import calculate_speed_score
from app.tms_core.domain.ranking import RankEntry, apply_publication_rule
from app.tms_core.domain.rounding import seconds_to_rounded_minutes
from app.tms_core.services.core import TmsService, UserInputError, display_user_name

CONTRIBUTION_MONTHS = 6


@dataclass(frozen=True)
class MonthValue:
    Month: str
    OutputMinutes: int
    EffortMinutes: int
    WorkSpeed: float
    TeachingMinutes: int


@dataclass(frozen=True)
class UserContribution:
    UserId: str
    Visible: bool
    Months: list[MonthValue] = field(default_factory=list)


@dataclass(frozen=True)
class TeamContribution:
    TeamId: str
    MemberCount: int
    Months: list[MonthValue]
    PerPersonOutputMinutes: list[float]


def _month_start(value: date) -> date:
    return date(value.year, value.month, 1)


def _shift_month(value: date, months: int) -> date:
    index = value.year * 12 + value.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


class ColonyService:
    def __init__(self, *, repository: TmsRepository, tms: TmsService):
        self.repository = repository
        self.tms = tms

    # ---- 読み取り ----

    def _nodes(self) -> list[TeamNode]:
        return [
            TeamNode(Id=team.Id, ParentTeamId=team.ParentTeamId, LeaderUserId=team.LeaderUserId)
            for team in self.repository.list_colony_teams()
        ]

    def _actor(self, actor_id: str):
        actor = self.repository.get_user(actor_id)
        if actor is None or not actor.IsActive:
            raise UserInputError("操作する人が見つかりません")
        return actor

    def _role(self, user) -> str:
        # Top Manager は既存画面の管理者でもあるので、巣でも必ず社長として扱う。
        if getattr(user, "IsTopManager", False):
            return "president"
        return normalize_role(getattr(user, "ColonyRole", "member"))

    def get_org(self, viewer_id: str) -> dict[str, Any]:
        viewer = self._actor(viewer_id)
        role = self._role(viewer)
        teams = self.repository.list_colony_teams()
        nodes = self._nodes()
        members = self.repository.list_colony_team_members()
        by_team: dict[str, list[str]] = defaultdict(list)
        for member in members:
            by_team[member.TeamId].append(member.UserId)
        users = self.repository.list_users()
        scope = led_subtree(viewer.Id, nodes) if role == "manager" else set()
        return {
            "ViewerRole": role,
            "Roles": {user.Id: self._role(user) for user in users},
            "RoleLabels": ROLE_LABELS,
            "SweetLabels": {str(size): label for size, label in SWEET_LABELS.items()},
            "Emblems": list(EMBLEMS),
            "Teams": [
                {
                    "Id": team.Id,
                    "Name": team.Name,
                    "ParentTeamId": team.ParentTeamId,
                    "LeaderUserId": team.LeaderUserId,
                    "Emblem": team.Emblem,
                    "DisplayOrder": team.DisplayOrder,
                    "MemberIds": by_team.get(team.Id, []),
                }
                for team in teams
            ],
            "Policies": [
                {
                    "Id": policy.Id,
                    "Kind": policy.Kind,
                    "Text": policy.Text,
                    "X": policy.X,
                    "Y": policy.Y,
                    "Size": policy.Size,
                    "Points": json.loads(policy.PointsJson or "[]"),
                }
                for policy in self.repository.list_colony_policies()
            ],
            "Permissions": {
                "MaxSweetSize": max_sweet_size(role),
                "WritePolicy": can_write_policy(role),
                "CreateArea": can_create_area(role),
                "AssignOthers": role in ("chief", "manager", "president"),
                "EditAllTeams": role == "president",
                "EditableTeamIds": sorted(scope),
            },
        }

    # ---- 役職 ----

    def set_role(self, actor_id: str, target_id: str, new_role: str) -> None:
        actor = self._actor(actor_id)
        target = self.repository.get_user(target_id)
        if target is None:
            raise UserInputError("役職を変える人が見つかりません")
        target_teams = [m.TeamId for m in self.repository.list_colony_team_members() if m.UserId == target_id]
        if not can_set_role(
            self._role(actor),
            actor.Id,
            target_id=target_id,
            target_role=self._role(target),
            new_role=new_role,
            target_team_ids=target_teams,
            teams=self._nodes(),
        ):
            raise ColonyPermissionError("この役職を決められるのは社長です（課長は自分の部屋から下の係長・一般だけ）")
        if target.IsTopManager and new_role != "president":
            raise UserInputError("管理者（Top Manager）は社長のままにしてください")
        self.repository.set_user_colony_role(target_id, new_role)
        if new_role not in LEADER_ROLES:
            # 主になれない役職になったら、その人が主の部屋は主なしにする。
            for team in self.repository.list_colony_teams():
                if team.LeaderUserId == target_id:
                    self.repository.update_colony_team(team.Id, LeaderUserId=None)

    # ---- 部屋 ----

    def dig_room(self, actor_id: str, *, parent_team_id: str | None, name: str, emblem: str | None) -> str:
        actor = self._actor(actor_id)
        nodes = self._nodes()
        if parent_team_id is not None and not any(node.Id == parent_team_id for node in nodes):
            raise UserInputError("掘り始める部屋が見つかりません")
        if not can_dig_room(self._role(actor), actor.Id, parent_team_id, nodes):
            raise ColonyPermissionError("部屋を掘れるのは課長以上です（課長は自分が主の部屋から下だけ）")
        clean = name.strip()
        if not clean:
            raise UserInputError("部屋の名前を入れてください")
        used = [team.Emblem for team in self.repository.list_colony_teams()]
        chosen = emblem if emblem in EMBLEMS else min(EMBLEMS, key=lambda item: (used.count(item), EMBLEMS.index(item)))
        team = self.repository.create_colony_team(
            name=clean[:128],
            parent_team_id=parent_team_id,
            leader_user_id=None,
            emblem=chosen,
        )
        return team.Id

    def update_room(
        self,
        actor_id: str,
        team_id: str,
        *,
        name: str | None = None,
        emblem: str | None = None,
        leader_user_id: str | None = None,
        clear_leader: bool = False,
        parent_team_id: str | None = None,
        move_to_root: bool = False,
    ) -> None:
        actor = self._actor(actor_id)
        role = self._role(actor)
        nodes = self._nodes()
        if self.repository.get_colony_team(team_id) is None:
            raise UserInputError("部屋が見つかりません")
        changes: dict[str, Any] = {}
        if name is not None or emblem is not None:
            if not can_rename_team(role, actor.Id, team_id, nodes):
                raise ColonyPermissionError("部屋の名前と紋章を変えられるのは、その部屋を受け持つ課長以上です")
            if name is not None:
                if not name.strip():
                    raise UserInputError("部屋の名前を入れてください")
                changes["Name"] = name.strip()[:128]
            if emblem is not None:
                if emblem not in EMBLEMS:
                    raise UserInputError("その紋章は使えません")
                changes["Emblem"] = emblem
        if leader_user_id is not None or clear_leader:
            if not can_change_leader_or_remove(role, actor.Id, team_id, nodes):
                raise ColonyPermissionError("部屋の主を決められるのは社長と、その上の部屋の課長です")
            if leader_user_id is not None:
                leader = self.repository.get_user(leader_user_id)
                if leader is None or self._role(leader) not in LEADER_ROLES:
                    raise UserInputError("部屋の主になれるのは課長か係長です")
                changes["LeaderUserId"] = leader_user_id
                # 主はその部屋のメンバーでもある。
                self.repository.add_colony_team_member(team_id, leader_user_id)
            else:
                changes["LeaderUserId"] = None
        if parent_team_id is not None or move_to_root:
            if role != "president":
                raise ColonyPermissionError("部屋のつながりを付け替えられるのは社長です")
            if parent_team_id is not None and is_ancestor(team_id, parent_team_id, nodes):
                raise UserInputError("部屋を自分より下の部屋へつなぐことはできません")
            changes["ParentTeamId"] = parent_team_id
        if changes:
            self.repository.update_colony_team(team_id, **changes)

    def fill_room(self, actor_id: str, team_id: str) -> None:
        """部屋を埋める（チームをなくす）。下に部屋が残っている間は埋められない。"""
        actor = self._actor(actor_id)
        nodes = self._nodes()
        if not any(node.Id == team_id for node in nodes):
            raise UserInputError("部屋が見つかりません")
        if not can_change_leader_or_remove(self._role(actor), actor.Id, team_id, nodes):
            raise ColonyPermissionError("部屋を埋められるのは社長と、その上の部屋の課長です")
        if any(node.ParentTeamId == team_id for node in nodes):
            raise UserInputError("下に部屋が残っています。先に下の部屋を埋めてください")
        self.repository.delete_colony_team(team_id)

    def join(self, actor_id: str, team_id: str, user_id: str) -> None:
        actor = self._actor(actor_id)
        if self.repository.get_colony_team(team_id) is None:
            raise UserInputError("部屋が見つかりません")
        if self.repository.get_user(user_id) is None:
            raise UserInputError("所属させる人が見つかりません")
        if not can_edit_team_members(self._role(actor), actor.Id, team_id, self._nodes()):
            raise ColonyPermissionError("所属を決められるのは、その部屋を受け持つ課長以上です")
        self.repository.add_colony_team_member(team_id, user_id)

    def leave(self, actor_id: str, team_id: str, user_id: str) -> None:
        actor = self._actor(actor_id)
        team = self.repository.get_colony_team(team_id)
        if team is None:
            raise UserInputError("部屋が見つかりません")
        if not can_edit_team_members(self._role(actor), actor.Id, team_id, self._nodes()):
            raise ColonyPermissionError("所属を外せるのは、その部屋を受け持つ課長以上です")
        if team.LeaderUserId == user_id:
            raise UserInputError("部屋の主は外せません。先に主を替えてください")
        self.repository.remove_colony_team_member(team_id, user_id)

    # ---- 方針（チョーク）----

    def write_policy(
        self,
        actor_id: str,
        *,
        kind: str,
        text: str,
        x: float,
        y: float,
        size: float,
        points: list[list[float]],
    ) -> str:
        actor = self._actor(actor_id)
        if not can_write_policy(self._role(actor)):
            raise ColonyPermissionError("チョークで方針を書けるのは社長だけです")
        if kind not in ("text", "line"):
            raise UserInputError("方針は文字か線で書いてください")
        if kind == "text" and not text.strip():
            raise UserInputError("方針の言葉を入れてください")
        if kind == "line" and len(points) < 2:
            raise UserInputError("線は2点以上で描いてください")
        clean_points = [[float(p[0]), float(p[1])] for p in points[:400] if len(p) >= 2]
        policy = self.repository.create_colony_policy(
            kind=kind,
            text=text.strip()[:200],
            x=float(x),
            y=float(y),
            size=max(0.5, min(4.0, float(size))),
            points=clean_points,
            author_user_id=actor.Id,
        )
        return policy.Id

    def move_policy(self, actor_id: str, policy_id: str, *, text: str | None, x: float | None, y: float | None) -> None:
        actor = self._actor(actor_id)
        if not can_write_policy(self._role(actor)):
            raise ColonyPermissionError("方針を書き直せるのは社長だけです")
        if text is not None and not text.strip():
            raise UserInputError("方針の言葉を入れてください")
        self.repository.update_colony_policy(
            policy_id, text=text.strip()[:200] if text is not None else None, x=x, y=y
        )

    def erase_policy(self, actor_id: str, policy_id: str) -> None:
        actor = self._actor(actor_id)
        if not can_write_policy(self._role(actor)):
            raise ColonyPermissionError("方針を消せるのは社長だけです")
        self.repository.delete_colony_policy(policy_id)

    # ---- エサを置く・エリア・采配 ----

    def drop_sweet(
        self,
        actor_id: str,
        *,
        area_task_id: str,
        name: str,
        size: int,
        assigned_minutes: int,
        end_date: date,
    ) -> tuple[str, list[str]]:
        actor = self._actor(actor_id)
        if not can_place_sweet(self._role(actor), size):
            raise ColonyPermissionError(
                f"{SWEET_LABELS.get(size, 'そのお菓子')}を置ける役職ではありません"
            )
        task_id, warnings = self.tms.create_task_returning_id(
            name=name,
            assignee_user_id=actor.Id,
            creator_user_id=actor.Id,
            assigned_manhour=assigned_minutes,
            end_date=end_date,
            parent_task_id=area_task_id,
            planned_deliverables=[],
            is_rework=False,
        )
        self.repository.set_task_colony_size(task_id, size)
        return task_id, warnings

    def create_area(self, actor_id: str, *, name: str, end_date: date) -> tuple[str, list[str]]:
        actor = self._actor(actor_id)
        if not can_create_area(self._role(actor)):
            raise ColonyPermissionError("エリアを作れるのは課長以上です")
        # エリアは一番上のタスク。工数は中のお菓子に持たせるので、最小の30分で作る。
        return self.tms.create_task_returning_id(
            name=name,
            assignee_user_id=actor.Id,
            creator_user_id=actor.Id,
            assigned_manhour=30,
            end_date=end_date,
            parent_task_id=None,
            planned_deliverables=[],
            is_rework=False,
            allow_root_by_caller=True,
        )

    def assign(self, actor_id: str, task_id: str, assignee_id: str) -> None:
        actor = self._actor(actor_id)
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("タスクが見つかりません")
        if not can_assign(
            self._role(actor),
            actor_id=actor.Id,
            assignee_id=assignee_id,
            task_not_started=task.Status == "NotStart",
        ):
            raise ColonyPermissionError("誰が何をやるかを決められるのは係長以上です（一般は未着手の仕事を自分で引き受けるだけ）")
        # 役職で許したので、既存の「担当者本人だけ編集できる」判定は通さない。
        self.tms.update_task_assignee(task_id=task_id, assignee_user_id=assignee_id, operator_user_id=None)

    # ---- 貢献 ----

    def contributions(self, viewer_id: str, year_month: str) -> dict[str, Any]:
        """直近6か月の、運び込んだエサの量（完了した仕事の見積）・速さ・教えた時間。

        ランキングと同じ数え方。他人の数字は、その月の出来高で上位半分に入った人だけ見える。
        本人の数字はいつでも、社長は全員分を見られる。チームと会社の合計は全員分を足す。
        """
        viewer = self._actor(viewer_id)
        try:
            anchor = _month_start(date.fromisoformat(f"{year_month}-01"))
        except ValueError as exc:
            raise UserInputError("月は YYYY-MM 形式で指定してください") from exc
        months = [_shift_month(anchor, offset) for offset in range(-(CONTRIBUTION_MONTHS - 1), 1)]
        users, summaries = self.tms._build_ranking_task_summaries_for_bounds(
            period_start=months[0],
            period_end_exclusive=_shift_month(anchor, 1),
        )
        output: dict[tuple[str, date], int] = defaultdict(int)
        effort: dict[tuple[str, date], int] = defaultdict(int)
        teaching: dict[tuple[str, date], float] = defaultdict(float)
        for summary in summaries:
            month = _month_start(summary.CompletionDate)
            output[(summary.AssigneeUserId, month)] += summary.AssignedMinutes
            effort[(summary.AssigneeUserId, month)] += summary.ActualMinutes
            for teacher_id, seconds in summary.TeachingSecondsByUser:
                teaching[(teacher_id, month)] += seconds

        def month_value(user_ids: list[str], month: date) -> MonthValue:
            out = sum(output[(uid, month)] for uid in user_ids)
            eff = sum(effort[(uid, month)] for uid in user_ids)
            teach = sum(teaching[(uid, month)] for uid in user_ids)
            return MonthValue(
                Month=month.strftime("%Y-%m"),
                OutputMinutes=out,
                EffortMinutes=eff,
                WorkSpeed=calculate_speed_score(out, eff),
                TeachingMinutes=seconds_to_rounded_minutes(teach),
            )

        role = self._role(viewer)
        published = apply_publication_rule(
            [
                RankEntry(UserId=user.Id, UserName=display_user_name(user), Value=float(output[(user.Id, anchor)]))
                for user in users
            ],
            self_user_id=viewer.Id,
            publish_all=role == "president",
        )
        visible = {entry.UserId for entry in published if entry.IsPublished or entry.IsSelf}
        if role == "president":
            visible = {user.Id for user in users}
        all_users = self.repository.list_users()
        people = [
            UserContribution(
                UserId=user.Id,
                Visible=user.Id in visible,
                Months=[month_value([user.Id], month) for month in months] if user.Id in visible else [],
            )
            for user in all_users
        ]
        by_team: dict[str, list[str]] = defaultdict(list)
        for member in self.repository.list_colony_team_members():
            by_team[member.TeamId].append(member.UserId)
        teams = []
        for team in self.repository.list_colony_teams():
            member_ids = by_team.get(team.Id, [])
            values = [month_value(member_ids, month) for month in months]
            count = len(member_ids)
            teams.append(
                TeamContribution(
                    TeamId=team.Id,
                    MemberCount=count,
                    Months=values,
                    PerPersonOutputMinutes=[round(v.OutputMinutes / count, 1) if count else 0.0 for v in values],
                )
            )
        everyone = [user.Id for user in users]
        return {
            "Months": [month.strftime("%Y-%m") for month in months],
            "People": people,
            "Teams": teams,
            "Company": [month_value(everyone, month) for month in months],
            "CompanyMemberCount": len(everyone),
        }
