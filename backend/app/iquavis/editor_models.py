from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal


IquavisForceSyncDirection = Literal["qolony_to_iquavis", "iquavis_to_qolony"]
IquavisSyncTargetKind = Literal["task", "task_domain", "unknown"]


@dataclass(frozen=True)
class SyncedIquavisTarget:
    iquavis_id: str
    kind: IquavisSyncTargetKind = "unknown"
    force_sync: IquavisForceSyncDirection | None = None
    qolony_task_id: str | None = None
    qolony_parent_task_id: str | None = None


@dataclass(frozen=True)
class IquavisAssigneeRecord:
    iquavis_user_id: str | None = None
    login_id: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class IquavisTaskDomainRecord:
    iquavis_task_domain_id: str
    name: str
    project_id: str | None
    parent_task_domain_id: str | None
    start_date: date
    end_date: date
    note: str = ""
    assignee: IquavisAssigneeRecord | None = None
    creator: IquavisAssigneeRecord | None = None


@dataclass(frozen=True)
class IquavisTaskRecord:
    iquavis_task_id: str
    name: str
    project_id: str | None
    task_domain_id: str | None
    parent_task_id: str | None
    start_date: date
    end_date: date
    assigned_minutes: int = 0
    note: str = ""
    task_type: str = "Task"
    status: str | None = None
    progress_rate: int | None = None
    assignee: IquavisAssigneeRecord | None = None
    creator: IquavisAssigneeRecord | None = None


@dataclass(frozen=True)
class IquavisMemberRecord:
    iquavis_user_id: str | None
    login_id: str | None
    name: str


@dataclass(frozen=True)
class IquavisTaskSyncProperties:
    name: str
    start_date: date
    end_date: date
    assigned_minutes: int
    actual_minutes: int
    status: str
    progress_rate: int | None = None
    memo: str = ""


@dataclass(frozen=True)
class IquavisTaskSyncRecord:
    iquavis_id: str
    kind: IquavisSyncTargetKind
    previous: IquavisTaskSyncProperties | None
    iquavis_current: IquavisTaskSyncProperties
    force_sync: IquavisForceSyncDirection | None = None


@dataclass(frozen=True)
class IquavisEditorQolonyUpdate:
    iquavis_id: str
    properties: IquavisTaskSyncProperties
    field_names: tuple[str, ...]


@dataclass(frozen=True)
class IquavisEditorSyncWriteResult:
    updated_ids: list[str] = field(default_factory=list)
    cleared_force_sync_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IquavisEditorSnapshot:
    synced_targets: list[SyncedIquavisTarget] = field(default_factory=list)
    task_domains: list[IquavisTaskDomainRecord] = field(default_factory=list)
    tasks: list[IquavisTaskRecord] = field(default_factory=list)
    members: list[IquavisMemberRecord] = field(default_factory=list)
    task_sync_records: list[IquavisTaskSyncRecord] = field(default_factory=list)
