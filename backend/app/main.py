from __future__ import annotations

import asyncio
from contextlib import contextmanager
import hmac
import logging
from threading import Lock, RLock
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.api.charts import HAS_MATPLOTLIB, render_progress_chart_png
from app.api.user_facing_text import normalize_user_facing_text
from app.api.serializers import to_jsonable
from app.runtime import Runtime, build_runtime
from app.iquavis.sync import IquavisConflictPermissionError, IquavisConflictStaleError
from app.tms_core.domain.colony import ColonyPermissionError
from app.tms_core.services.core import UserInputError, task_revision

logger = logging.getLogger(__name__)


class TaskConflictError(Exception):
    """先着優先で後着の保存を拒否したときの例外。

    誰の変更とぶつかったのかを利用者どうしが話せるよう、相手の現在値を添えて返す。
    """

    def __init__(self, message: str, conflict: dict[str, Any] | None = None):
        super().__init__(message)
        self.conflict = conflict


_task_locks: dict[str, RLock] = {}
_task_locks_guard = Lock()
_task_mutations_lock = RLock()


def _task_lock(task_id: str) -> RLock:
    with _task_locks_guard:
        return _task_locks.setdefault(task_id, RLock())


@contextmanager
def _guard_task_mutation() -> Iterator[None]:
    """祖先集計や日程調整を含むタスク更新を、単一プロセス内で一貫して直列化する。"""
    with _task_mutations_lock:
        yield


def _run_guarded_task_mutation(action: Any) -> Any:
    """バックグラウンド処理もHTTP更新と同じ排他範囲で実行する。"""
    with _guard_task_mutation():
        return action()


