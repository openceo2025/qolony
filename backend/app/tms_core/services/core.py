from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any

from app.iquavis.link_targets import read_member_rows, write_member_sheet
from app.tms_core.config import Settings
from app.iquavis.sync_state import (
    IQUAVIS_SYNC_EXCLUDED_ROOTS_STATE_KEY,
    IQUAVIS_TASK_CONFLICT_STATE_KEY,
    parse_conflict_state,
    parse_excluded_root_state,
    serialize_conflict_state,
    serialize_excluded_root_state,
    task_sync_state,
)
from app.tms_core.db.models import DailyTaskActual, RawWorklog, Task, TimerSession, User
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.domain.budget import DailyBudget, calculate_daily_budget
from app.tms_core.domain.infeasibility import (
    RemainingTask,
    detect_personal_infeasibility,
    detect_team_infeasibility,
)
from app.tms_core.domain.metrics import calculate_speed_score
from app.tms_core.domain.progress import (
    LeafProgress,
    calculate_leaf_progress_rate,
    calculate_parent_progress_rate,
)
from app.tms_core.domain.ranking import RankEntry, apply_publication_rule
from app.tms_core.domain.rounding import (
    normalize_minutes_multiple_of_30,
    round_half_up,
    seconds_to_rounded_minutes,
)
from app.tms_core.domain.signal import (
    TaskSignalInput,
    calculate_task_signal,
    count_workdays_inclusive,
)
from app.tms_core.domain.speed import (
    ProgressSnapshot,
    SpeedResult,
    calculate_speed_result,
    calculate_v_critical,
    calculate_v_safe,
    decide_signal,
)


class UserInputError(Exception):
    pass


