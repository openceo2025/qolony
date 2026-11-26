from __future__ import annotations

from datetime import date, datetime

import pytest

from app.tms_core.db.models import Task
from app.tms_core.domain.colony import ColonyPermissionError
from app.tms_core.services.colony import ColonyService
from app.tms_core.services.core import UserInputError

from tests.services.test_rankings import _build_service, _create_task


def _colony(tmp_path):
    service, repository, ids = _build_service(tmp_path)
    colony = ColonyService(repository=repository, tms=service)
    return colony, service, repository, ids


def test_top_manager_is_president_and_others_start_as_members(tmp_path) -> None:
    colony, _, _, ids = _colony(tmp_path)
    org = colony.get_org(ids["worker"])
    assert org["Roles"][ids["top"]] == "president"
    assert org["Roles"][ids["worker"]] == "member"
    assert org["Permissions"]["MaxSweetSize"] == 0
    assert org["Permissions"]["WritePolicy"] is False


def test_manager_edits_only_rooms_below_the_room_they_lead(tmp_path) -> None:
    colony, _, _, ids = _colony(tmp_path)
    colony.set_role(ids["top"], ids["mentor"], "manager")
    sales = colony.dig_room(ids["top"], parent_team_id=None, name="営業課", emblem="leaf")
    other = colony.dig_room(ids["top"], parent_team_id=None, name="開発課", emblem=None)
    colony.update_room(ids["top"], sales, leader_user_id=ids["mentor"])

    # 課長は自分が主の部屋の下に掘れるが、社長室の真下や他の課には掘れない。
    sub = colony.dig_room(ids["mentor"], parent_team_id=sales, name="営業一係", emblem=None)
    with pytest.raises(ColonyPermissionError):
        colony.dig_room(ids["mentor"], parent_team_id=None, name="勝手な課", emblem=None)
    with pytest.raises(ColonyPermissionError):
        colony.join(ids["mentor"], other, ids["worker"])

    # 所属は掛け持ちできる。
    colony.join(ids["mentor"], sales, ids["worker"])
    colony.join(ids["mentor"], sub, ids["worker"])
    org = colony.get_org(ids["mentor"])
    rooms = {team["Id"]: team for team in org["Teams"]}
    assert ids["worker"] in rooms[sales]["MemberIds"]
    assert ids["worker"] in rooms[sub]["MemberIds"]
    assert ids["mentor"] in rooms[sales]["MemberIds"]  # 主は自動でメンバーになる
    assert set(org["Permissions"]["EditableTeamIds"]) == {sales, sub}

    # 課長は係長を決められる。係長は下の部屋の主になれる。
    colony.set_role(ids["mentor"], ids["worker"], "chief")
    colony.update_room(ids["mentor"], sub, leader_user_id=ids["worker"])
    with pytest.raises(ColonyPermissionError):
        colony.set_role(ids["mentor"], ids["worker"], "manager")
    with pytest.raises(ColonyPermissionError):
        # 自分が主の部屋そのものの主替えは社長が決める。
        colony.update_room(ids["mentor"], sales, leader_user_id=ids["worker"])

    colony.leave(ids["mentor"], sales, ids["worker"])
    rooms = {team["Id"]: team for team in colony.get_org(ids["top"])["Teams"]}
    assert ids["worker"] not in rooms[sales]["MemberIds"]
    assert ids["worker"] in rooms[sub]["MemberIds"]


def test_leader_must_be_manager_or_chief_and_rooms_fill_bottom_up(tmp_path) -> None:
    colony, _, _, ids = _colony(tmp_path)
    room = colony.dig_room(ids["top"], parent_team_id=None, name="品質課", emblem=None)
    child = colony.dig_room(ids["top"], parent_team_id=room, name="検査係", emblem=None)
    with pytest.raises(UserInputError):
        colony.update_room(ids["top"], room, leader_user_id=ids["worker"])
    with pytest.raises(UserInputError):
        colony.fill_room(ids["top"], room)
    with pytest.raises(UserInputError):
        colony.update_room(ids["top"], room, parent_team_id=child)
    colony.fill_room(ids["top"], child)
    colony.fill_room(ids["top"], room)
    assert colony.get_org(ids["top"])["Teams"] == []


def test_dropping_role_clears_rooms_led_by_that_person(tmp_path) -> None:
    colony, _, _, ids = _colony(tmp_path)
    colony.set_role(ids["top"], ids["mentor"], "chief")
    room = colony.dig_room(ids["top"], parent_team_id=None, name="教育係", emblem=None)
    colony.update_room(ids["top"], room, leader_user_id=ids["mentor"])
    colony.set_role(ids["top"], ids["mentor"], "member")
    team = next(t for t in colony.get_org(ids["top"])["Teams"] if t["Id"] == room)
    assert team["LeaderUserId"] is None


