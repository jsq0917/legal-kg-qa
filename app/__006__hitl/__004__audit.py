"""HITL 审计链：每次状态变更一条 JSONL 记录（who / when / 状态哈希）。

为什么用哈希链而不是普通日志：审计的价值在于「事后能证明没被改过」。
每条记录同时保存变更前后的 checkpoint 规范化 sha256，下一条的 ``from_state_hash``
必须等于上一条的 ``to_state_hash``；``verify`` 就是重放这条链，任何一次绕开
审计的改写都会断链，能定位到第一个断点。

只记**挂起任务的生命周期**（挂起 / 超时转队列 / 人工处置），不记检索与生成，
避免把审计写成第二份业务日志。时间可注入（``clock``），便于模拟 24 h 超时。
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.path_utils import append_jsonl, read_jsonl  # noqa: E402


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


class AuditLog:
    def __init__(self, path: str | Path | None = None, clock: Callable[[], float] | None = None) -> None:
        if path is None:
            from config import settings

            path = settings.session_db.parent / "hitl_audit.jsonl"
        self.path = Path(path)
        self._clock = clock

    def _now(self) -> float:
        import time

        return (self._clock or time.time)()

    def record(
        self,
        *,
        task_id: str,
        actor: str,
        action: str,
        from_status: str,
        to_status: str,
        from_state_hash: str | None,
        to_state_hash: str,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entries = self.entries(task_id)
        at = self._now()
        row = {
            "seq": len(entries) + 1,
            "task_id": task_id,
            "at": at,
            "at_iso": _iso(at),
            "actor": actor,
            "action": action,
            "from_status": from_status,
            "to_status": to_status,
            "from_state_hash": from_state_hash,
            "to_state_hash": to_state_hash,
            "detail": detail or {},
        }
        append_jsonl(self.path, row)
        return row

    def entries(self, task_id: str | None = None) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = read_jsonl(self.path)
        if task_id is None:
            return rows
        return [r for r in rows if r.get("task_id") == task_id]

    def verify(self, task_id: str) -> dict[str, Any]:
        """重放单任务哈希链：相邻记录 from == 上一条 to，seq 连续，who/when/hash 齐全。"""
        rows = self.entries(task_id)
        problems: list[str] = []
        for i, row in enumerate(rows):
            if row.get("seq") != i + 1:
                problems.append(f"seq 不连续: 第 {i + 1} 条为 {row.get('seq')}")
            if not row.get("actor") or not row.get("at_iso") or not row.get("to_state_hash"):
                problems.append(f"第 {i + 1} 条缺 who/when/state-hash")
            if i > 0 and row.get("from_state_hash") != rows[i - 1].get("to_state_hash"):
                problems.append(f"第 {i + 1} 条 from_hash 与第 {i} 条 to_hash 不匹配")
        first = rows[0] if rows else None
        if first is not None and first.get("from_state_hash") is not None:
            problems.append("首条记录不应有 from_state_hash")
        return {
            "task_id": task_id,
            "ok": bool(rows) and not problems,
            "entries": len(rows),
            "transitions": [
                {
                    "seq": r["seq"],
                    "actor": r["actor"],
                    "action": r["action"],
                    "from": r["from_status"],
                    "to": r["to_status"],
                    "at": r["at"],
                    "at_iso": r["at_iso"],
                    "to_state_hash": r["to_state_hash"][:12],
                }
                for r in rows
            ],
            "problems": problems,
        }


def state_hash(blob: dict[str, Any]) -> str:
    """状态规范化哈希：字段排序、紧凑分隔符，避免 dict 顺序造成假差异。"""
    raw = json.dumps(blob, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
