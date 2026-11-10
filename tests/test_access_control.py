"""访问控制与审计（LW-N3）：管理操作需要令牌；会话有归属；所有管理动作留痕。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.__005__dialog.__001__session_store import SessionStore
from app.__008__api import __000__server as server


@pytest.fixture()
def client(tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(server, "ADMIN_AUDIT_PATH", str(audit))
    return TestClient(server.app), audit


def test_cache_clear_open_in_local_demo(client):
    c, audit = client
    r = c.post("/admin/cache/clear")
    assert r.status_code == 200
    row = json.loads(audit.read_text(encoding="utf-8").splitlines()[-1])
    assert row["action"] == "cache_clear" and row["auth"] == "local_demo"
    assert row["result"] == "allowed_local_demo"


def test_cache_clear_requires_token_when_configured(client, monkeypatch):
    c, audit = client
    monkeypatch.setattr(server, "ADMIN_TOKEN", "s3cret")
    assert c.post("/admin/cache/clear").status_code == 403
    assert c.post("/admin/cache/clear", headers={"X-Admin-Token": "wrong"}).status_code == 403
    assert c.post("/admin/cache/clear", headers={"X-Admin-Token": "s3cret"}).status_code == 200
    results = [json.loads(line)["result"] for line in audit.read_text(encoding="utf-8").splitlines()]
    assert results == ["denied", "denied", "ok"]


def test_session_claim_rejects_other_client(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    assert store.claim("s1", "alice") is True
    assert store.owner_of("s1") == "alice"
    assert store.claim("s1", "bob") is False
    assert store.claim("s1", "") is True  # 未启用客户端标识时保持演示行为
