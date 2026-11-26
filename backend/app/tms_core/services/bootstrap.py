from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.tms_core.config import Settings, TaskSeed
from app.tms_core.db.models import Task, User
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.db.session import build_sqlite_url
from app.tms_core.domain.rounding import normalize_minutes_multiple_of_30


DEMO_TASK_SEED_STATE_KEY = "DemoSeedTasksV2"



def run_migrations(db_path: Path, project_root: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    cfg = Config(str(project_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(project_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", build_sqlite_url(db_path))
    # ログ設定は呼び出し側（アプリ起動）のものを使う。alembic/env.py 参照。
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")



def _seed_task_tree(
    repository: TmsRepository,
    project_id: str,
    task_seed: TaskSeed,
    user_id_by_name: dict[str, str],
    parent_task_id: str | None,
    project_start_date: date,
) -> None:
    assignee = user_id_by_name[task_seed.assignee]
    creator = user_id_by_name[task_seed.creator]
    task_type = "GroupTask" if task_seed.children else "Task"

    created = repository.create_task(
        NewTaskInput(
            ProjectId=project_id,
            ParentTaskId=parent_task_id,
            Name=task_seed.name,
            Type=task_type,
            AssigneeUserId=assignee,
            CreatorUserId=creator,
            StartDate=project_start_date,
            EndDate=task_seed.due_date,
            AssignedManHour=normalize_minutes_multiple_of_30(task_seed.assigned_minutes),
            PlannedDeliverables=task_seed.planned_deliverables,
            IsRework=False,
        )
    )

    for child in task_seed.children:
        _seed_task_tree(
            repository=repository,
            project_id=project_id,
            task_seed=child,
            user_id_by_name=user_id_by_name,
            parent_task_id=created.Id,
            project_start_date=project_start_date,
        )



def _get_or_create_task(
    repository: TmsRepository,
    existing_by_name: dict[str, Task],
    *,
    project_id: str,
    name: str,
    task_type: str,
    assignee_user_id: str,
    creator_user_id: str,
    parent_task_id: str | None,
    start_date: date,
    end_date: date,
    assigned_minutes: int,
    planned_deliverables: list[dict[str, str]] | None = None,
    is_rework: bool = False,
) -> Task:
    current = existing_by_name.get(name)
    if current is not None:
        return current

    created = repository.create_task(
        NewTaskInput(
            ProjectId=project_id,
            ParentTaskId=parent_task_id,
            Name=name,
            Type=task_type,
            AssigneeUserId=assignee_user_id,
            CreatorUserId=creator_user_id,
            StartDate=start_date,
            EndDate=end_date,
            AssignedManHour=normalize_minutes_multiple_of_30(assigned_minutes),
            PlannedDeliverables=planned_deliverables or [],
            IsRework=is_rework,
        )
    )
    existing_by_name[name] = created
    return created



def _set_demo_leaf_state(
    repository: TmsRepository,
    *,
    task_id: str,
    status: str,
    actual_minutes: int,
    output_deliverables: list[dict[str, str]] | None = None,
) -> None:
    normalized_actual = normalize_minutes_multiple_of_30(actual_minutes)
    repository.update_task_actual_manhour(task_id, normalized_actual)

    if output_deliverables is not None:
        repository.update_task_output_deliverables(task_id, output_deliverables)

    progress = 100.0 if status == "Completion" else 0.0
    repository.update_task_status(task_id=task_id, status=status, progress_rate=progress)



def _calculate_project_progress(tasks: list[Task]) -> float:
    parent_ids = {task.ParentTaskId for task in tasks if task.ParentTaskId}
    leaves = [task for task in tasks if task.Id not in parent_ids]

    total = sum(max(0, task.AssignedManHour) for task in leaves)
    if total <= 0:
        return 0.0

    done = sum(max(0, task.AssignedManHour) for task in leaves if task.Status == "Completion")
    return (done / total) * 100.0



def _ensure_demo_tasks(
    repository: TmsRepository,
    settings: Settings,
    *,
    project_id: str,
    users: list[User],
    top_manager_id: str,
) -> dict[str, str]:
    if repository.get_state(DEMO_TASK_SEED_STATE_KEY) == "1":
        tasks = repository.list_tasks(project_id)
        user_active_task: dict[str, str] = {}
        for user in users:
            prefix = f"[DEMO] {user.Name} 進行中タスク"
            found = next((task for task in tasks if task.Name == prefix), None)
            if found is not None:
                user_active_task[user.Id] = found.Id
        return user_active_task

    existing = repository.list_tasks(project_id)
    existing_by_name = {task.Name: task for task in existing}

    today = date.today()
    user_active_task: dict[str, str] = {}
    done_tasks_for_rework: list[str] = []

    for idx, user in enumerate(users):
        group_name = f"[DEMO] {user.Name} 週間WBS"
        group_due = today + timedelta(days=35 + (idx % 6))

        group = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=group_name,
            task_type="GroupTask",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=None,
            start_date=settings.team.project_start_date,
            end_date=group_due,
            assigned_minutes=720,
            planned_deliverables=[
                {
                    "title": f"{user.Name} 週間計画",
                    "url": f"https://example.com/demo/{idx}/weekly-plan",
                }
            ],
        )

        done_task = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} 実施済みタスク",
            task_type="Task",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=group.Id,
            start_date=settings.team.project_start_date,
            end_date=today - timedelta(days=(idx % 4) + 1),
            assigned_minutes=180,
            planned_deliverables=[
                {
                    "title": f"{user.Name} 完了成果物(予定)",
                    "url": f"https://example.com/demo/{idx}/planned-done",
                }
            ],
        )
        deep_phase = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} 深掘りフェーズ",
            task_type="GroupTask",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=group.Id,
            start_date=today - timedelta(days=(idx % 4)),
            end_date=today + timedelta(days=12 + (idx % 5)),
            assigned_minutes=480,
            planned_deliverables=[
                {
                    "title": f"{user.Name} 深掘り計画",
                    "url": f"https://example.com/demo/{idx}/deep-plan",
                }
            ],
        )
        design_group = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} 深掘り-設計",
            task_type="GroupTask",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=deep_phase.Id,
            start_date=today - timedelta(days=(idx % 3)),
            end_date=today + timedelta(days=8 + (idx % 4)),
            assigned_minutes=240,
            planned_deliverables=[
                {
                    "title": f"{user.Name} 設計方針",
                    "url": f"https://example.com/demo/{idx}/deep-design",
                }
            ],
        )
        design_spec = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} 深掘り-設計-仕様化",
            task_type="Task",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=design_group.Id,
            start_date=today - timedelta(days=3),
            end_date=today + timedelta(days=2 + (idx % 3)),
            assigned_minutes=120,
            planned_deliverables=[
                {
                    "title": f"{user.Name} 仕様書(予定)",
                    "url": f"https://example.com/demo/{idx}/deep-spec",
                }
            ],
        )
        design_review = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} 深掘り-設計-レビュー",
            task_type="Task",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=design_group.Id,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=4 + (idx % 3)),
            assigned_minutes=120,
            planned_deliverables=[
                {
                    "title": f"{user.Name} 設計レビュー記録(予定)",
                    "url": f"https://example.com/demo/{idx}/deep-review",
                }
            ],
        )
        impl_group = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} 深掘り-実装",
            task_type="GroupTask",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=deep_phase.Id,
            start_date=today - timedelta(days=(idx % 2)),
            end_date=today + timedelta(days=10 + (idx % 4)),
            assigned_minutes=240,
            planned_deliverables=[
                {
                    "title": f"{user.Name} 実装計画",
                    "url": f"https://example.com/demo/{idx}/deep-impl",
                }
            ],
        )
        impl_api = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} 深掘り-実装-API",
            task_type="Task",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=impl_group.Id,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=5 + (idx % 3)),
            assigned_minutes=120,
            planned_deliverables=[
                {
                    "title": f"{user.Name} API実装(予定)",
                    "url": f"https://example.com/demo/{idx}/deep-api",
                }
            ],
        )
        impl_ui = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} 深掘り-実装-UI",
            task_type="Task",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=impl_group.Id,
            start_date=today,
            end_date=today + timedelta(days=6 + (idx % 3)),
            assigned_minutes=120,
            planned_deliverables=[
                {
                    "title": f"{user.Name} UI実装(予定)",
                    "url": f"https://example.com/demo/{idx}/deep-ui",
                }
            ],
        )

        doing_task = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} 進行中タスク",
            task_type="Task",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=group.Id,
            start_date=today - timedelta(days=(idx % 3) + 1),
            end_date=today + timedelta(days=(idx % 7) + 3),
            assigned_minutes=240,
            planned_deliverables=[
                {
                    "title": f"{user.Name} 進行中成果物(予定)",
                    "url": f"https://example.com/demo/{idx}/planned-doing",
                }
            ],
        )

        backlog_task = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} 未着手タスク",
            task_type="Task",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=group.Id,
            start_date=today,
            end_date=today + timedelta(days=(idx % 9) + 8),
            assigned_minutes=300,
            planned_deliverables=[
                {
                    "title": f"{user.Name} 未着手成果物(予定)",
                    "url": f"https://example.com/demo/{idx}/planned-backlog",
                }
            ],
        )

        _set_demo_leaf_state(
            repository,
            task_id=done_task.Id,
            status="Completion",
            actual_minutes=210,
            output_deliverables=[
                {
                    "title": f"{user.Name} 完了成果物",
                    "url": f"https://example.com/demo/{idx}/done",
                }
            ],
        )
        _set_demo_leaf_state(
            repository,
            task_id=doing_task.Id,
            status="DuringStart",
            actual_minutes=120,
        )
        _set_demo_leaf_state(
            repository,
            task_id=backlog_task.Id,
            status="NotStart",
            actual_minutes=0,
        )
        _set_demo_leaf_state(
            repository,
            task_id=design_spec.Id,
            status="Completion",
            actual_minutes=120,
            output_deliverables=[
                {
                    "title": f"{user.Name} 深掘り仕様書",
                    "url": f"https://example.com/demo/{idx}/deep-spec-done",
                }
            ],
        )
        _set_demo_leaf_state(
            repository,
            task_id=design_review.Id,
            status="DuringStart",
            actual_minutes=60,
        )
        _set_demo_leaf_state(
            repository,
            task_id=impl_api.Id,
            status="DuringStart",
            actual_minutes=90,
        )
        _set_demo_leaf_state(
            repository,
            task_id=impl_ui.Id,
            status="NotStart",
            actual_minutes=0,
        )

        done_tasks_for_rework.append(done_task.Id)
        done_tasks_for_rework.append(design_spec.Id)
        user_active_task[user.Id] = doing_task.Id

    for idx, user in enumerate(users[:6]):
        rework = _get_or_create_task(
            repository,
            existing_by_name,
            project_id=project_id,
            name=f"[DEMO] {user.Name} リカバリタスク",
            task_type="Task",
            assignee_user_id=user.Id,
            creator_user_id=top_manager_id,
            parent_task_id=None,
            start_date=today - timedelta(days=1),
            end_date=today + timedelta(days=6 + idx),
            assigned_minutes=120,
            is_rework=True,
            planned_deliverables=[
                {
                    "title": f"{user.Name} リカバリ成果物(予定)",
                    "url": f"https://example.com/demo/rework/{idx}",
                }
            ],
        )
        _set_demo_leaf_state(
            repository,
            task_id=rework.Id,
            status="DuringStart",
            actual_minutes=60,
        )

        prevented = done_tasks_for_rework[(idx + 1) % len(done_tasks_for_rework)]
        repository.set_rework_link(rework.Id, prevented)

    repository.set_state(DEMO_TASK_SEED_STATE_KEY, "1")
    return user_active_task



