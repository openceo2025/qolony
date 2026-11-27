from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.iquavis import excel_server_save

# 会社PCは Windows PowerShell 5.1 でこのスクリプトを動かす。PowerShell 7 では通る書き方が
# 5.1 では落ちるので、5.1 そのもので確かめる。
WINDOWS_POWERSHELL = shutil.which("powershell.exe")
needs_windows_powershell = pytest.mark.skipif(
    WINDOWS_POWERSHELL is None, reason="Windows PowerShell 5.1 (powershell.exe) が必要"
)
SCRIPT_PATH = Path(excel_server_save.__file__).with_name("excel_server_save.ps1")

# スクリプトの関数定義だけを取り出し、Excel を起動せずに結果の集計を動かす。
# 結果は実物と同じく [ordered]@{} の列にする。5.1 の Measure-Object はこのキーを読めず、
# サーバー保存が終わった後の集計で落ちていた（2026-11-27 会社PC）。
TOTALS_HARNESS = """param([string]$ScriptPath, [string]$WorkbookPath)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$ErrorActionPreference = "Stop"
$ExpectedProjectCount = 3
$ast = [System.Management.Automation.Language.Parser]::ParseFile($ScriptPath, [ref]$null, [ref]$null)
$definitions = $ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false)
foreach ($definition in $definitions) {
  . ([scriptblock]::Create($definition.Extent.Text))
}
$results = New-Object System.Collections.Generic.List[object]
$results.Add([ordered]@{ sheet = "案件A全タスク"; status = "ok"; summary = "Total: 5"; total_count = 5; success_count = 5; failure_count = 0; automation_error = $false })
$results.Add([ordered]@{ sheet = "案件B全タスク"; status = "ok"; summary = ""; total_count = -1; success_count = -1; failure_count = 0; automation_error = $false })
$results.Add([ordered]@{ sheet = "案件C全タスク"; status = "failed"; summary = "Total: 4"; total_count = 4; success_count = 2; failure_count = 2; automation_error = $false })
Write-Result "failed" "One or more project server saves failed" $results
"""


def test_run_excel_server_save_keeps_credentials_out_of_command(tmp_path: Path, monkeypatch) -> None:
    workbook_path = tmp_path / "editor.xlsm"
    workbook_path.write_bytes(b"xlsm")
    calls: list[dict] = []

    def fake_run(command, **kwargs):
        calls.append({"command": command, **kwargs})
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"status": "ok", "project_count": 3}).encode("utf-8"),
            stderr=b"",
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
            stdout=json.dumps({"status": "failed", "message": "Excelが起動中です"}, ensure_ascii=False).encode(
                "utf-8"
            ),
            stderr=b"",
        ),
    )

    with pytest.raises(excel_server_save.ExcelServerSaveError, match="Excelが起動中です"):
        excel_server_save.run_excel_server_save(
            username="user-1",
            password="secret-password",
            workbook_path=workbook_path,
        )


def test_cp932_error_from_powershell_is_readable(tmp_path: Path, monkeypatch) -> None:
    # スクリプトが UTF-8 へ切り替える前に落ちると、5.1 は cp932 で書く。
    # 以前はこれを UTF-8 として読み、「�I�u�W�F�N�g」のような化けた文を画面に出していた。
    workbook_path = tmp_path / "editor.xlsm"
    workbook_path.write_bytes(b"xlsm")
    stderr_text = 'Measure-Object : オブジェクトに対する入力でプロパティ "total_count" が見つかりません。'

    monkeypatch.setattr(excel_server_save.os, "name", "nt")
    monkeypatch.setattr(excel_server_save, "_oem_encoding", lambda: "cp932")
    monkeypatch.setattr(
        excel_server_save.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=b"", stderr=stderr_text.encode("cp932")),
    )

    with pytest.raises(excel_server_save.ExcelServerSaveError) as raised:
        excel_server_save.run_excel_server_save(
            username="user-1",
            password="secret-password",
            workbook_path=workbook_path,
        )
    assert stderr_text in str(raised.value)


def test_decode_powershell_output_prefers_utf8() -> None:
    text = "案件A全タスク"
    assert excel_server_save.decode_powershell_output(text.encode("utf-8")) == text
    assert excel_server_save.decode_powershell_output(None) == ""


@needs_windows_powershell
def test_result_totals_work_on_windows_powershell_51(tmp_path: Path) -> None:
    harness = tmp_path / "harness.ps1"
    # 5.1 は BOM の無い .ps1 を cp932 として読むので、日本語を含むハーネスは BOM 付きで書く。
    harness.write_text(TOTALS_HARNESS, encoding="utf-8-sig")
    workbook_path = tmp_path / "editor.xlsm"

    completed = subprocess.run(
        [
            WINDOWS_POWERSHELL,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(harness),
            "-ScriptPath",
            str(SCRIPT_PATH),
            "-WorkbookPath",
            str(workbook_path),
        ],
        capture_output=True,
        timeout=120,
    )

    stderr = excel_server_save.decode_powershell_output(completed.stderr)
    assert completed.returncode == 0, stderr
    payload = excel_server_save.parse_result(excel_server_save.decode_powershell_output(completed.stdout))
    assert payload["project_count"] == 3
    assert payload["sent_count"] == 9
    assert payload["success_count"] == 7
    assert payload["failure_count"] == 2
    assert payload["unknown_summary_count"] == 1
    assert [project["sheet"] for project in payload["projects"]] == ["案件A全タスク", "案件B全タスク", "案件C全タスク"]
    # 無人実行で原因を追える唯一の手がかりなので、集計の後にログが残ることも確かめる。
    assert list((tmp_path / "qolony_logs").glob("excel_server_save_*.json"))


@needs_windows_powershell
def test_script_error_reaches_qolony_without_mojibake(tmp_path: Path) -> None:
    # ブックが無い段階で止まるので Excel は起動しない。日本語のパスがそのまま読めることを確かめる。
    missing_workbook = tmp_path / "会社データ" / "iquavis_editer_latest_ver.xlsm"
    command = excel_server_save.build_powershell_command(
        script_path=SCRIPT_PATH,
        workbook_path=missing_workbook,
        expected_project_count=3,
        timeout_seconds=60,
        powershell_executable=WINDOWS_POWERSHELL,
    )

    completed = subprocess.run(command, capture_output=True, timeout=120)

    payload = excel_server_save.parse_result(excel_server_save.decode_powershell_output(completed.stdout))
    assert completed.returncode == 1
    assert payload["status"] == "failed"
    assert str(missing_workbook) in payload["message"]
    assert payload["sent_count"] == 0
    assert payload["unknown_summary_count"] == 0
