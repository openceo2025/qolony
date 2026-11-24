from __future__ import annotations

from app.iquavis.excel_server_save import format_excel_server_save_event


def test_reports_how_many_changes_were_actually_sent() -> None:
    message, warning = format_excel_server_save_event(
        {"project_count": 3, "sent_count": 12, "success_count": 12, "failure_count": 0}
    )
    assert "3案件" in message
    assert "送信12件" in message
    assert "成功12件" in message
    assert warning is False


def test_sending_nothing_is_not_reported_as_a_clean_save() -> None:
    # 1件も送っていないのに「保存完了」と読めたため、反映漏れに月曜まで気づけなかった。
    message, warning = format_excel_server_save_event(
        {"project_count": 3, "sent_count": 0, "success_count": 0, "failure_count": 0}
    )
    assert "送信0件" in message
    assert "送った変更はありません" in message
    assert warning is True


def test_failures_are_visible_in_the_message() -> None:
    message, _warning = format_excel_server_save_event(
        {"project_count": 3, "sent_count": 10, "success_count": 7, "failure_count": 3}
    )
    assert "失敗3件" in message


def test_unreadable_summary_is_flagged_rather_than_hidden() -> None:
    message, warning = format_excel_server_save_event(
        {
            "project_count": 3,
            "sent_count": 5,
            "success_count": 5,
            "failure_count": 0,
            "unknown_summary_count": 1,
        }
    )
    assert "件数を読み取れませんでした" in message
    assert warning is True


def test_missing_counts_do_not_crash_the_nightly_run() -> None:
    # PowerShellのMeasure-Objectは対象0件でnullを返す。旧バージョンの結果も来うる。
    message, warning = format_excel_server_save_event({"project_count": 3})
    assert "送信0件" in message
    assert warning is True


def test_project_results_are_summarised_for_later_diagnosis() -> None:
    # 夜間は無人なので、失敗理由が残らないと開発側から原因を追えない。
    # ログ設定はテスト間で汚れるため、行の組み立てだけを純粋関数として確かめる。
    from app.iquavis.excel_server_save import build_project_log_lines

    lines = build_project_log_lines(
        {
            "projects": [
                {
                    "sheet": "551全タスク",
                    "status": "failed",
                    "total_count": 4,
                    "success_count": 1,
                    "failure_count": 3,
                    "summary": "Total: 4 / Success: 1 / Failure: 3\r\nFailureDetail:\r\n[行12] PUT ... (HTTP 400)",
                },
                {
                    "sheet": "VC74全タスク",
                    "status": "ok",
                    "total_count": 2,
                    "success_count": 2,
                    "failure_count": 0,
                },
            ]
        }
    )

    assert [level for level, _message in lines] == ["WARNING", "INFO"]
    failed = lines[0][1]
    assert "551全タスク" in failed
    assert "failure=3" in failed
    assert "[行12]" in failed
    # 改行を潰して1行に収める。ログが行ごとにばらけると追いにくい。
    assert "\r\n" not in failed


def test_summary_building_tolerates_a_missing_projects_key() -> None:
    from app.iquavis.excel_server_save import build_project_log_lines

    assert build_project_log_lines({}) == []
    assert build_project_log_lines({"projects": "unexpected"}) == []
