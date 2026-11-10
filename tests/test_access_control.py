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


class _FakeStore:
    def __init__(self) -> None:
        self.owners = {"s1": "alice"}

    def owner_of(self, sid: str) -> str:
        return self.owners.get(sid, "")


class _FakeDialog:
    store = _FakeStore()

    def history(self, sid: str) -> list:
        return [{"turn": 1, "session": sid}]


class _FakeHitl:
    def pending(self) -> list:
        return [{"id": "t1", "owner": "alice", "query": "q1"}, {"id": "t2", "owner": "bob", "query": "q2"}]

    def stats(self) -> dict:
        return {"pending": 2}


def test_history_hides_other_clients_in_public_mode(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(server, "_dialog", lambda: _FakeDialog())
    monkeypatch.setattr(server, "ADMIN_TOKEN", "s3cret")
    assert c.get("/chat/s1/history").status_code == 403  # 无客户端标识，不泄露
    assert c.get("/chat/s1/history", headers={"X-Client-Id": "bob"}).status_code == 403
    assert c.get("/chat/s1/history", headers={"X-Client-Id": "alice"}).status_code == 200
    assert c.get("/chat/s1/history", headers={"X-Admin-Token": "s3cret"}).status_code == 200


def test_hitl_list_scopes_to_owner_in_public_mode(client, monkeypatch):
    c, _ = client
    # 这条用例连发 4 个请求，先关掉限流，避免和默认 IP 桶互相干扰。
    from app.__008__api.__001__ratelimit import LIMITER

    old = LIMITER.config
    try:
        from app.__008__api.__001__ratelimit import LimitConfig

        LIMITER.configure(LimitConfig(enabled=False))
        monkeypatch.setattr(server, "_hitl", lambda: _FakeHitl())
        monkeypatch.setattr(server, "ADMIN_TOKEN", "s3cret")
        assert c.get("/hitl").status_code == 403  # 公开模式必须带身份
        alice = c.get("/hitl", headers={"X-Client-Id": "alice"})
        assert alice.status_code == 200 and [t["id"] for t in alice.json()["pending"]] == ["t1"]
        admin = c.get("/hitl", headers={"X-Admin-Token": "s3cret"})
        assert admin.status_code == 200 and len(admin.json()["pending"]) == 2
    finally:
        LIMITER.configure(old)
