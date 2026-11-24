from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


IQUAVIS_TASK_CONFLICT_STATE_KEY = "IQuavisTaskConflictStateV1"
IQUAVIS_SYNC_EXCLUDED_ROOTS_STATE_KEY = "IQuavisSyncExcludedRootTaskIdsV1"
IQUAVIS_CONFLICT_STATE = "Conflict"
IQUAVIS_PENDING_SAVE_STATE = "PendingIquavisSave"
IQUAVIS_CONFLICT_HOLD_STATE = "ConflictHold"


@dataclass(frozen=True)
class IquavisTaskConflictField:
    field: str
    baseline: Any
    qolony: Any
    excel: Any


@dataclass(frozen=True)
class IquavisTaskConflict:
    task_id: str
    iquavis_id: str
    message: str
    recorded_at: datetime
    fields: dict[str, IquavisTaskConflictField] = field(default_factory=dict)
    pending_fields: dict[str, IquavisTaskConflictField] = field(default_factory=dict)
    workbook_fingerprint: str | None = None
    sync_state: str = IQUAVIS_CONFLICT_STATE


def _parse_conflict_fields(raw: Any) -> dict[str, IquavisTaskConflictField]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, IquavisTaskConflictField] = {}
    for field_name, values in raw.items():
        normalized_name = str(field_name).strip()
        if not normalized_name or not isinstance(values, dict):
            continue
        result[normalized_name] = IquavisTaskConflictField(
            field=normalized_name,
            baseline=values.get("baseline"),
            qolony=values.get("qolony"),
            excel=values.get("excel"),
        )
    return result


def parse_conflict_state(raw: str | None) -> dict[str, IquavisTaskConflict]:
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    result: dict[str, IquavisTaskConflict] = {}
    for task_id, item in payload.items():
        if not isinstance(item, dict):
            continue
        try:
            sync_state = str(item.get("sync_state", IQUAVIS_CONFLICT_STATE)).strip()
            if sync_state not in {
                IQUAVIS_CONFLICT_STATE,
                IQUAVIS_PENDING_SAVE_STATE,
                IQUAVIS_CONFLICT_HOLD_STATE,
            }:
                sync_state = IQUAVIS_CONFLICT_STATE
            result[str(task_id)] = IquavisTaskConflict(
                task_id=str(task_id),
                iquavis_id=str(item.get("iquavis_id", "")),
                message=str(item.get("message", "")),
                recorded_at=datetime.fromisoformat(str(item.get("recorded_at"))),
                fields=_parse_conflict_fields(item.get("fields")),
                pending_fields=_parse_conflict_fields(item.get("pending_fields")),
                workbook_fingerprint=str(item.get("workbook_fingerprint", "")).strip() or None,
                sync_state=sync_state,
            )
        except Exception:
            continue
    return result


def serialize_conflict_state(conflicts: dict[str, IquavisTaskConflict]) -> str:
    payload: dict[str, dict[str, Any]] = {}
    for task_id, item in conflicts.items():
        payload[task_id] = {
            "iquavis_id": item.iquavis_id,
            "message": item.message,
            "recorded_at": item.recorded_at.isoformat(),
            "fields": {
                field_name: {
                    "baseline": values.baseline,
                    "qolony": values.qolony,
                    "excel": values.excel,
                }
                for field_name, values in item.fields.items()
            },
            "pending_fields": {
                field_name: {
                    "baseline": values.baseline,
                    "qolony": values.qolony,
                    "excel": values.excel,
                }
                for field_name, values in item.pending_fields.items()
            },
            "workbook_fingerprint": item.workbook_fingerprint,
            "sync_state": item.sync_state,
        }
    return json.dumps(payload, ensure_ascii=False)


def parse_excluded_root_state(raw: str | None) -> set[str]:
    if not raw:
        return set()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return set()
    if not isinstance(payload, list):
        return set()
    return {str(item) for item in payload if str(item).strip()}


def serialize_excluded_root_state(root_task_ids: set[str]) -> str:
    return json.dumps(sorted(root_task_ids), ensure_ascii=False)


def task_sync_state(
    *,
    task_id: str,
    has_iquavis_link: bool,
    conflicts: dict[str, IquavisTaskConflict],
    is_excluded: bool = False,
) -> str:
    if is_excluded:
        return "Excluded"
    if task_id in conflicts:
        conflict = conflicts[task_id]
        if conflict.sync_state == IQUAVIS_PENDING_SAVE_STATE or conflict.fields:
            return conflict.sync_state
        if conflict.sync_state == IQUAVIS_CONFLICT_HOLD_STATE:
            return IQUAVIS_CONFLICT_HOLD_STATE
    if has_iquavis_link:
        return "Linked"
    return "Unlinked"