def _operator_user_id(body: Any) -> str | None:
    """リクエストボディから操作者を取り出す。項目名がuser_id/operator_user_idで揺れている。"""
    for field_name in ("operator_user_id", "user_id"):
        value = getattr(body, field_name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _task_conflict_detail(rt: Runtime, task_id: str) -> dict[str, Any]:
    """競合相手の現在値をまとめる。UIで「誰が・何を」変えたのかを出すために使う。"""
    current = rt.repository.get_task(task_id)
    if current is None:
        return {"task_id": task_id}
    updated_by = (
        rt.repository.get_user(current.UpdatedByUserId) if current.UpdatedByUserId else None
    )
    assignee = rt.repository.get_user(current.AssigneeUserId) if current.AssigneeUserId else None
    return {
        "task_id": current.Id,
        "task_name": current.Name,
        "current_revision": task_revision(current),
        "updated_by_user_id": current.UpdatedByUserId,
        "updated_by_user_name": getattr(updated_by, "Name", None),
        "updated_at": current.UpdateDate.isoformat() if current.UpdateDate else None,
        "server": {
            "Name": current.Name,
            "Status": current.Status,
            "ProgressRate": float(current.ProgressRate),
            "StartDate": current.StartDate.isoformat(),
            "EndDate": current.EndDate.isoformat(),
            "AssignedManHour": int(current.AssignedManHour),
            "ActualManHour": int(current.ActualManHour),
            "Memo": current.Memo or "",
            "AssigneeUserId": current.AssigneeUserId,
            "AssigneeName": getattr(assignee, "Name", None),
            "TodayOverride": current.TodayOverride,
        },
    }


def _require_order_revision(expected_revision: str | None, current_revision: str) -> None:
    """取得後に並び順が変わっていた場合は、後着の保存を拒否する。"""
    if not expected_revision:
        raise TaskConflictError(
            "並び順のリビジョンがありません。最新内容を再読込してから、もう一度実行してください。"
        )
    if expected_revision != current_revision:
        raise TaskConflictError(
            "別のユーザーが先に並び順を更新しました。最新内容を再読込したため、変更内容を確認してもう一度実行してください。"
        )


@contextmanager
def _guard_task_revisions(
    rt: Runtime,
    expected_by_task_id: dict[str, str | None],
    *,
    operator_user_id: str | None = None,
) -> Iterator[None]:
    """複数タスクの更新を一定順序で直列化し、古い画面を基にした変更を拒否する。"""
    with _guard_task_mutation():
        locks = [_task_lock(task_id) for task_id in sorted(expected_by_task_id)]
        for lock in locks:
            lock.acquire()
        try:
            for task_id, expected_revision in expected_by_task_id.items():
                current = rt.repository.get_task(task_id)
                if current is None:
                    raise UserInputError("タスクが見つかりません")
                if expected_revision and task_revision(current) != expected_revision:
                    raise TaskConflictError(
                        "別のユーザーが先にこのタスクを更新しました。どちらの変更を残すか選んでください。",
                        _task_conflict_detail(rt, task_id),
                    )
            yield
            if operator_user_id:
                for task_id in expected_by_task_id:
                    rt.repository.mark_task_updated_by(task_id, operator_user_id)
        finally:
            for lock in reversed(locks):
                lock.release()


@contextmanager
def _guard_task_revision(
    rt: Runtime,
    task_id: str,
    expected_revision: str | None,
    *,
    operator_user_id: str | None = None,
) -> Iterator[None]:
    """同一タスクの更新を直列化し、古い画面を基にした変更は先着優先で拒否する。"""
    with _guard_task_revisions(rt, {task_id: expected_revision}, operator_user_id=operator_user_id):
        yield


def _task_mutation_payload(
    rt: Runtime,
    task_id: str,
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    current = rt.repository.get_task(task_id)
    return {
        "status": "ok",
        "revision": task_revision(current) if current is not None else None,
        "warnings": warnings or [],
    }

ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_ROOT_DIR = ROOT_DIR.parent / "frontend"
FRONTEND_DIST_DIR = FRONTEND_ROOT_DIR / "dist"

runtime: Runtime | None = None
background_task: asyncio.Task | None = None
server_started_at: datetime | None = None


def get_runtime() -> Runtime:
    global runtime
    if runtime is None:
        runtime = build_runtime(ROOT_DIR)
    return runtime


def _sanitize_sync_status_message(message: str) -> str:
    detail = normalize_user_facing_text(message)
    lowered = detail[:1024].lower()
    if (
        "<!doctype" in lowered
        or "<html" in lowered
        or "dns look up failed" in lowered
        or "iQUAVIS APIに接続できません" in detail
        or "ネットワーク/DNS/プロキシ設定" in detail
    ):
        return ""
    return detail


def _sync_status_payload(event: Any, *, started_at: datetime | None) -> dict[str, Any]:
    if event is None:
        return {"message": "", "is_error": False, "recorded_at": None}
    if started_at is not None and event.recorded_at < started_at:
        return {"message": "", "is_error": False, "recorded_at": None}
    return {
        "message": _sanitize_sync_status_message(event.message),
        "is_error": event.is_error,
        "recorded_at": event.recorded_at.isoformat(),
    }


class PasswordCheckBody(BaseModel):
    user_id: str
    password: str = ""


class TodayToggleBody(BaseModel):
    user_id: str
    enabled: bool
    expected_revision: str | None = None


class TodayReorderBody(BaseModel):
    user_id: str
    ordered_task_ids: list[str]
    expected_order_revision: str | None = None


class WbsMoveBody(BaseModel):
    user_id: str
    direction: int
    expected_order_revision: str | None = None


class WbsReorderBody(BaseModel):
    user_id: str
    ordered_task_ids: list[str]
    expected_order_revision: str | None = None


class WbsSiblingReorderBody(BaseModel):
    user_id: str
    parent_task_id: str | None = None
    ordered_task_ids: list[str]
    expected_order_revision: str | None = None


class ColonyRoleBody(BaseModel):
    user_id: str
    target_user_id: str
    role: str


class ColonyRoomCreateBody(BaseModel):
    user_id: str
    parent_team_id: str | None = None
    name: str
    emblem: str | None = None


class ColonyRoomUpdateBody(BaseModel):
    user_id: str
    name: str | None = None
    emblem: str | None = None
    leader_user_id: str | None = None
    clear_leader: bool = False
    parent_team_id: str | None = None
    move_to_root: bool = False


class ColonyActorBody(BaseModel):
    user_id: str


class ColonyMemberBody(BaseModel):
    user_id: str
    member_user_id: str


class ColonyPolicyBody(BaseModel):
    user_id: str
    kind: str = "text"
    text: str = ""
    x: float = 0
    y: float = 0
    size: float = 1
    points: list[list[float]] = Field(default_factory=list)


class ColonyPolicyUpdateBody(BaseModel):
    user_id: str
    text: str | None = None
    x: float | None = None
    y: float | None = None


class ColonySweetBody(BaseModel):
    user_id: str
    area_task_id: str
    name: str
    size: int
    assigned_manhour: int
    end_date: str


class ColonyAreaBody(BaseModel):
    user_id: str
    name: str
    end_date: str


class ColonyAssignBody(BaseModel):
    user_id: str
    assignee_user_id: str
    expected_revision: str | None = None


class TaskCreateBody(BaseModel):
    name: str
    assignee_user_id: str
    creator_user_id: str
    assigned_manhour: int
    end_date: str
    parent_task_id: str | None = None
    planned_deliverables: list[dict[str, str]] = Field(default_factory=list)
    is_rework: bool = False
    memo: str = ""


class TaskMemoBody(BaseModel):
    user_id: str
    memo: str
    expected_revision: str | None = None


class TaskActualManHourBody(BaseModel):
    user_id: str
    actual_manhour: int
    expected_revision: str | None = None


class TaskAssignedManHourBody(BaseModel):
    user_id: str
    assigned_manhour: int
    expected_revision: str | None = None


class TaskAssignmentBody(BaseModel):
    user_id: str
    assignee_user_id: str
    end_date: str
    expected_revision: str | None = None


class TaskAssigneeBody(BaseModel):
    user_id: str
    assignee_user_id: str
    expected_revision: str | None = None


class TaskScheduleBody(BaseModel):
    user_id: str
    start_date: str
    end_date: str
    expected_revision: str | None = None


class TaskChildDependencyModeBody(BaseModel):
    user_id: str
    child_dependency_mode: str
    expected_revision: str | None = None


class TaskParentBody(BaseModel):
    user_id: str
    parent_task_id: str | None = None
    expected_revision: str | None = None


class BulkTaskAssigneeRow(BaseModel):
    task_id: str
    assignee_user_id: str
    expected_revision: str | None = None


class BulkTaskAssigneeBody(BaseModel):
    user_id: str
    updates: list[BulkTaskAssigneeRow]


class BulkTaskFieldsRow(BaseModel):
    task_id: str
    name: str
    assigned_manhour: int
    memo: str
    end_date: str
    assignee_user_id: str
    expected_revision: str | None = None


class BulkTaskFieldsBody(BaseModel):
    user_id: str
    parent_task_id: str
    expected_parent_revision: str | None = None
    updates: list[BulkTaskFieldsRow]


class OutputBody(BaseModel):
    user_id: str
    title: str
    url: str
    expected_revision: str | None = None


class SplitChildBody(BaseModel):
    ExistingTaskId: str | None = None
    ExpectedRevision: str | None = None
    Name: str
    AssignedManHour: int
    ActualManHour: int = 0
    EndDate: str
    Memo: str = ""
    AssigneeUserId: str | None = None


class SplitBody(BaseModel):
    user_id: str
    children: list[SplitChildBody]
    parent_child_dependency_mode: str | None = None
    expected_revision: str | None = None


class TimerStartBody(BaseModel):
    user_id: str
    task_id: str
    is_ojt: bool = False
    teacher_user_id: str | None = None


class TimerUserBody(BaseModel):
    user_id: str
    expected_revision: str | None = None


class TimerAddActualBody(BaseModel):
    user_id: str
    task_id: str
    started_at_ms: int
    ended_at_ms: int
    is_ojt: bool = False
    teacher_user_id: str | None = None


class EducationRateBody(BaseModel):
    user_id: str
    year_month: str
    rate: float


class CrashZoneBody(BaseModel):
    value: float


class ReworkLinkBody(BaseModel):
    rework_task_id: str
    prevented_by_task_id: str


class DailyActualFinalizeBody(BaseModel):
    force: bool = False


class IQuavisSyncExclusionBody(BaseModel):
    operator_user_id: str
    root_task_id: str | None = None
    root_task_ids: list[str] = Field(default_factory=list)
    excluded: bool


class IQuavisConflictResolveBody(BaseModel):
    operator_user_id: str
    direction: Literal["qolony_to_iquavis", "iquavis_to_qolony"]
    expected_recorded_at: str


class IQuavisConflictRefreshBody(BaseModel):
    operator_user_id: str


class IQuavisConflictHoldBody(BaseModel):
    operator_user_id: str
    hold: bool


class IQuavisAuthoritativeImportBody(BaseModel):
    operator_user_id: str
    ignored_iquavis_ids: list[str] = Field(default_factory=list)
    ignored_review_item_keys: list[str] = Field(default_factory=list)


class IQuavisAuthoritativeImportApplyBody(BaseModel):
    operator_user_id: str
    expected_workbook_fingerprint: str
    confirmation: str
    ignored_iquavis_ids: list[str] = Field(default_factory=list)
    ignored_review_item_keys: list[str] = Field(default_factory=list)


class TaskSubtreeAssigneeBody(BaseModel):
    operator_user_id: str
    assignee_user_id: str


class IQuavisForcedImportBody(BaseModel):
    operator_user_id: str


class IQuavisForcedImportApplyBody(BaseModel):
    operator_user_id: str
    expected_workbook_fingerprint: str
    confirmation: str


class MemberProfileBody(BaseModel):
    operator_user_id: str
    default_commit_rate: float
    condition_icon: str = "normal"
    formation_role: str = ""
    formation_position: str = ""
    formation_x: int = 50
    formation_y: int = 50
    display_order: int = 0
    title_tags: list[str] = Field(default_factory=list)
    special_abilities: list[str] = Field(default_factory=list)


class MemberIdentityBody(BaseModel):
    operator_user_id: str
    nickname: str
    is_active: bool


class MemberDayConfigBody(BaseModel):
    operator_user_id: str
    work_date: str
    is_vacation: bool = False
    leave_type: str = ""
    commit_rate_override: float | None = None
    note: str = ""


app = FastAPI(title="QOLONY API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(UserInputError)
async def _handle_user_error(request: Request, exc: UserInputError):  # type: ignore[override]
    detail = normalize_user_facing_text(str(exc))
    logger.warning("user input error: %s %s -> %s", request.method, request.url.path, detail)
    return JSONResponse(status_code=400, content={"detail": detail})


@app.exception_handler(ColonyPermissionError)
async def _handle_colony_permission(request: Request, exc: ColonyPermissionError):  # type: ignore[override]
    detail = str(exc)
    logger.info("colony permission: %s %s -> %s", request.method, request.url.path, detail)
    return JSONResponse(status_code=403, content={"detail": detail, "code": "colony_permission"})


@app.exception_handler(TaskConflictError)
async def _handle_task_conflict(request: Request, exc: TaskConflictError):  # type: ignore[override]
    detail = normalize_user_facing_text(str(exc))
    logger.info("task conflict: %s %s -> %s", request.method, request.url.path, detail)
    content: dict[str, Any] = {"detail": detail, "code": "task_conflict"}
    if getattr(exc, "conflict", None):
        content["conflict"] = exc.conflict
    return JSONResponse(status_code=409, content=content)


@app.on_event("startup")
async def _startup() -> None:
    global background_task, server_started_at
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server_started_at = datetime.now()
    logger.info("startup: begin runtime initialization")
    get_runtime()
    logger.info("startup: runtime initialized")
    _run_guarded_task_mutation(get_runtime().service.finalize_daily_actuals_if_due)
    logger.info("startup: daily actual finalize check completed")
    background_task = asyncio.create_task(_background_loop())
    logger.info("startup: QOLONY is ready")


@app.on_event("shutdown")
async def _shutdown() -> None:
    global background_task
    if background_task is not None:
        background_task.cancel()
        try:
            await background_task
        except asyncio.CancelledError:
            pass
        background_task = None
    if runtime is not None:
        runtime.iquavis_sync.close()


async def _background_loop() -> None:
    while True:
        try:
            now = datetime.now()
            rt = get_runtime()
            if now.hour >= 3:
                await asyncio.to_thread(
                    _run_guarded_task_mutation,
                    lambda: rt.service.finalize_daily_actuals_if_due(now=now),
                )
            await asyncio.to_thread(rt.service.run_daily_snapshot_if_due, now=now)
            await asyncio.to_thread(
                _run_guarded_task_mutation,
                lambda: rt.iquavis_sync.run_background_tick(now=now),
            )
        except Exception:
            logger.exception("background loop error")
        await asyncio.sleep(60)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/meta")
def get_meta() -> dict[str, Any]:
    rt = get_runtime()
    try:
        top = rt.service.get_top_manager()
    except RuntimeError:
        top = None
    project = rt.service.get_project()
    users = rt.service.list_users(active_only=True)
    return {
        "users": [
            {
                "Id": user.Id,
                "Name": getattr(user, "Nickname", "") or user.Name,
                "Nickname": getattr(user, "Nickname", "") or user.Name,
                "FullName": getattr(user, "FullName", "") or "",
                "LoginId": user.LoginId,
                "IQuavisUserId": user.IQuavisUserId,
                "IsTopManager": bool(user.IsTopManager),
                "IsActive": bool(getattr(user, "IsActive", True)),
                "FreeTimeRate": float(user.FreeTimeRate),
                # パスワードそのものは画面へ送らない。照合は /api/auth/verify でサーバーが行う。
                "HasPassword": bool(rt.settings.users.passwords.get(user.Name, "").strip()),
                "CreateDate": user.CreateDate.isoformat() if user.CreateDate else None,
            }
            for user in users
        ],
        "top_manager_id": top.Id if top else "",
        "project": {
            "Id": project.Id,
            "Name": project.Name,
            "StartDate": project.StartDate.isoformat(),
            "EndDate": project.EndDate.isoformat(),
            "CrashZonePct": float(project.CrashZonePct),
            "CreateDate": project.CreateDate.isoformat() if project.CreateDate else None,
        },
        "split_templates": to_jsonable(rt.settings.split_templates),
        # iQUAVIS 連携は任意。WebAPI か Excel マクロブックのどちらかが設定されているときだけ画面に出す。
        "iquavis": {
            "available": bool(
                getattr(getattr(rt.settings, "iquavis", None), "enabled", False)
                or getattr(getattr(rt.settings, "iquavis", None), "editor_workbook_path", None)
            ),
        },
        "has_matplotlib": HAS_MATPLOTLIB,
        "ui": {
            "language": rt.settings.ui.language,
            "messages": rt.settings.ui.messages,
            "fallback_messages": rt.settings.ui.fallback_messages,
        },
    }


@app.post("/api/auth/verify")
def verify_user_password(body: PasswordCheckBody) -> dict[str, bool]:
    """利用者切替のパスワードを照合する。

    LAN 内で管理画面へ入る前の簡易な確認であり、API 全体を守る認証ではない。
    以前は全員のパスワードを /api/meta で画面へ送り、画面側で比べていた。
    """
    rt = get_runtime()
    user = next((u for u in rt.service.list_users(active_only=True) if u.Id == body.user_id), None)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    required = rt.settings.users.passwords.get(user.Name, "").strip()
    if not required:
        return {"ok": True}
    return {"ok": hmac.compare_digest(body.password.encode("utf-8"), required.encode("utf-8"))}


@app.get("/api/sync/status")
def get_sync_status() -> dict[str, Any]:
    event = get_runtime().iquavis_sync.get_latest_event()
    return _sync_status_payload(event, started_at=server_started_at)


def _parse_iso_date(value: str, *, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise UserInputError(f"{label}はYYYY-MM-DD形式で指定してください") from exc


@app.get("/api/members")
def list_members(
    date_from: str | None = Query(None),
    date_to: str | None = Query(None),
) -> list[dict[str, Any]]:
    rt = get_runtime()
    today = date.today()
    start = _parse_iso_date(date_from, label="開始日") if date_from else today - timedelta(days=7)
    end = _parse_iso_date(date_to, label="終了日") if date_to else today + timedelta(days=30)
    return to_jsonable(rt.service.list_members(date_from=start, date_to=end))


@app.post("/api/members/{user_id}/profile")
def update_member_profile(user_id: str, body: MemberProfileBody) -> dict[str, str]:
    rt = get_runtime()
    rt.service.update_member_profile(
        user_id=user_id,
        operator_user_id=body.operator_user_id,
        default_commit_rate=body.default_commit_rate,
        condition_icon=body.condition_icon,
        formation_role=body.formation_role,
        formation_position=body.formation_position,
        formation_x=body.formation_x,
        formation_y=body.formation_y,
        display_order=body.display_order,
        title_tags=body.title_tags,
        special_abilities=body.special_abilities,
    )
    return {"status": "ok"}


@app.post("/api/members/{user_id}/identity")
def update_member_identity(user_id: str, body: MemberIdentityBody) -> dict[str, str]:
    rt = get_runtime()
    rt.service.update_member_identity(
        user_id=user_id,
        operator_user_id=body.operator_user_id,
        nickname=body.nickname,
        is_active=body.is_active,
    )
    return {"status": "ok"}


@app.post("/api/members/{user_id}/day-config")
def upsert_member_day_config(user_id: str, body: MemberDayConfigBody) -> dict[str, str]:
    rt = get_runtime()
    rt.service.upsert_member_day_config(
        user_id=user_id,
        operator_user_id=body.operator_user_id,
        work_date=_parse_iso_date(body.work_date, label="対象日"),
        is_vacation=body.is_vacation,
        commit_rate_override=body.commit_rate_override,
        leave_type=body.leave_type,
        note=body.note,
    )
    return {"status": "ok"}


@app.post("/api/admin/iquavis-link-targets/members/import")
def import_member_link_targets(workbook_path: str | None = None) -> dict[str, Any]:
    rt = get_runtime()
    return rt.service.import_member_link_targets(workbook_path=workbook_path)


@app.post("/api/admin/iquavis-link-targets/members/export")
def export_member_link_targets(workbook_path: str | None = None) -> dict[str, Any]:
    rt = get_runtime()
    return rt.service.export_member_link_targets(workbook_path=workbook_path)


@app.get("/api/tasks")
def list_tasks() -> list[dict[str, Any]]:
    rt = get_runtime()
    return to_jsonable(rt.service.list_tasks())


@app.get("/api/tasks/my")
def list_my_tasks(user_id: str = Query(...)) -> list[dict[str, Any]]:
    rt = get_runtime()
    return to_jsonable(rt.service.list_my_tasks(user_id))


@app.get("/api/tasks/today")
def list_today_tasks(user_id: str = Query(...)) -> list[dict[str, Any]]:
    rt = get_runtime()
    return to_jsonable(rt.service.list_today_tasks(user_id))


@app.post("/api/tasks/{task_id}/today")
def set_today_toggle(task_id: str, body: TodayToggleBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        rt.service.set_today_toggle(body.user_id, task_id, body.enabled)
        return _task_mutation_payload(rt, task_id)


@app.post("/api/tasks/today/reorder")
def reorder_today(body: TodayReorderBody) -> dict[str, str]:
    rt = get_runtime()
    with _guard_task_mutation():
        _require_order_revision(body.expected_order_revision, rt.service.today_order_revision(body.user_id))
        order_revision = rt.service.reorder_today_tasks(body.user_id, body.ordered_task_ids)
    return {"status": "ok", "order_revision": order_revision}


@app.post("/api/tasks/{task_id}/wbs/move")
def move_wbs_task(task_id: str, body: WbsMoveBody) -> dict[str, str]:
    rt = get_runtime()
    with _guard_task_mutation():
        _require_order_revision(body.expected_order_revision, rt.service.wbs_task_sibling_order_revision(task_id))
        order_revision = rt.service.move_wbs_task(
            operator_user_id=body.user_id,
            task_id=task_id,
            direction=body.direction,
        )
    return {"status": "ok", "order_revision": order_revision}


@app.post("/api/tasks/wbs/reorder")
def reorder_wbs_tasks(body: WbsReorderBody) -> dict[str, str]:
    rt = get_runtime()
    with _guard_task_mutation():
        _require_order_revision(body.expected_order_revision, rt.service.wbs_order_revision())
        order_revision = rt.service.reorder_wbs_tasks(
            operator_user_id=body.user_id,
            ordered_task_ids=body.ordered_task_ids,
        )
    return {"status": "ok", "order_revision": order_revision}


@app.post("/api/tasks/wbs/reorder-siblings")
def reorder_wbs_sibling_tasks(body: WbsSiblingReorderBody) -> dict[str, str]:
    rt = get_runtime()
    with _guard_task_mutation():
        _require_order_revision(
            body.expected_order_revision,
            rt.service.wbs_sibling_order_revision(body.parent_task_id),
        )
        order_revision = rt.service.reorder_wbs_sibling_tasks(
            operator_user_id=body.user_id,
            parent_task_id=body.parent_task_id,
            ordered_task_ids=body.ordered_task_ids,
        )
    return {"status": "ok", "order_revision": order_revision}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    rt = get_runtime()
    return to_jsonable(rt.service.get_task(task_id))


@app.post("/api/tasks/create")
def create_task(body: TaskCreateBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_mutation():
        warnings = rt.service.create_task(
            name=body.name,
            assignee_user_id=body.assignee_user_id,
            creator_user_id=body.creator_user_id,
            assigned_manhour=body.assigned_manhour,
            end_date=date.fromisoformat(body.end_date),
            parent_task_id=body.parent_task_id,
            planned_deliverables=body.planned_deliverables,
            is_rework=body.is_rework,
            memo=body.memo,
        )
    return {"status": "ok", "warnings": warnings}


@app.post("/api/tasks/{task_id}/memo")
def set_task_memo(task_id: str, body: TaskMemoBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        rt.service.set_task_memo(task_id, body.memo, operator_user_id=body.user_id)
        return _task_mutation_payload(rt, task_id)


@app.post("/api/tasks/{task_id}/actual-manhour")
def set_task_actual_manhour(task_id: str, body: TaskActualManHourBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        rt.service.set_task_actual_manhour(
            task_id=task_id,
            actual_manhour=body.actual_manhour,
            operator_user_id=body.user_id,
        )
        return _task_mutation_payload(rt, task_id)


@app.post("/api/tasks/{task_id}/assigned-manhour")
def set_task_assigned_manhour(task_id: str, body: TaskAssignedManHourBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        rt.service.set_root_task_assigned_manhour(
            task_id=task_id,
            assigned_manhour=body.assigned_manhour,
            operator_user_id=body.user_id,
        )
        return _task_mutation_payload(rt, task_id)


@app.post("/api/tasks/{task_id}/assignment")
def update_task_assignment(task_id: str, body: TaskAssignmentBody) -> dict[str, Any]:
    rt = get_runtime()
    try:
        end_date = date.fromisoformat(body.end_date)
    except ValueError as exc:
        raise UserInputError("期限はYYYY-MM-DD形式で指定してください") from exc

    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        warnings = rt.service.update_task_assignee_and_end_date(
            task_id=task_id,
            assignee_user_id=body.assignee_user_id,
            end_date=end_date,
            operator_user_id=body.user_id,
        )
        return _task_mutation_payload(rt, task_id, warnings=warnings)


@app.post("/api/tasks/{task_id}/assignee")
def update_task_assignee(task_id: str, body: TaskAssigneeBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        rt.service.update_task_assignee(
            task_id=task_id,
            assignee_user_id=body.assignee_user_id,
            operator_user_id=body.user_id,
        )
        return _task_mutation_payload(rt, task_id)


@app.post("/api/tasks/{task_id}/schedule")
def update_task_schedule(task_id: str, body: TaskScheduleBody) -> dict[str, Any]:
    rt = get_runtime()
    try:
        start_date = date.fromisoformat(body.start_date)
        end_date = date.fromisoformat(body.end_date)
    except ValueError as exc:
        raise UserInputError("開始日と期限はYYYY-MM-DD形式で指定してください") from exc

    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        warnings = rt.service.update_task_schedule(
            task_id=task_id,
            start_date=start_date,
            end_date=end_date,
            operator_user_id=body.user_id,
        )
        return _task_mutation_payload(rt, task_id, warnings=warnings)


@app.post("/api/tasks/{task_id}/child-dependency-mode")
def update_task_child_dependency_mode(task_id: str, body: TaskChildDependencyModeBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        rt.service.update_task_child_dependency_mode(
            task_id=task_id,
            child_dependency_mode=body.child_dependency_mode,
            operator_user_id=body.user_id,
        )
        return _task_mutation_payload(rt, task_id)


@app.post("/api/tasks/{task_id}/parent")
def update_task_parent(task_id: str, body: TaskParentBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        warnings = rt.service.update_task_parent(
            task_id=task_id,
            parent_task_id=body.parent_task_id,
            operator_user_id=body.user_id,
        )
        return _task_mutation_payload(rt, task_id, warnings=warnings)


@app.post("/api/tasks/{task_id}/subtree-assignee")
def reassign_task_subtree_assignee(task_id: str, body: TaskSubtreeAssigneeBody) -> dict[str, Any]:
    rt = get_runtime()
    # サブツリー全体を1つの排他区間で書き換える。途中に別の更新が挟まると担当が混ざる。
    with _guard_task_mutation():
        return rt.service.reassign_task_subtree_assignee(
            root_task_id=task_id,
            assignee_user_id=body.assignee_user_id,
            operator_user_id=body.operator_user_id,
        )


@app.post("/api/tasks/assignees/bulk")
def bulk_update_task_assignees(body: BulkTaskAssigneeBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_mutation():
        locks = [_task_lock(task_id) for task_id in sorted({row.task_id for row in body.updates})]
        for lock in locks:
            lock.acquire()
        try:
            result_slots: list[dict[str, Any] | None] = [None] * len(body.updates)
            valid_rows: list[tuple[int, BulkTaskAssigneeRow]] = []
            seen_task_ids: set[str] = set()
            for index, row in enumerate(body.updates):
                if row.task_id in seen_task_ids:
                    result_slots[index] = {
                        "task_id": row.task_id,
                        "assignee_user_id": row.assignee_user_id,
                        "success": False,
                        "changed": False,
                        "message": "同一タスクが一括変更内で重複しています。行を一つにまとめてください。",
                    }
                    continue
                seen_task_ids.add(row.task_id)
                current = rt.repository.get_task(row.task_id) if row.expected_revision else None
                if current is not None and task_revision(current) != row.expected_revision:
                    result_slots[index] = {
                        "task_id": row.task_id,
                        "assignee_user_id": row.assignee_user_id,
                        "success": False,
                        "changed": False,
                        "message": "別のユーザーが先に更新しました。最新内容を確認してもう一度実行してください。",
                    }
                    continue
                valid_rows.append((index, row))

            service_result = rt.service.bulk_update_task_assignees(
                operator_user_id=body.user_id,
                updates=[row.model_dump(exclude={"expected_revision"}) for _, row in valid_rows],
            )
            for (index, _), row_result in zip(valid_rows, service_result["results"], strict=True):
                result_slots[index] = dict(row_result)
                if row_result["success"]:
                    current = rt.repository.get_task(row_result["task_id"])
                    result_slots[index]["revision"] = task_revision(current) if current is not None else None
        finally:
            for lock in reversed(locks):
                lock.release()

    results = [row for row in result_slots if row is not None]
    changed = sum(1 for row in results if row["changed"])
    failed = sum(1 for row in results if not row["success"])
    return {
        "status": "partial" if failed > 0 else "ok",
        "changed": changed,
        "failed": failed,
        "results": results,
    }


@app.post("/api/tasks/fields/bulk")
def bulk_update_task_fields(body: BulkTaskFieldsBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_mutation():
        locked_task_ids = {row.task_id for row in body.updates}
        locked_task_ids.add(body.parent_task_id)
        locks = [_task_lock(task_id) for task_id in sorted(locked_task_ids)]
        for lock in locks:
            lock.acquire()
        try:
            parent = rt.repository.get_task(body.parent_task_id)
            if parent is None:
                raise UserInputError("親タスクが見つかりません")
            if (
                body.expected_parent_revision
                and task_revision(parent) != body.expected_parent_revision
            ):
                raise TaskConflictError(
                    "別のユーザーが先に親タスクを更新しました。最新内容を再読込し、分割案を確認してもう一度実行してください。"
                )

            result_slots: list[dict[str, Any] | None] = [None] * len(body.updates)
            valid_rows: list[tuple[int, BulkTaskFieldsRow]] = []
            seen_task_ids: set[str] = set()
            for index, row in enumerate(body.updates):
                if row.task_id in seen_task_ids:
                    result_slots[index] = {
                        "task_id": row.task_id,
                        "success": False,
                        "changed": False,
                        "message": "同一タスクが一括変更内で重複しています。行を一つにまとめてください。",
                        "warnings": [],
                    }
                    continue
                seen_task_ids.add(row.task_id)

                current = rt.repository.get_task(row.task_id) if row.expected_revision else None
                if current is not None and task_revision(current) != row.expected_revision:
                    result_slots[index] = {
                        "task_id": row.task_id,
                        "success": False,
                        "changed": False,
                        "code": "task_conflict",
                        "message": "別のユーザーが先に更新しました。最新内容を確認してもう一度実行してください。",
                        "warnings": [],
                        "revision": task_revision(current),
                        "current": {
                            "name": current.Name,
                            "assigned_manhour": int(current.AssignedManHour),
                            "memo": current.Memo,
                            "end_date": current.EndDate.isoformat(),
                            "assignee_user_id": current.AssigneeUserId,
                        },
                    }
                    continue
                valid_rows.append((index, row))

            service_result = rt.service.bulk_update_task_fields(
                operator_user_id=body.user_id,
                parent_task_id=body.parent_task_id,
                updates=[row.model_dump(exclude={"expected_revision"}) for _, row in valid_rows],
            )
            for (index, _), row_result in zip(valid_rows, service_result["results"], strict=True):
                result_slots[index] = dict(row_result)
                if row_result["success"]:
                    current = rt.repository.get_task(row_result["task_id"])
                    result_slots[index]["revision"] = task_revision(current) if current is not None else None
            current_parent = rt.repository.get_task(body.parent_task_id)
            parent_revision = task_revision(current_parent) if current_parent is not None else None
        finally:
            for lock in reversed(locks):
                lock.release()

    results = [row for row in result_slots if row is not None]
    changed = sum(1 for row in results if row["changed"])
    failed = sum(1 for row in results if not row["success"])
    return {
        "status": "partial" if failed > 0 else "ok",
        "changed": changed,
        "failed": failed,
        "results": results,
        "parent_revision": parent_revision,
    }


@app.post("/api/tasks/{task_id}/complete")
def complete_task(task_id: str, body: TimerUserBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        rt.service.complete_task(task_id, operator_user_id=body.user_id)
        return _task_mutation_payload(rt, task_id)


@app.post("/api/tasks/{task_id}/reopen")
def reopen_task(task_id: str, body: TimerUserBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        rt.service.reopen_task(task_id, operator_user_id=body.user_id)
        return _task_mutation_payload(rt, task_id)


@app.post("/api/tasks/{task_id}/outputs")
def add_output(task_id: str, body: OutputBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=_operator_user_id(body)):
        rt.service.add_output_deliverable(
            task_id,
            body.title,
            body.url,
            operator_user_id=body.user_id,
        )
        return _task_mutation_payload(rt, task_id)


@app.delete("/api/tasks/{task_id}/outputs/{index}")
def remove_output(
    task_id: str,
    index: int,
    user_id: str = Query(...),
    expected_revision: str | None = Query(None),
) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, expected_revision):
        rt.service.remove_output_deliverable(task_id, index, operator_user_id=user_id)
        return _task_mutation_payload(rt, task_id)


@app.post("/api/tasks/{task_id}/split")
def split_task(task_id: str, body: SplitBody) -> dict[str, Any]:
    rt = get_runtime()
    expected_by_task_id = {task_id: body.expected_revision}
    expected_by_task_id.update(
        {
            child.ExistingTaskId: child.ExpectedRevision
            for child in body.children
            if child.ExistingTaskId and child.ExpectedRevision
        }
    )
    with _guard_task_revisions(rt, expected_by_task_id):
        warnings = rt.service.split_task(
            parent_task_id=task_id,
            operator_user_id=body.user_id,
            children=[child.model_dump() for child in body.children],
            parent_child_dependency_mode=body.parent_child_dependency_mode,
        )
        return _task_mutation_payload(rt, task_id, warnings=warnings)


@app.post("/api/timers/start")
def start_timer(body: TimerStartBody) -> dict[str, str]:
    rt = get_runtime()
    with _guard_task_mutation():
        rt.service.start_timer(
            user_id=body.user_id,
            task_id=body.task_id,
            is_ojt=body.is_ojt,
            teacher_user_id=body.teacher_user_id,
        )
    return {"status": "ok"}


@app.post("/api/timers/pause")
def pause_timer(body: TimerUserBody) -> dict[str, str]:
    rt = get_runtime()
    with _guard_task_mutation():
        rt.service.pause_timer(body.user_id)
    return {"status": "ok"}


@app.post("/api/timers/done")
def stop_timer(body: TimerUserBody) -> dict[str, str]:
    rt = get_runtime()
    with _guard_task_mutation():
        rt.service.stop_timer(body.user_id)
    return {"status": "ok"}


@app.post("/api/timers/add-actual")
def add_timer_actual(body: TimerAddActualBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_mutation():
        rt.service.record_timer_interval(
            user_id=body.user_id,
            task_id=body.task_id,
            started_at_ms=body.started_at_ms,
            ended_at_ms=body.ended_at_ms,
            is_ojt=body.is_ojt,
            teacher_user_id=body.teacher_user_id,
        )
        # 続けて完了操作を送る画面が、全件再取得を待たず最新リビジョンを使えるよう返す。
        return _task_mutation_payload(rt, body.task_id)


@app.get("/api/timers/active")
def get_active_timer(user_id: str = Query(...)) -> dict[str, Any] | None:
    rt = get_runtime()
    active = rt.service.get_active_timer_info(user_id)
    return to_jsonable(active) if active else None


@app.get("/api/today/summary")
def get_today_summary(user_id: str = Query(...)) -> dict[str, Any]:
    rt = get_runtime()
    return to_jsonable(rt.service.get_today_summary(user_id))


@app.get("/api/progress/dashboard")
def get_progress_dashboard() -> dict[str, Any]:
    rt = get_runtime()
    return to_jsonable(rt.service.get_progress_dashboard())


@app.get("/api/progress/task-dashboard")
def get_task_progress_dashboard(task_id: str = Query(...)) -> dict[str, Any]:
    rt = get_runtime()
    return to_jsonable(rt.service.get_task_progress_dashboard(task_id=task_id))


@app.get("/api/progress/chart")
def get_progress_chart(mode: str = Query("speed")) -> Response:
    rt = get_runtime()
    dashboard = rt.service.get_progress_dashboard()
    png = render_progress_chart_png(dashboard, mode=mode)
    if not png:
        raise HTTPException(status_code=503, detail="chart renderer is unavailable")
    return Response(content=png, media_type="image/png")


@app.get("/api/rankings")
def get_rankings(
    user_id: str = Query(...),
    year_month: str = Query(...),
    period_months: int = Query(1),
) -> dict[str, Any]:
    rt = get_runtime()
    return to_jsonable(
        rt.service.get_rankings(
            self_user_id=user_id,
            year_month=year_month,
            period_months=period_months,
        )
    )


@app.get("/api/rankings/history")
def get_ranking_history(
    user_id: str = Query(...),
    year_month: str = Query(...),
    period_months: int = Query(1),
) -> dict[str, Any]:
    rt = get_runtime()
    return to_jsonable(
        rt.service.get_ranking_histories(
            self_user_id=user_id,
            year_month=year_month,
            period_months=period_months,
        )
    )


# ---- 巣（組織・役職・方針・貢献）----


def _colony_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise UserInputError("期日はYYYY-MM-DD形式で指定してください") from exc


@app.get("/api/colony/org")
def get_colony_org(user_id: str = Query(...)) -> dict[str, Any]:
    return to_jsonable(get_runtime().colony.get_org(user_id))


@app.get("/api/colony/contributions")
def get_colony_contributions(user_id: str = Query(...), year_month: str = Query(...)) -> dict[str, Any]:
    return to_jsonable(get_runtime().colony.contributions(user_id, year_month))


@app.post("/api/colony/roles")
def set_colony_role(body: ColonyRoleBody) -> dict[str, Any]:
    get_runtime().colony.set_role(body.user_id, body.target_user_id, body.role)
    return {"status": "ok"}


@app.post("/api/colony/rooms")
def dig_colony_room(body: ColonyRoomCreateBody) -> dict[str, Any]:
    team_id = get_runtime().colony.dig_room(
        body.user_id, parent_team_id=body.parent_team_id, name=body.name, emblem=body.emblem
    )
    return {"status": "ok", "team_id": team_id}


@app.post("/api/colony/rooms/{team_id}")
def update_colony_room(team_id: str, body: ColonyRoomUpdateBody) -> dict[str, Any]:
    get_runtime().colony.update_room(
        body.user_id,
        team_id,
        name=body.name,
        emblem=body.emblem,
        leader_user_id=body.leader_user_id,
        clear_leader=body.clear_leader,
        parent_team_id=body.parent_team_id,
        move_to_root=body.move_to_root,
    )
    return {"status": "ok"}


@app.post("/api/colony/rooms/{team_id}/fill")
def fill_colony_room(team_id: str, body: ColonyActorBody) -> dict[str, Any]:
    get_runtime().colony.fill_room(body.user_id, team_id)
    return {"status": "ok"}


@app.post("/api/colony/rooms/{team_id}/join")
def join_colony_room(team_id: str, body: ColonyMemberBody) -> dict[str, Any]:
    get_runtime().colony.join(body.user_id, team_id, body.member_user_id)
    return {"status": "ok"}


@app.post("/api/colony/rooms/{team_id}/leave")
def leave_colony_room(team_id: str, body: ColonyMemberBody) -> dict[str, Any]:
    get_runtime().colony.leave(body.user_id, team_id, body.member_user_id)
    return {"status": "ok"}


@app.post("/api/colony/policies")
def write_colony_policy(body: ColonyPolicyBody) -> dict[str, Any]:
    policy_id = get_runtime().colony.write_policy(
        body.user_id,
        kind=body.kind,
        text=body.text,
        x=body.x,
        y=body.y,
        size=body.size,
        points=body.points,
    )
    return {"status": "ok", "policy_id": policy_id}


@app.post("/api/colony/policies/{policy_id}")
def update_colony_policy(policy_id: str, body: ColonyPolicyUpdateBody) -> dict[str, Any]:
    get_runtime().colony.move_policy(body.user_id, policy_id, text=body.text, x=body.x, y=body.y)
    return {"status": "ok"}


@app.post("/api/colony/policies/{policy_id}/erase")
def erase_colony_policy(policy_id: str, body: ColonyActorBody) -> dict[str, Any]:
    get_runtime().colony.erase_policy(body.user_id, policy_id)
    return {"status": "ok"}


@app.post("/api/colony/sweets")
def drop_colony_sweet(body: ColonySweetBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_mutation():
        task_id, warnings = rt.colony.drop_sweet(
            body.user_id,
            area_task_id=body.area_task_id,
            name=body.name,
            size=body.size,
            assigned_minutes=body.assigned_manhour,
            end_date=_colony_date(body.end_date),
        )
    return {"status": "ok", "task_id": task_id, "warnings": warnings}


@app.post("/api/colony/areas")
def create_colony_area(body: ColonyAreaBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_mutation():
        task_id, warnings = rt.colony.create_area(body.user_id, name=body.name, end_date=_colony_date(body.end_date))
    return {"status": "ok", "task_id": task_id, "warnings": warnings}


@app.post("/api/colony/tasks/{task_id}/assign")
def assign_colony_task(task_id: str, body: ColonyAssignBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_revision(rt, task_id, body.expected_revision, operator_user_id=body.user_id):
        rt.colony.assign(body.user_id, task_id, body.assignee_user_id)
        return _task_mutation_payload(rt, task_id)


@app.get("/api/admin/infeasibility")
def get_infeasibility() -> dict[str, Any]:
    rt = get_runtime()
    return to_jsonable(rt.service.get_infeasibility_alerts())


@app.get("/api/admin/rework-tasks")
def list_rework_tasks() -> list[dict[str, Any]]:
    rt = get_runtime()
    return to_jsonable(rt.service.list_rework_tasks())


@app.post("/api/admin/education-rate")
def set_education_rate(body: EducationRateBody) -> dict[str, str]:
    rt = get_runtime()
    rt.service.set_monthly_education_rate(body.user_id, body.year_month, body.rate)
    return {"status": "ok"}


@app.post("/api/admin/crash-zone")
def set_crash_zone(body: CrashZoneBody) -> dict[str, str]:
    rt = get_runtime()
    rt.service.set_crash_zone_pct(body.value)
    return {"status": "ok"}


@app.post("/api/admin/rework-link")
def set_rework_link(body: ReworkLinkBody) -> dict[str, str]:
    rt = get_runtime()
    with _guard_task_mutation():
        rt.service.set_rework_prevented_by(
            rework_task_id=body.rework_task_id,
            prevented_by_task_id=body.prevented_by_task_id,
        )
    return {"status": "ok"}


@app.post("/api/admin/daily-actual-finalize")
def finalize_daily_actuals(body: DailyActualFinalizeBody) -> dict[str, Any]:
    rt = get_runtime()
    with _guard_task_mutation():
        if body.force:
            executed = rt.service.force_finalize_daily_actuals()
        else:
            executed = rt.service.finalize_daily_actuals_if_due()
    return {"status": "ok", "executed": executed}


@app.post("/api/admin/iquavis-sync-exclusion")
def set_iquavis_sync_exclusion(body: IQuavisSyncExclusionBody) -> dict[str, Any]:
    rt = get_runtime()
    task_ids = [*body.root_task_ids]
    if body.root_task_id:
        task_ids.append(body.root_task_id)
    return rt.service.set_iquavis_sync_exclusions(
        root_task_ids=task_ids,
        operator_user_id=body.operator_user_id,
        excluded=body.excluded,
    )


@app.get("/api/admin/iquavis-conflicts")
def list_iquavis_conflicts() -> dict[str, Any]:
    return get_runtime().iquavis_sync.list_editor_conflicts()


@app.post("/api/admin/iquavis-conflicts/refresh")
def refresh_iquavis_conflicts(body: IQuavisConflictRefreshBody) -> dict[str, Any]:
    try:
        with _guard_task_mutation():
            return get_runtime().iquavis_sync.refresh_editor_conflicts(
                operator_user_id=body.operator_user_id,
            )
    except IquavisConflictPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/admin/iquavis-authoritative-import/preview")
def preview_iquavis_authoritative_import(body: IQuavisAuthoritativeImportBody) -> dict[str, Any]:
    try:
        with _guard_task_mutation():
                return get_runtime().iquavis_sync.preview_editor_authoritative_import(
                    operator_user_id=body.operator_user_id,
                    ignored_iquavis_ids=body.ignored_iquavis_ids,
                    ignored_review_item_keys=body.ignored_review_item_keys,
                )
    except IquavisConflictPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except IquavisConflictStaleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/admin/iquavis-authoritative-import/apply")
def apply_iquavis_authoritative_import(body: IQuavisAuthoritativeImportApplyBody) -> dict[str, Any]:
    try:
        with _guard_task_mutation():
                return get_runtime().iquavis_sync.apply_editor_authoritative_import(
                    operator_user_id=body.operator_user_id,
                    expected_workbook_fingerprint=body.expected_workbook_fingerprint,
                    confirmation=body.confirmation,
                    ignored_iquavis_ids=body.ignored_iquavis_ids,
                    ignored_review_item_keys=body.ignored_review_item_keys,
                )
    except IquavisConflictPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except IquavisConflictStaleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/admin/iquavis-forced-import/preview")
def preview_iquavis_forced_import(body: IQuavisForcedImportBody) -> dict[str, Any]:
    try:
        with _guard_task_mutation():
            return get_runtime().iquavis_sync.preview_editor_forced_import(
                operator_user_id=body.operator_user_id,
            )
    except IquavisConflictPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except IquavisConflictStaleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/admin/iquavis-forced-import/apply")
def apply_iquavis_forced_import(body: IQuavisForcedImportApplyBody) -> dict[str, Any]:
    try:
        with _guard_task_mutation():
            return get_runtime().iquavis_sync.apply_editor_forced_import(
                operator_user_id=body.operator_user_id,
                expected_workbook_fingerprint=body.expected_workbook_fingerprint,
                confirmation=body.confirmation,
            )
    except IquavisConflictPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except IquavisConflictStaleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/admin/iquavis-conflicts/{task_id}/resolve")
def resolve_iquavis_conflict(task_id: str, body: IQuavisConflictResolveBody) -> dict[str, Any]:
    try:
        with _guard_task_mutation():
            return get_runtime().iquavis_sync.resolve_editor_conflict(
                task_id=task_id,
                operator_user_id=body.operator_user_id,
                direction=body.direction,
                expected_recorded_at=body.expected_recorded_at,
            )
    except IquavisConflictPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except IquavisConflictStaleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/admin/iquavis-conflicts/{task_id}/hold")
def hold_iquavis_conflict(task_id: str, body: IQuavisConflictHoldBody) -> dict[str, Any]:
    try:
        with _guard_task_mutation():
            return get_runtime().iquavis_sync.set_editor_conflict_hold(
                task_id=task_id,
                operator_user_id=body.operator_user_id,
                hold=body.hold,
            )
    except IquavisConflictPermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


if FRONTEND_DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")
elif FRONTEND_ROOT_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_ROOT_DIR), html=True), name="frontend")









