"""会话状态持久化（SQLite）。

选 SQLite 而不是 Redis/PostgreSQL，理由写在 README：单机可复现、零外部依赖、
事务足够保证「进程重启后槽位不丢」。接口保持窄（保存/读取状态 + 追加轮次），
将来换成 Redis 只需实现同一组方法。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.logging_utils import get_logger  # noqa: E402

log = get_logger("session_store")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn_no INTEGER NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    payload_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_id, turn_no);
"""


class SessionStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            from config import settings

            db_path = settings.session_db
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA)
            # 旧库没有 owner 列：补一列，空串表示「本地演示、不校验归属」。
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(sessions)").fetchall()}
            if "owner" not in cols:
                conn.execute("ALTER TABLE sessions ADD COLUMN owner TEXT NOT NULL DEFAULT ''")

    # ---------------- 归属 ----------------
    def claim(self, session_id: str, owner: str) -> bool:
        """把会话绑到 owner；已被别人占用则返回 False。

        空 owner（未启用客户端标识）永远返回 True，保持本地演示行为不变。
        """
        if not owner:
            return True
        with self._connect() as conn:
            row = conn.execute("SELECT owner FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                # 先占位建行，后面 save_state 的 upsert 只更新 state_json，不会把 owner 冲掉。
                conn.execute(
                    "INSERT INTO sessions(session_id, state_json, owner) VALUES(?, '{}', ?)", (session_id, owner)
                )
                return True
            if row["owner"] and row["owner"] != owner:
                return False
            conn.execute(
                "UPDATE sessions SET owner=? WHERE session_id=? AND (owner IS NULL OR owner='')",
                (owner, session_id),
            )
        return True

    def owner_of(self, session_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT owner FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return (row["owner"] if row else "") or ""

    # ---------------- 状态 ----------------
    def save_state(self, session_id: str, state: dict[str, Any]) -> None:
        payload = json.dumps(state, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions(session_id, state_json) VALUES(?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET state_json=excluded.state_json, updated_at=datetime('now')",
                (session_id, payload),
            )

    def load_state(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT state_json FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return json.loads(row["state_json"]) if row else None

    def reset(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM turns WHERE session_id=?", (session_id,))

    # ---------------- 轮次 ----------------
    def append_turn(self, session_id: str, role: str, content: str, payload: dict[str, Any] | None = None) -> int:
        with self._connect() as conn:
            turn_no = (
                conn.execute("SELECT COALESCE(MAX(turn_no), 0) + 1 AS n FROM turns WHERE session_id=?", (session_id,)).fetchone()["n"]
            )
            conn.execute(
                "INSERT INTO turns(session_id, turn_no, role, content, payload_json) VALUES(?,?,?,?,?)",
                (session_id, turn_no, role, content, json.dumps(payload, ensure_ascii=False) if payload else None),
            )
        return int(turn_no)

    def turns(self, session_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT turn_no, role, content, payload_json FROM turns WHERE session_id=? ORDER BY turn_no",
                (session_id,),
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "turn_no": r["turn_no"],
                    "role": r["role"],
                    "content": r["content"],
                    "payload": json.loads(r["payload_json"]) if r["payload_json"] else None,
                }
            )
        return out

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT session_id, updated_at FROM sessions ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]