def test_policy_is_president_only(tmp_path) -> None:
    colony, _, _, ids = _colony(tmp_path)
    with pytest.raises(ColonyPermissionError):
        colony.write_policy(ids["worker"], kind="text", text="お客様第一", x=0, y=0, size=1, points=[])
    policy = colony.write_policy(ids["top"], kind="text", text="お客様第一", x=10, y=-20, size=2, points=[])
    colony.write_policy(ids["top"], kind="line", text="", x=0, y=0, size=1, points=[[0, 0], [100, 40]])
    org = colony.get_org(ids["worker"])
    assert sorted(p["Text"] for p in org["Policies"]) == ["", "お客様第一"]
    colony.erase_policy(ids["top"], policy)
    assert len(colony.get_org(ids["top"])["Policies"]) == 1


def test_sweet_size_is_capped_by_role_and_area_needs_manager(tmp_path) -> None:
    colony, _, repository, ids = _colony(tmp_path)
    area_id, _ = colony.create_area(ids["top"], name="新規事業", end_date=date(2026, 12, 1))
    with pytest.raises(ColonyPermissionError):
        colony.create_area(ids["worker"], name="勝手なエリア", end_date=date(2026, 12, 1))
    with pytest.raises(ColonyPermissionError):
        colony.drop_sweet(
            ids["worker"], area_task_id=area_id, name="大きな仕事", size=1, assigned_minutes=60, end_date=date(2026, 11, 30)
        )
    crumb, _ = colony.drop_sweet(
        ids["worker"], area_task_id=area_id, name="見積を確認", size=0, assigned_minutes=60, end_date=date(2026, 11, 30)
    )
    task = repository.get_task(crumb)
    assert task is not None and task.ColonySize == 0 and task.ParentTaskId == area_id
    assert task.AssigneeUserId == ids["worker"]

    colony.set_role(ids["top"], ids["mentor"], "manager")
    colony.create_area(ids["mentor"], name="課長のエリア", end_date=date(2026, 12, 1))
    candy, _ = colony.drop_sweet(
        ids["mentor"], area_task_id=area_id, name="中くらいの仕事", size=2, assigned_minutes=120, end_date=date(2026, 11, 30)
    )
    assert repository.get_task(candy).ColonySize == 2


def test_assignment_rights(tmp_path) -> None:
    colony, _, repository, ids = _colony(tmp_path)
    area_id, _ = colony.create_area(ids["top"], name="エリア", end_date=date(2026, 12, 1))
    task_id, _ = colony.drop_sweet(
        ids["top"], area_task_id=area_id, name="仕事", size=0, assigned_minutes=60, end_date=date(2026, 11, 30)
    )
    # 一般は他人に割り振れないが、未着手の仕事は自分で引き受けられる。
    with pytest.raises(ColonyPermissionError):
        colony.assign(ids["worker"], task_id, ids["other"])
    colony.assign(ids["worker"], task_id, ids["worker"])
    assert repository.get_task(task_id).AssigneeUserId == ids["worker"]
    colony.set_role(ids["top"], ids["mentor"], "chief")
    colony.assign(ids["mentor"], task_id, ids["other"])
    assert repository.get_task(task_id).AssigneeUserId == ids["other"]


def test_contributions_follow_ranking_publication_and_sum_teams(tmp_path) -> None:
    colony, _, repository, ids = _colony(tmp_path)
    for user, minutes in (("worker", 600), ("mentor", 300), ("other", 60), ("top", 0)):
        _create_task(
            repository,
            project_id=ids["project"],
            parent_task_id=None,
            name=f"{user}-done",
            assignee_user_id=ids[user],
            creator_user_id=ids["top"],
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 30),
            assigned_manhour=minutes or 30,
            actual_manhour=minutes or 30,
            completed_at=datetime(2026, 5, 20, 12, 0) if minutes else None,
        )
    room = colony.dig_room(ids["top"], parent_team_id=None, name="営業課", emblem=None)
    colony.join(ids["top"], room, ids["worker"])
    colony.join(ids["top"], room, ids["other"])

    as_other = colony.contributions(ids["other"], "2026-05")
    assert as_other["Months"] == ["2025-12", "2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
    people = {p.UserId: p for p in as_other["People"]}
    assert people[ids["worker"]].Visible  # 上位半分は見える
    assert people[ids["other"]].Visible  # 本人はいつでも見える
    assert people[ids["other"]].Months[-1].OutputMinutes == 60
    assert not people[ids["top"]].Visible
    team = as_other["Teams"][0]
    assert team.MemberCount == 2
    assert team.Months[-1].OutputMinutes == 660
    assert team.PerPersonOutputMinutes[-1] == 330.0
    assert as_other["Company"][-1].OutputMinutes == 960

    as_top = colony.contributions(ids["top"], "2026-05")
    assert all(p.Visible for p in as_top["People"])