def _ensure_demo_attendance_and_timers(
    repository: TmsRepository,
    *,
    users: list[User],
    active_task_by_user: dict[str, str],
    top_manager_id: str,
) -> None:
    if repository.get_state("DemoSeedAttendanceV1") == "1":
        return

    today = date.today()

    for idx, user in enumerate(users):
        active_task_id = active_task_by_user.get(user.Id)
        if active_task_id is None:
            continue

        for day_offset in range(10, 2, -1):
            work_day = today - timedelta(days=day_offset)

            start_at = datetime.combine(work_day, time(9, 0))
            end_at = datetime.combine(work_day, time(18, 0)) + timedelta(minutes=(idx % 3) * 15)
            work_minutes = max(0, int((end_at - start_at).total_seconds() // 60))

            repository.upsert_daily_attendance(
                user_id=user.Id,
                work_date=work_day,
                start_at=start_at,
                end_at=end_at,
                work_minutes=work_minutes,
            )

            task_start = start_at + timedelta(minutes=30)
            timer = repository.start_timer(
                user_id=user.Id,
                task_id=active_task_id,
                now=task_start,
                is_ojt=False,
                teacher_user_id=None,
            )
            repository.stop_timer(
                timer_id=timer.Id,
                status="Stopped",
                now=task_start + timedelta(minutes=120 + (idx % 2) * 30),
            )

            if idx % 4 == 0:
                ojt_start = start_at + timedelta(minutes=220)
                ojt = repository.start_timer(
                    user_id=user.Id,
                    task_id=active_task_id,
                    now=ojt_start,
                    is_ojt=True,
                    teacher_user_id=top_manager_id,
                )
                repository.stop_timer(
                    timer_id=ojt.Id,
                    status="Stopped",
                    now=ojt_start + timedelta(minutes=60),
                )

    repository.set_state("DemoSeedAttendanceV1", "1")



def _ensure_demo_progress_history(repository: TmsRepository, *, project_id: str) -> None:
    if repository.get_state("DemoSeedHistoryV1") == "1":
        return

    tasks = repository.list_tasks(project_id)
    current = _calculate_project_progress(tasks)

    now = datetime.now().replace(hour=18, minute=0, second=0, microsecond=0)

    start_progress = max(0.0, current - 28.0)
    points: list[float] = []
    for idx in range(12):
        ratio = idx / 11
        value = start_progress + (current - start_progress) * ratio
        value += (idx % 3) * 0.4
        value = min(current, max(0.0, value))
        if points:
            value = max(points[-1], value)
        points.append(round(value, 2))

    for idx, value in enumerate(points):
        recorded_at = now - timedelta(days=(len(points) - idx - 1))
        repository.add_progress_history(
            project_id=project_id,
            recorded_at=recorded_at,
            progress_rate=value,
            reason="DemoSeed",
        )

    repository.set_state("DemoSeedHistoryV1", "1")



def _ensure_demo_colony(repository: TmsRepository, *, users: list, top_manager_id: str) -> None:
    """デモ用の巣。役職4段・部屋の入れ子・掛け持ち・方針のチョークが一通り見えるようにする。"""
    if repository.list_colony_teams():
        return
    others = [user for user in users if user.Id != top_manager_id]
    repository.set_user_colony_role(top_manager_id, "president")
    if not others:
        return
    manager = others[0]
    chief = others[1] if len(others) > 1 else None
    repository.set_user_colony_role(manager.Id, "manager")
    if chief is not None:
        repository.set_user_colony_role(chief.Id, "chief")
    planning = repository.create_colony_team(
        name="企画課", parent_team_id=None, leader_user_id=manager.Id, emblem="leaf"
    )
    operations = repository.create_colony_team(
        name="運営課", parent_team_id=None, leader_user_id=None, emblem="acorn"
    )
    first_unit = repository.create_colony_team(
        name="企画一係",
        parent_team_id=planning.Id,
        leader_user_id=chief.Id if chief is not None else None,
        emblem="flower",
    )
    repository.add_colony_team_member(planning.Id, manager.Id)
    if chief is not None:
        repository.add_colony_team_member(planning.Id, chief.Id)
        repository.add_colony_team_member(first_unit.Id, chief.Id)
    for index, user in enumerate(others[2:]):
        repository.add_colony_team_member(first_unit.Id if index % 2 == 0 else operations.Id, user.Id)
    if len(others) > 2:
        # 掛け持ちの例
        repository.add_colony_team_member(operations.Id, others[2].Id)
    repository.create_colony_policy(
        kind="text",
        text="お客様の時間を大切に",
        x=-120,
        y=-260,
        size=2.2,
        points=[],
        author_user_id=top_manager_id,
    )


def bootstrap_data(settings: Settings, repository: TmsRepository) -> None:
    # メンバーの初期投入は iquavis_link_targets.xlsx を正とする。
    # ただし利用者が1人もいない空のDB（iQUAVIS を使わない新規導入）では、
    # 誰も操作できなくなるので settings.yaml の users.members から作る。既に利用者がいるDBは触らない。
    if not repository.list_users() and settings.users.member_names():
        repository.ensure_users(
            names=settings.users.member_names(),
            top_manager_name=settings.users.top_manager,
            free_time_rate_default=settings.app.free_time_rate_default,
            login_id_by_name=settings.users.member_login_ids_by_name(),
            iquavis_user_id_by_name=settings.users.member_iquavis_user_ids_by_name(),
        )
    repository.ensure_member_profiles()

    project = repository.ensure_single_project(
        name=settings.team.project_name,
        start_date=settings.team.project_start_date,
        end_date=settings.team.project_end_date,
        crash_zone_pct=settings.app.crash_zone_pct,
    )

    current_ym = datetime.now().strftime("%Y-%m")
    users = repository.list_users()
    for user in users:
        repository.upsert_monthly_education_rate(
            user_id=user.Id,
            year_month=current_ym,
            rate=settings.team.default_education_rate_monthly,
        )

    should_seed_on_empty = repository.count_tasks(project.Id) == 0

    if should_seed_on_empty and settings.app.seed_initial_tasks_on_empty_db:
        user_id_by_name = {user.Name: user.Id for user in users}

        for task_seed in settings.initial_tasks:
            _seed_task_tree(
                repository=repository,
                project_id=project.Id,
                task_seed=task_seed,
                user_id_by_name=user_id_by_name,
                parent_task_id=None,
                project_start_date=settings.team.project_start_date,
            )

    if should_seed_on_empty and settings.app.seed_demo_data_on_empty_db:
        top_manager = repository.get_top_manager()
        if top_manager is None:
            raise RuntimeError("Top Manager が見つかりません")

        active_task_by_user = _ensure_demo_tasks(
            repository=repository,
            settings=settings,
            project_id=project.Id,
            users=users,
            top_manager_id=top_manager.Id,
        )

        _ensure_demo_attendance_and_timers(
            repository=repository,
            users=users,
            active_task_by_user=active_task_by_user,
            top_manager_id=top_manager.Id,
        )

        _ensure_demo_progress_history(repository=repository, project_id=project.Id)
        _ensure_demo_colony(repository=repository, users=users, top_manager_id=top_manager.Id)
