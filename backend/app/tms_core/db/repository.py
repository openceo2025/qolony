from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterator
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy import Select, and_, func, select
from sqlalchemy.orm import Session, sessionmaker

from .models import (
    AppState,
    ColonyPolicy,
    ColonyTeam,
    ColonyTeamMember,
    DailyAttendance,
    DailyProgressSnapshot,
    DailyTaskActual,
    IQuavisSyncJob,
    IQuavisTaskBaseline,
    MemberDayConfig,
    MemberProfile,
    MonthlyEducationRate,
    ProgressRateHistory,
    Project,
    RawWorklog,
    Task,
    TimerSession,
    User,
)


@dataclass(frozen=True)
class NewTaskInput:
    ProjectId: str
    ParentTaskId: str | None
    Name: str
    Type: str
    AssigneeUserId: str
    CreatorUserId: str
    StartDate: date | None
    EndDate: date
    AssignedManHour: int
    PlannedDeliverables: list[dict[str, str]]
    IsRework: bool = False
    Memo: str = ""


@dataclass(frozen=True)
class TaskImportUpdate:
    """外部データ取込で既存タスクへ一括適用する値。"""

    task_id: str
    parent_task_id: str | None
    name: str
    task_type: str
    assignee_user_id: str
    start_date: date
    end_date: date
    actual_manhour: int
    status: str
    progress_rate: float
    memo: str
    pulled_at: datetime


@dataclass(frozen=True)
class IquavisTaskBaselineInput:
    """QOLONYとiQUAVISが合意した共有項目の値。3値比較のbaselineとして保存する。"""

    task_id: str
    iquavis_task_id: str
    name: str
    start_date: date
    end_date: date
    actual_manhour: int
    status: str
    progress_rate: int | None
    memo: str


