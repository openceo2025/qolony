from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Literal

import yaml


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.iquavis.link_target_discovery import append_tasks_under_linked_domains  # noqa: E402


PathKind = Literal["editor", "link_targets"]


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip().replace("/", "-"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("日付は YYYY-MM-DD または YYYY/MM/DD で指定してください") from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "登録済みiQUAVISタスクドメイン配下の未登録タスクを、"
            "iquavis_link_targets.xlsx の QOLONY_linked_tasks へ追記します。"
        )
    )
    parser.add_argument(
        "--editor",
        required=False,
        type=Path,
        help="iquavis_editer_latest_ver.xlsm のパス",
    )
    parser.add_argument(
        "--link-targets",
        required=False,
        type=Path,
        help="iquavis_link_targets.xlsx のパス",
    )
    parser.add_argument(
        "--start-date-from",
        required=True,
        type=_parse_date,
        help="この日以降に開始するタスクだけを候補にします。例: 2026-05-01",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="元のxlsxを更新せず、一時コピーで候補数だけ確認します。",
    )
    parser.add_argument(
        "--skip-qolony-import",
        action="store_true",
        help="xlsx追記だけを実行し、QOLONY DBへの取り込みは行いません。",
    )
    parser.add_argument("--creator-user-name", help="取り込み時の作成者名。未指定なら既存既定値を使います。")
    parser.add_argument("--default-assignee-name", help="担当者が解決できない場合の既定担当者名。未指定なら既存既定値を使います。")
    return parser.parse_args()


def _as_path(value: Any, *, base_dir: Path | None = None) -> Path | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        return path
    if base_dir is not None:
        return (base_dir / path).resolve()
    return (Path.cwd() / path).resolve()


def _settings_candidates(kind: PathKind, *, backend_dir: Path) -> list[Path]:
    settings_path = backend_dir / "config" / "settings.yaml"
    if not settings_path.exists():
        return []
    raw = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
    iquavis_raw = ((raw.get("integrations") or {}).get("iquavis") or {})
    if kind == "editor":
        values = [
            iquavis_raw.get("editor_workbook_path"),
            iquavis_raw.get("workbook_path"),
        ]
    else:
        values = [iquavis_raw.get("link_targets_workbook_path")]
    return [path for value in values if (path := _as_path(value, base_dir=backend_dir)) is not None]


def _default_candidates(kind: PathKind, *, root: Path, backend_dir: Path) -> list[Path]:
    if kind == "editor":
        return [
            root / "docs" / "iquavis_editer_latest_ver.xlsm",
            root / "docs" / "iquavis_editer.xlsm",
            Path("C:/QOLONY_DATA/iquavis_editer_latest_ver.xlsm"),
        ]
    return [
        backend_dir / "data" / "iquavis_link_targets.xlsx",
        root / "deploy_bundle" / "backend" / "data" / "iquavis_link_targets.xlsx",
        Path("C:/qolony/backend/data/iquavis_link_targets.xlsx"),
    ]


def _resolve_input_path(
    explicit_path: Path | None,
    kind: PathKind,
    *,
    root: Path = ROOT,
    backend_dir: Path = BACKEND,
) -> Path:
    if explicit_path is not None:
        return _as_path(explicit_path) or explicit_path

    env_name = "QOLONY_IQUAVIS_EDITOR_PATH" if kind == "editor" else "QOLONY_IQUAVIS_LINK_TARGETS_PATH"
    env_path = _as_path(os.getenv(env_name))
    candidates = [
        *([env_path] if env_path is not None else []),
        *_settings_candidates(kind, backend_dir=backend_dir),
        *_default_candidates(kind, root=root, backend_dir=backend_dir),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate

    label = "iquavis_editer_latest_ver.xlsm" if kind == "editor" else "iquavis_link_targets.xlsx"
    checked = "\n  - ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"{label} が見つかりません。--{kind.replace('_', '-')} で明示指定してください。\n  - {checked}")


def _import_discovered_targets_to_qolony(
    *,
    editor_path: Path,
    creator_user_name: str | None,
    default_assignee_name: str | None,
) -> None:
    from app.runtime import build_runtime

    runtime = build_runtime(BACKEND)
    try:
        result = runtime.iquavis_sync.import_new_tasks_from_editor_workbook(
            workbook_path=editor_path,
            creator_user_name=creator_user_name,
            default_assignee_name=default_assignee_name,
        )
        print(f"qolony_imported_domains: {len(result.imported_task_domain_ids)}")
        print(f"qolony_imported_tasks: {len(result.imported_task_ids)}")
        print(f"qolony_skipped_existing: {len(result.skipped_existing_ids)}")
        print(f"qolony_missing_targets: {len(result.missing_target_ids)}")
    finally:
        runtime.iquavis_sync.close()


def main() -> int:
    args = parse_args()
    editor_path = _resolve_input_path(args.editor, "editor")
    link_targets_path = _resolve_input_path(args.link_targets, "link_targets")
    print(f"editor_workbook_path: {editor_path}")
    print(f"link_targets_workbook_path: {link_targets_path}")

    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="qolony_iquavis_discovery_") as temp_dir:
            temp_link_targets_path = Path(temp_dir) / link_targets_path.name
            shutil.copy2(link_targets_path, temp_link_targets_path)
            result = append_tasks_under_linked_domains(
                editor_workbook_path=editor_path,
                link_targets_workbook_path=temp_link_targets_path,
                start_date_from=args.start_date_from,
            )
            print("dry_run: true")
    else:
        result = append_tasks_under_linked_domains(
            editor_workbook_path=editor_path,
            link_targets_workbook_path=link_targets_path,
            start_date_from=args.start_date_from,
        )
    for key, value in result.__dict__.items():
        if isinstance(value, list):
            value = ",".join(value)
        print(f"{key}: {value}")
    if not args.dry_run and not args.skip_qolony_import:
        _import_discovered_targets_to_qolony(
            editor_path=editor_path,
            creator_user_name=args.creator_user_name,
            default_assignee_name=args.default_assignee_name,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
