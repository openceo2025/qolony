from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from app.tms_core.config import IQuavisConfig


IQUAVIS_DIRECT_API_DISABLED_MESSAGE = "iQUAVIS WebAPI直接連携は廃止済みです。Excel取込経由で同期してください。"


def _looks_like_html(text: str) -> bool:
    lowered = text[:1024].lower()
    return "<!doctype" in lowered or "<html" in lowered


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


@dataclass(frozen=True)
class IQuavisRemoteTask:
    id: str
    project_id: str
    parent_task_id: str | None
    name: str
    task_type: str
    start_date: date
    end_date: date
    note: str
    progress_type: str | None
    status: str | None
    progress_rate: int | None
    first_assign_iquavis_user_id: str | None
    first_assign_login_id: str | None
    first_assign_name: str | None
    creator_iquavis_user_id: str | None = None
    creator_login_id: str | None = None
    creator_name: str | None = None


class IQuavisApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class IQuavisRetryableError(IQuavisApiError):
    pass


class IQuavisConflictError(IQuavisRetryableError):
    pass


class IQuavisClient:
    def __init__(self, config: IQuavisConfig):
        self._config = config
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            headers={"Accept": "application/json"},
        )
        self._access_token: str | None = None
        self._token_expires_at: datetime | None = None

    def close(self) -> None:
        self._client.close()

    def _ensure_token(self) -> str:
        now = datetime.now()
        if self._access_token and self._token_expires_at and now < self._token_expires_at:
            return self._access_token

        try:
            response = self._client.post(
                "/token",
                data={
                    "grant_type": "password",
                    "username": self._config.username,
                    "password": self._config.password,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except httpx.RequestError as exc:
            raise IQuavisRetryableError(IQUAVIS_DIRECT_API_DISABLED_MESSAGE) from exc
        payload = self._decode_payload(response)
        if response.status_code >= 400:
            message = self._extract_error_message(payload) or "iQUAVISトークン取得に失敗しました"
            raise IQuavisApiError(message, status_code=response.status_code)

        access_token = str(payload.get("access_token", "")).strip()
        if not access_token:
            raise IQuavisApiError("iQUAVISトークン応答に access_token がありません")

        expires_in = int(payload.get("expires_in", 3600) or 3600)
        # 有効期限直前の境界で失敗しないよう少し手前で再取得する。
        self._access_token = access_token
        self._token_expires_at = now + timedelta(seconds=max(60, expires_in - 60))
        return access_token

    def _decode_payload(self, response: httpx.Response) -> Any:
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text

    def _extract_error_message(self, payload: Any) -> str | None:
        if isinstance(payload, dict):
            for key in ("Message", "message", "detail", "error_description", "error"):
                value = payload.get(key)
                if value:
                    return str(value)
        if isinstance(payload, str) and payload.strip():
            text = payload.strip()
            if _looks_like_html(text):
                return IQUAVIS_DIRECT_API_DISABLED_MESSAGE
            if len(text) > 500:
                return text[:500] + "..."
            return text
        return None

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        token = self._ensure_token()
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.RequestError as exc:
            raise IQuavisRetryableError(IQUAVIS_DIRECT_API_DISABLED_MESSAGE) from exc
        payload = self._decode_payload(response)

        if response.status_code == 409:
            message = self._extract_error_message(payload) or "iQUAVIS編集中の競合が発生しました"
            raise IQuavisConflictError(message, status_code=response.status_code)
        if response.status_code in {429, 500, 502, 503, 504}:
            message = self._extract_error_message(payload) or "iQUAVIS一時エラー"
            raise IQuavisRetryableError(message, status_code=response.status_code)
        if response.status_code >= 400:
            message = self._extract_error_message(payload) or f"iQUAVIS API error: {response.status_code}"
            raise IQuavisApiError(message, status_code=response.status_code)
        return payload

    def get_task(self, task_id: str, *, include_progress: bool = True, include_assigns: bool = False) -> IQuavisRemoteTask:
        include_parts: list[str] = []
        if include_progress:
            include_parts.append("Progress")
        if include_assigns:
            include_parts.append("Assigns")
        params = {"include": ",".join(include_parts)} if include_parts else None
        payload = self._request("GET", f"/v1/tasks/{task_id}", params=params)

        progress = payload.get("Progress") if isinstance(payload, dict) else None
        assigns = payload.get("Assigns") if isinstance(payload, dict) else None
        first_assign = assigns[0] if isinstance(assigns, list) and assigns else {}
        create_user = payload.get("CreateUser") if isinstance(payload, dict) else None
        created_by = payload.get("CreatedBy") if isinstance(payload, dict) else None
        creator_payload = create_user if isinstance(create_user, dict) else created_by if isinstance(created_by, dict) else {}

        start_date = _parse_date(payload.get("StartDate"))
        end_date = _parse_date(payload.get("EndDate"))
        if start_date is None or end_date is None:
            raise IQuavisApiError("iQUAVISタスクの日付が取得できませんでした")

        return IQuavisRemoteTask(
            id=str(payload.get("Id", task_id)),
            project_id=str(payload.get("ProjectId", "")).strip(),
            parent_task_id=str(payload.get("ParentTaskId", "")).strip() or None,
            name=str(payload.get("Name", "")).strip(),
            task_type=str(payload.get("Type", "Task")).strip() or "Task",
            start_date=start_date,
            end_date=end_date,
            note=str(payload.get("Note", "") or ""),
            progress_type=str(payload.get("ProgressType", "")).strip() or None,
            status=str((progress or {}).get("Status", "")).strip() or None,
            progress_rate=(
                int((progress or {}).get("ProgressRate"))
                if isinstance((progress or {}).get("ProgressRate"), (int, float))
                else None
            ),
            first_assign_iquavis_user_id=str(first_assign.get("Id", "")).strip() or None,
            first_assign_login_id=str(first_assign.get("LoginId", "")).strip() or None,
            first_assign_name=str(first_assign.get("Name", "")).strip() or None,
            creator_iquavis_user_id=(
                str(
                    payload.get("CreateUserId")
                    or payload.get("CreatorUserId")
                    or payload.get("CreatedUserId")
                    or payload.get("CreatedByUserId")
                    or creator_payload.get("Id", "")
                    or creator_payload.get("UserId", "")
                ).strip()
                or None
            ),
            creator_login_id=str(creator_payload.get("LoginId", "")).strip() or None,
            creator_name=str(creator_payload.get("Name", "")).strip() or None,
        )

    def put_task_schedule(self, task_id: str, *, start_date: date, end_date: date) -> None:
        self._request(
            "PUT",
            f"/v1/tasks/{task_id}",
            json_body={
                "StartDate": start_date.isoformat(),
                "EndDate": end_date.isoformat(),
            },
        )

    def put_task_progress(
        self,
        task_id: str,
        *,
        status: str,
        progress_rate: int | None,
    ) -> None:
        payload: dict[str, Any] = {"Status": status}
        if progress_rate is not None:
            payload["ProgressRate"] = int(progress_rate)
        self._request("PUT", f"/v1/tasks/{task_id}/progress", json_body=payload)