class TmsRepository:
    def __init__(self, session_factory: sessionmaker):
        self._session_factory = session_factory

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session: Session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ensure_users(
        self,
        names: list[str],
        top_manager_name: str,
        free_time_rate_default: float,
        login_id_by_name: dict[str, str] | None = None,
        iquavis_user_id_by_name: dict[str, str] | None = None,
    ) -> None:
        with self._session() as session:
            normalized_names = [str(name).strip() for name in names if str(name).strip()]
            normalized_top = str(top_manager_name).strip()
            normalized_login_ids = {
                str(name).strip(): str(login_id).strip()
                for name, login_id in (login_id_by_name or {}).items()
                if str(name).strip() and str(login_id).strip()
            }
            normalized_iquavis_user_ids = {
                str(name).strip(): str(iquavis_user_id).strip()
                for name, iquavis_user_id in (iquavis_user_id_by_name or {}).items()
                if str(name).strip() and str(iquavis_user_id).strip()
            }
            existing_by_name = {
                user.Name: user for user in session.scalars(select(User)).all()
            }

            for name in normalized_names:
                desired_login_id = normalized_login_ids.get(name) or name
                desired_iquavis_user_id = normalized_iquavis_user_ids.get(name) or None
                if name in existing_by_name:
                    existing = existing_by_name[name]
                    existing.LoginId = desired_login_id
                    existing.IQuavisUserId = desired_iquavis_user_id
                    continue
                user = User(
                    Id=str(uuid4()),
                    Name=name,
                    Nickname=name,
                    FullName="",
                    LoginId=desired_login_id,
                    IQuavisUserId=desired_iquavis_user_id,
                    IsTopManager=False,
                    IsActive=True,
                    FreeTimeRate=free_time_rate_default,
                )
                session.add(user)
                existing_by_name[name] = user

            # autoflush=False のため、再検索前に flush する。
            session.flush()
            users = session.scalars(select(User)).all()
            for user in users:
                user.IsTopManager = user.Name == normalized_top

    def ensure_member_profiles(self) -> None:
        with self._session() as session:
            users = session.scalars(select(User).order_by(User.Name)).all()
            existing_user_ids = {
                profile.UserId for profile in session.scalars(select(MemberProfile)).all()
            }
            for index, user in enumerate(users):
                if user.Id in existing_user_ids:
                    continue
                session.add(
                    MemberProfile(
                        UserId=user.Id,
                        DefaultCommitRate=1.0,
                        ConditionIcon="normal",
                        FormationRole="",
                        FormationPosition="",
                        FormationX=50,
                        FormationY=min(92, 12 + index * 8),
                        DisplayOrder=index + 1,
                        TitleTagsJson="[]",
                        SpecialAbilitiesJson="[]",
                    )
                )

    def list_users(self, *, active_only: bool = False) -> list[User]:
        with self._session() as session:
            stmt = select(User)
            if active_only:
                stmt = stmt.where(User.IsActive.is_(True))
            return session.scalars(stmt.order_by(User.Name)).all()

    def update_member_identity(
        self,
        *,
        user_id: str,
        nickname: str,
        is_active: bool,
    ) -> None:
        with self._session() as session:
            user = session.get(User, user_id)
            if user is None:
                raise ValueError("member not found")
            user.Nickname = nickname
            user.IsActive = bool(is_active)

    def upsert_member_from_link_target(
        self,
        *,
        qolony_user_id: str | None,
        nickname: str,
        full_name: str,
        login_id: str | None,
        iquavis_user_id: str | None,
        is_top_manager: bool,
        is_active: bool,
        free_time_rate_default: float,
    ) -> tuple[str, bool]:
        with self._session() as session:
            user: User | None = None
            if qolony_user_id:
                user = session.get(User, qolony_user_id)
            if user is None and iquavis_user_id:
                user = session.scalar(select(User).where(User.IQuavisUserId == iquavis_user_id))
            if user is None and login_id:
                user = session.scalar(select(User).where(User.LoginId == login_id))

            created = False
            if user is None:
                if not nickname:
                    raise ValueError("nickname is required for new member")
                user_id = qolony_user_id or str(uuid4())
                stable_name = full_name or nickname
                if session.scalar(select(User).where(User.Name == stable_name)) is not None:
                    stable_name = f"{stable_name} ({user_id[:8]})"
                desired_login_id = login_id or f"local:{user_id}"
                user = User(
                    Id=user_id,
                    Name=stable_name,
                    Nickname=nickname,
                    FullName=full_name,
                    LoginId=desired_login_id,
                    IQuavisUserId=iquavis_user_id,
                    IsTopManager=bool(is_top_manager),
                    IsActive=bool(is_active),
                    FreeTimeRate=free_time_rate_default,
                )
                session.add(user)
                created = True
            else:
                if nickname:
                    user.Nickname = nickname
                user.FullName = full_name
                if login_id:
                    user.LoginId = login_id
                if iquavis_user_id:
                    user.IQuavisUserId = iquavis_user_id
                user.IsTopManager = bool(is_top_manager)
                user.IsActive = bool(is_active)

            session.flush()
            if user.IsTopManager:
                for other in session.scalars(select(User).where(User.Id != user.Id)).all():
                    other.IsTopManager = False

            profile = session.get(MemberProfile, user.Id)
            if profile is None:
                session.add(MemberProfile(UserId=user.Id))
            return user.Id, created

    def list_member_profiles(self) -> list[MemberProfile]:
        with self._session() as session:
            return session.scalars(select(MemberProfile).order_by(MemberProfile.DisplayOrder, MemberProfile.UserId)).all()

    def get_member_profile(self, user_id: str) -> MemberProfile | None:
        with self._session() as session:
            return session.get(MemberProfile, user_id)

    def update_member_profile(
        self,
        *,
        user_id: str,
        default_commit_rate: float,
        condition_icon: str,
        formation_role: str,
        formation_position: str,
        formation_x: int,
        formation_y: int,
        display_order: int,
        title_tags_json: str,
        special_abilities_json: str,
    ) -> None:
        with self._session() as session:
            profile = session.get(MemberProfile, user_id)
            if profile is None:
                profile = MemberProfile(UserId=user_id)
                session.add(profile)
            profile.DefaultCommitRate = float(default_commit_rate)
            profile.ConditionIcon = condition_icon
            profile.FormationRole = formation_role
            profile.FormationPosition = formation_position
            profile.FormationX = int(formation_x)
            profile.FormationY = int(formation_y)
            profile.DisplayOrder = int(display_order)
            profile.TitleTagsJson = title_tags_json
            profile.SpecialAbilitiesJson = special_abilities_json

    def list_member_day_configs(
        self,
        *,
        date_from: date,
        date_to: date,
        user_id: str | None = None,
    ) -> list[MemberDayConfig]:
        with self._session() as session:
            cond = [
                MemberDayConfig.WorkDate >= date_from,
                MemberDayConfig.WorkDate <= date_to,
            ]
            if user_id is not None:
                cond.append(MemberDayConfig.UserId == user_id)
            stmt = select(MemberDayConfig).where(and_(*cond))
            return session.scalars(stmt.order_by(MemberDayConfig.WorkDate, MemberDayConfig.UserId)).all()

    def get_member_day_config(self, *, user_id: str, work_date: date) -> MemberDayConfig | None:
        with self._session() as session:
            return session.scalar(
                select(MemberDayConfig).where(
                    and_(MemberDayConfig.UserId == user_id, MemberDayConfig.WorkDate == work_date)
                )
            )

    def upsert_member_day_config(
        self,
        *,
        user_id: str,
        work_date: date,
        is_vacation: bool,
        leave_type: str = "",
        commit_rate_override: float | None = None,
        note: str = "",
    ) -> None:
        with self._session() as session:
            item = session.scalar(
                select(MemberDayConfig).where(
                    and_(MemberDayConfig.UserId == user_id, MemberDayConfig.WorkDate == work_date)
                )
            )
            if item is None:
                item = MemberDayConfig(
                    Id=str(uuid4()),
                    UserId=user_id,
                    WorkDate=work_date,
                    IsVacation=bool(is_vacation),
                    LeaveType=leave_type,
                    CommitRateOverride=commit_rate_override,
                    Note=note,
                )
                session.add(item)
            else:
                item.IsVacation = bool(is_vacation)
                item.LeaveType = leave_type
                item.CommitRateOverride = commit_rate_override
                item.Note = note

    def delete_member_day_config(self, *, user_id: str, work_date: date) -> None:
        with self._session() as session:
            session.execute(
                sa.delete(MemberDayConfig).where(
                    and_(MemberDayConfig.UserId == user_id, MemberDayConfig.WorkDate == work_date)
                )
            )

    def get_user(self, user_id: str) -> User | None:
        with self._session() as session:
            return session.get(User, user_id)

    def get_user_by_name(self, user_name: str) -> User | None:
        with self._session() as session:
            return session.scalar(select(User).where(User.Name == user_name))

    def get_user_by_login_id(self, login_id: str) -> User | None:
        with self._session() as session:
            return session.scalar(select(User).where(User.LoginId == login_id))

    def get_user_by_iquavis_user_id(self, iquavis_user_id: str) -> User | None:
        with self._session() as session:
            return session.scalar(select(User).where(User.IQuavisUserId == iquavis_user_id))

    def get_top_manager(self) -> User | None:
        with self._session() as session:
            return session.scalar(select(User).where(User.IsTopManager.is_(True)))

    def ensure_single_project(
        self,
        name: str,
        start_date: date,
        end_date: date,
        crash_zone_pct: float,
    ) -> Project:
        with self._session() as session:
            project = session.scalar(select(Project).limit(1))
            if project is None:
                project = Project(
                    Id=str(uuid4()),
                    Name=name,
                    StartDate=start_date,
                    EndDate=end_date,
                    CrashZonePct=crash_zone_pct,
                )
                session.add(project)
            else:
                project.Name = name
                project.StartDate = start_date
                project.EndDate = end_date
                project.CrashZonePct = crash_zone_pct
            session.flush()
            return project

    def get_project(self) -> Project | None:
        with self._session() as session:
            return session.scalar(select(Project).limit(1))

    def set_crash_zone_pct(self, crash_zone_pct: float) -> None:
        with self._session() as session:
            project = session.scalar(select(Project).limit(1))
            if project is None:
                raise ValueError("繝励Ο繧ｸ繧ｧ繧ｯ繝医′蟄伜惠縺励∪縺帙ｓ")
            project.CrashZonePct = crash_zone_pct

    def create_task(self, payload: NewTaskInput) -> Task:
        with self._session() as session:
            created_at = datetime.now()
            task = Task(
                Id=str(uuid4()),
                ProjectId=payload.ProjectId,
                ParentTaskId=payload.ParentTaskId,
                Name=payload.Name,
                Type=payload.Type,
                AssigneeUserId=payload.AssigneeUserId,
                CreatorUserId=payload.CreatorUserId,
                StartDate=payload.StartDate or date.today(),
                EndDate=payload.EndDate,
                AssignedManHour=payload.AssignedManHour,
                PlannedDeliverablesJson=json.dumps(payload.PlannedDeliverables, ensure_ascii=False),
                OutputDeliverablesJson="[]",
                IsRework=payload.IsRework,
                Memo=payload.Memo,
                Status="NotStart",
                ProgressRate=0.0,
                # SQLite の CURRENT_TIMESTAMP は UTC のため、日付境界では
                # ローカル日付で決める StartDate と1日ずれる。アプリと同じ
                # ローカル時刻を明示して、作成日と既定開始日を一致させる。
                CreateDate=created_at,
            )
            session.add(task)
            session.flush()
            if payload.StartDate is None:
                # StartDate の既定値は CreateDate と同日にそろえる。
                task.StartDate = task.CreateDate.date()
                session.flush()
            session.refresh(task)
            return task

    def get_task(self, task_id: str) -> Task | None:
        with self._session() as session:
            return session.get(Task, task_id)

    def get_task_by_iquavis_task_id(self, iquavis_task_id: str) -> Task | None:
        with self._session() as session:
            return session.scalar(select(Task).where(Task.IQuavisTaskId == iquavis_task_id))

    def list_tasks(self, project_id: str) -> list[Task]:
        with self._session() as session:
            stmt: Select[tuple[Task]] = select(Task).where(Task.ProjectId == project_id)
            return session.scalars(stmt.order_by(Task.EndDate, Task.CreateDate)).all()

    def list_paired_tasks(self, project_id: str | None = None) -> list[Task]:
        with self._session() as session:
            cond = [Task.IQuavisTaskId.is_not(None)]
            if project_id is not None:
                cond.append(Task.ProjectId == project_id)
            stmt = select(Task).where(and_(*cond))
            return session.scalars(stmt.order_by(Task.UpdateDate, Task.CreateDate)).all()

    def list_incomplete_tasks(self, project_id: str) -> list[Task]:
        with self._session() as session:
            stmt = select(Task).where(
                and_(
                    Task.ProjectId == project_id,
                    Task.Status.notin_(("Completion", "Abort")),
                )
            )
            return session.scalars(stmt.order_by(Task.EndDate)).all()

    def list_leaf_tasks(self, project_id: str) -> list[Task]:
        with self._session() as session:
            subq = select(Task.ParentTaskId).where(Task.ProjectId == project_id).subquery()
            stmt = select(Task).where(
                and_(
                    Task.ProjectId == project_id,
                    ~Task.Id.in_(select(subq.c.ParentTaskId).where(subq.c.ParentTaskId.is_not(None))),
                )
            )
            return session.scalars(stmt).all()

    def update_task_status(self, task_id: str, status: str, progress_rate: float) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            task.Status = status
            task.ProgressRate = progress_rate
            task.CompletedAt = datetime.now() if status == "Completion" else None

    def update_task_output_deliverables(
        self,
        task_id: str,
        deliverables: list[dict[str, str]],
    ) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            task.OutputDeliverablesJson = json.dumps(deliverables, ensure_ascii=False)

    def update_task_actual_manhour(self, task_id: str, actual_manhour: int) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            task.ActualManHour = actual_manhour

    def update_task_assigned_manhours(self, assigned_by_task_id: dict[str, int]) -> None:
        with self._session() as session:
            for task_id, assigned_manhour in assigned_by_task_id.items():
                task = session.get(Task, task_id)
                if task is None:
                    raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
                task.AssignedManHour = int(assigned_manhour)

    def update_task_progress_rate(self, task_id: str, progress_rate: float) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            task.ProgressRate = progress_rate

    def update_task_progress_states(
        self,
        updates: dict[str, tuple[float, str | None]],
    ) -> None:
        """進捗率と状態の変更を一つのトランザクションで保存する。

        変更された祖先ごとにSQLiteセッションを開くと、大規模WBSの完了操作が
        停止したように見えるため、計算結果を変えずに行単位の確定コストを除く。
        """
        if not updates:
            return
        with self._session() as session:
            task_ids = list(updates)
            task_by_id: dict[str, Task] = {}
            # SQLiteのバインド変数上限を超えないよう、読込みだけを分割する。
            for offset in range(0, len(task_ids), 900):
                chunk = task_ids[offset : offset + 900]
                tasks = session.scalars(select(Task).where(Task.Id.in_(chunk))).all()
                task_by_id.update({task.Id: task for task in tasks})
            missing = set(updates) - set(task_by_id)
            if missing:
                raise ValueError(f"task not found: {sorted(missing)[0]}")
            for task_id, (progress_rate, status) in updates.items():
                task = task_by_id[task_id]
                task.ProgressRate = float(progress_rate)
                if status is not None:
                    task.Status = status
                    task.CompletedAt = datetime.now() if status == "Completion" else None

    def update_task_today_override(self, task_id: str, today_override: bool | None) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            task.TodayOverride = today_override

    def update_task_today_sort_order(self, task_id: str, today_sort_order: int | None) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            task.TodaySortOrder = today_sort_order

    def set_today_sort_orders(self, ordered_task_ids: list[str]) -> None:
        with self._session() as session:
            for idx, task_id in enumerate(ordered_task_ids, start=1):
                task = session.get(Task, task_id)
                if task is None:
                    raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
                task.TodaySortOrder = idx

    def set_wbs_sort_orders(self, ordered_task_ids: list[str]) -> None:
        with self._session() as session:
            for idx, task_id in enumerate(ordered_task_ids, start=1):
                task = session.get(Task, task_id)
                if task is None:
                    raise ValueError("タスクが存在しません")
                task.WbsSortOrder = idx

    def list_wbs_sibling_tasks(self, *, project_id: str, parent_task_id: str | None) -> list[Task]:
        with self._session() as session:
            stmt = select(Task).where(Task.ProjectId == project_id)
            if parent_task_id is None:
                stmt = stmt.where(Task.ParentTaskId.is_(None))
            else:
                stmt = stmt.where(Task.ParentTaskId == parent_task_id)
            return list(session.scalars(stmt).all())

    def update_task_type(self, task_id: str, task_type: str) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            task.Type = task_type

    def update_task_child_dependency_mode(self, task_id: str, child_dependency_mode: str) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("タスクが存在しません")
            task.ChildDependencyMode = child_dependency_mode
    def update_task_memo(self, task_id: str, memo: str) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            task.Memo = memo

    def update_task_name(self, task_id: str, name: str) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.Name = name

    def update_task_fields(
        self,
        task_id: str,
        *,
        name: str,
        assigned_manhour: int,
        memo: str,
        start_date: date,
        end_date: date,
        assignee_user_id: str,
    ) -> None:
        """Split画面の既存行で編集できる項目を、一つのトランザクションで保存する。"""
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.Name = name
            task.AssignedManHour = int(assigned_manhour)
            task.Memo = memo
            task.StartDate = start_date
            task.EndDate = end_date
            task.AssigneeUserId = assignee_user_id

    def update_task_import_fields(
        self,
        task_id: str,
        *,
        parent_task_id: str | None,
        name: str,
        task_type: str,
        assignee_user_id: str,
        start_date: date,
        end_date: date,
        memo: str,
    ) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.ParentTaskId = parent_task_id
            task.Name = name
            task.Type = task_type
            task.AssigneeUserId = assignee_user_id
            task.StartDate = start_date
            task.EndDate = end_date
            task.Memo = memo

    def apply_task_import_updates(self, updates: list[TaskImportUpdate]) -> None:
        """iQUAVIS取込値を一つのトランザクションで既存タスクへ反映する。"""
        if not updates:
            return
        with self._session() as session:
            update_by_id = {update.task_id: update for update in updates}
            task_by_id: dict[str, Task] = {}
            task_ids = list(update_by_id)
            for offset in range(0, len(task_ids), 900):
                chunk = task_ids[offset : offset + 900]
                tasks = session.scalars(select(Task).where(Task.Id.in_(chunk))).all()
                task_by_id.update({task.Id: task for task in tasks})
            missing = set(update_by_id) - set(task_by_id)
            if missing:
                raise ValueError(f"task not found: {sorted(missing)[0]}")

            completed_at = datetime.now()
            for task_id, update in update_by_id.items():
                task = task_by_id[task_id]
                task.ParentTaskId = update.parent_task_id
                task.Name = update.name
                task.Type = update.task_type
                task.AssigneeUserId = update.assignee_user_id
                task.StartDate = update.start_date
                task.EndDate = update.end_date
                task.ActualManHour = max(0, int(update.actual_manhour))
                task.Status = update.status
                task.ProgressRate = max(0.0, min(100.0, float(update.progress_rate)))
                task.Memo = update.memo
                task.CompletedAt = (
                    task.CompletedAt or completed_at
                    if update.status == "Completion"
                    else None
                )
                task.IQuavisLastPulledAt = update.pulled_at

    def set_task_iquavis_link(
        self,
        task_id: str,
        *,
        iquavis_task_id: str | None,
        iquavis_project_id: str | None,
    ) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.IQuavisTaskId = iquavis_task_id
            task.IQuavisProjectId = iquavis_project_id

    def update_task_parent(self, task_id: str, parent_task_id: str | None) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.ParentTaskId = parent_task_id

    def mark_task_iquavis_last_pulled(self, task_id: str, pulled_at: datetime) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.IQuavisLastPulledAt = pulled_at

    def mark_task_iquavis_last_pushed(self, task_id: str, pushed_at: datetime) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.IQuavisLastPushedAt = pushed_at

    def update_task_assignee_and_end_date(
        self,
        task_id: str,
        assignee_user_id: str,
        end_date: date,
    ) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            task.AssigneeUserId = assignee_user_id
            task.EndDate = end_date

    def update_task_schedule(
        self,
        task_id: str,
        start_date: date,
        end_date: date,
    ) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            task.StartDate = start_date
            task.EndDate = end_date

    def enqueue_iquavis_sync_job(
        self,
        *,
        task_id: str,
        iquavis_task_id: str,
        job_type: str,
        payload_json: str,
        next_run_at: datetime,
    ) -> str:
        with self._session() as session:
            existing = session.scalar(
                select(IQuavisSyncJob)
                .where(
                    and_(
                        IQuavisSyncJob.TaskId == task_id,
                        IQuavisSyncJob.JobType == job_type,
                        IQuavisSyncJob.Status.in_(("Pending", "Retry")),
                    )
                )
                .order_by(IQuavisSyncJob.CreateDate.desc())
            )
            if existing is not None:
                existing.IQuavisTaskId = iquavis_task_id
                existing.PayloadJson = payload_json
                existing.NextRunAt = next_run_at
                existing.LastError = None
                existing.LastHttpStatus = None
                existing.Status = "Pending"
                return existing.Id

            job = IQuavisSyncJob(
                Id=str(uuid4()),
                TaskId=task_id,
                IQuavisTaskId=iquavis_task_id,
                JobType=job_type,
                PayloadJson=payload_json,
                Status="Pending",
                Attempts=0,
                NextRunAt=next_run_at,
                LastStartedAt=None,
                LastFinishedAt=None,
                LastHttpStatus=None,
                LastError=None,
            )
            session.add(job)
            session.flush()
            return job.Id

    def list_due_iquavis_sync_jobs(self, *, now: datetime, limit: int = 20) -> list[IQuavisSyncJob]:
        with self._session() as session:
            stmt = (
                select(IQuavisSyncJob)
                .where(
                    and_(
                        IQuavisSyncJob.Status.in_(("Pending", "Retry")),
                        IQuavisSyncJob.NextRunAt <= now,
                    )
                )
                .order_by(IQuavisSyncJob.NextRunAt, IQuavisSyncJob.CreateDate)
                .limit(limit)
            )
            return session.scalars(stmt).all()

    def has_open_iquavis_sync_jobs(self, task_id: str) -> bool:
        with self._session() as session:
            stmt = (
                select(func.count())
                .select_from(IQuavisSyncJob)
                .where(
                    and_(
                        IQuavisSyncJob.TaskId == task_id,
                        IQuavisSyncJob.Status.in_(("Pending", "Retry", "Running")),
                    )
                )
            )
            return int(session.scalar(stmt) or 0) > 0

    def mark_iquavis_sync_job_running(self, job_id: str, *, started_at: datetime) -> None:
        with self._session() as session:
            job = session.get(IQuavisSyncJob, job_id)
            if job is None:
                raise ValueError("iquavis sync job not found")
            job.Status = "Running"
            job.Attempts = int(job.Attempts or 0) + 1
            job.LastStartedAt = started_at

    def mark_iquavis_sync_job_succeeded(
        self,
        job_id: str,
        *,
        finished_at: datetime,
        http_status: int | None,
    ) -> None:
        with self._session() as session:
            job = session.get(IQuavisSyncJob, job_id)
            if job is None:
                raise ValueError("iquavis sync job not found")
            job.Status = "Succeeded"
            job.LastFinishedAt = finished_at
            job.LastHttpStatus = http_status
            job.LastError = None

    def mark_iquavis_sync_job_retry(
        self,
        job_id: str,
        *,
        next_run_at: datetime,
        finished_at: datetime,
        error: str,
        http_status: int | None,
    ) -> None:
        with self._session() as session:
            job = session.get(IQuavisSyncJob, job_id)
            if job is None:
                raise ValueError("iquavis sync job not found")
            job.Status = "Retry"
            job.NextRunAt = next_run_at
            job.LastFinishedAt = finished_at
            job.LastHttpStatus = http_status
            job.LastError = error

    def mark_iquavis_sync_job_failed(
        self,
        job_id: str,
        *,
        finished_at: datetime,
        error: str,
        http_status: int | None,
    ) -> None:
        with self._session() as session:
            job = session.get(IQuavisSyncJob, job_id)
            if job is None:
                raise ValueError("iquavis sync job not found")
            job.Status = "Failed"
            job.LastFinishedAt = finished_at
            job.LastHttpStatus = http_status
            job.LastError = error

    def set_rework_link(self, rework_task_id: str, prevented_by_task_id: str) -> None:
        with self._session() as session:
            rework_task = session.get(Task, rework_task_id)
            prevented = session.get(Task, prevented_by_task_id)
            if rework_task is None or prevented is None:
                raise ValueError("謖・ｮ壹ち繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            if rework_task.ProjectId != prevented.ProjectId:
                raise ValueError("prevented_by は同一プロジェクト内のみ指定可能です")
            if rework_task.Id == prevented.Id:
                raise ValueError("prevented_by 縺ｫ閾ｪ蟾ｱ蜿ら・縺ｯ謖・ｮ壹〒縺阪∪縺帙ｓ")
            rework_task.PreventedByTaskId = prevented_by_task_id

    def list_rework_tasks(self, project_id: str) -> list[Task]:
        with self._session() as session:
            stmt = select(Task).where(
                and_(Task.ProjectId == project_id, Task.IsRework.is_(True))
            )
            return session.scalars(stmt.order_by(Task.EndDate)).all()

    def get_active_timer(self, user_id: str) -> TimerSession | None:
        with self._session() as session:
            return session.scalar(
                select(TimerSession).where(
                    and_(
                        TimerSession.UserId == user_id,
                        TimerSession.EndAt.is_(None),
                        TimerSession.Status == "Running",
                    )
                )
            )

    def start_timer(
        self,
        user_id: str,
        task_id: str,
        now: datetime,
        is_ojt: bool,
        teacher_user_id: str | None,
    ) -> TimerSession:
        with self._session() as session:
            timer = TimerSession(
                Id=str(uuid4()),
                UserId=user_id,
                TaskId=task_id,
                StartAt=now,
                EndAt=None,
                Status="Running",
                IsOjt=is_ojt,
                TeacherUserId=teacher_user_id,
                LastHeartbeatAt=now,
                LastInteractionAt=now,
                AutoStopReason=None,
            )
            session.add(timer)
            session.flush()
            return timer

    def stop_timer(self, timer_id: str, status: str, now: datetime, reason: str | None = None) -> None:
        with self._session() as session:
            timer = session.get(TimerSession, timer_id)
            if timer is None:
                raise ValueError("繧ｿ繧､繝槭′蟄伜惠縺励∪縺帙ｓ")
            if timer.EndAt is None:
                timer.EndAt = now
                timer.Status = status
                timer.AutoStopReason = reason

    def list_running_timers(self) -> list[TimerSession]:
        with self._session() as session:
            stmt = select(TimerSession).where(
                and_(TimerSession.EndAt.is_(None), TimerSession.Status == "Running")
            )
            return session.scalars(stmt).all()

    def list_timers_by_task(self, task_id: str) -> list[TimerSession]:
        with self._session() as session:
            stmt = select(TimerSession).where(TimerSession.TaskId == task_id)
            return session.scalars(stmt.order_by(TimerSession.StartAt)).all()

    def list_timers_by_user_and_day(self, user_id: str, work_date: date) -> list[TimerSession]:
        start = datetime.combine(work_date, datetime.min.time())
        end = datetime.combine(work_date, datetime.max.time())
        with self._session() as session:
            stmt = select(TimerSession).where(
                and_(
                    TimerSession.UserId == user_id,
                    TimerSession.StartAt >= start,
                    TimerSession.StartAt <= end,
                    TimerSession.EndAt.is_not(None),
                )
            )
            return session.scalars(stmt.order_by(TimerSession.StartAt)).all()

    def list_timers_by_user_and_month(self, user_id: str, year_month: str) -> list[TimerSession]:
        year, month = map(int, year_month.split("-"))
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)

        with self._session() as session:
            stmt = select(TimerSession).where(
                and_(
                    TimerSession.UserId == user_id,
                    TimerSession.StartAt >= start,
                    TimerSession.StartAt < end,
                    TimerSession.EndAt.is_not(None),
                )
            )
            return session.scalars(stmt).all()

    def list_timers_for_month(self, year_month: str) -> list[TimerSession]:
        year, month = map(int, year_month.split("-"))
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)

        with self._session() as session:
            stmt = select(TimerSession).where(
                and_(
                    TimerSession.StartAt >= start,
                    TimerSession.StartAt < end,
                    TimerSession.EndAt.is_not(None),
                )
            )
            return session.scalars(stmt).all()

    def add_raw_worklog(
        self,
        *,
        user_id: str,
        task_id: str,
        start_at: datetime,
        end_at: datetime,
        duration_seconds: float,
        is_ojt: bool,
        teacher_user_id: str | None,
        source_timer_id: str | None,
    ) -> bool:
        with self._session() as session:
            existing_id = session.scalar(
                select(RawWorklog.Id)
                .where(
                    and_(
                        RawWorklog.UserId == user_id,
                        RawWorklog.TaskId == task_id,
                        RawWorklog.StartAt == start_at,
                        RawWorklog.EndAt == end_at,
                        RawWorklog.IsOjt == is_ojt,
                        RawWorklog.TeacherUserId == teacher_user_id,
                        RawWorklog.SourceTimerId == source_timer_id,
                    )
                )
                .limit(1)
            )
            if existing_id is not None:
                return False
            session.add(
                RawWorklog(
                    Id=str(uuid4()),
                    UserId=user_id,
                    TaskId=task_id,
                    SourceTimerId=source_timer_id,
                    WorkDate=start_at.date(),
                    StartAt=start_at,
                    EndAt=end_at,
                    DurationSeconds=max(0.0, float(duration_seconds)),
                    IsOjt=is_ojt,
                    TeacherUserId=teacher_user_id,
                )
            )
            return True

    def list_raw_worklogs_by_user_and_day(self, user_id: str, work_date: date) -> list[RawWorklog]:
        with self._session() as session:
            stmt = select(RawWorklog).where(
                and_(
                    RawWorklog.UserId == user_id,
                    RawWorklog.WorkDate == work_date,
                )
            )
            return session.scalars(stmt.order_by(RawWorklog.StartAt)).all()

    def list_raw_worklogs_by_task(self, task_id: str) -> list[RawWorklog]:
        with self._session() as session:
            stmt = select(RawWorklog).where(RawWorklog.TaskId == task_id)
            return session.scalars(stmt.order_by(RawWorklog.StartAt)).all()

    def list_raw_worklogs_by_tasks(self, task_ids: list[str]) -> list[RawWorklog]:
        if not task_ids:
            return []
        with self._session() as session:
            stmt = select(RawWorklog).where(RawWorklog.TaskId.in_(task_ids))
            return session.scalars(stmt.order_by(RawWorklog.TaskId, RawWorklog.StartAt)).all()

    def replace_daily_task_actuals(
        self,
        *,
        project_id: str,
        user_id: str,
        work_date: date,
        rows: list[dict[str, Any]],
    ) -> None:
        with self._session() as session:
            session.execute(
                sa.delete(DailyTaskActual).where(
                    and_(
                        DailyTaskActual.UserId == user_id,
                        DailyTaskActual.WorkDate == work_date,
                    )
                )
            )
            for row in rows:
                timer_value = max(0.0, float(row.get("timer_minutes", 0.0)))
                distributed_value = max(0.0, float(row.get("distributed_minutes", 0.0)))
                total_value = timer_value + distributed_value
                session.add(
                    DailyTaskActual(
                        Id=str(uuid4()),
                        ProjectId=project_id,
                        UserId=user_id,
                        TaskId=str(row["task_id"]),
                        WorkDate=work_date,
                        TimerMinutes=timer_value,
                        DistributedMinutes=distributed_value,
                        TotalMinutes=total_value,
                    )
                )

    def upsert_daily_task_actual(
        self,
        *,
        project_id: str,
        user_id: str,
        task_id: str,
        work_date: date,
        timer_minutes: float,
        distributed_minutes: float,
    ) -> None:
        with self._session() as session:
            item = session.scalar(
                select(DailyTaskActual).where(
                    and_(
                        DailyTaskActual.UserId == user_id,
                        DailyTaskActual.TaskId == task_id,
                        DailyTaskActual.WorkDate == work_date,
                    )
                )
            )
            timer_value = max(0.0, float(timer_minutes))
            distributed_value = max(0.0, float(distributed_minutes))
            total_value = timer_value + distributed_value

            if item is None:
                item = DailyTaskActual(
                    Id=str(uuid4()),
                    ProjectId=project_id,
                    UserId=user_id,
                    TaskId=task_id,
                    WorkDate=work_date,
                    TimerMinutes=timer_value,
                    DistributedMinutes=distributed_value,
                    TotalMinutes=total_value,
                )
                session.add(item)
            else:
                item.ProjectId = project_id
                item.TimerMinutes = timer_value
                item.DistributedMinutes = distributed_value
                item.TotalMinutes = total_value

    def list_daily_task_actuals_by_user_and_day(
        self,
        user_id: str,
        work_date: date,
    ) -> list[DailyTaskActual]:
        with self._session() as session:
            stmt = select(DailyTaskActual).where(
                and_(
                    DailyTaskActual.UserId == user_id,
                    DailyTaskActual.WorkDate == work_date,
                )
            )
            return session.scalars(stmt.order_by(DailyTaskActual.TaskId)).all()

    def list_daily_task_actuals_by_task(
        self,
        task_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[DailyTaskActual]:
        with self._session() as session:
            cond = [DailyTaskActual.TaskId == task_id]
            if date_from is not None:
                cond.append(DailyTaskActual.WorkDate >= date_from)
            if date_to is not None:
                cond.append(DailyTaskActual.WorkDate <= date_to)
            stmt = select(DailyTaskActual).where(and_(*cond))
            return session.scalars(stmt.order_by(DailyTaskActual.WorkDate)).all()

    def list_daily_task_actuals_by_project(
        self,
        project_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[DailyTaskActual]:
        with self._session() as session:
            cond = [DailyTaskActual.ProjectId == project_id]
            if date_from is not None:
                cond.append(DailyTaskActual.WorkDate >= date_from)
            if date_to is not None:
                cond.append(DailyTaskActual.WorkDate <= date_to)
            stmt = select(DailyTaskActual).where(and_(*cond))
            return session.scalars(stmt.order_by(DailyTaskActual.WorkDate, DailyTaskActual.TaskId)).all()

    def sum_daily_task_actual_total_minutes(self, task_id: str) -> float:
        with self._session() as session:
            stmt = (
                select(func.coalesce(func.sum(DailyTaskActual.TotalMinutes), 0.0))
                .where(DailyTaskActual.TaskId == task_id)
            )
            return float(session.scalar(stmt) or 0.0)

    def replace_daily_progress_snapshots(
        self,
        *,
        project_id: str,
        task_id: str,
        points: list[dict[str, Any]],
    ) -> None:
        with self._session() as session:
            session.execute(
                sa.delete(DailyProgressSnapshot).where(DailyProgressSnapshot.TaskId == task_id)
            )
            for point in points:
                session.add(
                    DailyProgressSnapshot(
                        Id=str(uuid4()),
                        ProjectId=project_id,
                        TaskId=task_id,
                        SnapshotDate=point["SnapshotDate"],
                        ProgressRate=float(point["ProgressRate"]),
                        PlannedProgressRate=float(point["PlannedProgressRate"]),
                        CumActualMinutes=float(point["CumActualMinutes"]),
                        VActual=float(point["VActual"]),
                        VAverage=float(point.get("VAverage", 0.0)),
                        VCritical=float(point["VCritical"]),
                        VSafe=float(point["VSafe"]),
                    )
                )

    def list_daily_progress_snapshots(
        self,
        task_id: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[DailyProgressSnapshot]:
        with self._session() as session:
            cond = [DailyProgressSnapshot.TaskId == task_id]
            if date_from is not None:
                cond.append(DailyProgressSnapshot.SnapshotDate >= date_from)
            if date_to is not None:
                cond.append(DailyProgressSnapshot.SnapshotDate <= date_to)
            stmt = select(DailyProgressSnapshot).where(and_(*cond))
            return session.scalars(stmt.order_by(DailyProgressSnapshot.SnapshotDate)).all()

    def upsert_daily_attendance(
        self,
        user_id: str,
        work_date: date,
        start_at: datetime,
        end_at: datetime,
        work_minutes: int,
    ) -> None:
        with self._session() as session:
            entry = session.scalar(
                select(DailyAttendance).where(
                    and_(
                        DailyAttendance.UserId == user_id,
                        DailyAttendance.WorkDate == work_date,
                    )
                )
            )
            if entry is None:
                entry = DailyAttendance(
                    Id=str(uuid4()),
                    UserId=user_id,
                    WorkDate=work_date,
                    StartAt=start_at,
                    EndAt=end_at,
                    WorkMinutes=work_minutes,
                )
                session.add(entry)
            else:
                entry.StartAt = start_at
                entry.EndAt = end_at
                entry.WorkMinutes = work_minutes

    def list_daily_attendance(
        self,
        user_id: str,
        date_from: date,
        date_to: date,
    ) -> list[DailyAttendance]:
        with self._session() as session:
            stmt = select(DailyAttendance).where(
                and_(
                    DailyAttendance.UserId == user_id,
                    DailyAttendance.WorkDate >= date_from,
                    DailyAttendance.WorkDate <= date_to,
                )
            )
            return session.scalars(stmt.order_by(DailyAttendance.WorkDate)).all()

    def list_daily_attendance_all(
        self,
        date_from: date,
        date_to: date,
    ) -> list[DailyAttendance]:
        with self._session() as session:
            stmt = select(DailyAttendance).where(
                and_(
                    DailyAttendance.WorkDate >= date_from,
                    DailyAttendance.WorkDate <= date_to,
                )
            )
            return session.scalars(stmt).all()

    def upsert_monthly_education_rate(self, user_id: str, year_month: str, rate: float) -> None:
        with self._session() as session:
            item = session.scalar(
                select(MonthlyEducationRate).where(
                    and_(
                        MonthlyEducationRate.UserId == user_id,
                        MonthlyEducationRate.YearMonth == year_month,
                    )
                )
            )
            if item is None:
                item = MonthlyEducationRate(
                    Id=str(uuid4()),
                    UserId=user_id,
                    YearMonth=year_month,
                    EducationRateMonthly=rate,
                )
                session.add(item)
            else:
                item.EducationRateMonthly = rate

    def get_monthly_education_rate(self, user_id: str, year_month: str) -> float | None:
        with self._session() as session:
            item = session.scalar(
                select(MonthlyEducationRate).where(
                    and_(
                        MonthlyEducationRate.UserId == user_id,
                        MonthlyEducationRate.YearMonth == year_month,
                    )
                )
            )
            return None if item is None else item.EducationRateMonthly

    def list_monthly_education_rates(self, year_month: str) -> list[MonthlyEducationRate]:
        with self._session() as session:
            stmt = select(MonthlyEducationRate).where(MonthlyEducationRate.YearMonth == year_month)
            return session.scalars(stmt).all()

    def add_progress_history(
        self,
        project_id: str,
        recorded_at: datetime,
        progress_rate: float,
        reason: str,
    ) -> None:
        with self._session() as session:
            session.add(
                ProgressRateHistory(
                    Id=str(uuid4()),
                    ProjectId=project_id,
                    RecordedAt=recorded_at,
                    ProgressRate=progress_rate,
                    Reason=reason,
                )
            )

    def list_progress_history(self, project_id: str, limit: int = 120) -> list[ProgressRateHistory]:
        with self._session() as session:
            stmt = (
                select(ProgressRateHistory)
                .where(ProgressRateHistory.ProjectId == project_id)
                .order_by(ProgressRateHistory.RecordedAt.desc())
                .limit(limit)
            )
            return list(reversed(session.scalars(stmt).all()))

    def get_state(self, key: str) -> str | None:
        with self._session() as session:
            item = session.get(AppState, key)
            return None if item is None else item.Value

    def set_state(self, key: str, value: str) -> None:
        with self._session() as session:
            item = session.get(AppState, key)
            if item is None:
                session.add(AppState(Key=key, Value=value))
            else:
                item.Value = value

    def update_task_assignees(self, assignee_by_task_id: dict[str, str]) -> None:
        """複数タスクの担当者を一つのトランザクションで差し替える。

        サブツリー一括変更は数千件になりうる。1件ずつセッションを開くと実用速度にならない。
        """
        if not assignee_by_task_id:
            return
        with self._session() as session:
            task_ids = list(assignee_by_task_id)
            # SQLiteのバインド変数上限を超えないよう、読込みだけを分割する。
            for offset in range(0, len(task_ids), 900):
                chunk = task_ids[offset : offset + 900]
                for task in session.scalars(select(Task).where(Task.Id.in_(chunk))).all():
                    task.AssigneeUserId = assignee_by_task_id[task.Id]

    def mark_task_updated_by(self, task_id: str, user_id: str | None) -> None:
        """最後にこのタスクを変更した人を記録する。競合相手を伝えるために使う。"""
        if not user_id:
            return
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                return
            if session.get(User, user_id) is None:
                return
            task.UpdatedByUserId = user_id

    def list_iquavis_task_baselines(self) -> list[IQuavisTaskBaseline]:
        with self._session() as session:
            return list(session.scalars(select(IQuavisTaskBaseline)).all())

    def upsert_iquavis_task_baselines(
        self,
        baselines: list[IquavisTaskBaselineInput],
        *,
        captured_at: datetime,
    ) -> int:
        """合意済みの共有項目値を1トランザクションで保存する。"""
        if not baselines:
            return 0
        with self._session() as session:
            latest_by_task_id = {baseline.task_id: baseline for baseline in baselines}
            existing_by_task_id: dict[str, IQuavisTaskBaseline] = {}
            task_ids = list(latest_by_task_id)
            # SQLiteのバインド変数上限を超えないよう分割して読む。
            for offset in range(0, len(task_ids), 900):
                chunk = task_ids[offset : offset + 900]
                rows = session.scalars(
                    select(IQuavisTaskBaseline).where(IQuavisTaskBaseline.TaskId.in_(chunk))
                ).all()
                existing_by_task_id.update({row.TaskId: row for row in rows})
            for task_id, baseline in latest_by_task_id.items():
                row = existing_by_task_id.get(task_id)
                if row is None:
                    row = IQuavisTaskBaseline(TaskId=task_id)
                    session.add(row)
                row.IQuavisTaskId = baseline.iquavis_task_id
                row.Name = baseline.name
                row.StartDate = baseline.start_date
                row.EndDate = baseline.end_date
                row.ActualManHour = int(baseline.actual_manhour)
                row.Status = baseline.status
                row.ProgressRate = baseline.progress_rate
                row.Memo = baseline.memo
                row.CapturedAt = captured_at
            return len(latest_by_task_id)

    def delete_iquavis_task_baselines(self, task_ids: list[str]) -> int:
        if not task_ids:
            return 0
        deleted = 0
        with self._session() as session:
            for offset in range(0, len(task_ids), 900):
                chunk = task_ids[offset : offset + 900]
                deleted += session.query(IQuavisTaskBaseline).filter(
                    IQuavisTaskBaseline.TaskId.in_(chunk)
                ).delete(synchronize_session=False)
        return deleted

    def sum_rework_minutes_by_prevented_task(self, task_id: str) -> int:
        with self._session() as session:
            stmt = (
                select(func.coalesce(func.sum(Task.ActualManHour), 0))
                .where(and_(Task.PreventedByTaskId == task_id, Task.IsRework.is_(True)))
            )
            return int(session.scalar(stmt) or 0)

    def list_tasks_by_creator(self, project_id: str, creator_user_id: str) -> list[Task]:
        with self._session() as session:
            stmt = select(Task).where(
                and_(Task.ProjectId == project_id, Task.CreatorUserId == creator_user_id)
            )
            return session.scalars(stmt).all()

    def list_tasks_by_assignee(self, project_id: str, assignee_user_id: str) -> list[Task]:
        with self._session() as session:
            stmt = select(Task).where(
                and_(Task.ProjectId == project_id, Task.AssigneeUserId == assignee_user_id)
            )
            return session.scalars(stmt).all()

    def count_tasks(self, project_id: str) -> int:
        with self._session() as session:
            stmt = select(func.count()).select_from(Task).where(Task.ProjectId == project_id)
            return int(session.scalar(stmt) or 0)

    def task_output_deliverables(self, task_id: str) -> list[dict[str, str]]:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            try:
                return json.loads(task.OutputDeliverablesJson)
            except Exception:
                return []

    def task_planned_deliverables(self, task_id: str) -> list[dict[str, str]]:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("繧ｿ繧ｹ繧ｯ縺悟ｭ伜惠縺励∪縺帙ｓ")
            try:
                return json.loads(task.PlannedDeliverablesJson)
            except Exception:
                return []

    # ---- 巣（組織）: 役職・部屋・所属・方針 ----

    def set_user_colony_role(self, user_id: str, role: str) -> None:
        with self._session() as session:
            user = session.get(User, user_id)
            if user is None:
                raise ValueError("user not found")
            user.ColonyRole = role

    def list_colony_teams(self) -> list[ColonyTeam]:
        with self._session() as session:
            return session.scalars(
                select(ColonyTeam).order_by(ColonyTeam.DisplayOrder, ColonyTeam.CreateDate, ColonyTeam.Id)
            ).all()

    def get_colony_team(self, team_id: str) -> ColonyTeam | None:
        with self._session() as session:
            return session.get(ColonyTeam, team_id)

    def create_colony_team(
        self,
        *,
        name: str,
        parent_team_id: str | None,
        leader_user_id: str | None,
        emblem: str,
    ) -> ColonyTeam:
        with self._session() as session:
            order = session.scalar(
                select(func.coalesce(func.max(ColonyTeam.DisplayOrder), 0)).where(
                    ColonyTeam.ParentTeamId.is_(None)
                    if parent_team_id is None
                    else ColonyTeam.ParentTeamId == parent_team_id
                )
            )
            team = ColonyTeam(
                Id=str(uuid4()),
                Name=name,
                ParentTeamId=parent_team_id,
                LeaderUserId=leader_user_id,
                Emblem=emblem,
                DisplayOrder=int(order or 0) + 1,
            )
            session.add(team)
            session.flush()
            session.refresh(team)
            return team

    def update_colony_team(self, team_id: str, **fields: Any) -> None:
        allowed = {"Name", "ParentTeamId", "LeaderUserId", "Emblem"}
        with self._session() as session:
            team = session.get(ColonyTeam, team_id)
            if team is None:
                raise ValueError("team not found")
            for key, value in fields.items():
                if key not in allowed:
                    raise ValueError(f"unknown team field: {key}")
                setattr(team, key, value)

    def delete_colony_team(self, team_id: str) -> None:
        with self._session() as session:
            session.execute(sa.delete(ColonyTeamMember).where(ColonyTeamMember.TeamId == team_id))
            team = session.get(ColonyTeam, team_id)
            if team is not None:
                session.delete(team)

    def list_colony_team_members(self) -> list[ColonyTeamMember]:
        with self._session() as session:
            return session.scalars(
                select(ColonyTeamMember).order_by(ColonyTeamMember.CreateDate, ColonyTeamMember.UserId)
            ).all()

    def add_colony_team_member(self, team_id: str, user_id: str) -> None:
        with self._session() as session:
            if session.get(ColonyTeamMember, (team_id, user_id)) is None:
                session.add(ColonyTeamMember(TeamId=team_id, UserId=user_id))

    def remove_colony_team_member(self, team_id: str, user_id: str) -> None:
        with self._session() as session:
            member = session.get(ColonyTeamMember, (team_id, user_id))
            if member is not None:
                session.delete(member)

    def list_colony_policies(self) -> list[ColonyPolicy]:
        with self._session() as session:
            return session.scalars(select(ColonyPolicy).order_by(ColonyPolicy.CreateDate, ColonyPolicy.Id)).all()

    def create_colony_policy(
        self,
        *,
        kind: str,
        text: str,
        x: float,
        y: float,
        size: float,
        points: list[list[float]],
        author_user_id: str,
    ) -> ColonyPolicy:
        with self._session() as session:
            policy = ColonyPolicy(
                Id=str(uuid4()),
                Kind=kind,
                Text=text,
                X=x,
                Y=y,
                Size=size,
                PointsJson=json.dumps(points),
                AuthorUserId=author_user_id,
            )
            session.add(policy)
            session.flush()
            session.refresh(policy)
            return policy

    def update_colony_policy(self, policy_id: str, *, text: str | None, x: float | None, y: float | None) -> None:
        with self._session() as session:
            policy = session.get(ColonyPolicy, policy_id)
            if policy is None:
                raise ValueError("policy not found")
            if text is not None:
                policy.Text = text
            if x is not None:
                policy.X = x
            if y is not None:
                policy.Y = y

    def delete_colony_policy(self, policy_id: str) -> None:
        with self._session() as session:
            policy = session.get(ColonyPolicy, policy_id)
            if policy is not None:
                session.delete(policy)

    def set_task_colony_size(self, task_id: str, size: int | None) -> None:
        with self._session() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise ValueError("task not found")
            task.ColonySize = size
