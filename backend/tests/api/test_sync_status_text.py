from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.main import _sanitize_sync_status_message, _sync_status_payload


def test_sync_status_message_hides_stored_html_error_page() -> None:
    message = _sanitize_sync_status_message("<html><body>504 DNS look up failed</body></html>")

    assert message == ""


def test_sync_status_message_hides_old_direct_api_network_message() -> None:
    message = _sanitize_sync_status_message("iQUAVIS APIに接続できません。ネットワーク/DNS/プロキシ設定を確認してください。")

    assert message == ""


def test_sync_status_payload_hides_events_recorded_before_server_start() -> None:
    started_at = datetime(2026, 5, 11, 13, 35, 9)
    event = SimpleNamespace(
        message="iquavis_editor定時同期に失敗しました",
        is_error=True,
        recorded_at=started_at - timedelta(seconds=1),
    )

    assert _sync_status_payload(event, started_at=started_at) == {
        "message": "",
        "is_error": False,
        "recorded_at": None,
    }


def test_sync_status_payload_keeps_events_recorded_after_server_start() -> None:
    started_at = datetime(2026, 5, 11, 13, 35, 9)
    event = SimpleNamespace(
        message="iquavis_editor定時同期が完了しました",
        is_error=False,
        recorded_at=started_at + timedelta(seconds=1),
    )

    assert _sync_status_payload(event, started_at=started_at) == {
        "message": "iquavis_editor定時同期が完了しました",
        "is_error": False,
        "recorded_at": "2026-05-11T13:35:10",
    }
