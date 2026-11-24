from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from app.iquavis.editor_models import (
    IquavisAssigneeRecord,
    IquavisEditorSnapshot,
    IquavisTaskDomainRecord,
    IquavisTaskRecord,
)
from app.tms_core.db.repository import NewTaskInput, TmsRepository
from app.tms_core.services.core import TmsService, UserInputError


def _effective_iquavis_id(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    normalized = text.replace("\u3000", "").strip()
    if not normalized:
        return ""
    lowered = normalized.lower()
    if lowered in {
        "0",
        "0.0",
        "#n/a",
        "n/a",
        "#na",
        "na",
        "null",
        "#null",
        "none",
        "#none",
        "undefined",
        "#undefined",
        "nan",
        "#nan",
    }:
        return ""
    return normalized


@dataclass(frozen=True)
class IquavisEditorImportResult:
    imported_task_ids: list[str] = field(default_factory=list)
    imported_task_domain_ids: list[str] = field(default_factory=list)
    skipped_existing_ids: list[str] = field(default_factory=list)
    missing_target_ids: list[str] = field(default_factory=list)


class IquavisEditorImportService:
    def __init__(self, *, repository: TmsRepository, tms_service: TmsService):
        self.repository = repository
        self.tms_service = tms_service
        self.settings = tms_service.settings

    def import_new_tasks_from_snapshot(
        self,
        snapshot: IquavisEditorSnapshot,
        *,
        creator_user_name: str | None = None,
        default_assignee_name: str | None = None,
    ) -> IquavisEditorImportResult:
        project = self.tms_service.get_project()
        creator = (
            self.repository.get_user_by_name(creator_user_name)
            if creator_user_name
            else self.repository.get_top_manager()
        )
        if creator is None:
            raise UserInputError("作成者ユーザーが見つかりません")

        default_assignee = (
            self.repository.get_user_by_name(default_assignee_name)
            if default_assignee_name
            else creator
        )
        if default_assignee is not None and not getattr(default_assignee, "IsActive", True):
            default_assignee = self.repository.get_top_manager()
        if default_assignee is None:
            raise UserInputError("既定担当者ユーザーが見つかりません")

        task_by_id = {
            _effective_iquavis_id(item.iquavis_task_id): item
            for item in snapshot.tasks
            if _effective_iquavis_id(item.iquavis_task_id)
        }
        domain_by_id = {
            _effective_iquavis_id(item.iquavis_task_domain_id): item
            for item in snapshot.task_domains
            if _effective_iquavis_id(item.iquavis_task_domain_id)
        }
        target_by_id = {
            _effective_iquavis_id(target.iquavis_id): target
            for target in snapshot.synced_targets
            if _effective_iquavis_id(target.iquavis_id)
        }

        target_ids = self._ordered_unique_target_ids(snapshot)
        target_task_ids: list[str] = []
        explicit_domain_ids: list[str] = []
        missing_target_ids: list[str] = []

        for target_id in target_ids:
            if target_id in task_by_id:
                target_task_ids.append(target_id)
            elif target_id in domain_by_id:
                explicit_domain_ids.append(target_id)
            else:
                missing_target_ids.append(target_id)

        required_domain_ids: set[str] = set()
        for domain_id in explicit_domain_ids:
            required_domain_ids.update(self._collect_domain_ancestors(domain_id, domain_by_id))
        for task_id in target_task_ids:
            task = task_by_id[task_id]
            task_domain_id = _effective_iquavis_id(task.task_domain_id)
            if task_domain_id:
                required_domain_ids.update(self._collect_domain_ancestors(task_domain_id, domain_by_id))

        local_id_by_iquavis_id = self._load_existing_local_ids([*required_domain_ids, *target_task_ids])
        self._apply_explicit_local_links(
            target_by_id=target_by_id,
            task_by_id=task_by_id,
            domain_by_id=domain_by_id,
            local_id_by_iquavis_id=local_id_by_iquavis_id,
        )
        imported_domain_local_ids: list[str] = []
        imported_task_local_ids: list[str] = []
        skipped_existing_ids: list[str] = []
        domain_bounds = self._domain_schedule_bounds_by_id(
            required_domain_ids=required_domain_ids,
            target_task_ids=set(target_task_ids),
            domain_by_id=domain_by_id,
            task_by_id=task_by_id,
        )

        for domain_id in self._order_domains(required_domain_ids, domain_by_id):
            if domain_id in local_id_by_iquavis_id:
                skipped_existing_ids.append(domain_id)
                continue
            domain = domain_by_id[domain_id]
            if not self._is_project_allowed(domain.project_id):
                continue
            parent_task_id = self._resolve_domain_parent_id(
                domain,
                local_id_by_iquavis_id,
                target_by_id.get(domain_id),
            )
            assignee_user_id = self._resolve_local_assignee_id(
                domain.assignee,
                parent_task_id=parent_task_id,
                default_assignee_id=default_assignee.Id,
            )
            start_date, end_date = domain_bounds.get(domain_id, (domain.start_date, domain.end_date))
            created = self.repository.create_task(
                NewTaskInput(
                    ProjectId=project.Id,
                    ParentTaskId=parent_task_id,
                    Name=domain.name.strip(),
                    Type="GroupTask",
                    AssigneeUserId=assignee_user_id,
                    CreatorUserId=creator.Id,
                    StartDate=start_date,
                    EndDate=end_date,
                    AssignedManHour=0,
                    PlannedDeliverables=[],
                    IsRework=False,
                    Memo=domain.note.strip(),
                )
            )
            self.repository.set_task_iquavis_link(
                created.Id,
                iquavis_task_id=domain.iquavis_task_domain_id,
                iquavis_project_id=domain.project_id,
            )
            self.repository.mark_task_iquavis_last_pulled(created.Id, datetime.now())
            local_id_by_iquavis_id[domain_id] = created.Id
            imported_domain_local_ids.append(created.Id)

        for task_id in self._order_tasks(target_task_ids, task_by_id):
            task = task_by_id[task_id]
            if task_id in local_id_by_iquavis_id:
                skipped_existing_ids.append(task_id)
                continue
            if not self._is_project_allowed(task.project_id):
                continue
            local_parent_id = self._resolve_task_parent_id(
                task,
                local_id_by_iquavis_id,
                target_by_id.get(task_id),
            )
            if self._has_unresolved_parent_reference(
                task,
                local_parent_id=local_parent_id,
                local_id_by_iquavis_id=local_id_by_iquavis_id,
            ):
                missing_target_ids.append(task_id)
                continue
            assignee_user_id = self._resolve_local_assignee_id(
                task.assignee,
                parent_task_id=local_parent_id,
                default_assignee_id=default_assignee.Id,
            )
            created = self.repository.create_task(
                NewTaskInput(
                    ProjectId=project.Id,
                    ParentTaskId=local_parent_id,
                    Name=task.name.strip(),
                    Type=task.task_type.strip() or "Task",
                    AssigneeUserId=assignee_user_id,
                    CreatorUserId=creator.Id,
                    StartDate=task.start_date,
                    EndDate=task.end_date,
                    AssignedManHour=0,
                    PlannedDeliverables=[],
                    IsRework=False,
                    Memo=task.note.strip(),
                )
            )
            self.repository.set_task_iquavis_link(
                created.Id,
                iquavis_task_id=task.iquavis_task_id,
                iquavis_project_id=task.project_id,
            )
            self.repository.mark_task_iquavis_last_pulled(created.Id, datetime.now())
            self._apply_progress_state(
                task_id=created.Id,
                status=task.status,
                progress_rate=task.progress_rate,
            )
            local_id_by_iquavis_id[task_id] = created.Id
            imported_task_local_ids.append(created.Id)

        if imported_domain_local_ids or imported_task_local_ids:
            self.tms_service.recompute_all_task_progress()

        return IquavisEditorImportResult(
            imported_task_ids=imported_task_local_ids,
            imported_task_domain_ids=imported_domain_local_ids,
            skipped_existing_ids=list(dict.fromkeys(skipped_existing_ids)),
            missing_target_ids=missing_target_ids,
        )

    def _ordered_unique_target_ids(self, snapshot: IquavisEditorSnapshot) -> list[str]:
        return list(
            dict.fromkeys(
                _effective_iquavis_id(target.iquavis_id)
                for target in snapshot.synced_targets
                if _effective_iquavis_id(target.iquavis_id)
            )
        )

    def _collect_domain_ancestors(
        self,
        domain_id: str,
        domain_by_id: dict[str, IquavisTaskDomainRecord],
    ) -> list[str]:
        result: list[str] = []
        visiting: set[str] = set()
        current_id: str | None = domain_id
        while current_id:
            if current_id in visiting:
                raise UserInputError("iQUAVIS task domain の親子関係が循環しています")
            visiting.add(current_id)
            domain = domain_by_id.get(current_id)
            if domain is None:
                break
            result.append(current_id)
            current_id = _effective_iquavis_id(domain.parent_task_domain_id)
        return result

    def _order_domains(
        self,
        required_domain_ids: set[str],
        domain_by_id: dict[str, IquavisTaskDomainRecord],
    ) -> list[str]:
        ordered: list[str] = []
        remaining = set(required_domain_ids)
        while remaining:
            progressed = False
            for domain_id in sorted(remaining):
                domain = domain_by_id.get(domain_id)
                if domain is None:
                    remaining.remove(domain_id)
                    progressed = True
                    continue
                parent_id = _effective_iquavis_id(domain.parent_task_domain_id)
                if not parent_id or parent_id not in remaining:
                    ordered.append(domain_id)
                    remaining.remove(domain_id)
                    progressed = True
            if progressed:
                continue
            raise UserInputError("iQUAVIS task domain の親子関係を解決できません")
        return ordered

    def _load_existing_local_ids(self, iquavis_ids: list[str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for iquavis_id in dict.fromkeys(item for item in iquavis_ids if item):
            existing = self.repository.get_task_by_iquavis_task_id(iquavis_id)
            if existing is not None:
                result[iquavis_id] = existing.Id
        return result

    def _domain_schedule_bounds_by_id(
        self,
        *,
        required_domain_ids: set[str],
        target_task_ids: set[str],
        domain_by_id: dict[str, IquavisTaskDomainRecord],
        task_by_id: dict[str, IquavisTaskRecord],
    ) -> dict[str, tuple[date, date]]:
        children_by_domain: dict[str, list[str]] = {}
        for domain_id, domain in domain_by_id.items():
            parent_id = _effective_iquavis_id(domain.parent_task_domain_id)
            if parent_id:
                children_by_domain.setdefault(parent_id, []).append(domain_id)

        cache: dict[str, tuple[date, date]] = {}

        def bounds(domain_id: str) -> tuple[date, date]:
            cached = cache.get(domain_id)
            if cached is not None:
                return cached
            domain = domain_by_id[domain_id]
            starts = [domain.start_date]
            ends = [domain.end_date]
            for child_domain_id in children_by_domain.get(domain_id, []):
                if child_domain_id not in required_domain_ids:
                    continue
                child_start, child_end = bounds(child_domain_id)
                starts.append(child_start)
                ends.append(child_end)
            for task_id in target_task_ids:
                task = task_by_id.get(task_id)
                if task is None:
                    continue
                task_domain_id = _effective_iquavis_id(task.task_domain_id)
                if task_domain_id != domain_id:
                    continue
                starts.append(task.start_date)
                ends.append(task.end_date)
            result = min(starts), max(ends)
            cache[domain_id] = result
            return result

        return {domain_id: bounds(domain_id) for domain_id in required_domain_ids if domain_id in domain_by_id}

    def _apply_explicit_local_links(
        self,
        *,
        target_by_id: dict[str, SyncedIquavisTarget],
        task_by_id: dict[str, IquavisTaskRecord],
        domain_by_id: dict[str, IquavisTaskDomainRecord],
        local_id_by_iquavis_id: dict[str, str],
    ) -> None:
        claimed_local_ids: dict[str, str] = {}
        for iquavis_id, target in target_by_id.items():
            if not target.qolony_task_id:
                continue
            local_task = self.repository.get_task(target.qolony_task_id)
            if local_task is None:
                raise UserInputError(f"QOLONY親子リンク対象タスクが見つかりません: {target.qolony_task_id}")
            previously_claimed = claimed_local_ids.get(local_task.Id)
            if previously_claimed is not None and previously_claimed != iquavis_id:
                raise UserInputError(
                    "同じQOLONYタスクを複数のiQUAVIS IDへリンクできません: "
                    f"{local_task.Id}"
                )
            claimed_local_ids[local_task.Id] = iquavis_id
            if local_task.IQuavisTaskId and local_task.IQuavisTaskId != iquavis_id:
                raise UserInputError(
                    "QOLONYタスクは別のiQUAVIS IDへリンク済みです: "
                    f"{local_task.IQuavisTaskId}"
                )

            existing = self.repository.get_task_by_iquavis_task_id(iquavis_id)
            if existing is not None and existing.Id != local_task.Id:
                raise UserInputError(f"iQUAVIS ID は別のQOLONYタスクにリンク済みです: {iquavis_id}")

            project_id = None
            if iquavis_id in task_by_id:
                project_id = task_by_id[iquavis_id].project_id
            elif iquavis_id in domain_by_id:
                project_id = domain_by_id[iquavis_id].project_id
            self.repository.set_task_iquavis_link(
                local_task.Id,
                iquavis_task_id=iquavis_id,
                iquavis_project_id=project_id,
            )
            self.repository.mark_task_iquavis_last_pulled(local_task.Id, datetime.now())
            local_id_by_iquavis_id[iquavis_id] = local_task.Id

    def _resolve_domain_parent_id(
        self,
        domain: IquavisTaskDomainRecord,
        local_id_by_iquavis_id: dict[str, str],
        target: SyncedIquavisTarget | None,
    ) -> str | None:
        explicit_parent_id = self._resolve_explicit_parent_id(target)
        if explicit_parent_id is not None:
            return explicit_parent_id
        parent_task_domain_id = _effective_iquavis_id(domain.parent_task_domain_id)
        if parent_task_domain_id:
            local_parent_id = local_id_by_iquavis_id.get(parent_task_domain_id)
            if local_parent_id is not None:
                return local_parent_id
            existing_parent = self.repository.get_task_by_iquavis_task_id(parent_task_domain_id)
            if existing_parent is not None:
                return existing_parent.Id
        return None

    def _resolve_task_parent_id(
        self,
        task: IquavisTaskRecord,
        local_id_by_iquavis_id: dict[str, str],
        target: SyncedIquavisTarget | None = None,
    ) -> str | None:
        explicit_parent_id = self._resolve_explicit_parent_id(target)
        if explicit_parent_id is not None:
            return explicit_parent_id
        parent_task_id = _effective_iquavis_id(task.parent_task_id)
        if parent_task_id and parent_task_id in local_id_by_iquavis_id:
            return local_id_by_iquavis_id[parent_task_id]
        if parent_task_id:
            existing_parent = self.repository.get_task_by_iquavis_task_id(parent_task_id)
            if existing_parent is not None:
                return existing_parent.Id
        task_domain_id = _effective_iquavis_id(task.task_domain_id)
        if task_domain_id:
            local_domain_id = local_id_by_iquavis_id.get(task_domain_id)
            if local_domain_id:
                return local_domain_id
            existing_domain = self.repository.get_task_by_iquavis_task_id(task_domain_id)
            if existing_domain is not None:
                return existing_domain.Id
        return None

    def _has_unresolved_parent_reference(
        self,
        task: IquavisTaskRecord,
        *,
        local_parent_id: str | None,
        local_id_by_iquavis_id: dict[str, str],
    ) -> bool:
        if local_parent_id is not None:
            return False
        parent_task_id = _effective_iquavis_id(task.parent_task_id)
        if parent_task_id:
            if parent_task_id in local_id_by_iquavis_id:
                return False
            return self.repository.get_task_by_iquavis_task_id(parent_task_id) is None
        task_domain_id = _effective_iquavis_id(task.task_domain_id)
        if task_domain_id:
            if task_domain_id in local_id_by_iquavis_id:
                return False
            return self.repository.get_task_by_iquavis_task_id(task_domain_id) is None
        return False

    def _resolve_explicit_parent_id(self, target: SyncedIquavisTarget | None) -> str | None:
        if target is None or not target.qolony_parent_task_id:
            return None
        parent = self.repository.get_task(target.qolony_parent_task_id)
        if parent is None:
            raise UserInputError(f"QOLONY親タスクが見つかりません: {target.qolony_parent_task_id}")
        return parent.Id

    def _order_tasks(
        self,
        target_task_ids: list[str],
        task_by_id: dict[str, IquavisTaskRecord],
    ) -> list[str]:
        ordered: list[str] = []
        remaining = list(dict.fromkeys(target_task_ids))
        remaining_set = set(remaining)
        while remaining:
            progressed = False
            for task_id in list(remaining):
                task = task_by_id.get(task_id)
                parent_id = _effective_iquavis_id(task.parent_task_id) if task else None
                if not parent_id or parent_id not in remaining_set:
                    ordered.append(task_id)
                    remaining.remove(task_id)
                    remaining_set.remove(task_id)
                    progressed = True
            if progressed:
                continue
            ordered.extend(remaining)
            break
        return ordered

    def _resolve_local_assignee_id(
        self,
        assignee: IquavisAssigneeRecord | None,
        *,
        parent_task_id: str | None = None,
        default_assignee_id: str,
    ) -> str:
        resolved = self._resolve_active_local_user_id(assignee)
        if resolved is not None:
            return resolved
        parent_assignee_id = self._resolve_active_parent_assignee_id(parent_task_id)
        if parent_assignee_id is not None:
            return parent_assignee_id
        return default_assignee_id

    def _resolve_local_user_id(self, record: IquavisAssigneeRecord | None) -> str | None:
        if record is None:
            return None
        if record.iquavis_user_id:
            user = self.repository.get_user_by_iquavis_user_id(record.iquavis_user_id)
            if user is not None:
                return user.Id
        if record.login_id:
            user = self.repository.get_user_by_login_id(record.login_id)
            if user is not None:
                return user.Id
            user = self.repository.get_user_by_name(record.login_id)
            if user is not None:
                return user.Id
        if record.name:
            user = self.repository.get_user_by_name(record.name)
            if user is not None:
                return user.Id
            short_name = str(record.name).replace("\u3000", " ").split(" ")[0].strip()
            if short_name:
                user = self.repository.get_user_by_name(short_name)
                if user is not None:
                    return user.Id
        return None

    def _resolve_active_local_user_id(self, record: IquavisAssigneeRecord | None) -> str | None:
        user_id = self._resolve_local_user_id(record)
        if user_id is None:
            return None
        user = self.repository.get_user(user_id)
        if user is None or not getattr(user, "IsActive", True):
            return None
        return user.Id

    def _resolve_active_parent_assignee_id(self, parent_task_id: str | None) -> str | None:
        if not parent_task_id:
            return None
        parent = self.repository.get_task(parent_task_id)
        if parent is None:
            return None
        parent_user = self.repository.get_user(parent.AssigneeUserId)
        if parent_user is None or not getattr(parent_user, "IsActive", True):
            return None
        return parent_user.Id

    def _apply_progress_state(self, *, task_id: str, status: str | None, progress_rate: int | None) -> None:
        if status == "Completion":
            self.repository.update_task_status(task_id, "Completion", 100.0)
            return
        if status == "NotStart":
            self.repository.update_task_status(task_id, "NotStart", 0.0)
            return
        if status == "DuringStart":
            self.repository.update_task_status(task_id, "DuringStart", float(progress_rate or 0))
            return
        if status == "Abort":
            self.repository.update_task_status(task_id, "Abort", 0.0)

    def _is_project_allowed(self, project_id: str | None) -> bool:
        allowed = self.settings.iquavis.allowed_project_ids
        if not allowed:
            return True
        return bool(project_id and project_id in allowed)
