from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.iquavis.mapping_seed import generate_mapping_seed_workbook
from app.tms_core.config import load_settings
from app.runtime import build_runtime


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _configured_db_path() -> Path:
    settings = load_settings(_backend_root() / "config" / "settings.yaml")
    db_path = settings.app.db_path
    if db_path.is_absolute():
        return db_path
    return (_backend_root() / db_path).resolve()


def _load_task_ids(args: argparse.Namespace) -> list[str]:
    task_ids = [str(item).strip() for item in getattr(args, "iquavis_task_id", []) if str(item).strip()]
    task_id_file = getattr(args, "task_id_file", None)
    if task_id_file:
        path = Path(task_id_file)
        for line in path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if text:
                task_ids.append(text.split(",")[0].strip())
    # 重複指定は読み込み順を保ったまま除外する。
    return list(dict.fromkeys(task_ids))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QOLONY iQUAVIS utility")
    sub = parser.add_subparsers(dest="command", required=True)

    pair_parser = sub.add_parser("pair-existing", help="既存QOLONYタスクにiQUAVISタスクIDを紐付ける")
    pair_parser.add_argument("--local-task-id", required=True)
    pair_parser.add_argument("--iquavis-task-id", required=True)

    import_parser = sub.add_parser("import-tasks", help="iQUAVISタスクを明示IDでQOLONYへ取り込む")
    import_parser.add_argument("--iquavis-task-id", action="append", default=[])
    import_parser.add_argument("--task-id-file")
    import_parser.add_argument("--creator-user-name")
    import_parser.add_argument("--default-assignee-name")

    xlsm_parser = sub.add_parser("import-xlsm", help="xlsmから選択行のiQUAVISタスクをQOLONYへ取り込む")
    xlsm_parser.add_argument("--workbook", required=True)
    xlsm_parser.add_argument("--sheet", action="append", required=True)
    xlsm_parser.add_argument("--creator-user-name")
    xlsm_parser.add_argument("--default-assignee-name")

    editor_parser = sub.add_parser(
        "import-editor-workbook",
        help="iquavis_editor.xlsm の同期対象IDからQOLONY未存在タスクを取り込む",
    )
    editor_parser.add_argument("--workbook")
    editor_parser.add_argument("--creator-user-name")
    editor_parser.add_argument("--default-assignee-name")

    mapping_parser = sub.add_parser("build-mapping-workbook", help="見た目ガントと未完了タスクからマッピング用xlsxを生成する")
    mapping_parser.add_argument("--workbook", required=True)
    mapping_parser.add_argument("--output", required=True)
    mapping_parser.add_argument("--db")
    mapping_parser.add_argument("--task-sheet", action="append", default=[])
    mapping_parser.add_argument("--visual-sheet", action="append", default=[])

    pull_parser = sub.add_parser("pull-once", help="paired task の iQUAVIS 更新を1回取り込む")
    pull_parser.add_argument("--verbose", action="store_true")

    push_parser = sub.add_parser("process-once", help="queued sync job を1回送信する")
    push_parser.add_argument("--verbose", action="store_true")

    tick_parser = sub.add_parser("run-once", help="outbound / inbound をまとめて1回実行する")
    tick_parser.add_argument("--verbose", action="store_true")

    editor_sync_parser = sub.add_parser(
        "editor-sync-once",
        help="iquavis_editor.xlsm の新規取り込み・競合判定を1回実行する",
    )
    editor_sync_parser.add_argument("--workbook")
    editor_sync_parser.add_argument("--verbose", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "build-mapping-workbook":
        try:
            db_path = Path(str(args.db)).resolve() if args.db else _configured_db_path()
            result = generate_mapping_seed_workbook(
                workbook_path=Path(str(args.workbook)),
                db_path=db_path,
                output_path=Path(str(args.output)),
                task_sheet_names=[str(name) for name in args.task_sheet] or None,
                visual_sheet_names=[str(name) for name in args.visual_sheet] or None,
            )
            print(f"output={result.output_path}")
            print(f"visual_sheets={len(result.visual_sheet_names)}")
            print(f"visual_items={len(result.visual_items)}")
            print(f"task_rows={len(result.task_rows)}")
            print(f"candidates={len(result.candidates)}")
            return 0
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1

    runtime = build_runtime(_backend_root())
    sync = runtime.iquavis_sync

    try:
        if args.command == "pair-existing":
            sync.pair_existing_task(
                local_task_id=str(args.local_task_id).strip(),
                iquavis_task_id=str(args.iquavis_task_id).strip(),
            )
            print("paired")
            return 0

        if args.command == "import-tasks":
            task_ids = _load_task_ids(args)
            local_ids = sync.import_remote_tasks(
                iquavis_task_ids=task_ids,
                creator_user_name=args.creator_user_name,
                default_assignee_name=args.default_assignee_name,
            )
            print(f"imported={len(local_ids)}")
            for local_id in local_ids:
                print(local_id)
            return 0

        if args.command == "import-xlsm":
            result = sync.import_tasks_from_xlsm(
                workbook_path=Path(str(args.workbook)),
                sheet_names=[str(name) for name in args.sheet],
                creator_user_name=args.creator_user_name,
                default_assignee_name=args.default_assignee_name,
            )
            print(f"selected={result.selected_count}")
            print(f"imported={len(result.imported_local_ids)}")
            print(f"skipped_existing={result.skipped_existing_count}")
            print(f"duplicates={result.duplicate_count}")
            for local_id in result.imported_local_ids:
                print(local_id)
            return 0

        if args.command == "import-editor-workbook":
            result = sync.import_new_tasks_from_editor_workbook(
                workbook_path=Path(str(args.workbook)) if args.workbook else None,
                creator_user_name=args.creator_user_name,
                default_assignee_name=args.default_assignee_name,
            )
            print(f"imported_domains={len(result.imported_task_domain_ids)}")
            print(f"imported_tasks={len(result.imported_task_ids)}")
            print(f"skipped_existing={len(result.skipped_existing_ids)}")
            print(f"missing_targets={len(result.missing_target_ids)}")
            for local_id in result.imported_task_domain_ids:
                print(local_id)
            for local_id in result.imported_task_ids:
                print(local_id)
            return 0

        if args.command == "pull-once":
            pulled = sync.pull_remote_updates()
            if args.verbose:
                print(f"pulled={pulled}")
            return 0

        if args.command == "process-once":
            processed = sync.process_outbound_jobs()
            if args.verbose:
                print(f"processed={processed}")
            return 0

        if args.command == "run-once":
            result = sync.run_background_tick()
            if args.verbose:
                print(f"processed={result['processed']}")
                print(f"pulled={result['pulled']}")
                print(f"editor_synced={result.get('editor_synced', 0)}")
            return 0

        if args.command == "editor-sync-once":
            result = sync.run_editor_workbook_sync(
                workbook_path=Path(str(args.workbook)) if args.workbook else None,
            )
            if args.verbose:
                print(f"imported_domains={len(result.imported.imported_task_domain_ids)}")
                print(f"imported_tasks={len(result.imported.imported_task_ids)}")
                print(f"local_updates_written={len(result.conflicts.local_updates_written)}")
                print(f"remote_updates_applied={len(result.conflicts.remote_updates_applied)}")
                print(f"conflicts={len(result.conflicts.conflict_task_ids)}")
            return 0

        parser.error("unknown command")
        return 2
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        sync.close()


if __name__ == "__main__":
    raise SystemExit(main())
