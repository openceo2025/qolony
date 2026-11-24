from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.iquavis.sync import IquavisConflictPermissionError, IquavisConflictStaleError


def _install_runtime(monkeypatch, sync_service) -> None:
    runtime = SimpleNamespace(iquavis_sync=sync_service)
    monkeypatch.setattr(main, "get_runtime", lambda: runtime)


def test_forced_import_preview_endpoint_passes_operator(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeSync:
        def preview_editor_forced_import(self, *, operator_user_id):
            calls.append({"operator_user_id": operator_user_id})
            return {"workbook_fingerprint": "sha256:abc", "conflict_task_count": 2}

    _install_runtime(monkeypatch, FakeSync())
    response = TestClient(main.app).post(
        "/api/admin/iquavis-forced-import/preview",
        json={"operator_user_id": "top-1"},
    )

    assert response.status_code == 200
    assert response.json()["conflict_task_count"] == 2
    assert calls == [{"operator_user_id": "top-1"}]


def test_forced_import_apply_endpoint_forwards_fingerprint_and_confirmation(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeSync:
        def apply_editor_forced_import(self, *, operator_user_id, expected_workbook_fingerprint, confirmation):
            calls.append(
                {
                    "operator_user_id": operator_user_id,
                    "expected_workbook_fingerprint": expected_workbook_fingerprint,
                    "confirmation": confirmation,
                }
            )
            return {"status": "ok", "updated_task_count": 596}

    _install_runtime(monkeypatch, FakeSync())
    response = TestClient(main.app).post(
        "/api/admin/iquavis-forced-import/apply",
        json={
            "operator_user_id": "top-1",
            "expected_workbook_fingerprint": "sha256:abc",
            "confirmation": "IQUAVIS_FORCE_TO_QOLONY",
        },
    )

    assert response.status_code == 200
    assert response.json()["updated_task_count"] == 596
    assert calls[0]["expected_workbook_fingerprint"] == "sha256:abc"
    assert calls[0]["confirmation"] == "IQUAVIS_FORCE_TO_QOLONY"


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (IquavisConflictPermissionError("top manager only"), 403),
        (IquavisConflictStaleError("workbook changed"), 409),
    ],
)
def test_forced_import_apply_maps_errors_to_http_status(monkeypatch, error, expected_status) -> None:
    class FakeSync:
        def apply_editor_forced_import(self, **_kwargs):
            raise error

    _install_runtime(monkeypatch, FakeSync())
    response = TestClient(main.app).post(
        "/api/admin/iquavis-forced-import/apply",
        json={
            "operator_user_id": "member-1",
            "expected_workbook_fingerprint": "sha256:abc",
            "confirmation": "IQUAVIS_FORCE_TO_QOLONY",
        },
    )

    assert response.status_code == expected_status
