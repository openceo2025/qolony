from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import main


def _runtime(passwords: dict[str, str]):
    users = [
        SimpleNamespace(Id="u-admin", Name="管理者", IsTopManager=True),
        SimpleNamespace(Id="u-member", Name="メンバー", IsTopManager=False),
    ]
    service = SimpleNamespace(list_users=lambda active_only=True: users)
    settings = SimpleNamespace(users=SimpleNamespace(passwords=passwords))
    return SimpleNamespace(service=service, settings=settings)


def test_verify_password_checks_on_server(monkeypatch) -> None:
    monkeypatch.setattr(main, "get_runtime", lambda: _runtime({"管理者": "secret"}))
    assert main.verify_user_password(main.PasswordCheckBody(user_id="u-admin", password="secret")) == {"ok": True}
    assert main.verify_user_password(main.PasswordCheckBody(user_id="u-admin", password="wrong")) == {"ok": False}
    # パスワード未設定の利用者は照合なしで通す（従来の画面側の挙動と同じ）
    assert main.verify_user_password(main.PasswordCheckBody(user_id="u-member", password="")) == {"ok": True}


def test_verify_password_unknown_user_is_404(monkeypatch) -> None:
    monkeypatch.setattr(main, "get_runtime", lambda: _runtime({}))
    with pytest.raises(HTTPException) as caught:
        main.verify_user_password(main.PasswordCheckBody(user_id="nobody", password="x"))
    assert caught.value.status_code == 404


def test_meta_does_not_send_passwords(monkeypatch) -> None:
    """パスワードの値は /api/meta に載せない。有無だけを返す。"""
    from datetime import date

    def user(user_id: str, name: str, top: bool):
        return SimpleNamespace(
            Id=user_id,
            Name=name,
            Nickname="",
            FullName="",
            LoginId="",
            IQuavisUserId=None,
            IsTopManager=top,
            IsActive=True,
            FreeTimeRate=0.2,
            CreateDate=None,
        )

    users = [user("u-admin", "管理者", True), user("u-member", "メンバー", False)]
    project = SimpleNamespace(
        Id="p", Name="QOLONY", StartDate=date(2026, 1, 1), EndDate=date(2026, 12, 31), CrashZonePct=20, CreateDate=None
    )
    service = SimpleNamespace(
        list_users=lambda active_only=True: users,
        get_top_manager=lambda: users[0],
        get_project=lambda: project,
    )
    settings = SimpleNamespace(
        users=SimpleNamespace(passwords={"管理者": "secret"}),
        split_templates=[],
        ui=SimpleNamespace(language="ja", messages={}, fallback_messages={}),
    )
    monkeypatch.setattr(main, "get_runtime", lambda: SimpleNamespace(service=service, settings=settings))
    payload = main.get_meta()
    assert [u["HasPassword"] for u in payload["users"]] == [True, False]
    assert all("Password" not in u for u in payload["users"])
    assert "secret" not in repr(payload)
