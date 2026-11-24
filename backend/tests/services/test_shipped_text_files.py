from __future__ import annotations

from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]

# 配布物へ入る、人が読む/実行するテキスト。ここに制御文字が混ざると現地で詰まる。
SHIPPED_TEXT_FILES = [
    *sorted((REPO_ROOT / "docs").glob("0*.md")),
    REPO_ROOT / "scripts" / "build_offline_bundle.ps1",
    REPO_ROOT / "backend" / "app" / "iquavis" / "excel_server_save.ps1",
]

# 許すのは改行とタブだけ。バックスペース等が入るとパスや文面が壊れる。
ALLOWED_CONTROL_CHARS = {"\n", "\r", "\t"}


@pytest.mark.parametrize("path", SHIPPED_TEXT_FILES, ids=lambda p: p.name)
def test_shipped_text_has_no_stray_control_characters(path: Path) -> None:
    """`\b` などがバックスペース文字として展開される事故を検出する。

    実際に `D:\QOLONY_UPDATE\bundle` が `D:\QOLONY_UPDATE<BS>undle` になり、
    手順書のインポート先とビルドスクリプトの出力先が壊れた。
    """
    if not path.is_file():
        pytest.skip(f"not present: {path}")
    text = path.read_text(encoding="utf-8")
    offenders = [
        (index, repr(char))
        for index, char in enumerate(text)
        if ord(char) < 32 and char not in ALLOWED_CONTROL_CHARS
    ]
    assert offenders == [], f"{path.name} に制御文字: {offenders[:5]}"


def test_powershell_scripts_stay_ascii_only() -> None:
    """PowerShell 5.1 はBOM無し .ps1 をANSI(cp932)として読む。

    非ASCIIを入れると化けるだけでなく、直後の行を飲み込んでコードが消える。
    """
    for path in SHIPPED_TEXT_FILES:
        if path.suffix.lower() != ".ps1" or not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            raw.decode("ascii")
        except UnicodeDecodeError as exc:  # pragma: no cover - 失敗時のみ
            pytest.fail(f"{path.name} に非ASCII: {exc}")