def task_revision(task: Task) -> str:
    """楽観的同時実行制御に使う、編集対象内容の安定したリビジョンを返す。"""
    payload = {
        "id": task.Id,
        "parent_task_id": task.ParentTaskId,
        "name": task.Name,
        "type": task.Type,
        "assignee_user_id": task.AssigneeUserId,
        "start_date": task.StartDate.isoformat(),
        "end_date": task.EndDate.isoformat(),
        "assigned_manhour": int(task.AssignedManHour),
        "actual_manhour": int(task.ActualManHour),
        "progress_rate": float(task.ProgressRate),
        "status": task.Status,
        "memo": task.Memo,
        "planned_deliverables": task.PlannedDeliverablesJson,
        "output_deliverables": task.OutputDeliverablesJson,
        "today_override": task.TodayOverride,
        "child_dependency_mode": task.ChildDependencyMode,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def task_order_revision(ordered_task_ids: list[str]) -> str:
    """並び順の先着優先判定に使う、ブラウザと共通の決定的リビジョンを返す。"""
    encoded = json.dumps(ordered_task_ids, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hash_value = 0xCBF29CE484222325
    for byte in encoded:
        hash_value ^= byte
        hash_value = (hash_value * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"order-v1-{hash_value:016x}"


CHILD_DEPENDENCY_MODE_SERIAL = "Serial"
CHILD_DEPENDENCY_MODE_PARALLEL = "Parallel"
CHILD_DEPENDENCY_MODE_ALIASES: dict[str, str] = {
    CHILD_DEPENDENCY_MODE_SERIAL: CHILD_DEPENDENCY_MODE_SERIAL,
    CHILD_DEPENDENCY_MODE_PARALLEL: CHILD_DEPENDENCY_MODE_PARALLEL,
    "直列": CHILD_DEPENDENCY_MODE_SERIAL,
    "並列": CHILD_DEPENDENCY_MODE_PARALLEL,
}
TERMINAL_TASK_STATUSES = frozenset({"Completion", "Abort"})
MEMBER_ROLE_VALUES = {"", "承認", "審査", "チームリーダー", "設計", "トレーサー"}
FULL_DAY_LEAVE_VALUES = {"全休", "full_day", "FullDay"}
HALF_DAY_LEAVE_VALUES = {"AM半休", "PM半休", "am_half", "pm_half", "AM Half", "PM Half"}


def display_user_name(user: User) -> str:
    return getattr(user, "Nickname", "") or user.Name

@dataclass(frozen=True)
class ActiveTimerInfo:
    TimerId: str
    TaskId: str
    TaskName: str
    StartedAt: datetime
    IsOjt: bool
    TeacherUserId: str | None


@dataclass(frozen=True)
class TodaySummary:
    WorkMinutes: int
    Budget: DailyBudget
    ActualWorkMinutes: int
    OjtMinutes: int
    RemainingWorkMinutes: int
    ActiveTimer: ActiveTimerInfo | None
    Alerts: list[str]


@dataclass(frozen=True)
class MemberDayConfigView:
    UserId: str
    WorkDate: date
    IsVacation: bool
    LeaveType: str
    CommitRateOverride: float | None
    Note: str


@dataclass(frozen=True)
class MemberProfileView:
    UserId: str
    DefaultCommitRate: float
    ConditionIcon: str
    FormationRole: str
    FormationPosition: str
    FormationX: int
    FormationY: int
    DisplayOrder: int
    TitleTags: list[str]
    SpecialAbilities: list[str]


@dataclass(frozen=True)
class MemberView:
    UserId: str
    Name: str
    Nickname: str
    FullName: str
    LoginId: str
    IQuavisUserId: str | None
    IsTopManager: bool
    IsActive: bool
    FreeTimeRate: float
    Profile: MemberProfileView
    DayConfigs: list[MemberDayConfigView]


@dataclass(frozen=True)
class TaskView:
    Id: str
    ParentTaskId: str | None
    Name: str
    AssigneeUserId: str
    AssigneeName: str
    CreatorUserId: str
    StartDate: date
    EndDate: date
    CreateDate: datetime
    Revision: str
    AssignedManHour: int
    ActualManHour: int
    ChildAssignedManHour: int
    EstimateBalanceManHour: int
    EstimateSignal: str
    EstimateSignalMessage: str | None
    ProgressRate: float
    Signal: str
    Status: str
    Type: str
    IsRework: bool
    PreventedByTaskId: str | None
    Memo: str
    TodayOverride: bool | None
    TodaySortOrder: int | None
    WbsSortOrder: int | None
    ChildDependencyMode: str
    IQuavisTaskId: str | None
    IQuavisProjectId: str | None
    IQuavisSyncState: str
    IQuavisSyncExcluded: bool
    IQuavisConflictMessage: str | None
    PlannedDeliverables: list[dict[str, str]]
    OutputDeliverables: list[dict[str, str]]


@dataclass(frozen=True)
class ProgressDashboard:
    ProgressRate: float
    Speed: SpeedResult
    History: list[ProgressSnapshot]
    ProjectEndDate: date
    CrashZonePct: float


@dataclass(frozen=True)
class TaskProgressPoint:
    Date: date
    ProgressRate: float
    PlannedProgressRate: float
    ActualCumMinutes: float


@dataclass(frozen=True)
class TaskSpeedPoint:
    Date: date
    VActual: float
    VAverage: float
    VCritical: float
    VSafe: float


@dataclass(frozen=True)
class TaskProgressDashboard:
    TaskId: str
    TaskName: str
    Signal: str
    CrashZonePct: float
    AssignedTotalMinutes: float
    ProgressPoints: list[TaskProgressPoint]
    SpeedPoints: list[TaskSpeedPoint]


@dataclass(frozen=True)
class MetricRow:
    UserId: str
    UserName: str
    OutputVolume: int
    WorkSpeed: float
    EducationEfficiency: float
    TeachingTime: int


@dataclass(frozen=True)
class RankingTaskSummary:
    CompletionDate: date
    AssigneeUserId: str
    AssignedMinutes: int
    ActualMinutes: int
    MajorityTeacherUserIds: tuple[str, ...]
    TeachingSecondsByUser: tuple[tuple[str, float], ...]


@dataclass
class RankingAccumulator:
    OutputVolumeByUser: defaultdict[str, int]
    EffortByUser: defaultdict[str, int]
    EducationOutputByUser: defaultdict[str, int]
    EducationEffortByUser: defaultdict[str, int]
    TeachingSecondsByUser: defaultdict[str, float]


@dataclass(frozen=True)
class RankingHistoryPoint:
    Date: date
    Value: float


@dataclass(frozen=True)
class RankingHistorySeries:
    UserId: str
    UserName: str
    IsSelf: bool
    Points: list[RankingHistoryPoint]


@dataclass(frozen=True)
class TaskAggregate:
    AssignedTotal: int
    ActualTotal: int
    ProgressRate: float
    AssigneeIds: set[str]
    Started: bool
    StartedAt: date
    Aborted: bool


@dataclass(frozen=True)
class EstimateInfo:
    ChildAssignedManHour: int
    EstimateBalanceManHour: int
    EstimateSignal: str
    EstimateSignalMessage: str | None


class TmsService:
    def __init__(self, settings: Settings, repository: TmsRepository):
        self.settings = settings
        self.repository = repository

    def list_users(self, *, active_only: bool = False) -> list[User]:
        return self.repository.list_users(active_only=active_only)

    def list_members(self, *, date_from: date, date_to: date) -> list[MemberView]:
        if date_to < date_from:
            raise UserInputError("日付範囲が不正です")

        self.repository.ensure_member_profiles()
        users = self.repository.list_users()
        profiles = {profile.UserId: profile for profile in self.repository.list_member_profiles()}
        day_config_views_by_user: dict[str, list[MemberDayConfigView]] = defaultdict(list)
        for item in self.repository.list_member_day_configs(date_from=date_from, date_to=date_to):
            day_config_views_by_user[item.UserId].append(
                MemberDayConfigView(
                    UserId=item.UserId,
                    WorkDate=item.WorkDate,
                    IsVacation=bool(item.IsVacation),
                    LeaveType=getattr(item, "LeaveType", "") or "",
                    CommitRateOverride=(
                        None if item.CommitRateOverride is None else float(item.CommitRateOverride)
                    ),
                    Note=item.Note,
                )
            )

        members: list[MemberView] = []
        for user in users:
            profile = profiles.get(user.Id)
            if profile is None:
                self.repository.ensure_member_profiles()
                profile = self.repository.get_member_profile(user.Id)
            if profile is None:
                raise RuntimeError("MemberProfileの初期化に失敗しました")

            members.append(
                MemberView(
                    UserId=user.Id,
                    Name=getattr(user, "Nickname", "") or user.Name,
                    Nickname=getattr(user, "Nickname", "") or user.Name,
                    FullName=getattr(user, "FullName", "") or "",
                    LoginId=user.LoginId,
                    IQuavisUserId=user.IQuavisUserId,
                    IsTopManager=bool(user.IsTopManager),
                    IsActive=bool(getattr(user, "IsActive", True)),
                    FreeTimeRate=float(user.FreeTimeRate),
                    Profile=MemberProfileView(
                        UserId=user.Id,
                        DefaultCommitRate=float(profile.DefaultCommitRate),
                        ConditionIcon=profile.ConditionIcon,
                        FormationRole=profile.FormationRole,
                        FormationPosition=profile.FormationPosition,
                        FormationX=int(profile.FormationX),
                        FormationY=int(profile.FormationY),
                        DisplayOrder=int(profile.DisplayOrder),
                        TitleTags=self._parse_string_list_json(profile.TitleTagsJson),
                        SpecialAbilities=self._parse_string_list_json(profile.SpecialAbilitiesJson),
                    ),
                    DayConfigs=day_config_views_by_user.get(user.Id, []),
                )
            )

        return sorted(members, key=lambda m: (m.Profile.DisplayOrder, m.Name))

    def update_member_identity(
        self,
        *,
        user_id: str,
        operator_user_id: str,
        nickname: str,
        is_active: bool,
    ) -> None:
        self._ensure_top_manager(operator_user_id)
        target = self.repository.get_user(user_id)
        if target is None:
            raise UserInputError("対象メンバーが見つかりません")
        normalized_nickname = self._normalize_member_text(nickname, max_length=128)
        if not normalized_nickname:
            raise UserInputError("ニックネームは必須です")
        self.repository.update_member_identity(
            user_id=user_id,
            nickname=normalized_nickname,
            is_active=bool(is_active),
        )

    def update_member_profile(
        self,
        *,
        user_id: str,
        operator_user_id: str,
        default_commit_rate: float,
        condition_icon: str,
        formation_role: str,
        formation_position: str,
        formation_x: int,
        formation_y: int,
        display_order: int,
        title_tags: list[str],
        special_abilities: list[str],
    ) -> None:
        target = self.repository.get_user(user_id)
        if target is None:
            raise UserInputError("対象メンバーが見つかりません")
        operator = self.repository.get_user(operator_user_id)
        if operator is None:
            raise UserInputError("実行ユーザーが見つかりません")

        self.repository.ensure_member_profiles()
        current = self.repository.get_member_profile(user_id)
        if current is None:
            raise RuntimeError("MemberProfileの初期化に失敗しました")

        normalized_rate = self._coerce_rate(default_commit_rate, label="既定コミット率")
        normalized_condition = self._normalize_member_text(condition_icon, max_length=32) or "normal"
        normalized_role = self._normalize_member_text(formation_role, max_length=128)
        if normalized_role not in MEMBER_ROLE_VALUES:
            raise UserInputError("Roleは承認、審査、チームリーダー、設計、トレーサーから選択してください")
        normalized_position = self._normalize_member_text(formation_position, max_length=128)
        normalized_x = max(0, min(100, int(formation_x)))
        normalized_y = max(0, min(100, int(formation_y)))
        normalized_order = max(0, int(display_order))
        normalized_titles = self._normalize_member_labels(title_tags)
        normalized_abilities = self._normalize_member_labels(special_abilities)
        titles_json = json.dumps(normalized_titles, ensure_ascii=False)
        abilities_json = json.dumps(normalized_abilities, ensure_ascii=False)

        if not operator.IsTopManager:
            if operator.Id != user_id:
                raise UserInputError("本人またはTop Managerのみメンバー情報を編集できます")
            unchanged = (
                abs(float(current.DefaultCommitRate) - normalized_rate) < 0.000001
                and current.FormationRole == normalized_role
                and current.FormationPosition == normalized_position
                and int(current.FormationX) == normalized_x
                and int(current.FormationY) == normalized_y
                and int(current.DisplayOrder) == normalized_order
                and self._parse_string_list_json(current.TitleTagsJson) == normalized_titles
                and self._parse_string_list_json(current.SpecialAbilitiesJson) == normalized_abilities
            )
            if not unchanged:
                raise UserInputError("本人が編集できるのは調子アイコンのみです")

        self.repository.update_member_profile(
            user_id=user_id,
            default_commit_rate=normalized_rate,
            condition_icon=normalized_condition,
            formation_role=normalized_role,
            formation_position=normalized_position,
            formation_x=normalized_x,
            formation_y=normalized_y,
            display_order=normalized_order,
            title_tags_json=titles_json,
            special_abilities_json=abilities_json,
        )

    def export_member_link_targets(self, workbook_path: str | None = None) -> dict[str, Any]:
        path = self._resolve_link_targets_workbook_path(workbook_path)
        write_member_sheet(path, self.repository.list_users())
        return {"workbook_path": str(path), "members": len(self.repository.list_users())}

    def import_member_link_targets(self, workbook_path: str | None = None) -> dict[str, Any]:
        path = self._resolve_link_targets_workbook_path(workbook_path)
        if not path.exists():
            raise UserInputError(f"メンバー連携ファイルが見つかりません: {path}")
        rows = read_member_rows(path)
        created = 0
        updated = 0
        for row in rows:
            _, was_created = self.repository.upsert_member_from_link_target(
                qolony_user_id=row.qolony_user_id,
                nickname=row.nickname,
                full_name=row.full_name,
                login_id=row.login_id,
                iquavis_user_id=row.iquavis_user_id,
                is_top_manager=row.is_top_manager,
                is_active=row.is_active,
                free_time_rate_default=self.settings.app.free_time_rate_default,
            )
            if was_created:
                created += 1
            else:
                updated += 1
        self.repository.ensure_member_profiles()
        write_member_sheet(path, self.repository.list_users())
        return {
            "workbook_path": str(path),
            "read": len(rows),
            "created": created,
            "updated": updated,
        }

    def _resolve_link_targets_workbook_path(self, workbook_path: str | None) -> Any:
        from pathlib import Path

        raw_path = workbook_path or self.settings.iquavis.link_targets_workbook_path or Path("data/iquavis_link_targets.xlsx")
        path = raw_path if isinstance(raw_path, Path) else Path(str(raw_path))
        if path.is_absolute():
            return path
        backend_root = Path(__file__).resolve().parents[3]
        return (backend_root / path).resolve()

    def upsert_member_day_config(
        self,
        *,
        user_id: str,
        operator_user_id: str,
        work_date: date,
        is_vacation: bool,
        commit_rate_override: float | None,
        leave_type: str = "",
        note: str = "",
    ) -> None:
        self._ensure_top_manager(operator_user_id)
        if self.repository.get_user(user_id) is None:
            raise UserInputError("対象メンバーが見つかりません")

        normalized_note = self._normalize_member_text(note, max_length=1000)
        normalized_leave_type = self._normalize_member_text(leave_type, max_length=64)
        if is_vacation and not normalized_leave_type:
            normalized_leave_type = "全休"
        normalized_is_vacation = is_vacation or normalized_leave_type == "全休"
        normalized_rate = (
            None
            if commit_rate_override is None
            else self._coerce_rate(commit_rate_override, label="日次コミット率")
        )
        if not normalized_is_vacation and normalized_rate is None and not normalized_note and not normalized_leave_type:
            self.repository.delete_member_day_config(user_id=user_id, work_date=work_date)
            return

        self.repository.upsert_member_day_config(
            user_id=user_id,
            work_date=work_date,
            is_vacation=normalized_is_vacation,
            leave_type=normalized_leave_type,
            commit_rate_override=normalized_rate,
            note=normalized_note,
        )

    def get_project(self):
        project = self.repository.get_project()
        if project is None:
            raise RuntimeError("project is not initialized")
        return project

    def get_top_manager(self) -> User:
        top = self.repository.get_top_manager()
        if top is None:
            raise RuntimeError("Top Managerが設定されていません")
        return top

    def _ensure_top_manager(self, operator_user_id: str) -> User:
        operator = self.repository.get_user(operator_user_id)
        if operator is None:
            raise UserInputError("実行ユーザーが見つかりません")
        if not operator.IsTopManager:
            raise UserInputError("Top Managerのみ実行できます")
        return operator

    def _parse_deliverables(self, text: str) -> list[dict[str, str]]:
        try:
            value = json.loads(text)
            if isinstance(value, list):
                return [
                    {"title": str(item.get("title", "")), "url": str(item.get("url", ""))}
                    for item in value
                    if isinstance(item, dict)
                ]
            return []
        except Exception:
            return []

    def _parse_string_list_json(self, text: str) -> list[str]:
        try:
            value = json.loads(text or "[]")
        except Exception:
            return []
        if not isinstance(value, list):
            return []
        return self._normalize_member_labels([str(item) for item in value])

    def _normalize_member_labels(self, values: list[str]) -> list[str]:
        labels: list[str] = []
        seen: set[str] = set()
        for raw in values:
            label = self._normalize_member_text(raw, max_length=40)
            if not label or label in seen:
                continue
            labels.append(label)
            seen.add(label)
            if len(labels) >= 12:
                break
        return labels

    def _normalize_member_text(self, value: str | None, *, max_length: int) -> str:
        text = "" if value is None else str(value).strip()
        if len(text) > max_length:
            text = text[:max_length]
        return text

    def _coerce_rate(self, value: float, *, label: str) -> float:
        rate = float(value)
        if not isfinite(rate) or rate < 0.0 or rate > 1.0:
            raise UserInputError(f"{label}は0〜1で指定してください")
        return rate

    def _coerce_child_dependency_mode(self, mode: str | None) -> str:
        if mode is None:
            return CHILD_DEPENDENCY_MODE_SERIAL
        normalized = CHILD_DEPENDENCY_MODE_ALIASES.get(str(mode).strip())
        if normalized is None:
            return CHILD_DEPENDENCY_MODE_SERIAL
        return normalized

    def _parse_child_dependency_mode(self, mode: str) -> str:
        normalized = CHILD_DEPENDENCY_MODE_ALIASES.get(str(mode).strip())
        if normalized is None:
            raise UserInputError("依存モードは直列または並列で指定してください")
        return normalized

    def _queue_iquavis_schedule_sync(self, task_id: str) -> None:
        task = self.repository.get_task(task_id)
        if task is None or not task.IQuavisTaskId:
            return
        if self.is_iquavis_sync_excluded(task_id):
            return
        payload_json = json.dumps(
            {
                "task_name": task.Name,
                "start_date": task.StartDate.isoformat(),
                "end_date": task.EndDate.isoformat(),
            },
            ensure_ascii=False,
        )
        self.repository.enqueue_iquavis_sync_job(
            task_id=task.Id,
            iquavis_task_id=task.IQuavisTaskId,
            job_type="Schedule",
            payload_json=payload_json,
            next_run_at=datetime.now(),
        )

    def _queue_iquavis_progress_sync_task(
        self,
        task: Task | None,
        *,
        excluded_task_ids: set[str] | None = None,
    ) -> None:
        if task is None or not task.IQuavisTaskId:
            return
        if task.Type != "Task":
            return
        if excluded_task_ids is None:
            if self.is_iquavis_sync_excluded(task.Id):
                return
        elif task.Id in excluded_task_ids:
            return

        payload_json = json.dumps(
            {
                "task_name": task.Name,
                "status": task.Status,
                "progress_rate": int(max(0, min(100, round_half_up(task.ProgressRate)))),
            },
            ensure_ascii=False,
        )
        self.repository.enqueue_iquavis_sync_job(
            task_id=task.Id,
            iquavis_task_id=task.IQuavisTaskId,
            job_type="Progress",
            payload_json=payload_json,
            next_run_at=datetime.now(),
        )

    def _queue_iquavis_progress_sync(self, task_id: str) -> None:
        self._queue_iquavis_progress_sync_task(self.repository.get_task(task_id))

    def _queue_iquavis_progress_sync_for_tasks(
        self,
        task_ids: set[str],
        *,
        tasks: list[Task] | None = None,
    ) -> None:
        if not task_ids:
            return
        if tasks is None:
            project = self.get_project()
            tasks = self.repository.list_tasks(project.Id)
        task_by_id = {task.Id: task for task in tasks}
        excluded_task_ids = self._iquavis_sync_excluded_task_ids(tasks)
        for task_id in sorted(task_ids):
            self._queue_iquavis_progress_sync_task(
                task_by_id.get(task_id),
                excluded_task_ids=excluded_task_ids,
            )

    def _build_task_aggregates(self, tasks: list[Task]) -> dict[str, TaskAggregate]:
        task_by_id = {task.Id: task for task in tasks}
        children_by_parent: dict[str, list[Task]] = {}
        for task in tasks:
            if task.ParentTaskId is None:
                continue
            children_by_parent.setdefault(task.ParentTaskId, []).append(task)

        cache: dict[str, TaskAggregate] = {}

        def walk(task_id: str) -> TaskAggregate:
            cached = cache.get(task_id)
            if cached is not None:
                return cached

            task = task_by_id[task_id]
            children = children_by_parent.get(task_id, [])
            if not children:
                aborted = task.Status == "Abort"
                assigned = 0 if aborted else max(0, task.AssignedManHour)
                actual = max(0, task.ActualManHour)
                progress = calculate_leaf_progress_rate(
                    assigned_manhour=assigned,
                    actual_manhour=actual,
                    status=task.Status,
                )
                started = not aborted and (task.Status != "NotStart" or actual > 0 or progress > 0.0)
                result = TaskAggregate(
                    AssignedTotal=assigned,
                    ActualTotal=actual,
                    ProgressRate=progress,
                    AssigneeIds={task.AssigneeUserId},
                    Started=started,
                    StartedAt=task.StartDate,
                    Aborted=aborted,
                )
                cache[task_id] = result
                return result

            child_aggregates = [walk(child.Id) for child in children]
            weighted = [
                LeafProgress(
                    AssignedManHour=max(0, agg.AssignedTotal),
                    ProgressRate=agg.ProgressRate,
                )
                for agg in child_aggregates
                if not agg.Aborted and agg.AssignedTotal > 0
            ]

            if weighted:
                assigned_total = sum(item.AssignedManHour for item in weighted)
                progress = calculate_parent_progress_rate(weighted)
            elif child_aggregates and all(agg.Aborted for agg in child_aggregates):
                assigned_total = 0
                progress = 0.0
            else:
                assigned_total = max(0, task.AssignedManHour)
                progress = calculate_leaf_progress_rate(
                    assigned_manhour=assigned_total,
                    actual_manhour=max(0, task.ActualManHour),
                    status=task.Status,
                )

            actual_total = sum(max(0, agg.ActualTotal) for agg in child_aggregates)

            assignee_ids: set[str] = set()
            aborted = bool(child_aggregates) and all(agg.Aborted for agg in child_aggregates)
            started = not aborted and task.Status not in {"NotStart", "Abort"}
            started_dates: list[date] = []
            if started:
                started_dates.append(task.StartDate)

            for agg in child_aggregates:
                assignee_ids.update(agg.AssigneeIds)
                if agg.Started and not agg.Aborted:
                    started = True
                    started_dates.append(agg.StartedAt)

            if not assignee_ids:
                assignee_ids.add(task.AssigneeUserId)

            result = TaskAggregate(
                AssignedTotal=assigned_total,
                ActualTotal=actual_total,
                ProgressRate=progress,
                AssigneeIds=assignee_ids,
                Started=started,
                StartedAt=min(started_dates) if started_dates else task.StartDate,
                Aborted=aborted,
            )
            cache[task_id] = result
            return result

        for task in tasks:
            walk(task.Id)
        return cache

    def _calculate_task_signals(
        self,
        *,
        tasks: list[Task],
        aggregates: dict[str, TaskAggregate],
        crash_zone_pct: float,
        today: date | None = None,
    ) -> dict[str, str]:
        current = today or date.today()
        holiday_dates = set(self.settings.calendar.holidays)
        capacity = self.settings.app.task_signal_capacity_minutes_per_day
        signals: dict[str, str] = {}

        for task in tasks:
            agg = aggregates.get(task.Id)
            if agg is None:
                continue
            signals[task.Id] = calculate_task_signal(
                TaskSignalInput(
                    status=task.Status,
                    due_date=task.EndDate,
                    start_date=agg.StartedAt,
                    progress_rate=agg.ProgressRate,
                    assigned_minutes=agg.AssignedTotal,
                    actual_minutes=agg.ActualTotal,
                    person_count=max(1, len(agg.AssigneeIds)),
                    capacity_minutes_per_day=capacity,
                    crash_zone_pct=crash_zone_pct,
                    today=current,
                    holiday_dates=holiday_dates,
                    started=agg.Started,
                )
            )
        return signals

    def _build_task_estimate_infos(self, tasks: list[Task]) -> dict[str, EstimateInfo]:
        task_by_id = {task.Id: task for task in tasks}
        children_by_parent = self._task_children_by_parent(tasks)
        infos: dict[str, EstimateInfo] = {}

        for task in tasks:
            direct_children = children_by_parent.get(task.Id, [])
            child_assigned = sum(max(0, int(child.AssignedManHour)) for child in direct_children)
            balance = int(task.AssignedManHour) - child_assigned
            signal = "None"
            message: str | None = None

            if task.Status not in TERMINAL_TASK_STATUSES and task.AssignedManHour <= 0:
                parent = task_by_id.get(task.ParentTaskId) if task.ParentTaskId else None
                if task.ParentTaskId is None:
                    signal = "Strong"
                    message = "ルート直下タスクに見積MHがありません"
                elif parent is not None and parent.AssigneeUserId != task.AssigneeUserId:
                    signal = "Medium"
                    message = "担当者をまたぐ依頼タスクに見積MHがありません"
                elif parent is not None and parent.ParentTaskId is not None:
                    signal = "Weak"
                    message = "同一担当者の分割タスクに見積MHがありません"

            infos[task.Id] = EstimateInfo(
                ChildAssignedManHour=child_assigned,
                EstimateBalanceManHour=balance,
                EstimateSignal=signal,
                EstimateSignalMessage=message,
            )
        return infos

    def list_tasks(self) -> list[TaskView]:
        project = self.get_project()
        users = {u.Id: display_user_name(u) for u in self.repository.list_users()}
        tasks = self.repository.list_tasks(project.Id)
        conflicts = parse_conflict_state(self.repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
        excluded_task_ids = self._iquavis_sync_excluded_task_ids(tasks)
        aggregates = self._build_task_aggregates(tasks)
        signals = self._calculate_task_signals(
            tasks=tasks,
            aggregates=aggregates,
            crash_zone_pct=project.CrashZonePct,
        )
        estimate_infos = self._build_task_estimate_infos(tasks)
        return [
            TaskView(
                Id=t.Id,
                ParentTaskId=t.ParentTaskId,
                Name=t.Name,
                AssigneeUserId=t.AssigneeUserId,
                AssigneeName=users.get(t.AssigneeUserId, "(不明)"),
                CreatorUserId=t.CreatorUserId,
                StartDate=t.StartDate,
                EndDate=t.EndDate,
                CreateDate=t.CreateDate,
                Revision=task_revision(t),
                AssignedManHour=t.AssignedManHour,
                ActualManHour=aggregates[t.Id].ActualTotal if t.Id in aggregates else t.ActualManHour,
                ChildAssignedManHour=estimate_infos[t.Id].ChildAssignedManHour,
                EstimateBalanceManHour=estimate_infos[t.Id].EstimateBalanceManHour,
                EstimateSignal=estimate_infos[t.Id].EstimateSignal,
                EstimateSignalMessage=estimate_infos[t.Id].EstimateSignalMessage,
                ProgressRate=aggregates[t.Id].ProgressRate if t.Id in aggregates else 0.0,
                Signal=signals.get(t.Id, "Green"),
                Status=t.Status,
                Type=t.Type,
                IsRework=t.IsRework,
                PreventedByTaskId=t.PreventedByTaskId,
                Memo=t.Memo,
                TodayOverride=t.TodayOverride,
                TodaySortOrder=t.TodaySortOrder,
                WbsSortOrder=getattr(t, "WbsSortOrder", None),
                ChildDependencyMode=self._coerce_child_dependency_mode(getattr(t, "ChildDependencyMode", None)),
                IQuavisTaskId=t.IQuavisTaskId,
                IQuavisProjectId=t.IQuavisProjectId,
                IQuavisSyncState=task_sync_state(
                    task_id=t.Id,
                    has_iquavis_link=bool(t.IQuavisTaskId),
                    conflicts=conflicts,
                    is_excluded=t.Id in excluded_task_ids,
                ),
                IQuavisSyncExcluded=t.Id in excluded_task_ids,
                IQuavisConflictMessage=(
                    None
                    if t.Id in excluded_task_ids
                    else conflicts[t.Id].message
                    if t.Id in conflicts and (conflicts[t.Id].fields or conflicts[t.Id].sync_state == "PendingIquavisSave")
                    else None
                ),
                PlannedDeliverables=self._parse_deliverables(t.PlannedDeliverablesJson),
                OutputDeliverables=self._parse_deliverables(t.OutputDeliverablesJson),
            )
            for t in tasks
        ]

    def _task_children_by_parent(self, tasks: list[Task]) -> dict[str, list[Task]]:
        children_by_parent: dict[str, list[Task]] = {}
        for task in tasks:
            if task.ParentTaskId is None:
                continue
            children_by_parent.setdefault(task.ParentTaskId, []).append(task)
        return children_by_parent

    def _collect_task_subtree_ids(
        self,
        root_task_id: str,
        tasks: list[Task],
        children_by_parent: dict[str, list[Task]] | None = None,
    ) -> set[str]:
        # 複数ルートを回すときは呼び出し側で作った親子表を渡す。
        # ルートごとに作り直すと、ルート数×全タスク数のSQLAlchemy属性アクセスになる。
        if children_by_parent is None:
            children_by_parent = self._task_children_by_parent(tasks)
        collected: set[str] = set()
        stack = [root_task_id]
        while stack:
            task_id = stack.pop()
            if task_id in collected:
                continue
            collected.add(task_id)
            stack.extend(child.Id for child in children_by_parent.get(task_id, []))
        return collected

    def get_iquavis_sync_excluded_root_ids(self) -> set[str]:
        return parse_excluded_root_state(self.repository.get_state(IQUAVIS_SYNC_EXCLUDED_ROOTS_STATE_KEY))

    def _iquavis_sync_excluded_task_ids(self, tasks: list[Task]) -> set[str]:
        root_ids = self.get_iquavis_sync_excluded_root_ids()
        if not root_ids:
            return set()
        task_ids = {task.Id for task in tasks}
        target_root_ids = sorted(root_ids & task_ids)
        if not target_root_ids:
            return set()
        # 除外ルートは百件を超えうる。親子表は1回だけ作って全ルートで使い回す。
        children_by_parent = self._task_children_by_parent(tasks)
        excluded: set[str] = set()
        for root_id in target_root_ids:
            excluded.update(
                self._collect_task_subtree_ids(root_id, tasks, children_by_parent)
            )
        return excluded

    def get_iquavis_sync_excluded_task_ids(self) -> set[str]:
        project = self.get_project()
        return self._iquavis_sync_excluded_task_ids(self.repository.list_tasks(project.Id))

    def is_iquavis_sync_excluded(self, task_id: str) -> bool:
        return task_id in self.get_iquavis_sync_excluded_task_ids()

    def set_iquavis_sync_exclusion(self, *, root_task_id: str, operator_user_id: str, excluded: bool) -> dict[str, int | str]:
        return self.set_iquavis_sync_exclusions(
            root_task_ids=[root_task_id],
            operator_user_id=operator_user_id,
            excluded=excluded,
        )

    def set_iquavis_sync_exclusions(
        self,
        *,
        root_task_ids: list[str],
        operator_user_id: str,
        excluded: bool,
    ) -> dict[str, int | str]:
        operator = self.repository.get_user(operator_user_id)
        if operator is None or not operator.IsTopManager:
            raise UserInputError("同期対象の切替はトップマネージャーのみ可能です")

        requested_ids = [task_id for task_id in dict.fromkeys(root_task_ids) if task_id.strip()]
        if not requested_ids:
            raise UserInputError("同期対象を切り替えるタスクを選択してください")

        project = self.get_project()
        tasks = self.repository.list_tasks(project.Id)
        task_by_id = {task.Id: task for task in tasks}
        missing_ids = [task_id for task_id in requested_ids if task_id not in task_by_id]
        if missing_ids:
            raise UserInputError("タスクが見つかりません")

        children_by_parent = self._task_children_by_parent(tasks)
        subtree_ids: set[str] = set()
        for root_task_id in requested_ids:
            subtree_ids.update(
                self._collect_task_subtree_ids(root_task_id, tasks, children_by_parent)
            )

        root_ids = self.get_iquavis_sync_excluded_root_ids()
        if excluded:
            root_ids.update(requested_ids)
        else:
            root_ids.difference_update(requested_ids)
        self.repository.set_state(IQUAVIS_SYNC_EXCLUDED_ROOTS_STATE_KEY, serialize_excluded_root_state(root_ids))

        if excluded:
            conflicts = parse_conflict_state(self.repository.get_state(IQUAVIS_TASK_CONFLICT_STATE_KEY))
            remaining = {task_id: conflict for task_id, conflict in conflicts.items() if task_id not in subtree_ids}
            if len(remaining) != len(conflicts):
                self.repository.set_state(IQUAVIS_TASK_CONFLICT_STATE_KEY, serialize_conflict_state(remaining))

        return {"status": "ok", "affected_count": len(subtree_ids)}

    def _is_today_auto_on(self, task: TaskView, *, base_date: date | None = None) -> bool:
        today = base_date or date.today()
        return task.EndDate <= (today + timedelta(days=1))

    def is_today_on(self, task: TaskView, *, base_date: date | None = None) -> bool:
        if task.TodayOverride is not None:
            return bool(task.TodayOverride)
        return self._is_today_auto_on(task, base_date=base_date)

    def list_my_tasks(self, user_id: str) -> list[TaskView]:
        all_tasks = self.list_tasks()
        tasks = [
            task
            for task in all_tasks
            if (
                task.AssigneeUserId == user_id
                and task.Status not in TERMINAL_TASK_STATUSES
            )
        ]
        return sorted(tasks, key=lambda t: (t.EndDate, t.CreateDate, t.Name))

    def list_today_tasks(self, user_id: str) -> list[TaskView]:
        all_tasks = self.list_tasks()
        parent_ids = {task.ParentTaskId for task in all_tasks if task.ParentTaskId}
        leaf_ids = {task.Id for task in all_tasks if task.Id not in parent_ids}

        candidates = [
            task
            for task in all_tasks
            if (
                task.AssigneeUserId == user_id
                and task.Status not in TERMINAL_TASK_STATUSES
                and task.Id in leaf_ids
                and self.is_today_on(task)
            )
        ]
        return sorted(
            candidates,
            key=lambda t: (
                t.TodaySortOrder is None,
                t.TodaySortOrder if t.TodaySortOrder is not None else 10**9,
                t.EndDate,
                t.CreateDate,
                t.Name,
                t.Id,
            ),
        )

    def set_today_toggle(self, user_id: str, task_id: str, enabled: bool) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        if task.AssigneeUserId != user_id:
            raise UserInputError("todayトグルは担当者本人のみ変更できます")
        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了または中止したタスクのtoday設定は変更できません")
        if not self._is_leaf(task_id):
            raise UserInputError("蟄舌ち繧ｹ繧ｯ繧呈戟縺､繧ｿ繧ｹ繧ｯ縺ｫ縺ｯtoday險ｭ螳壹〒縺阪∪縺帙ｓ")

        auto_on = task.EndDate <= (date.today() + timedelta(days=1))
        next_override: bool | None
        if enabled == auto_on:
            next_override = None
        else:
            next_override = enabled
        self.repository.update_task_today_override(task_id, next_override)

        if enabled and task.TodaySortOrder is None:
            existing = self.list_today_tasks(user_id)
            max_order = max((t.TodaySortOrder or 0 for t in existing), default=0)
            self.repository.update_task_today_sort_order(task_id, max_order + 1)

    def today_order_revision(self, user_id: str) -> str:
        return task_order_revision([task.Id for task in self.list_today_tasks(user_id)])

    def reorder_today_tasks(self, user_id: str, ordered_task_ids: list[str]) -> str:
        today_tasks = self.list_today_tasks(user_id)
        today_ids = [task.Id for task in today_tasks]
        if len(today_ids) != len(ordered_task_ids) or set(today_ids) != set(ordered_task_ids):
            raise UserInputError("today並び順の対象タスクが不正です")
        self.repository.set_today_sort_orders(ordered_task_ids)
        return task_order_revision(ordered_task_ids)

    def _wbs_sibling_sort_key(self, task: Task) -> tuple[bool, int, date, datetime, str, str]:
        sort_order = getattr(task, "WbsSortOrder", None)
        return (
            sort_order is None,
            int(sort_order) if sort_order is not None else 10**9,
            task.EndDate,
            task.CreateDate,
            task.Name,
            task.Id,
        )

    def _ordered_wbs_siblings(self, parent_task_id: str | None) -> list[Task]:
        project = self.get_project()
        return sorted(
            self.repository.list_wbs_sibling_tasks(project_id=project.Id, parent_task_id=parent_task_id),
            key=self._wbs_sibling_sort_key,
        )

    def wbs_sibling_order_revision(self, parent_task_id: str | None) -> str:
        return task_order_revision([task.Id for task in self._ordered_wbs_siblings(parent_task_id)])

    def wbs_task_sibling_order_revision(self, task_id: str) -> str:
        target = self.repository.get_task(task_id)
        if target is None:
            raise UserInputError("タスクが見つかりません")
        siblings = sorted(
            self.repository.list_wbs_sibling_tasks(
                project_id=target.ProjectId,
                parent_task_id=target.ParentTaskId,
            ),
            key=self._wbs_sibling_sort_key,
        )
        return task_order_revision([task.Id for task in siblings])

    def wbs_order_revision(self) -> str:
        project = self.get_project()
        tasks = self.repository.list_tasks(project.Id)
        siblings_by_parent: dict[str | None, list[Task]] = defaultdict(list)
        for task in tasks:
            siblings_by_parent[task.ParentTaskId].append(task)

        revision_tokens: list[str] = []
        parent_ids = sorted(siblings_by_parent, key=lambda parent_id: (parent_id is not None, parent_id or ""))
        for parent_id in parent_ids:
            revision_tokens.extend(("parent", parent_id or ""))
            revision_tokens.extend(
                task.Id for task in sorted(siblings_by_parent[parent_id], key=self._wbs_sibling_sort_key)
            )
        return task_order_revision(revision_tokens)

    def move_wbs_task(self, operator_user_id: str, task_id: str, direction: int) -> str:
        if direction not in {-1, 1}:
            raise UserInputError("WBS並び替え方向が不正です")
        if self.repository.get_user(operator_user_id) is None:
            raise UserInputError("ユーザーが見つかりません")

        target = self.repository.get_task(task_id)
        if target is None:
            raise UserInputError("タスクが見つかりません")

        siblings = sorted(
            self.repository.list_wbs_sibling_tasks(project_id=target.ProjectId, parent_task_id=target.ParentTaskId),
            key=self._wbs_sibling_sort_key,
        )
        current_index = next((index for index, task in enumerate(siblings) if task.Id == task_id), -1)
        target_index = current_index + direction
        if current_index < 0 or target_index < 0 or target_index >= len(siblings):
            return task_order_revision([task.Id for task in siblings])

        siblings[current_index], siblings[target_index] = siblings[target_index], siblings[current_index]
        ordered_ids = [task.Id for task in siblings]
        self.repository.set_wbs_sort_orders(ordered_ids)
        return task_order_revision(ordered_ids)

    def reorder_wbs_tasks(self, operator_user_id: str, ordered_task_ids: list[str]) -> str:
        if self.repository.get_user(operator_user_id) is None:
            raise UserInputError("ユーザーが見つかりません")
        ordered_ids = [task_id for task_id in dict.fromkeys(ordered_task_ids) if task_id.strip()]
        if not ordered_ids:
            raise UserInputError("WBS並び順の対象タスクが不正です")

        project = self.get_project()
        tasks = self.repository.list_tasks(project.Id)
        task_by_id = {task.Id: task for task in tasks}
        if set(ordered_ids) != set(task_by_id):
            raise UserInputError("WBS並び順の対象タスクが不正です")

        sibling_ids_by_parent: dict[str, list[str]] = {}
        for task_id in ordered_ids:
            task = task_by_id[task_id]
            parent_key = task.ParentTaskId or "__root__"
            sibling_ids_by_parent.setdefault(parent_key, []).append(task_id)

        for ordered_sibling_ids in sibling_ids_by_parent.values():
            self.repository.set_wbs_sort_orders(ordered_sibling_ids)
        return self.wbs_order_revision()

    def reorder_wbs_sibling_tasks(
        self,
        *,
        operator_user_id: str,
        parent_task_id: str | None,
        ordered_task_ids: list[str],
    ) -> str:
        if self.repository.get_user(operator_user_id) is None:
            raise UserInputError("ユーザーが見つかりません")
        ordered_ids = [task_id for task_id in dict.fromkeys(ordered_task_ids) if task_id.strip()]
        if not ordered_ids:
            raise UserInputError("WBS並び順の対象タスクが不正です")

        project = self.get_project()
        siblings = self.repository.list_wbs_sibling_tasks(project_id=project.Id, parent_task_id=parent_task_id)
        sibling_ids = {task.Id for task in siblings}
        if set(ordered_ids) != sibling_ids:
            raise UserInputError("WBS並び順の対象タスクが不正です")
        self.repository.set_wbs_sort_orders(ordered_ids)
        return task_order_revision(ordered_ids)

    def create_task(
        self,
        *,
        name: str,
        assignee_user_id: str,
        creator_user_id: str,
        assigned_manhour: int,
        end_date: date,
        parent_task_id: str | None,
        planned_deliverables: list[dict[str, str]],
        is_rework: bool,
        memo: str = "",
    ) -> list[str]:
        if not name.strip():
            raise UserInputError("繧ｿ繧ｹ繧ｯ蜷阪ｒ蜈･蜉帙＠縺ｦ縺上□縺輔＞")
        if assigned_manhour <= 0:
            raise UserInputError("AssignedManHour 縺ｯ1莉･荳翫〒謖・ｮ壹＠縺ｦ縺上□縺輔＞")

        project = self.get_project()
        assignee = self.repository.get_user(assignee_user_id)
        creator = self.repository.get_user(creator_user_id)
        if assignee is None or creator is None:
            raise UserInputError("諡・ｽ楢・∪縺溘・菴懈・閠・′蟄伜惠縺励∪縺帙ｓ")

        task_type = "Task"
        if parent_task_id:
            parent = self.repository.get_task(parent_task_id)
            if parent is None:
                raise UserInputError("隕ｪ繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
            if parent.Status in TERMINAL_TASK_STATUSES:
                raise UserInputError("完了・中止タスクの配下には子タスクを追加できません")
            task_type = "Task"
        else:
            if not creator.IsTopManager:
                raise UserInputError("ルートタスクはTop Managerのみ作成できます")

        start_date = min(date.today(), end_date)
        created = self.repository.create_task(
            NewTaskInput(
                ProjectId=project.Id,
                ParentTaskId=parent_task_id,
                Name=name.strip(),
                Type=task_type,
                AssigneeUserId=assignee_user_id,
                CreatorUserId=creator_user_id,
                StartDate=start_date,
                EndDate=end_date,
                AssignedManHour=normalize_minutes_multiple_of_30(assigned_manhour),
                PlannedDeliverables=planned_deliverables,
                IsRework=is_rework,
                Memo=memo.strip(),
            )
        )
        warnings: list[str] = []
        if start_date < project.StartDate or end_date > project.EndDate:
            warnings.append("タスク期間がプロジェクト期間外です。必要に応じてプロジェクト期間を見直してください。")
        if parent_task_id:
            all_tasks = self.repository.list_tasks(project.Id)
            parent = next((item for item in all_tasks if item.Id == parent_task_id), None)
            if parent and (start_date < parent.StartDate or end_date > parent.EndDate):
                self._expand_ancestor_schedules(
                    created,
                    all_tasks,
                    start_date,
                    end_date,
                    first_parent_id=parent_task_id,
                )
                warnings.append("親タスクの期間外だったため、親タスクと祖先タスクの期間を自動調整しました。")
        self.recompute_all_task_progress()
        return warnings

    def set_crash_zone_pct(self, value: float) -> None:
        if value < 0 or value > 100:
            raise UserInputError("CrashZonePct 縺ｯ0縲・00縺ｧ險ｭ螳壹＠縺ｦ縺上□縺輔＞")
        self.repository.set_crash_zone_pct(value)

    def get_task(self, task_id: str) -> TaskView:
        task = next((item for item in self.list_tasks() if item.Id == task_id), None)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        return task

    def set_task_memo(self, task_id: str, memo: str, operator_user_id: str | None = None) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_edit_permission(task, operator_user_id)
        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了・中止タスクは編集できません")
        self.repository.update_task_memo(task_id, memo.strip())
        self.recompute_all_task_progress()

    def set_task_actual_manhour(
        self,
        task_id: str,
        actual_manhour: int,
        operator_user_id: str | None = None,
    ) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_edit_permission(task, operator_user_id)
        if not self._is_leaf(task_id):
            raise UserInputError("実績MHは末端タスクのみ編集できます")
        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了・中止タスクは編集できません")
        if actual_manhour < 0:
            raise UserInputError("ActualManHour 縺ｯ0莉･荳翫〒謖・ｮ壹＠縺ｦ縺上□縺輔＞")
        self.repository.update_task_actual_manhour(task_id, int(actual_manhour))
        changed_task_ids = self.recompute_all_task_progress()
        changed_task_ids.add(task_id)
        self._queue_iquavis_progress_sync_for_tasks(changed_task_ids)

    def _distribute_minutes_by_ratio(self, total_minutes: int, weights: list[int]) -> list[int]:
        if total_minutes < 0:
            raise ValueError("total_minutes must be >= 0")
        if not weights:
            return []

        normalized = [max(0, int(weight)) for weight in weights]
        weight_sum = sum(normalized)
        size = len(normalized)

        if weight_sum <= 0:
            raw = [float(total_minutes) / float(size)] * size
        else:
            raw = [(float(total_minutes) * float(weight)) / float(weight_sum) for weight in normalized]

        floor_values = [int(value) for value in raw]
        remain = total_minutes - sum(floor_values)

        ranked = sorted(
            range(size),
            key=lambda idx: (raw[idx] - float(floor_values[idx]), raw[idx], -idx),
            reverse=True,
        )
        for idx in ranked[:remain]:
            floor_values[idx] += 1
        return floor_values

    def set_root_task_assigned_manhour(
        self,
        task_id: str,
        assigned_manhour: int,
        operator_user_id: str | None = None,
    ) -> None:
        if assigned_manhour < 0:
            raise UserInputError("AssignedManHour は0以上で指定してください")

        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_edit_permission(task, operator_user_id)
        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了・中止タスクは編集できません")

        assigned_updates: dict[str, int] = {task.Id: int(assigned_manhour)}
        self.repository.update_task_assigned_manhours(assigned_updates)
        changed_task_ids = self.recompute_all_task_progress()
        changed_task_ids.update(assigned_updates.keys())
        self._queue_iquavis_progress_sync_for_tasks(changed_task_ids)

    def update_task_assignee_and_end_date(
        self,
        task_id: str,
        assignee_user_id: str,
        end_date: date,
        operator_user_id: str | None = None,
    ) -> list[str]:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_edit_permission(task, operator_user_id)
        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了・中止タスクは編集できません")

        assignee = self.repository.get_user(assignee_user_id)
        if assignee is None:
            raise UserInputError("諡・ｽ楢・′蟄伜惠縺励∪縺帙ｓ")

        all_tasks = self.repository.list_tasks(task.ProjectId)
        if assignee_user_id != task.AssigneeUserId:
            self._ensure_task_assignee_change_allowed(task, all_tasks)

        start_date = min(task.StartDate, end_date)
        warnings = self._task_schedule_warnings(task, all_tasks, start_date, end_date)

        self.repository.update_task_assignee_and_end_date(task_id, assignee_user_id, end_date)
        if start_date != task.StartDate:
            self.repository.update_task_schedule(task_id, start_date, end_date)
            warnings.append("期限が着手日より前だったため、着手日も同じ日へ自動調整しました。")
        self._expand_ancestor_schedules(task, all_tasks, start_date, end_date)
        self.recompute_all_task_progress()
        if end_date != task.EndDate:
            self._queue_iquavis_schedule_sync(task_id)
        return warnings

    def update_task_schedule(
        self,
        task_id: str,
        start_date: date,
        end_date: date,
        operator_user_id: str | None = None,
    ) -> list[str]:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_edit_permission(task, operator_user_id)
        if task.Status == "Abort":
            raise UserInputError("中止タスクは編集できません")

        if task.StartDate == start_date and task.EndDate == end_date:
            return []

        all_tasks = self.repository.list_tasks(task.ProjectId)
        self._validate_task_schedule(task, all_tasks, start_date, end_date)
        warnings = self._task_schedule_warnings(task, all_tasks, start_date, end_date)
        self.repository.update_task_schedule(task_id, start_date, end_date)
        self._expand_ancestor_schedules(task, all_tasks, start_date, end_date)
        self.recompute_all_task_progress()
        self._queue_iquavis_schedule_sync(task_id)
        return warnings

    def update_task_parent(
        self,
        *,
        task_id: str,
        parent_task_id: str | None,
        operator_user_id: str | None = None,
    ) -> list[str]:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("タスクが見つかりません")
        self._ensure_task_edit_permission(task, operator_user_id)
        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了・中止タスクの親は変更できません")
        if parent_task_id == task.Id:
            raise UserInputError("自分自身を親タスクにはできません")

        operator = self.repository.get_user(operator_user_id) if operator_user_id else None
        if parent_task_id is None and operator is not None and not operator.IsTopManager:
            raise UserInputError("ルート直下への移動はTop Managerのみ実行できます")

        all_tasks = self.repository.list_tasks(task.ProjectId)
        task_by_id = {item.Id: item for item in all_tasks}
        parent = None
        if parent_task_id is not None:
            parent = task_by_id.get(parent_task_id)
            if parent is None:
                raise UserInputError("親タスクが見つかりません")
            if parent.ProjectId != task.ProjectId:
                raise UserInputError("別プロジェクトのタスクは親にできません")
            if parent.Status in TERMINAL_TASK_STATUSES:
                raise UserInputError("完了・中止タスクは親にできません")
            self._assert_no_parent_cycle(task.Id, parent.Id, task_by_id)
        else:
            project = self.get_project()

        if task.ParentTaskId == parent_task_id:
            return []

        self.repository.update_task_parent(task.Id, parent_task_id)
        warnings: list[str] = []
        if parent is not None and parent.Type != "GroupTask":
            self.repository.update_task_type(parent.Id, "GroupTask")
            self.repository.update_task_actual_manhour(parent.Id, 0)
        if parent is not None and (task.StartDate < parent.StartDate or task.EndDate > parent.EndDate):
            self._expand_ancestor_schedules(
                task,
                all_tasks,
                task.StartDate,
                task.EndDate,
                first_parent_id=parent.Id,
            )
            warnings.append("移動先の親タスク期間を、移動したタスクが収まるよう自動調整しました。")
        elif parent is None and (task.StartDate < project.StartDate or task.EndDate > project.EndDate):
            warnings.append("タスク期間がプロジェクト期間外です。必要に応じてプロジェクト期間を見直してください。")
        self.recompute_all_task_progress()
        return warnings

    def _assert_no_parent_cycle(
        self,
        task_id: str,
        parent_task_id: str,
        task_by_id: dict[str, Task],
    ) -> None:
        current_id: str | None = parent_task_id
        visited: set[str] = set()
        while current_id:
            if current_id == task_id:
                raise UserInputError("循環する親子関係は作成できません")
            if current_id in visited:
                raise UserInputError("既存の親子関係が循環しています")
            visited.add(current_id)
            current = task_by_id.get(current_id)
            current_id = current.ParentTaskId if current is not None else None

    def _validate_task_schedule(
        self,
        task: Task,
        all_tasks: list[Task],
        start_date: date,
        end_date: date,
    ) -> None:
        if start_date > end_date:
            raise UserInputError("開始日は期限以前で指定してください")

    def _task_schedule_warnings(
        self,
        task: Task,
        all_tasks: list[Task],
        start_date: date,
        end_date: date,
    ) -> list[str]:
        warnings: list[str] = []
        project = self.get_project()
        if start_date < project.StartDate or end_date > project.EndDate:
            warnings.append("タスク期間がプロジェクト期間外です。必要に応じてプロジェクト期間を見直してください。")

        # iQUAVISは非稼働日の予定日を保存時に拒否する(CED0129)。作った時点で気づけるようにする。
        non_working = [
            label
            for label, target_date in (("着手日", start_date), ("期限", end_date))
            if not self._is_business_day(target_date)
        ]
        if non_working:
            warnings.append(
                f"{'と'.join(non_working)}が非稼働日(土日・祝日)です。"
                "この日付はiQUAVISへ送れないため、Excel経由の同期対象から外れます。"
            )

        if task.ParentTaskId:
            parent = next((item for item in all_tasks if item.Id == task.ParentTaskId), None)
            if parent is None:
                warnings.append("親タスクが見つかりません。WBSの親子関係を確認してください。")
            elif start_date < parent.StartDate or end_date > parent.EndDate:
                warnings.append("親タスク期間外のため、親タスクと祖先タスクの期間を自動調整します。")

        direct_children = [child for child in all_tasks if child.ParentTaskId == task.Id]
        if direct_children:
            min_child_start = min(child.StartDate for child in direct_children)
            max_child_end = max(child.EndDate for child in direct_children)
            if start_date > min_child_start or end_date < max_child_end:
                warnings.append("指定期間に収まらない配下タスクがあります。配下タスクの日程も確認してください。")
        return warnings

    def _expand_ancestor_schedules(
        self,
        task: Task,
        all_tasks: list[Task],
        start_date: date,
        end_date: date,
        *,
        first_parent_id: str | None = None,
    ) -> None:
        task_by_id = {item.Id: item for item in all_tasks}
        current_parent_id = task.ParentTaskId if first_parent_id is None else first_parent_id
        current_start = start_date
        current_end = end_date
        while current_parent_id:
            parent = task_by_id.get(current_parent_id)
            if parent is None:
                return
            next_start = min(parent.StartDate, current_start)
            next_end = max(parent.EndDate, current_end)
            if next_start != parent.StartDate or next_end != parent.EndDate:
                self.repository.update_task_schedule(parent.Id, next_start, next_end)
                parent.StartDate = next_start
                parent.EndDate = next_end
                # 自動調整した親・祖先も、対象タスクと同様にiQUAVISへ同期する。
                self._queue_iquavis_schedule_sync(parent.Id)
            current_start = next_start
            current_end = next_end
            current_parent_id = parent.ParentTaskId

    def _ensure_task_assignee_change_allowed(self, task: Task, all_tasks: list[Task]) -> None:
        # 葉タスクは実績MHが0の間だけ担当変更可能。親タスクは担当変更可能。
        has_children = any(item.ParentTaskId == task.Id for item in all_tasks)
        if has_children:
            return
        if task.ActualManHour > 0:
            raise UserInputError("葉タスクの担当変更は実績MHが0のときのみ可能です")

    def reassign_task_subtree_assignee(
        self,
        *,
        root_task_id: str,
        assignee_user_id: str,
        operator_user_id: str,
    ) -> dict[str, Any]:
        """指定タスクとその配下すべての担当者を一括で差し替える。

        権限の考え方:

        - Top Manager … 対象を例外なく上書きする。
        - 一般ユーザー … 自分が担当しているタスクだけ、他の人へ渡せる。
          他人のタスクは黙って対象外にする（エラーにすると1件混ざるだけで全体が使えなくなる）。

        完了・中止タスクは誰が実行しても書き換えない。終わった仕事の担当を後から
        すり替えると、実績と担当の対応が崩れて評価が追えなくなる。
        """
        operator = self.repository.get_user(operator_user_id)
        if operator is None or not getattr(operator, "IsActive", True):
            raise UserInputError("操作ユーザーが見つかりません")
        assignee = self.repository.get_user(assignee_user_id)
        if assignee is None:
            raise UserInputError("指定された担当者が存在しません")

        root = self.repository.get_task(root_task_id)
        if root is None:
            raise UserInputError("タスクが見つかりません")

        all_tasks = self.repository.list_tasks(root.ProjectId)
        children_by_parent = self._task_children_by_parent(all_tasks)
        target_ids = self._collect_task_subtree_ids(root_task_id, all_tasks, children_by_parent)
        task_by_id = {task.Id: task for task in all_tasks}

        assignee_by_task_id: dict[str, str] = {}
        unchanged = 0
        skipped_completed = 0
        skipped_aborted = 0
        skipped_not_owned = 0

        for task_id in sorted(target_ids):
            task = task_by_id.get(task_id)
            if task is None:
                continue
            if task.Status == "Completion":
                skipped_completed += 1
                continue
            if task.Status == "Abort":
                skipped_aborted += 1
                continue
            if not operator.IsTopManager and task.AssigneeUserId != operator_user_id:
                skipped_not_owned += 1
                continue
            if task.AssigneeUserId == assignee_user_id:
                unchanged += 1
                continue
            assignee_by_task_id[task_id] = assignee_user_id

        if assignee_by_task_id:
            self.repository.update_task_assignees(assignee_by_task_id)
            for task_id in assignee_by_task_id:
                self.repository.mark_task_updated_by(task_id, operator_user_id)

        # 担当者はiQUAVISへ自動送信しない項目なので、同期キューへは入れない。
        return {
            "status": "ok",
            "root_task_id": root_task_id,
            "root_task_name": root.Name,
            "assignee_user_id": assignee_user_id,
            "assignee_name": assignee.Name,
            "scanned": len(target_ids),
            "changed": len(assignee_by_task_id),
            "unchanged": unchanged,
            "skipped_completed": skipped_completed,
            "skipped_aborted": skipped_aborted,
            "skipped_not_owned": skipped_not_owned,
            "is_top_manager": bool(operator.IsTopManager),
        }

    def _ensure_task_assignee_change_permission_for_split(self, task: Task, operator_user_id: str) -> None:
        operator = self.repository.get_user(operator_user_id)
        if operator is None:
            raise UserInputError("操作ユーザーが見つかりません")
        if operator.IsTopManager or task.AssigneeUserId == operator_user_id:
            return
        if task.ParentTaskId:
            parent = self.repository.get_task(task.ParentTaskId)
            if parent is not None and parent.AssigneeUserId == operator_user_id:
                return
        raise UserInputError("担当者本人・直上タスク担当者・Top Managerのみ担当変更できます")

    def update_task_assignee(
        self,
        task_id: str,
        assignee_user_id: str,
        operator_user_id: str | None = None,
    ) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_edit_permission(task, operator_user_id)
        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了・中止タスクは編集できません")

        assignee = self.repository.get_user(assignee_user_id)
        if assignee is None:
            raise UserInputError("諡・ｽ楢・′蟄伜惠縺励∪縺帙ｓ")
        if assignee_user_id == task.AssigneeUserId:
            return

        all_tasks = self.repository.list_tasks(task.ProjectId)
        self._ensure_task_assignee_change_allowed(task, all_tasks)
        self.repository.update_task_assignee_and_end_date(task_id, assignee_user_id, task.EndDate)
        self.recompute_all_task_progress()

    def update_task_child_dependency_mode(
        self,
        *,
        task_id: str,
        child_dependency_mode: str,
        operator_user_id: str | None = None,
    ) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("タスクが見つかりません")
        self._ensure_task_edit_permission(task, operator_user_id)
        if task.Status == "Abort":
            raise UserInputError("中止タスクは編集できません")

        next_mode = self._parse_child_dependency_mode(child_dependency_mode)
        if self._coerce_child_dependency_mode(getattr(task, "ChildDependencyMode", None)) == next_mode:
            return

        self.repository.update_task_child_dependency_mode(task_id, next_mode)

    def bulk_update_task_assignees(
        self,
        *,
        operator_user_id: str,
        updates: list[dict[str, str]],
    ) -> dict[str, Any]:
        changed = 0
        results: list[dict[str, Any]] = []
        for item in updates:
            task_id = str(item.get("task_id", "")).strip()
            assignee_user_id = str(item.get("assignee_user_id", "")).strip()
            try:
                if not task_id or not assignee_user_id:
                    raise UserInputError("タスクと変更先担当者を指定してください")

                task = self.repository.get_task(task_id)
                if task is None:
                    raise UserInputError("タスクが見つかりません")
                self._ensure_task_assignee_change_permission_for_split(task, operator_user_id)
                if task.Status in TERMINAL_TASK_STATUSES:
                    raise UserInputError("完了・中止タスクは担当者を変更できません")
                if task.AssigneeUserId == assignee_user_id:
                    results.append(
                        {
                            "task_id": task_id,
                            "assignee_user_id": assignee_user_id,
                            "success": True,
                            "changed": False,
                            "message": "担当者は既に選択したユーザーです",
                        }
                    )
                    continue

                assignee = self.repository.get_user(assignee_user_id)
                if assignee is None:
                    raise UserInputError("変更先の担当者が見つかりません")

                all_tasks = self.repository.list_tasks(task.ProjectId)
                self._ensure_task_assignee_change_allowed(task, all_tasks)
                self.repository.update_task_assignee_and_end_date(task_id, assignee_user_id, task.EndDate)
                changed += 1
                results.append(
                    {
                        "task_id": task_id,
                        "assignee_user_id": assignee_user_id,
                        "success": True,
                        "changed": True,
                        "message": "担当者を変更しました",
                    }
                )
            except UserInputError as exc:
                # 入力・権限エラーは行単位で返し、他の行の更新を継続する。
                results.append(
                    {
                        "task_id": task_id,
                        "assignee_user_id": assignee_user_id,
                        "success": False,
                        "changed": False,
                        "message": str(exc),
                    }
                )

        if changed > 0:
            self.recompute_all_task_progress()
        failed = sum(1 for result in results if not result["success"])
        return {"changed": changed, "failed": failed, "results": results}

    def bulk_update_task_fields(
        self,
        *,
        operator_user_id: str,
        parent_task_id: str,
        updates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Split画面の既存タスクを行単位で検証し、成功した行だけ保存する。"""
        changed = 0
        results: list[dict[str, Any]] = []
        project_tasks_by_id: dict[str, list[Task]] = {}
        project_task_maps_by_id: dict[str, dict[str, Task]] = {}
        progress_sync_task_ids: set[str] = set()
        schedule_sync_task_ids: set[str] = set()

        for item in updates:
            task_id = str(item.get("task_id", "")).strip()
            warnings: list[str] = []
            try:
                if not task_id:
                    raise UserInputError("タスクを指定してください")

                task = self.repository.get_task(task_id)
                if task is None:
                    raise UserInputError("タスクが見つかりません")
                if task.ParentTaskId != parent_task_id:
                    raise UserInputError("選択した親タスクの直接の子タスクのみ更新できます")
                self._ensure_task_assignee_change_permission_for_split(task, operator_user_id)
                if task.Status in TERMINAL_TASK_STATUSES:
                    raise UserInputError("完了タスク・中止タスクは編集できません")

                name = str(item.get("name", "")).strip()
                if not name:
                    raise UserInputError("タスク名を入力してください")

                try:
                    assigned_manhour = int(item.get("assigned_manhour"))
                except (TypeError, ValueError) as exc:
                    raise UserInputError("AssignedManHourは整数で指定してください") from exc
                if assigned_manhour < 0:
                    raise UserInputError("AssignedManHourは0以上で指定してください")

                raw_end_date = item.get("end_date")
                if isinstance(raw_end_date, str):
                    try:
                        end_date = date.fromisoformat(raw_end_date)
                    except ValueError as exc:
                        raise UserInputError("期限はYYYY-MM-DD形式で指定してください") from exc
                elif isinstance(raw_end_date, date):
                    end_date = raw_end_date
                else:
                    raise UserInputError("期限はYYYY-MM-DD形式で指定してください")

                assignee_user_id = str(item.get("assignee_user_id", "")).strip()
                if not assignee_user_id:
                    raise UserInputError("担当者を指定してください")
                assignee = self.repository.get_user(assignee_user_id)
                if assignee is None:
                    raise UserInputError("変更先の担当者が見つかりません")

                if task.ProjectId not in project_tasks_by_id:
                    project_tasks = self.repository.list_tasks(task.ProjectId)
                    project_tasks_by_id[task.ProjectId] = project_tasks
                    project_task_maps_by_id[task.ProjectId] = {
                        project_task.Id: project_task for project_task in project_tasks
                    }
                all_tasks = project_tasks_by_id[task.ProjectId]
                if assignee_user_id != task.AssigneeUserId:
                    self._ensure_task_assignee_change_allowed(task, all_tasks)

                start_date = min(task.StartDate, end_date)
                warnings = self._task_schedule_warnings(task, all_tasks, start_date, end_date)
                if start_date != task.StartDate:
                    warnings.append("期限が着手日より前だったため、着手日も同じ日へ自動調整しました。")

                memo = str(item.get("memo", "")).strip()
                name_changed = name != task.Name
                assigned_changed = assigned_manhour != int(task.AssignedManHour)
                schedule_changed = start_date != task.StartDate or end_date != task.EndDate
                row_changed = any(
                    (
                        name_changed,
                        assigned_changed,
                        memo != task.Memo,
                        schedule_changed,
                        assignee_user_id != task.AssigneeUserId,
                    )
                )

                if row_changed:
                    # 行内の全項目を先に検証し、部分保存を起こさない一括更新にする。
                    self.repository.update_task_fields(
                        task_id,
                        name=name,
                        assigned_manhour=assigned_manhour,
                        memo=memo,
                        start_date=start_date,
                        end_date=end_date,
                        assignee_user_id=assignee_user_id,
                    )
                    cached_task = project_task_maps_by_id[task.ProjectId].get(task.Id)
                    for updated_task in (task, cached_task):
                        if updated_task is None:
                            continue
                        updated_task.Name = name
                        updated_task.AssignedManHour = assigned_manhour
                        updated_task.Memo = memo
                        updated_task.StartDate = start_date
                        updated_task.EndDate = end_date
                        updated_task.AssigneeUserId = assignee_user_id

                    self._expand_ancestor_schedules(task, all_tasks, start_date, end_date)
                    if name_changed or schedule_changed:
                        schedule_sync_task_ids.add(task_id)
                    if name_changed or assigned_changed:
                        progress_sync_task_ids.add(task_id)
                    changed += 1

                results.append(
                    {
                        "task_id": task_id,
                        "success": True,
                        "changed": row_changed,
                        "message": "タスクを更新しました" if row_changed else "変更はありません",
                        "warnings": warnings,
                    }
                )
            except UserInputError as exc:
                # 一行の失敗で、検証済みの他行まで巻き戻さない。
                results.append(
                    {
                        "task_id": task_id,
                        "success": False,
                        "changed": False,
                        "message": str(exc),
                        "warnings": [],
                    }
                )

        if changed > 0:
            changed_progress_task_ids = self.recompute_all_task_progress()
            changed_progress_task_ids.update(progress_sync_task_ids)
            self._queue_iquavis_progress_sync_for_tasks(changed_progress_task_ids)
            for task_id in sorted(schedule_sync_task_ids):
                self._queue_iquavis_schedule_sync(task_id)

        failed = sum(1 for result in results if not result["success"])
        return {"changed": changed, "failed": failed, "results": results}

    def _timer_seconds(self, timer: TimerSession, now: datetime | None = None) -> float:
        end_at = timer.EndAt or now
        if end_at is None:
            return 0.0
        return max(0.0, (end_at - timer.StartAt).total_seconds())

    def _recompute_task_actual_manhour(self, task_id: str) -> None:
        daily_total = self.repository.sum_daily_task_actual_total_minutes(task_id)
        if daily_total > 0:
            self.repository.update_task_actual_manhour(task_id, round_half_up(daily_total))
            return

        timers = self.repository.list_timers_by_task(task_id)
        seconds = sum(self._timer_seconds(timer) for timer in timers if timer.EndAt is not None)
        self.repository.update_task_actual_manhour(task_id, seconds_to_rounded_minutes(seconds))

    def _recompute_daily_attendance(self, user_id: str, work_date: date, now: datetime | None = None) -> None:
        timers = self.repository.list_timers_by_user_and_day(user_id=user_id, work_date=work_date)
        active = self.repository.get_active_timer(user_id)

        all_starts = [t.StartAt for t in timers]
        all_ends = [t.EndAt for t in timers if t.EndAt is not None]

        if active is not None and active.StartAt.date() == work_date:
            all_starts.append(active.StartAt)
            all_ends.append(now or datetime.now())

        if not all_starts or not all_ends:
            return

        first_start = min(all_starts)
        last_end = max(all_ends)
        work_minutes = max(0, int((last_end - first_start).total_seconds() // 60))

        self.repository.upsert_daily_attendance(
            user_id=user_id,
            work_date=work_date,
            start_at=first_start,
            end_at=last_end,
            work_minutes=work_minutes,
        )

    def _split_interval_by_day(
        self,
        start_at: datetime,
        end_at: datetime,
    ) -> list[tuple[datetime, datetime]]:
        if end_at <= start_at:
            return []

        chunks: list[tuple[datetime, datetime]] = []
        cursor = start_at
        while cursor.date() < end_at.date():
            next_midnight = datetime.combine(cursor.date() + timedelta(days=1), datetime.min.time())
            chunks.append((cursor, next_midnight))
            cursor = next_midnight
        chunks.append((cursor, end_at))
        return chunks

    def _record_interval_and_refresh(
        self,
        *,
        user_id: str,
        task_id: str,
        start_at: datetime,
        end_at: datetime,
        is_ojt: bool,
        teacher_user_id: str | None,
        source_timer_id: str | None,
    ) -> None:
        if end_at <= start_at:
            return

        affected_days: set[date] = set()
        for chunk_start, chunk_end in self._split_interval_by_day(start_at, end_at):
            duration_seconds = max(0.0, (chunk_end - chunk_start).total_seconds())
            if duration_seconds <= 0:
                continue
            added = self.repository.add_raw_worklog(
                user_id=user_id,
                task_id=task_id,
                start_at=chunk_start,
                end_at=chunk_end,
                duration_seconds=duration_seconds,
                is_ojt=is_ojt,
                teacher_user_id=teacher_user_id,
                source_timer_id=source_timer_id,
            )
            if added is not False:
                affected_days.add(chunk_start.date())

        if not affected_days:
            affected_days.add(start_at.date())

        for work_date in sorted(affected_days):
            self._recompute_daily_attendance(
                user_id=user_id,
                work_date=work_date,
                now=end_at if work_date == end_at.date() else None,
            )

        touched_task_ids: set[str] = {task_id}
        for work_date in sorted(affected_days):
            touched_task_ids.update(
                self._recompute_daily_task_actuals_for_user_day(user_id=user_id, work_date=work_date)
            )
        for touched_task_id in touched_task_ids:
            self._recompute_task_actual_manhour(touched_task_id)

        changed_task_ids = self.recompute_all_task_progress()
        changed_task_ids.update(touched_task_ids)
        self._queue_iquavis_progress_sync_for_tasks(changed_task_ids)

    def _is_business_day(self, target_date: date) -> bool:
        return target_date.weekday() < 5 and target_date not in self.settings.calendar.holidays

    def _iter_business_days(self, start_date: date, end_date: date) -> list[date]:
        if end_date < start_date:
            return []

        days: list[date] = []
        cursor = start_date
        while cursor <= end_date:
            if self._is_business_day(cursor):
                days.append(cursor)
            cursor += timedelta(days=1)
        return days

    def _list_today_task_ids_on(self, user_id: str, work_date: date) -> list[str]:
        all_tasks = self.list_tasks()
        parent_ids = {task.ParentTaskId for task in all_tasks if task.ParentTaskId}
        return [
            task.Id
            for task in all_tasks
            if (
                task.AssigneeUserId == user_id
                and task.Status not in TERMINAL_TASK_STATUSES
                and task.Id not in parent_ids
                and self.is_today_on(task, base_date=work_date)
            )
        ]

    def _member_commit_rate_for_day(self, user_id: str, work_date: date) -> float:
        self.repository.ensure_member_profiles()
        profile = self.repository.get_member_profile(user_id)
        rate = float(profile.DefaultCommitRate) if profile is not None else 1.0
        day_config = self.repository.get_member_day_config(user_id=user_id, work_date=work_date)
        leave_type = ""
        if day_config is not None:
            leave_type = getattr(day_config, "LeaveType", "") or ""
        if day_config is not None and (day_config.IsVacation or leave_type in FULL_DAY_LEAVE_VALUES):
            return 0.0
        if day_config is not None and day_config.CommitRateOverride is not None:
            rate = float(day_config.CommitRateOverride)
        if leave_type in HALF_DAY_LEAVE_VALUES:
            rate *= 0.5
        return self._coerce_rate(rate, label="コミット率")

    def _recompute_daily_task_actuals_for_user_day(self, user_id: str, work_date: date) -> set[str]:
        user = self.repository.get_user(user_id)
        if user is None:
            return set()

        project = self.get_project()
        previous_rows = self.repository.list_daily_task_actuals_by_user_and_day(user_id=user_id, work_date=work_date)
        previous_task_ids = {row.TaskId for row in previous_rows}

        raw_worklogs = self.repository.list_raw_worklogs_by_user_and_day(user_id=user_id, work_date=work_date)
        timer_minutes_by_task: dict[str, float] = defaultdict(float)
        for worklog in raw_worklogs:
            if worklog.IsOjt:
                continue
            timer_minutes_by_task[worklog.TaskId] += max(0.0, float(worklog.DurationSeconds)) / 60.0

        attendance_list = self.repository.list_daily_attendance(
            user_id=user_id,
            date_from=work_date,
            date_to=work_date,
        )
        if attendance_list:
            work_minutes = max(0, int(attendance_list[-1].WorkMinutes))
        elif raw_worklogs:
            first_start = min(item.StartAt for item in raw_worklogs)
            last_end = max(item.EndAt for item in raw_worklogs)
            work_minutes = max(0, int((last_end - first_start).total_seconds() // 60))
        else:
            work_minutes = 0

        budget = calculate_daily_budget(
            work_minutes=work_minutes,
            free_time_rate=user.FreeTimeRate,
            education_rate_monthly=self._monthly_education_rate(user_id, work_date.strftime("%Y-%m")),
            regular_minutes_per_day=self.settings.app.regular_minutes_per_day,
        )
        commit_rate = self._member_commit_rate_for_day(user_id=user_id, work_date=work_date)
        target_work_budget_minutes = float(budget.WorkBudgetMinutes) * commit_rate
        known_minutes = sum(timer_minutes_by_task.values())
        unknown_minutes = max(0.0, target_work_budget_minutes - known_minutes)

        today_task_ids = self._list_today_task_ids_on(user_id=user_id, work_date=work_date)
        # 手動計上（timer_minutes>0）が1度でもあるタスクは自動等分配の対象から除外する。
        undistributed_task_ids = [
            task_id
            for task_id in today_task_ids
            if timer_minutes_by_task.get(task_id, 0.0) <= 0.0
        ]
        distribution_target_task_ids = undistributed_task_ids
        if not distribution_target_task_ids and unknown_minutes > 0:
            # 全Todayタスクにタイマ実績がある日は、残りMHを計測済みタスクへ按分する。
            distribution_target_task_ids = [
                task_id
                for task_id in today_task_ids
                if timer_minutes_by_task.get(task_id, 0.0) > 0.0
            ]

        distributed_minutes_by_task: dict[str, float] = defaultdict(float)
        if distribution_target_task_ids and unknown_minutes > 0:
            per_task = unknown_minutes / float(len(distribution_target_task_ids))
            for task_id in distribution_target_task_ids:
                distributed_minutes_by_task[task_id] += per_task

        touched_task_ids = set(previous_task_ids)
        touched_task_ids.update(timer_minutes_by_task.keys())
        touched_task_ids.update(distributed_minutes_by_task.keys())

        rows = [
            {
                "task_id": task_id,
                "timer_minutes": float(timer_minutes_by_task.get(task_id, 0.0)),
                "distributed_minutes": float(distributed_minutes_by_task.get(task_id, 0.0)),
            }
            for task_id in sorted(touched_task_ids)
            if timer_minutes_by_task.get(task_id, 0.0) > 0 or distributed_minutes_by_task.get(task_id, 0.0) > 0
        ]
        self.repository.replace_daily_task_actuals(
            project_id=project.Id,
            user_id=user_id,
            work_date=work_date,
            rows=rows,
        )
        return touched_task_ids

    def _finalize_daily_actuals_for_day(self, work_date: date) -> None:
        touched_task_ids: set[str] = set()
        for user in self.repository.list_users(active_only=True):
            touched_task_ids.update(
                self._recompute_daily_task_actuals_for_user_day(user_id=user.Id, work_date=work_date)
            )
        for task_id in touched_task_ids:
            self._recompute_task_actual_manhour(task_id)
        if touched_task_ids:
            changed_task_ids = self.recompute_all_task_progress()
            changed_task_ids.update(touched_task_ids)
            self._queue_iquavis_progress_sync_for_tasks(changed_task_ids)

    def _ensure_task_assignable(self, user_id: str, task: Task) -> None:
        if task.AssigneeUserId != user_id:
            raise UserInputError("担当者本人のみタイマ操作できます")

    def _validate_ojt_teacher(self, *, user_id: str, is_ojt: bool, teacher_user_id: str | None) -> None:
        if not is_ojt:
            return
        if not teacher_user_id:
            raise UserInputError("OJT受講モードではTeacherを指定してください")
        if teacher_user_id == user_id:
            raise UserInputError("OJT担当には本人以外を指定してください")
        if self.repository.get_user(teacher_user_id) is None:
            raise UserInputError("OJT担当が見つかりません")

    def _ensure_task_edit_permission(self, task: Task, operator_user_id: str | None) -> None:
        if operator_user_id is None:
            return
        operator = self.repository.get_user(operator_user_id)
        if operator is None:
            raise UserInputError("螳溯｡後Θ繝ｼ繧ｶ繝ｼ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        if task.AssigneeUserId != operator_user_id and not operator.IsTopManager:
            raise UserInputError("担当者本人またはTop Managerのみ編集できます")

    def start_timer(
        self,
        user_id: str,
        task_id: str,
        *,
        is_ojt: bool,
        teacher_user_id: str | None,
    ) -> None:
        now = datetime.now()
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_assignable(user_id, task)

        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了または中止したタスクは開始できません")
        if not self._is_leaf(task_id):
            raise UserInputError("蟄舌ち繧ｹ繧ｯ繧呈戟縺､繧ｿ繧ｹ繧ｯ縺ｯ繧ｿ繧､繝樣幕蟋九〒縺阪∪縺帙ｓ")

        self._validate_ojt_teacher(user_id=user_id, is_ojt=is_ojt, teacher_user_id=teacher_user_id)

        active = self.repository.get_active_timer(user_id)
        if active is not None:
            if active.TaskId == task_id and active.IsOjt == is_ojt and active.TeacherUserId == teacher_user_id:
                return
            # タスク切替は「旧タスクをPauseして新タスクをStart」と同じ扱いにする。
            self._stop_timer_by_id(
                active.Id,
                now=now,
                status="Paused",
                reason="ReplacedByNewTimer",
            )

        self.repository.start_timer(
            user_id=user_id,
            task_id=task_id,
            now=now,
            is_ojt=is_ojt,
            teacher_user_id=teacher_user_id,
        )

        if task.Status == "NotStart":
            self.repository.update_task_status(task_id=task_id, status="DuringStart", progress_rate=0.0)

        self._recompute_daily_attendance(user_id=user_id, work_date=now.date(), now=now)
        changed_task_ids = self.recompute_all_task_progress()
        changed_task_ids.add(task_id)
        self._queue_iquavis_progress_sync_for_tasks(changed_task_ids)

    def _stop_timer_by_id(self, timer_id: str, now: datetime, status: str, reason: str | None = None) -> None:
        timer = None
        for running in self.repository.list_running_timers():
            if running.Id == timer_id:
                timer = running
                break
        if timer is None:
            return

        self.repository.stop_timer(timer_id=timer_id, status=status, now=now, reason=reason)
        self._record_interval_and_refresh(
            user_id=timer.UserId,
            task_id=timer.TaskId,
            start_at=timer.StartAt,
            end_at=now,
            is_ojt=timer.IsOjt,
            teacher_user_id=timer.TeacherUserId,
            source_timer_id=timer.Id,
        )

    def record_timer_interval(
        self,
        *,
        user_id: str,
        task_id: str,
        started_at_ms: int,
        ended_at_ms: int,
        is_ojt: bool,
        teacher_user_id: str | None,
    ) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_assignable(user_id, task)

        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了または中止したタスクには実績を追加できません")
        if not self._is_leaf(task_id):
            raise UserInputError("蟄舌ち繧ｹ繧ｯ繧呈戟縺､繧ｿ繧ｹ繧ｯ縺ｯ繧ｿ繧､繝樣幕蟋九〒縺阪∪縺帙ｓ")
        self._validate_ojt_teacher(user_id=user_id, is_ojt=is_ojt, teacher_user_id=teacher_user_id)

        start_at = datetime.fromtimestamp(float(started_at_ms) / 1000.0)
        end_at = datetime.fromtimestamp(float(ended_at_ms) / 1000.0)
        now = datetime.now()
        if end_at > now:
            end_at = now
        if end_at <= start_at:
            return

        if task.Status == "NotStart":
            self.repository.update_task_status(task_id=task_id, status="DuringStart", progress_rate=0.0)

        self._record_interval_and_refresh(
            user_id=user_id,
            task_id=task_id,
            start_at=start_at,
            end_at=end_at,
            is_ojt=is_ojt,
            teacher_user_id=teacher_user_id,
            source_timer_id=None,
        )

    def pause_timer(self, user_id: str) -> None:
        timer = self.repository.get_active_timer(user_id)
        if timer is None:
            return
        self._stop_timer_by_id(timer.Id, now=datetime.now(), status="Paused")

    def stop_timer(self, user_id: str) -> None:
        timer = self.repository.get_active_timer(user_id)
        if timer is None:
            return
        self._stop_timer_by_id(timer.Id, now=datetime.now(), status="Stopped")

    def add_output_deliverable(
        self,
        task_id: str,
        title: str,
        url: str,
        operator_user_id: str | None = None,
    ) -> None:
        title = title.strip()
        url = url.strip()
        if not title or not url:
            raise UserInputError("謌先棡迚ｩ縺ｮ繧ｿ繧､繝医Ν縺ｨURL繧貞・蜉帙＠縺ｦ縺上□縺輔＞")
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_edit_permission(task, operator_user_id)
        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了・中止タスクは編集できません")

        outputs = self.repository.task_output_deliverables(task_id)
        outputs.append({"title": title, "url": url})
        self.repository.update_task_output_deliverables(task_id, outputs)
        self.recompute_all_task_progress()

    def remove_output_deliverable(
        self,
        task_id: str,
        index: int,
        operator_user_id: str | None = None,
    ) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_edit_permission(task, operator_user_id)
        if task.Status in TERMINAL_TASK_STATUSES:
            raise UserInputError("完了・中止タスクは編集できません")
        outputs = self.repository.task_output_deliverables(task_id)
        if index < 0 or index >= len(outputs):
            raise UserInputError("蜑企勁蟇ｾ雎｡縺悟ｭ伜惠縺励∪縺帙ｓ")
        del outputs[index]
        self.repository.update_task_output_deliverables(task_id, outputs)
        self.recompute_all_task_progress()

    def complete_task(self, task_id: str, operator_user_id: str | None = None) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_edit_permission(task, operator_user_id)
        if task.Status == "Abort":
            raise UserInputError("中止タスクは再開してから完了してください")

        self.repository.update_task_status(task_id=task_id, status="Completion", progress_rate=100.0)
        (
            changed_task_ids,
            project_progress_rate,
            parent_ids,
            recomputed_tasks,
        ) = self._recompute_all_task_progress_result()
        changed_task_ids.add(task_id)
        self._queue_iquavis_progress_sync_for_tasks(changed_task_ids, tasks=recomputed_tasks)

        if task_id not in parent_ids:
            # 再計算済みの集計値を再利用し、大規模WBSで二度目の全件集計を行わない。
            self.record_progress_snapshot(reason="LeafCompletion", progress_rate=project_progress_rate)

    def reopen_task(self, task_id: str, operator_user_id: str | None = None) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("繧ｿ繧ｹ繧ｯ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")
        self._ensure_task_edit_permission(task, operator_user_id)
        self.repository.update_task_status(task_id=task_id, status="DuringStart", progress_rate=0.0)
        changed_task_ids = self.recompute_all_task_progress()
        changed_task_ids.add(task_id)
        self._queue_iquavis_progress_sync_for_tasks(changed_task_ids)

    def _is_leaf(self, task_id: str) -> bool:
        project = self.get_project()
        tasks = self.repository.list_tasks(project.Id)
        parent_ids = {t.ParentTaskId for t in tasks if t.ParentTaskId}
        return task_id not in parent_ids

    def _desired_statuses_for_hierarchy(
        self,
        tasks: list[Task],
        aggregates: dict[str, TaskAggregate],
    ) -> dict[str, str]:
        task_by_id = {task.Id: task for task in tasks}
        children_by_parent: dict[str, list[Task]] = {}
        for task in tasks:
            if task.ParentTaskId is None:
                continue
            children_by_parent.setdefault(task.ParentTaskId, []).append(task)

        desired: dict[str, str] = {}

        def walk(task_id: str) -> str:
            cached = desired.get(task_id)
            if cached is not None:
                return cached

            task = task_by_id[task_id]
            children = children_by_parent.get(task_id, [])
            if not children:
                # 末端タスクの状態遷移は既存ロジック（タイマ/完了操作）に委ねる。
                desired_status = task.Status
                desired[task_id] = desired_status
                return desired_status

            child_statuses = [walk(child.Id) for child in children]
            active_child_statuses = [status for status in child_statuses if status != "Abort"]
            if child_statuses and not active_child_statuses:
                desired_status = "Abort"
            elif active_child_statuses and all(status == "Completion" for status in active_child_statuses):
                desired_status = "Completion"
            else:
                started = aggregates.get(task_id).Started if task_id in aggregates else (task.Status != "NotStart")
                desired_status = "DuringStart" if started else "NotStart"

            desired[task_id] = desired_status
            return desired_status

        for task in tasks:
            walk(task.Id)
        return desired

    def _recompute_all_task_progress_result(
        self,
    ) -> tuple[set[str], float, set[str], list[Task]]:
        """全タスクの進捗と親状態を再計算する。読み込んだタスクも返す。

        完了操作は再計算のあとにiQUAVIS同期キュー投入も行う。同じ全件読み込みを
        2回やると1万件規模で数百ミリ秒の無駄になるため、呼び出し側で使い回せるようにする。
        """
        project = self.get_project()
        tasks = self.repository.list_tasks(project.Id)
        aggregates = self._build_task_aggregates(tasks)
        parent_ids = {task.ParentTaskId for task in tasks if task.ParentTaskId}
        changed_task_ids: set[str] = set()
        progress_state_updates: dict[str, tuple[float, str | None]] = {}

        for task in tasks:
            progress = aggregates[task.Id].ProgressRate if task.Id in aggregates else 0.0
            if abs(float(task.ProgressRate) - float(progress)) > 1e-9:
                changed_task_ids.add(task.Id)
                progress_state_updates[task.Id] = (progress, None)

        desired_statuses = self._desired_statuses_for_hierarchy(tasks, aggregates)
        for task in tasks:
            if task.Id not in parent_ids:
                continue
            desired_status = desired_statuses.get(task.Id, task.Status)
            if desired_status == task.Status:
                continue
            progress = aggregates[task.Id].ProgressRate if task.Id in aggregates else task.ProgressRate
            progress_state_updates[task.Id] = (progress, desired_status)
            changed_task_ids.add(task.Id)

        self.repository.update_task_progress_states(progress_state_updates)
        # 書き込んだ値をメモリ上のタスクへも反映し、呼び出し側が再読込せずに使えるようにする。
        if progress_state_updates:
            task_by_id = {task.Id: task for task in tasks}
            for updated_task_id, (progress, status) in progress_state_updates.items():
                updated = task_by_id.get(updated_task_id)
                if updated is None:
                    continue
                updated.ProgressRate = progress
                if status is not None:
                    updated.Status = status
        return (
            changed_task_ids,
            self._project_progress_rate_from_tasks(tasks, aggregates),
            parent_ids,
            tasks,
        )

    def recompute_all_task_progress(self) -> set[str]:
        changed_task_ids, _, _, _ = self._recompute_all_task_progress_result()
        return changed_task_ids

    def _project_progress_rate_from_tasks(
        self,
        tasks: list[Task],
        aggregates: dict[str, TaskAggregate],
    ) -> float:
        task_by_id = {task.Id: task for task in tasks}

        root_weighted = [
            LeafProgress(
                AssignedManHour=max(0, agg.AssignedTotal),
                ProgressRate=agg.ProgressRate,
            )
            for task_id, agg in aggregates.items()
            if task_id in task_by_id
            and task_by_id[task_id].ParentTaskId is None
            and agg.AssignedTotal > 0
        ]
        if root_weighted:
            return calculate_parent_progress_rate(root_weighted)

        parent_ids = {task.ParentTaskId for task in tasks if task.ParentTaskId}
        leaves = [task for task in tasks if task.Id not in parent_ids]
        return calculate_parent_progress_rate(
            [
                LeafProgress(
                    AssignedManHour=max(0, task.AssignedManHour),
                    ProgressRate=calculate_leaf_progress_rate(
                        assigned_manhour=task.AssignedManHour,
                        actual_manhour=task.ActualManHour,
                        status=task.Status,
                    ),
                )
                for task in leaves
                if task.Status != "Abort"
            ]
        )

    def project_progress_rate(self) -> float:
        project = self.get_project()
        tasks = self.repository.list_tasks(project.Id)
        aggregates = self._build_task_aggregates(tasks)
        return self._project_progress_rate_from_tasks(tasks, aggregates)

    def record_progress_snapshot(self, reason: str, *, progress_rate: float | None = None) -> None:
        project = self.get_project()
        self.repository.add_progress_history(
            project_id=project.Id,
            recorded_at=datetime.now(),
            progress_rate=self.project_progress_rate() if progress_rate is None else progress_rate,
            reason=reason,
        )

    def finalize_daily_actuals_if_due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        today_date = now.date()
        today = today_date.isoformat()

        last_finalize = self.repository.get_state("LastDailyActualFinalizeDate")
        if last_finalize == today:
            return False

        self._finalize_daily_actuals_for_day(today_date - timedelta(days=1))
        self.repository.set_state("LastDailyActualFinalizeDate", today)
        return True

    def force_finalize_daily_actuals(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        today_date = now.date()
        self._finalize_daily_actuals_for_day(today_date - timedelta(days=1))
        self.repository.set_state("LastDailyActualFinalizeDate", today_date.isoformat())
        return True

    def run_daily_snapshot_if_due(self, now: datetime | None = None) -> bool:
        now = now or datetime.now()
        today_date = now.date()
        today = today_date.isoformat()

        last = self.repository.get_state("LastDailySnapshotDate")

        due = (
            now.hour > self.settings.app.daily_snapshot_hour
            or (
                now.hour == self.settings.app.daily_snapshot_hour
                and now.minute >= self.settings.app.daily_snapshot_minute
            )
        )

        if not due or last == today:
            return False

        # タスク別の詳細スナップショットは表示時に対象タスクだけ再生成する。
        # 起動直後や日次バッチで全タスク分を再生成すると、iQUAVIS由来の大量タスクで
        # サーバー起動が完了しなくなるため、日次バッチではプロジェクト全体履歴だけ記録する。
        self.record_progress_snapshot(reason="DailyBatch")
        self.repository.set_state("LastDailySnapshotDate", today)
        return True

    def get_progress_dashboard(self) -> ProgressDashboard:
        project = self.get_project()
        history_raw = self.repository.list_progress_history(project.Id)
        if not history_raw:
            self.record_progress_snapshot(reason="DailyBatch")
            history_raw = self.repository.list_progress_history(project.Id)

        history = [
            ProgressSnapshot(RecordedAt=item.RecordedAt, ProgressRate=item.ProgressRate)
            for item in history_raw
        ]

        progress_now = self.project_progress_rate()
        remain_days = max(0.0, (project.EndDate - date.today()).days)

        speed = calculate_speed_result(
            history=history,
            progress_now=progress_now,
            remain_days=remain_days,
            crash_zone_pct=project.CrashZonePct,
        )
        speed = SpeedResult(
            VActual=speed.VActual if isfinite(speed.VActual) else 0.0,
            VCritical=speed.VCritical if isfinite(speed.VCritical) else 0.0,
            VSafe=speed.VSafe if isfinite(speed.VSafe) else 0.0,
            Signal=speed.Signal,
        )

        return ProgressDashboard(
            ProgressRate=progress_now,
            Speed=speed,
            History=history,
            ProjectEndDate=project.EndDate,
            CrashZonePct=project.CrashZonePct,
        )

    def _collect_leaf_tasks(self, base_task_id: str, tasks: list[Task]) -> list[Task]:
        task_by_id = {task.Id: task for task in tasks}
        children_by_parent: dict[str, list[Task]] = defaultdict(list)
        for task in tasks:
            if task.ParentTaskId is None:
                continue
            children_by_parent[task.ParentTaskId].append(task)

        leaves: list[Task] = []
        stack = [base_task_id]
        visited: set[str] = set()
        while stack:
            current_id = stack.pop()
            if current_id in visited:
                continue
            visited.add(current_id)

            children = children_by_parent.get(current_id, [])
            if not children:
                task = task_by_id.get(current_id)
                if task is not None:
                    leaves.append(task)
                continue

            for child in children:
                stack.append(child.Id)
        return leaves

    def _has_direct_children(self, task_id: str, tasks: list[Task]) -> bool:
        return any(task.ParentTaskId == task_id for task in tasks)

    def _assigned_total_for_progress_task(self, task: Task, tasks: list[Task]) -> int:
        if not self._has_direct_children(task.Id, tasks):
            return 0 if task.Status == "Abort" else max(0, task.AssignedManHour)
        leaf_tasks = self._collect_leaf_tasks(base_task_id=task.Id, tasks=tasks)
        return sum(
            max(0, leaf_task.AssignedManHour)
            for leaf_task in leaf_tasks
            if leaf_task.Status != "Abort"
        )

    def _leaf_progress_on_date(
        self,
        leaf_task: Task,
        cumulative_minutes: float,
        snapshot_date: date,
    ) -> float:
        completion_date = leaf_task.CompletedAt.date() if leaf_task.CompletedAt else None
        if completion_date is None and leaf_task.Status == "Completion":
            completion_date = leaf_task.EndDate

        if completion_date is not None and snapshot_date >= completion_date:
            return 100.0

        return calculate_leaf_progress_rate(
            assigned_manhour=max(0, leaf_task.AssignedManHour),
            actual_manhour=max(0, round_half_up(cumulative_minutes)),
            status="DuringStart",
        )

    def _build_task_daily_snapshot_points(
        self,
        task: Task,
        tasks: list[Task],
        *,
        crash_zone_pct: float,
        today: date,
    ) -> list[dict[str, Any]]:
        leaf_tasks = self._collect_leaf_tasks(base_task_id=task.Id, tasks=tasks)
        if not leaf_tasks:
            leaf_tasks = [task]
        is_parent_mode = self._has_direct_children(task.Id, tasks)

        # 進捗グラフの左端は計画上の開始日（StartDate）を使う。
        snapshot_start = task.StartDate

        if task.Status == "Completion":
            completion_date = task.CompletedAt.date() if task.CompletedAt else task.EndDate
            snapshot_end = completion_date
        else:
            snapshot_end = today
        if snapshot_end < snapshot_start:
            snapshot_end = snapshot_start

        business_days = self._iter_business_days(snapshot_start, snapshot_end)
        if not business_days:
            business_days = [snapshot_start]

        planned_days = self._iter_business_days(snapshot_start, task.EndDate)
        planned_index = {day: idx for idx, day in enumerate(planned_days)}

        daily_minutes_by_leaf: dict[str, dict[date, float]] = {}
        daily_actual_by_date: dict[date, float] = defaultdict(float)
        for leaf_task in leaf_tasks:
            entries = self.repository.list_daily_task_actuals_by_task(
                task_id=leaf_task.Id,
                date_from=snapshot_start,
                date_to=snapshot_end,
            )
            day_map: dict[date, float] = defaultdict(float)
            for entry in entries:
                day_map[entry.WorkDate] += max(0.0, float(entry.TotalMinutes))
            daily_minutes_by_leaf[leaf_task.Id] = day_map
            for work_date, minutes in day_map.items():
                daily_actual_by_date[work_date] += minutes

        completion_assigned_by_date: dict[date, float] = defaultdict(float)
        for leaf_task in leaf_tasks:
            completion_date = leaf_task.CompletedAt.date() if leaf_task.CompletedAt else None
            if completion_date is None and leaf_task.Status == "Completion":
                completion_date = leaf_task.EndDate
            if completion_date is None:
                continue
            if completion_date < snapshot_start or completion_date > snapshot_end:
                continue
            completion_assigned_by_date[completion_date] += max(0, float(leaf_task.AssignedManHour))

        cumulative_by_leaf = {leaf_task.Id: 0.0 for leaf_task in leaf_tasks}
        assigned_minutes_total = self._assigned_total_for_progress_task(task, tasks)
        if assigned_minutes_total <= 0 and task.Status != "Abort":
            assigned_minutes_total = max(0, task.AssignedManHour)
        points: list[dict[str, Any]] = []

        holiday_dates = set(self.settings.calendar.holidays)
        actual_dates = sorted(work_date for work_date, minutes in daily_actual_by_date.items() if minutes > 0.0)
        actual_start_date = actual_dates[0] if actual_dates else None
        actual_last_date = actual_dates[-1] if actual_dates else None

        if is_parent_mode:
            speed_source_by_date = completion_assigned_by_date
        else:
            speed_source_by_date = daily_actual_by_date

        if actual_start_date and actual_last_date:
            actual_window_days = count_workdays_inclusive(
                start_date=actual_start_date,
                end_date=actual_last_date,
                holiday_dates=holiday_dates,
            )
            speed_total_window = 0.0
            for work_date in business_days:
                if actual_start_date <= work_date <= actual_last_date:
                    speed_total_window += max(0.0, float(speed_source_by_date.get(work_date, 0.0)))
            average_speed = (
                speed_total_window / float(actual_window_days)
                if actual_window_days > 0
                else 0.0
            )
        else:
            average_speed = 0.0

        cumulative_actual_minutes_total = 0.0
        cumulative_completed_assigned = 0.0
        for snapshot_date in business_days:
            weighted_progresses: list[LeafProgress] = []
            daily_actual_minutes_total = 0.0

            for leaf_task in leaf_tasks:
                daily_actual_minutes = daily_minutes_by_leaf[leaf_task.Id].get(snapshot_date, 0.0)
                cumulative_by_leaf[leaf_task.Id] += daily_actual_minutes
                daily_actual_minutes_total += daily_actual_minutes
            cumulative_actual_minutes_total += daily_actual_minutes_total

            if is_parent_mode:
                daily_completed_assigned = max(0.0, float(completion_assigned_by_date.get(snapshot_date, 0.0)))
                cumulative_completed_assigned += daily_completed_assigned
                if assigned_minutes_total > 0:
                    progress_rate = min(100.0, (cumulative_completed_assigned / float(assigned_minutes_total)) * 100.0)
                else:
                    progress_rate = 0.0
                v_actual = daily_completed_assigned
                progress_source_cumulative = cumulative_completed_assigned
            else:
                for leaf_task in leaf_tasks:
                    if leaf_task.Status == "Abort":
                        continue
                    assigned = max(0, leaf_task.AssignedManHour)
                    if assigned <= 0:
                        continue
                    weighted_progresses.append(
                        LeafProgress(
                            AssignedManHour=assigned,
                            ProgressRate=self._leaf_progress_on_date(
                                leaf_task=leaf_task,
                                cumulative_minutes=cumulative_by_leaf[leaf_task.Id],
                                snapshot_date=snapshot_date,
                            ),
                        )
                    )

                if weighted_progresses:
                    progress_rate = calculate_parent_progress_rate(weighted_progresses)
                else:
                    progress_rate = 100.0 if task.Status == "Completion" else 0.0
                v_actual = daily_actual_minutes_total
                progress_source_cumulative = cumulative_actual_minutes_total

            if not planned_days:
                planned_progress_rate = 100.0 if snapshot_date >= task.EndDate else 0.0
            elif snapshot_date in planned_index:
                planned_progress_rate = ((planned_index[snapshot_date] + 1) / len(planned_days)) * 100.0
            elif snapshot_date > planned_days[-1]:
                planned_progress_rate = 100.0
            else:
                planned_progress_rate = 0.0

            v_average = average_speed if (actual_start_date is not None and snapshot_date >= actual_start_date) else 0.0

            remain_days = float(
                count_workdays_inclusive(
                    start_date=snapshot_date,
                    end_date=task.EndDate,
                    holiday_dates=holiday_dates,
                )
            )
            if remain_days <= 0:
                v_critical = 0.0
                v_safe = 0.0
            else:
                remain_minutes = max(0.0, float(assigned_minutes_total) - progress_source_cumulative)
                safe_target_minutes = float(assigned_minutes_total) * (1.0 + max(0.0, crash_zone_pct) / 100.0)
                remain_safe_minutes = max(0.0, safe_target_minutes - progress_source_cumulative)
                v_critical = remain_minutes / remain_days
                v_safe = remain_safe_minutes / remain_days

            points.append(
                {
                    "SnapshotDate": snapshot_date,
                    "ProgressRate": float(progress_rate),
                    "PlannedProgressRate": float(planned_progress_rate),
                    "CumActualMinutes": float(cumulative_actual_minutes_total),
                    "VActual": float(v_actual if isfinite(v_actual) else 0.0),
                    "VAverage": float(v_average if isfinite(v_average) else 0.0),
                    "VCritical": float(v_critical if isfinite(v_critical) else 0.0),
                    "VSafe": float(v_safe if isfinite(v_safe) else 0.0),
                }
            )

        return points

    def _refresh_task_progress_snapshots(self, task_id: str, *, today: date | None = None) -> None:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("タスクが見つかりません")

        project = self.get_project()
        tasks = self.repository.list_tasks(project.Id)
        points = self._build_task_daily_snapshot_points(
            task=task,
            tasks=tasks,
            crash_zone_pct=project.CrashZonePct,
            today=today or date.today(),
        )
        self.repository.replace_daily_progress_snapshots(
            project_id=project.Id,
            task_id=task_id,
            points=points,
        )

    def get_task_progress_dashboard(
        self,
        task_id: str,
        *,
        today: date | None = None,
    ) -> TaskProgressDashboard:
        task = self.repository.get_task(task_id)
        if task is None:
            raise UserInputError("タスクが見つかりません")

        project = self.get_project()
        self._refresh_task_progress_snapshots(task_id=task_id, today=today)
        snapshots = self.repository.list_daily_progress_snapshots(task_id=task_id)
        tasks = self.repository.list_tasks(project.Id)
        assigned_total_minutes = self._assigned_total_for_progress_task(task, tasks)

        progress_points = [
            TaskProgressPoint(
                Date=item.SnapshotDate,
                ProgressRate=float(item.ProgressRate),
                PlannedProgressRate=float(item.PlannedProgressRate),
                ActualCumMinutes=float(item.CumActualMinutes),
            )
            for item in snapshots
        ]
        speed_points = [
            TaskSpeedPoint(
                Date=item.SnapshotDate,
                VActual=float(item.VActual),
                VAverage=float(item.VAverage),
                VCritical=float(item.VCritical),
                VSafe=float(item.VSafe),
            )
            for item in snapshots
        ]

        if speed_points:
            latest_speed = speed_points[-1]
            signal = decide_signal(
                v_actual=latest_speed.VActual,
                v_critical=latest_speed.VCritical,
                v_safe=latest_speed.VSafe,
            )
        else:
            signal = "Blue"

        return TaskProgressDashboard(
            TaskId=task.Id,
            TaskName=task.Name,
            Signal=signal,
            CrashZonePct=project.CrashZonePct,
            AssignedTotalMinutes=float(assigned_total_minutes),
            ProgressPoints=progress_points,
            SpeedPoints=speed_points,
        )

    def split_task(
        self,
        parent_task_id: str,
        operator_user_id: str,
        children: list[dict[str, Any]],
        parent_child_dependency_mode: str | None = None,
    ) -> list[str]:
        parent = self.repository.get_task(parent_task_id)
        if parent is None:
            raise UserInputError("親タスクが見つかりません")

        operator = self.repository.get_user(operator_user_id)
        if operator is None:
            raise UserInputError("操作ユーザーが見つかりません")
        if parent.AssigneeUserId != operator_user_id and not operator.IsTopManager:
            raise UserInputError("担当者本人またはTop Managerのみタスク分割できます")

        if not children:
            raise UserInputError("分割先が空です")

        today = date.today()
        warnings: list[str] = []

        project_tasks = self.repository.list_tasks(parent.ProjectId)
        children_by_parent: dict[str, list[Task]] = {}
        for task in project_tasks:
            if task.ParentTaskId is None:
                continue
            children_by_parent.setdefault(task.ParentTaskId, []).append(task)
        for key in list(children_by_parent):
            children_by_parent[key].sort(key=lambda t: (t.EndDate, t.CreateDate, t.Name, t.Id))

        existing_children = children_by_parent.get(parent.Id, [])
        existing_child_by_id = {task.Id: task for task in existing_children}
        is_resplit = len(existing_children) > 0
        if parent.Status == "Abort":
            raise UserInputError("中止タスクは分割できません")
        if parent.Status == "Completion" and not is_resplit:
            raise UserInputError("完了タスクは分割できません")

        parsed_rows: list[dict[str, Any]] = []
        actual_total = 0
        seen_existing_ids: set[str] = set()

        for child in children:
            try:
                assigned_value = int(child["AssignedManHour"])
            except (TypeError, ValueError) as exc:
                raise UserInputError("子タスクAssignedManHourは整数で指定してください") from exc
            if assigned_value < 0:
                raise UserInputError("子タスクAssignedManHourは0以上で指定してください")

            child_name = str(child["Name"]).strip()
            if not child_name:
                raise UserInputError("子タスク名を入力してください")

            child_end_date = child.get("EndDate", parent.EndDate)
            if isinstance(child_end_date, str):
                try:
                    child_end_date = date.fromisoformat(child_end_date)
                except ValueError as exc:
                    raise UserInputError("子タスク期限はYYYY-MM-DD形式で指定してください") from exc
            if not isinstance(child_end_date, date):
                raise UserInputError("子タスク期限が不正です")

            existing_task_id = child.get("ExistingTaskId")
            existing_task_id = str(existing_task_id).strip() if existing_task_id is not None else ""

            assignee_user_id: str
            actual_value: int

            if existing_task_id:
                if not is_resplit:
                    raise UserInputError("ExistingTaskIdは再分割時のみ指定できます")
                existing = existing_child_by_id.get(existing_task_id)
                if existing is None:
                    raise UserInputError("再分割対象に既存子タスク以外が含まれています")
                if existing_task_id in seen_existing_ids:
                    raise UserInputError("ExistingTaskIdが重複しています")
                seen_existing_ids.add(existing_task_id)

                assignee_user_id = existing.AssigneeUserId
                input_assignee = child.get("AssigneeUserId")
                if input_assignee not in (None, "", assignee_user_id):
                    raise UserInputError("既存子タスクの担当者は変更できません")

                direct_children = children_by_parent.get(existing.Id, [])
                if direct_children:
                    max_child_end_date = max(item.EndDate for item in direct_children)
                    if child_end_date < max_child_end_date:
                        warnings.append(
                            f"「{child_name}」の期限より後に完了する配下タスクがあります。配下日程も確認してください。"
                        )

                actual_value = existing.ActualManHour
                child_start_date = min(existing.StartDate, child_end_date)
                next_memo = str(child.get("Memo", "")).strip()
                if existing.Status == "Abort" and any(
                    (
                        child_name != existing.Name,
                        assigned_value != existing.AssignedManHour,
                        child_start_date != existing.StartDate,
                        child_end_date != existing.EndDate,
                        next_memo != (existing.Memo or "").strip(),
                    )
                ):
                    raise UserInputError("中止した既存子タスクは再分割で編集できません")
            else:
                assignee_user_id = str(child.get("AssigneeUserId") or parent.AssigneeUserId)
                assignee = self.repository.get_user(assignee_user_id)
                if assignee is None:
                    raise UserInputError("子タスク担当者が存在しません")

                try:
                    actual_value = int(child.get("ActualManHour", 0))
                except (TypeError, ValueError) as exc:
                    raise UserInputError("子タスクActualManHourは整数で指定してください") from exc
                if actual_value < 0:
                    raise UserInputError("子タスクActualManHourは0以上で指定してください")
                if is_resplit and actual_value != 0:
                    raise UserInputError("再分割時の新規タスクActualManHourは0固定です")
                child_start_date = min(today, child_end_date)

            parsed_rows.append(
                {
                    "ExistingTaskId": existing_task_id if existing_task_id else None,
                    "Name": child_name,
                    "AssignedManHour": assigned_value,
                    "ActualManHour": actual_value,
                    "EndDate": child_end_date,
                    "StartDate": child_start_date,
                    "Memo": str(child.get("Memo", "")).strip(),
                    "AssigneeUserId": assignee_user_id,
                    "PlannedDeliverables": child.get("PlannedDeliverables", []),
                    "IsRework": bool(child.get("IsRework", False)),
                }
            )

            actual_total += actual_value

        if is_resplit:
            existing_ids = {task.Id for task in existing_children}
            if seen_existing_ids != existing_ids:
                raise UserInputError("再分割時は既存子タスクをすべて含めてください")

        if not is_resplit:
            if actual_total != max(0, int(parent.ActualManHour)):
                raise UserInputError("子タスクActualManHour合計は親の実績MHと一致させてください")

        if is_resplit:
            assigned_updates: dict[str, int] = {
                str(row["ExistingTaskId"]): int(row["AssignedManHour"])
                for row in parsed_rows
                if row["ExistingTaskId"]
            }
            self.repository.update_task_assigned_manhours(assigned_updates)

            for row in parsed_rows:
                existing_task_id = row["ExistingTaskId"]
                if not existing_task_id:
                    continue
                self.repository.update_task_name(existing_task_id, row["Name"])
                self.repository.update_task_memo(existing_task_id, row["Memo"])
                self.repository.update_task_schedule(
                    existing_task_id,
                    row["StartDate"],
                    row["EndDate"],
                )

        for row in parsed_rows:
            if row["ExistingTaskId"]:
                continue

            created = self.repository.create_task(
                NewTaskInput(
                    ProjectId=parent.ProjectId,
                    ParentTaskId=parent.Id,
                    Name=row["Name"],
                    Type="Task",
                    AssigneeUserId=row["AssigneeUserId"],
                    CreatorUserId=operator_user_id,
                    StartDate=row["StartDate"],
                    EndDate=row["EndDate"],
                    AssignedManHour=row["AssignedManHour"],
                    PlannedDeliverables=row["PlannedDeliverables"],
                    IsRework=row["IsRework"],
                    Memo=row["Memo"],
                )
            )
            self.repository.update_task_actual_manhour(created.Id, int(row["ActualManHour"]))

        min_child_start = min(row["StartDate"] for row in parsed_rows)
        max_child_end = max(row["EndDate"] for row in parsed_rows)
        project = self.get_project()
        if min_child_start < project.StartDate or max_child_end > project.EndDate:
            warnings.append("子タスク期間がプロジェクト期間外です。必要に応じてプロジェクト期間を見直してください。")
        next_parent_start = min(parent.StartDate, min_child_start)
        next_parent_end = max(parent.EndDate, max_child_end)
        if next_parent_start != parent.StartDate or next_parent_end != parent.EndDate:
            self.repository.update_task_schedule(parent.Id, next_parent_start, next_parent_end)
            self._queue_iquavis_schedule_sync(parent.Id)
            parent.StartDate = next_parent_start
            parent.EndDate = next_parent_end
            self._expand_ancestor_schedules(parent, project_tasks, next_parent_start, next_parent_end)
            warnings.append("子タスクが収まるよう、親タスクと祖先タスクの期間を自動調整しました。")

        self.repository.update_task_type(parent_task_id, "GroupTask")
        self.repository.update_task_actual_manhour(parent_task_id, 0)

        if parent_child_dependency_mode is not None:
            normalized_mode = self._parse_child_dependency_mode(parent_child_dependency_mode)
            self.repository.update_task_child_dependency_mode(parent_task_id, normalized_mode)

        changed_task_ids = self.recompute_all_task_progress()
        changed_task_ids.add(parent_task_id)
        for row in parsed_rows:
            existing_task_id = row.get("ExistingTaskId")
            if existing_task_id:
                self._queue_iquavis_schedule_sync(str(existing_task_id))
        self._queue_iquavis_progress_sync_for_tasks(changed_task_ids)
        return warnings

    def get_active_timer_info(self, user_id: str) -> ActiveTimerInfo | None:
        timer = self.repository.get_active_timer(user_id)
        if timer is None:
            return None
        task = self.repository.get_task(timer.TaskId)
        task_name = task.Name if task else "(不明)"
        return ActiveTimerInfo(
            TimerId=timer.Id,
            TaskId=timer.TaskId,
            TaskName=task_name,
            StartedAt=timer.StartAt,
            IsOjt=timer.IsOjt,
            TeacherUserId=timer.TeacherUserId,
        )

    def _monthly_education_rate(self, user_id: str, year_month: str) -> float:
        value = self.repository.get_monthly_education_rate(user_id, year_month)
        if value is None:
            return self.settings.team.default_education_rate_monthly
        return value

    def _daily_minutes(
        self,
        sessions: list[TimerSession],
        *,
        include_ojt: bool | None,
        teacher_user_id: str | None = None,
        now: datetime | None = None,
    ) -> int:
        seconds = 0.0
        for session in sessions:
            if include_ojt is not None and session.IsOjt != include_ojt:
                continue
            if teacher_user_id is not None and session.TeacherUserId != teacher_user_id:
                continue
            seconds += self._timer_seconds(session, now=now)
        return seconds_to_rounded_minutes(seconds)

    def get_today_summary(self, user_id: str) -> TodaySummary:
        user = self.repository.get_user(user_id)
        if user is None:
            raise UserInputError("繝ｦ繝ｼ繧ｶ繝ｼ縺瑚ｦ九▽縺九ｊ縺ｾ縺帙ｓ")

        today = date.today()
        now = datetime.now()
        sessions = self.repository.list_timers_by_user_and_day(user_id, today)
        active = self.repository.get_active_timer(user_id)

        if active is not None and active.StartAt.date() == today:
            sessions_with_active = sessions + [active]
        else:
            sessions_with_active = sessions

        starts = [s.StartAt for s in sessions_with_active]
        ends = [(s.EndAt or now) for s in sessions_with_active]

        work_minutes = 0
        if starts and ends:
            work_minutes = max(0, int((max(ends) - min(starts)).total_seconds() // 60))

        year_month = today.strftime("%Y-%m")
        budget = calculate_daily_budget(
            work_minutes=work_minutes,
            free_time_rate=user.FreeTimeRate,
            education_rate_monthly=self._monthly_education_rate(user_id, year_month),
            regular_minutes_per_day=self.settings.app.regular_minutes_per_day,
        )

        actual_work = self._daily_minutes(
            sessions_with_active,
            include_ojt=False,
            now=now,
        )
        ojt_minutes = self._daily_minutes(
            sessions_with_active,
            include_ojt=True,
            now=now,
        )

        remaining = max(0, budget.WorkBudgetMinutes - actual_work - ojt_minutes)

        alerts: list[str] = []
        infeasible = self.get_infeasibility_alerts().get(user_id)
        if infeasible and infeasible["IsInfeasible"]:
            alerts.append(f"個人不成立アラート: ギャップ {infeasible['GapMinutes']}分")

        return TodaySummary(
            WorkMinutes=work_minutes,
            Budget=budget,
            ActualWorkMinutes=actual_work,
            OjtMinutes=ojt_minutes,
            RemainingWorkMinutes=remaining,
            ActiveTimer=self.get_active_timer_info(user_id),
            Alerts=alerts,
        )

    def set_monthly_education_rate(self, user_id: str, year_month: str, rate: float) -> None:
        if rate < 0 or rate > 1:
            raise UserInputError("謨呵ご豈皮紫縺ｯ0.0縲・.0縺ｧ險ｭ螳壹＠縺ｦ縺上□縺輔＞")
        self.repository.upsert_monthly_education_rate(user_id=user_id, year_month=year_month, rate=rate)

    def _next_month_start(self, value: date) -> date:
        if value.month == 12:
            return date(value.year + 1, 1, 1)
        return date(value.year, value.month + 1, 1)

    def _ranking_period_bounds(self, year_month: str, period_months: int) -> tuple[date, date]:
        try:
            year, month = map(int, year_month.split("-"))
            anchor_month_start = date(year, month, 1)
        except Exception as exc:
            raise UserInputError("ランキング期間は YYYY-MM 形式で指定してください") from exc

        if period_months not in {1, 3, 6, 12}:
            raise UserInputError("ランキング期間は 1, 3, 6, 12 か月のみ指定できます")

        anchor_total_months = (anchor_month_start.year * 12) + (anchor_month_start.month - 1)
        period_start_total_months = anchor_total_months - (period_months - 1)
        period_start_year = period_start_total_months // 12
        period_start_month = (period_start_total_months % 12) + 1
        period_start = date(period_start_year, period_start_month, 1)

        if anchor_month_start.month == 12:
            period_end_exclusive = date(anchor_month_start.year + 1, 1, 1)
        else:
            period_end_exclusive = date(anchor_month_start.year, anchor_month_start.month + 1, 1)
        return period_start, period_end_exclusive

    def _ranking_completion_date(self, task: Task) -> date | None:
        if task.Status != "Completion":
            return None
        if task.CompletedAt is not None:
            return task.CompletedAt.date()
        return task.EndDate

    def _completed_tasks_for_ranking(
        self,
        *,
        tasks: list[Task],
        period_start: date,
        period_end_exclusive: date,
    ) -> list[Task]:
        completed_tasks: list[Task] = []
        for task in tasks:
            completion_date = self._ranking_completion_date(task)
            if completion_date is None:
                continue
            if period_start <= completion_date < period_end_exclusive:
                completed_tasks.append(task)
        return completed_tasks

    def _new_ranking_accumulator(self) -> RankingAccumulator:
        return RankingAccumulator(
            OutputVolumeByUser=defaultdict(int),
            EffortByUser=defaultdict(int),
            EducationOutputByUser=defaultdict(int),
            EducationEffortByUser=defaultdict(int),
            TeachingSecondsByUser=defaultdict(float),
        )

    def _apply_ranking_task_summary(self, accumulator: RankingAccumulator, summary: RankingTaskSummary) -> None:
        accumulator.OutputVolumeByUser[summary.AssigneeUserId] += summary.AssignedMinutes
        accumulator.EffortByUser[summary.AssigneeUserId] += summary.ActualMinutes

        for teacher_user_id, seconds in summary.TeachingSecondsByUser:
            accumulator.TeachingSecondsByUser[teacher_user_id] += seconds

        for teacher_user_id in summary.MajorityTeacherUserIds:
            accumulator.EducationOutputByUser[teacher_user_id] += summary.AssignedMinutes
            accumulator.EducationEffortByUser[teacher_user_id] += summary.ActualMinutes

    def _revert_ranking_task_summary(self, accumulator: RankingAccumulator, summary: RankingTaskSummary) -> None:
        accumulator.OutputVolumeByUser[summary.AssigneeUserId] -= summary.AssignedMinutes
        accumulator.EffortByUser[summary.AssigneeUserId] -= summary.ActualMinutes

        for teacher_user_id, seconds in summary.TeachingSecondsByUser:
            accumulator.TeachingSecondsByUser[teacher_user_id] -= seconds

        for teacher_user_id in summary.MajorityTeacherUserIds:
            accumulator.EducationOutputByUser[teacher_user_id] -= summary.AssignedMinutes
            accumulator.EducationEffortByUser[teacher_user_id] -= summary.ActualMinutes

    def _build_metric_row_from_accumulator(self, user: User, accumulator: RankingAccumulator) -> MetricRow:
        output_volume = accumulator.OutputVolumeByUser.get(user.Id, 0)
        effort_minutes = accumulator.EffortByUser.get(user.Id, 0)
        education_output = accumulator.EducationOutputByUser.get(user.Id, 0)
        education_effort = accumulator.EducationEffortByUser.get(user.Id, 0)
        teaching_time = seconds_to_rounded_minutes(accumulator.TeachingSecondsByUser.get(user.Id, 0.0))
        return MetricRow(
            UserId=user.Id,
            UserName=display_user_name(user),
            OutputVolume=output_volume,
            WorkSpeed=calculate_speed_score(output_volume, effort_minutes),
            EducationEfficiency=calculate_speed_score(
                education_output,
                education_effort,
                expected_effort_factor=0.5,
            ),
            TeachingTime=teaching_time,
        )

    def _build_ranking_task_summaries_for_bounds(
        self,
        *,
        period_start: date,
        period_end_exclusive: date,
    ) -> tuple[list[User], list[RankingTaskSummary]]:
        project = self.get_project()
        users = self.repository.list_users(active_only=True)
        tasks = self.repository.list_tasks(project.Id)
        completed_tasks = self._completed_tasks_for_ranking(
            tasks=tasks,
            period_start=period_start,
            period_end_exclusive=period_end_exclusive,
        )
        children_by_parent = self._task_children_by_parent(tasks)
        aggregates = self._build_task_aggregates(tasks)

        task_ids = [task.Id for task in completed_tasks]
        raw_worklogs = self.repository.list_raw_worklogs_by_tasks(task_ids)

        teacher_seconds_by_task: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        task_seconds_by_task: dict[str, float] = defaultdict(float)

        for worklog in raw_worklogs:
            duration_seconds = max(0.0, float(worklog.DurationSeconds))
            task_seconds_by_task[worklog.TaskId] += duration_seconds
            if not worklog.IsOjt or not worklog.TeacherUserId:
                continue
            teacher_seconds_by_task[worklog.TaskId][worklog.TeacherUserId] += duration_seconds

        summaries: list[RankingTaskSummary] = []
        for task in completed_tasks:
            direct_children = children_by_parent.get(task.Id, [])
            direct_child_assigned = sum(max(0, int(child.AssignedManHour)) for child in direct_children)
            assigned_minutes = max(0, int(task.AssignedManHour) - direct_child_assigned)
            child_actual_total = sum(
                max(0, int(aggregates[child.Id].ActualTotal))
                for child in direct_children
                if child.Id in aggregates
            )
            task_actual_total = aggregates[task.Id].ActualTotal if task.Id in aggregates else task.ActualManHour
            actual_minutes = max(0, int(task_actual_total) - child_actual_total)

            task_total_seconds = task_seconds_by_task.get(task.Id, 0.0)
            if task_total_seconds <= 0.0 and actual_minutes > 0:
                # 旧データで RawWorklog が無い場合でも、順位計算自体は継続できるようにする。
                task_total_seconds = float(actual_minutes * 60)

            teacher_seconds_map = teacher_seconds_by_task.get(task.Id, {})
            majority_teacher_user_ids = tuple(
                sorted(
                    teacher_user_id
                    for teacher_user_id, teacher_seconds in teacher_seconds_map.items()
                    if teacher_seconds > (task_total_seconds / 2.0)
                )
            )
            completion_date = self._ranking_completion_date(task)
            if completion_date is None:
                continue
            if assigned_minutes <= 0 and actual_minutes <= 0 and not teacher_seconds_map:
                continue
            summaries.append(
                RankingTaskSummary(
                    CompletionDate=completion_date,
                    AssigneeUserId=task.AssigneeUserId,
                    AssignedMinutes=assigned_minutes,
                    ActualMinutes=actual_minutes,
                    MajorityTeacherUserIds=majority_teacher_user_ids,
                    TeachingSecondsByUser=tuple(sorted(teacher_seconds_map.items())),
                )
            )
        summaries.sort(key=lambda summary: (summary.CompletionDate, summary.AssigneeUserId, summary.AssignedMinutes))
        return users, summaries

    def _build_ranking_task_summaries(self, year_month: str, period_months: int) -> tuple[list[User], list[RankingTaskSummary]]:
        period_start, period_end_exclusive = self._ranking_period_bounds(year_month, period_months)
        return self._build_ranking_task_summaries_for_bounds(
            period_start=period_start,
            period_end_exclusive=period_end_exclusive,
        )

    def _build_ranking_metrics(self, year_month: str, period_months: int) -> list[MetricRow]:
        users, summaries = self._build_ranking_task_summaries(year_month, period_months)
        accumulator = self._new_ranking_accumulator()
        for summary in summaries:
            self._apply_ranking_task_summary(accumulator, summary)

        rows: list[MetricRow] = []
        for user in users:
            rows.append(self._build_metric_row_from_accumulator(user, accumulator))
        return rows

    def get_rankings(self, self_user_id: str, year_month: str, period_months: int = 1) -> dict[str, list[Any]]:
        rows = self._build_ranking_metrics(year_month, period_months)
        self_user = self.repository.get_user(self_user_id)
        publish_all = bool(self_user and self_user.IsTopManager)

        def make(value_getter):
            entries = [
                RankEntry(UserId=row.UserId, UserName=row.UserName, Value=value_getter(row))
                for row in rows
            ]
            return apply_publication_rule(
                entries,
                self_user_id=self_user_id,
                publish_all=publish_all,
            )

        return {
            "OutputVolume": make(lambda row: float(row.OutputVolume)),
            "WorkSpeed": make(lambda row: row.WorkSpeed),
            "EducationEfficiency": make(lambda row: row.EducationEfficiency),
            "TeachingTime": make(lambda row: float(row.TeachingTime)),
        }

    def _month_start(self, value: date) -> date:
        return date(value.year, value.month, 1)

    def _build_ranking_anchor_months(self, selected_anchor_month: date) -> list[date]:
        project = self.get_project()
        project_start_month = self._month_start(project.StartDate)
        tasks = self.repository.list_tasks(project.Id)
        completion_months = [
            self._month_start(completion_date)
            for completion_date in (self._ranking_completion_date(task) for task in tasks)
            if completion_date is not None
        ]
        latest_month = max(completion_months, default=project_start_month)
        final_month = max(project_start_month, latest_month, selected_anchor_month)

        months: list[date] = []
        current = project_start_month
        while current <= final_month:
            months.append(current)
            current = self._next_month_start(current)
        return months

    def get_ranking_histories(
        self,
        self_user_id: str,
        year_month: str,
        period_months: int = 1,
    ) -> dict[str, list[RankingHistorySeries]]:
        self_user = self.repository.get_user(self_user_id)
        if self_user is None:
            raise UserInputError("ユーザーが見つかりません")

        try:
            selected_anchor_month = date.fromisoformat(f"{year_month}-01")
        except ValueError as exc:
            raise UserInputError("ランキング期間は YYYY-MM 形式で指定してください") from exc

        anchor_months = self._build_ranking_anchor_months(self._month_start(selected_anchor_month))

        users, all_summaries = self._build_ranking_task_summaries_for_bounds(
            period_start=anchor_months[0],
            period_end_exclusive=self._next_month_start(anchor_months[-1]),
        )
        summaries_by_month: dict[date, list[RankingTaskSummary]] = defaultdict(list)
        for summary in all_summaries:
            summaries_by_month[self._month_start(summary.CompletionDate)].append(summary)

        visible_users = users if self_user.IsTopManager else [user for user in users if user.Id == self_user_id]
        series_map: dict[str, dict[str, list[RankingHistoryPoint]]] = {
            "OutputVolume": {user.Id: [] for user in visible_users},
            "WorkSpeed": {user.Id: [] for user in visible_users},
            "EducationEfficiency": {user.Id: [] for user in visible_users},
            "TeachingTime": {user.Id: [] for user in visible_users},
        }

        accumulator = self._new_ranking_accumulator()
        active_months: list[date] = []
        for anchor_month in anchor_months:
            for summary in summaries_by_month.get(anchor_month, []):
                self._apply_ranking_task_summary(accumulator, summary)
            active_months.append(anchor_month)

            while len(active_months) > period_months:
                expired_month = active_months.pop(0)
                for summary in summaries_by_month.get(expired_month, []):
                    self._revert_ranking_task_summary(accumulator, summary)

            for user in visible_users:
                row = self._build_metric_row_from_accumulator(user, accumulator)
                series_map["OutputVolume"][user.Id].append(
                    RankingHistoryPoint(Date=anchor_month, Value=float(row.OutputVolume))
                )
                series_map["WorkSpeed"][user.Id].append(
                    RankingHistoryPoint(Date=anchor_month, Value=row.WorkSpeed)
                )
                series_map["EducationEfficiency"][user.Id].append(
                    RankingHistoryPoint(Date=anchor_month, Value=row.EducationEfficiency)
                )
                series_map["TeachingTime"][user.Id].append(
                    RankingHistoryPoint(Date=anchor_month, Value=float(row.TeachingTime))
                )

        result: dict[str, list[RankingHistorySeries]] = {}
        for metric_name, points_by_user in series_map.items():
            result[metric_name] = [
                RankingHistorySeries(
                    UserId=user.Id,
                    UserName=display_user_name(user),
                    IsSelf=user.Id == self_user_id,
                    Points=points_by_user[user.Id],
                )
                for user in visible_users
            ]
        return result

    def _projected_daily_budget_map(self, user: User, until: date) -> dict[date, int]:
        current = date.today()
        year_month = current.strftime("%Y-%m")
        education_rate = self._monthly_education_rate(user.Id, year_month)

        daily: dict[date, int] = {}
        while current <= until:
            budget = calculate_daily_budget(
                work_minutes=self.settings.app.regular_minutes_per_day,
                free_time_rate=user.FreeTimeRate,
                education_rate_monthly=education_rate,
                regular_minutes_per_day=self.settings.app.regular_minutes_per_day,
            )
            daily[current] = budget.WorkBudgetMinutes
            current += timedelta(days=1)
        return daily

    def get_infeasibility_alerts(self) -> dict[str, dict[str, Any]]:
        project = self.get_project()
        users = self.repository.list_users(active_only=True)
        tasks = self.repository.list_incomplete_tasks(project.Id)

        by_user: dict[str, dict[str, Any]] = {}
        team_tasks: list[RemainingTask] = []
        team_budget_by_date: dict[date, int] = {}

        for user in users:
            user_tasks = [t for t in tasks if t.AssigneeUserId == user.Id]
            remainings = [
                RemainingTask(
                    TaskId=t.Id,
                    TaskName=t.Name,
                    AssigneeUserId=t.AssigneeUserId,
                    DueDate=t.EndDate,
                    RemainingAssignedManHour=t.AssignedManHour,
                )
                for t in user_tasks
            ]

            max_due = max((t.EndDate for t in user_tasks), default=date.today())
            daily_budget = self._projected_daily_budget_map(user, until=max_due)
            result = detect_personal_infeasibility(remainings, daily_budget)
            by_user[user.Id] = {
                "IsInfeasible": result.IsInfeasible,
                "GapMinutes": result.GapMinutes,
                "Causes": result.Causes,
            }

            team_tasks.extend(remainings)
            for d, v in daily_budget.items():
                team_budget_by_date[d] = team_budget_by_date.get(d, 0) + v

        team_result = detect_team_infeasibility(team_tasks, team_budget_by_date)
        by_user["__team__"] = {
            "IsInfeasible": team_result.IsInfeasible,
            "GapMinutes": team_result.GapMinutes,
            "Causes": team_result.Causes,
        }
        return by_user

    def _assert_no_prevented_cycle(self, rework_task_id: str, prevented_by_task_id: str) -> None:
        current = prevented_by_task_id
        visited: set[str] = set()
        # prevented_by の連鎖を追い、循環参照を防ぐ。
        while current:
            if current == rework_task_id:
                raise UserInputError("prevented_by 縺ｫ蠕ｪ迺ｰ蜿ら・縺ｯ謖・ｮ壹〒縺阪∪縺帙ｓ")
            if current in visited:
                break
            visited.add(current)
            task = self.repository.get_task(current)
            if task is None:
                break
            current = task.PreventedByTaskId

    def set_rework_prevented_by(self, rework_task_id: str, prevented_by_task_id: str) -> None:
        self._assert_no_prevented_cycle(rework_task_id, prevented_by_task_id)
        self.repository.set_rework_link(rework_task_id, prevented_by_task_id)

    def list_rework_tasks(self) -> list[TaskView]:
        return [task for task in self.list_tasks() if task.IsRework]














