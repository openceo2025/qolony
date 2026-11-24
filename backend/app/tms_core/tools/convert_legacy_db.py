from __future__ import annotations

import argparse
from pathlib import Path

from app.tms_core.services.db_conversion import convert_legacy_db



def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="convert_legacy_db",
        description="旧QOLONY SQLite DBを最新スキーマへ変換します。",
    )
    parser.add_argument("--source", required=True, help="変換元DBパス")
    parser.add_argument("--output", help="変換先DBパス（省略時はin-place変換）")
    parser.add_argument(
        "--backend-root",
        help="backendルートパス（省略時はこのスクリプトから自動解決）",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="--output が既存ファイルの場合に上書きを許可",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="変換前バックアップを作成しない",
    )
    return parser



def main() -> int:
    args = _build_parser().parse_args()

    backend_root = (
        Path(args.backend_root).resolve()
        if args.backend_root
        else Path(__file__).resolve().parents[3]
    )

    result = convert_legacy_db(
        source_db_path=Path(args.source),
        project_root=backend_root,
        target_db_path=Path(args.output) if args.output else None,
        overwrite=bool(args.overwrite),
        make_backup=not bool(args.no_backup),
    )

    print(f"source_db={result.source_path}")
    print(f"target_db={result.target_path}")
    print(f"backup_db={result.backup_path if result.backup_path else '-'}")
    print(f"inferred_revision={result.inferred_revision}")
    print(f"initial_revision={result.initial_revision}")
    print(f"stamped_revision={result.stamped_revision if result.stamped_revision else '-'}")
    print(f"final_revision={result.final_revision}")
    print(f"head_revision={result.head_revision}")

    if result.final_revision != result.head_revision:
        print("warning=final_revision is not head_revision")
        return 2

    print("status=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
