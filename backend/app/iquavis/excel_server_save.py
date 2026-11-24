from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


class ExcelServerSaveError(RuntimeError):
    """Excel/VBAによるiQUAVISサーバー保存に失敗した場合の例外。"""


def run_excel_server_save(
    *,
    username: str,
    password: str,
    workbook_path: Path,
    expected_project_count: int = 3,
    timeout_seconds: int = 900,
    powershell_executable: str = "powershell.exe",
) -> dict[str, Any]:
    """Excel COMから既存の「サーバーへ保存」VBAを3案件分実行する。"""
    if os.name != "nt":
        raise ExcelServerSaveError("Excel COM自動保存はWindows上でのみ実行できます")
    # 資格情報はブックの「ログイン情報」シート(B1/B2)を正とする。
    # QOLONY側に持たせると平文が設定ファイルと配布ZIPへ残るため、空のまま渡すことを許す。
    # 両方が空のときはVBA側が「シートのB1/B2を設定してください」で止める。

    resolved_workbook_path = Path(workbook_path)
    if not resolved_workbook_path.is_file():
        raise ExcelServerSaveError(f"iQUAVIS editorブックが見つかりません: {resolved_workbook_path}")
    if expected_project_count <= 0:
        raise ExcelServerSaveError("自動保存対象プロジェクト数は1以上で指定してください")
    if timeout_seconds <= 0:
        raise ExcelServerSaveError("Excel自動保存タイムアウトは1秒以上で指定してください")

    script_path = Path(__file__).with_name("excel_server_save.ps1")
    if not script_path.is_file():
        raise ExcelServerSaveError(f"Excel自動保存スクリプトが見つかりません: {script_path}")

    child_env = os.environ.copy()
    child_env["QOLONY_EXCEL_AUTOMATION_USERNAME"] = username
    child_env["QOLONY_EXCEL_AUTOMATION_PASSWORD"] = password
    command = [
        powershell_executable,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
        "-WorkbookPath",
        str(resolved_workbook_path),
        "-ExpectedProjectCount",
        str(expected_project_count),
        "-TimeoutSeconds",
        str(timeout_seconds),
        "-UseEnvironmentCredentials",
    ]

    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            timeout=timeout_seconds + 30,
        )
    except subprocess.TimeoutExpired as exc:
        raise ExcelServerSaveError(
            f"Excel自動保存がタイムアウトしました（{timeout_seconds}秒）"
        ) from exc
    except OSError as exc:
        raise ExcelServerSaveError(f"PowerShellまたはExcel自動保存を起動できません: {exc}") from exc

    payload = _parse_result(completed.stdout)
    if completed.returncode != 0:
        detail = _safe_detail(payload, completed.stderr)
        raise ExcelServerSaveError(f"Excel自動保存に失敗しました: {detail}")
    _log_projects(payload)
    if payload.get("status") != "ok":
        detail = _safe_detail(payload, completed.stderr)
        raise ExcelServerSaveError(f"Excel自動保存が完了しませんでした: {detail}")
    return payload


def _parse_result(stdout: str) -> dict[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return {}
    try:
        payload = json.loads(lines[-1])
    except json.JSONDecodeError:
        return {"message": lines[-1][:500]}
    return payload if isinstance(payload, dict) else {"message": str(payload)[:500]}


def _safe_detail(payload: dict[str, Any], stderr: str) -> str:
    message = str(payload.get("message", "")).strip()
    if message:
        return message[:500]
    return str(stderr).strip()[:500] or "詳細はQOLONYのイベントログを確認してください"


def build_project_log_lines(payload: dict[str, Any]) -> list[tuple[str, str]]:
    """案件ごとの結果を (ログレベル, 1行メッセージ) の列にする。

    夜間の無人実行なので、失敗理由がその場に残らないと開発側で追えない。
    VBAの FailureDetail（失敗行と理由）もここに含まれる。
    改行は潰して1行に収める。ログが行ごとにばらけると原因を追いにくい。
    """
    projects = payload.get("projects")
    if not isinstance(projects, list):
        return []

    lines: list[tuple[str, str]] = []
    for project in projects:
        if not isinstance(project, dict):
            continue
        raw_summary = str(project.get("summary", ""))
        summary = raw_summary.replace("\r\n", " / ").replace("\n", " / ")
        message = (
            "excel server save: sheet=%s status=%s total=%s success=%s failure=%s summary=%s"
            % (
                project.get("sheet"),
                project.get("status"),
                project.get("total_count"),
                project.get("success_count"),
                project.get("failure_count"),
                summary[:1000],
            )
        )
        lines.append(("INFO" if project.get("status") == "ok" else "WARNING", message))
    return lines


def _log_projects(payload: dict[str, Any]) -> None:
    for level, message in build_project_log_lines(payload):
        if level == "INFO":
            logger.info(message)
        else:
            logger.warning(message)


def format_excel_server_save_event(result: dict[str, Any]) -> tuple[str, bool]:
    """夜間のExcelサーバー保存の結果を、1行で読める形にする。

    件数を必ず出す。以前は「完了しました（3案件）」としか出さなかったため、
    1件も送っていないのに保存できたと読めてしまい、iQUAVISへ反映されていないことに
    月曜まで気づけなかった。案件数ではなく送信件数が判断材料になる。
    """
    project_count = _as_count(result.get("project_count"))
    sent = _as_count(result.get("sent_count"))
    success = _as_count(result.get("success_count"))
    failure = _as_count(result.get("failure_count"))
    unknown = _as_count(result.get("unknown_summary_count"))

    parts = [f"{project_count}案件", f"送信{sent}件", f"成功{success}件", f"失敗{failure}件"]
    message = "iquavis_editor定時同期とサーバー保存が完了しました（" + " / ".join(parts) + "）"

    # 送信0件は異常ではないが、黙って成功扱いにはしない。
    if sent == 0:
        return (
            message + " ※iQUAVISへ送った変更はありません。黄色セルが無かったか、対象列が違います。",
            True,
        )
    if unknown > 0:
        return (
            message + f" ※{unknown}案件は件数を読み取れませんでした。Excelの結果表示を確認してください。",
            True,
        )
    return message, False


def _as_count(value: Any) -> int:
    """PowerShellのMeasure-Objectは対象0件でnullを返すため、Noneを0として扱う。"""
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
