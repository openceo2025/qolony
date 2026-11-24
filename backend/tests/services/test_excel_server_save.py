from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.iquavis import excel_server_save


def test_run_excel_server_save_keeps_credentials_out_of_command(tmp_path: Path, monkeypatch) -> None:
    workbook_path = tmp_path / "editor.xlsm"
    workbook_path.write_bytes(b"xlsm")
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"status": "ok", "project_count": 3}),
            stderr="",
        )

    monkeypatch.setattr(excel_server_save.os, "name", "nt")
    monkeypatch.setattr(excel_server_save.subprocess, "run", fake_run)

    result = excel_server_save.run_excel_server_save(
        username="user-1",
        password="secret-password",
        workbook_path=workbook_path,
    )

    assert result["status"] == "ok"
    assert len(calls) == 1
    assert "secret-password" not in " ".join(calls[0]["command"])
    assert calls[0]["env"]["QOLONY_EXCEL_AUTOMATION_USERNAME"] == "user-1"
    assert calls[0]["env"]["QOLONY_EXCEL_AUTOMATION_PASSWORD"] == "secret-password"


def test_run_excel_server_save_raises_on_script_failure(tmp_path: Path, monkeypatch) -> None:
    workbook_path = tmp_path / "editor.xlsm"
    workbook_path.write_bytes(b"xlsm")

    monkeypatch.setattr(excel_server_save.os, "name", "nt")
    monkeypatch.setattr(
        excel_server_save.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1,
            stdout=json.dumps({"status": "failed", "message": "Excelが起動中です"}),
            stderr="",
        ),
    )

    with pytest.raises(excel_server_save.ExcelServerSaveError, match="Excelが起動中です"):
        excel_server_save.run_excel_server_save(
            username="user-1",
            password="secret-password",
            workbook_path=workbook_path,
        )
