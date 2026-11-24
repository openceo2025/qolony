from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_script_module():
    repo_root = Path(__file__).resolve().parents[3]
    script_path = repo_root / "scripts" / "discover_iquavis_domain_tasks.py"
    spec = importlib.util.spec_from_file_location("discover_iquavis_domain_tasks", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_discover_cli_resolves_development_paths_when_offline_paths_are_absent(tmp_path, monkeypatch) -> None:
    module = _load_script_module()
    real_exists = Path.exists
    offline_editor = Path("C:/QOLONY_DATA/iquavis_editer_latest_ver.xlsm")

    def exists_without_offline_editor(path: Path) -> bool:
        if path == offline_editor:
            return False
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", exists_without_offline_editor)
    root = tmp_path / "repo"
    backend = root / "backend"
    docs = root / "docs"
    config = backend / "config"
    data = backend / "data"
    docs.mkdir(parents=True)
    config.mkdir(parents=True)
    data.mkdir(parents=True)
    editor = docs / "iquavis_editer_latest_ver.xlsm"
    link_targets = data / "iquavis_link_targets.xlsx"
    editor.touch()
    link_targets.touch()
    (config / "settings.yaml").write_text(
        "\n".join(
            [
                "integrations:",
                "  iquavis:",
                "    editor_workbook_path: C:/QOLONY_DATA/iquavis_editer_latest_ver.xlsm",
                "    link_targets_workbook_path: data/iquavis_link_targets.xlsx",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("QOLONY_IQUAVIS_EDITOR_PATH", raising=False)
    monkeypatch.delenv("QOLONY_IQUAVIS_LINK_TARGETS_PATH", raising=False)

    assert module._resolve_input_path(None, "editor", root=root, backend_dir=backend) == editor
    assert module._resolve_input_path(None, "link_targets", root=root, backend_dir=backend) == link_targets


def test_discover_cli_environment_path_wins_over_development_default(tmp_path, monkeypatch) -> None:
    module = _load_script_module()
    root = tmp_path / "repo"
    backend = root / "backend"
    docs = root / "docs"
    data = backend / "data"
    env_dir = tmp_path / "env"
    docs.mkdir(parents=True)
    data.mkdir(parents=True)
    env_dir.mkdir(parents=True)
    (docs / "iquavis_editer_latest_ver.xlsm").touch()
    env_editor = env_dir / "iquavis_editer_latest_ver.xlsm"
    env_editor.touch()
    monkeypatch.setenv("QOLONY_IQUAVIS_EDITOR_PATH", str(env_editor))

    assert module._resolve_input_path(None, "editor", root=root, backend_dir=backend) == env_editor
