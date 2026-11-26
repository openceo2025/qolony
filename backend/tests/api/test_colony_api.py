from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import main
from app.tms_core.services.colony import ColonyService
from tests.services.test_rankings import _build_service


def _client(tmp_path, monkeypatch):
    service, repository, ids = _build_service(tmp_path)
    runtime = SimpleNamespace(colony=ColonyService(repository=repository, tms=service), service=service)
    monkeypatch.setattr(main, "get_runtime", lambda: runtime)
    # 起動時の初期化（会社データの読み込みなど）はこのテストでは走らせない。
    monkeypatch.setattr(main.app.router, "on_startup", [])
    monkeypatch.setattr(main.app.router, "on_shutdown", [])
    return TestClient(main.app), ids


def test_missing_role_is_403_with_message(tmp_path, monkeypatch) -> None:
    client, ids = _client(tmp_path, monkeypatch)
    res = client.post(
        "/api/colony/policies",
        json={"user_id": ids["worker"], "kind": "text", "text": "方針", "x": 0, "y": 0},
    )
    assert res.status_code == 403
    assert res.json()["code"] == "colony_permission"
    assert "社長" in res.json()["detail"]


def test_president_digs_room_and_members_join(tmp_path, monkeypatch) -> None:
    client, ids = _client(tmp_path, monkeypatch)
    res = client.post("/api/colony/rooms", json={"user_id": ids["top"], "name": "営業課"})
    assert res.status_code == 200
    team_id = res.json()["team_id"]
    assert client.post(
        f"/api/colony/rooms/{team_id}/join", json={"user_id": ids["top"], "member_user_id": ids["worker"]}
    ).status_code == 200
    org = client.get("/api/colony/org", params={"user_id": ids["worker"]}).json()
    assert org["Teams"][0]["MemberIds"] == [ids["worker"]]
    assert org["Roles"][ids["top"]] == "president"
    contributions = client.get(
        "/api/colony/contributions", params={"user_id": ids["worker"], "year_month": "2026-05"}
    ).json()
    assert len(contributions["Months"]) == 6
