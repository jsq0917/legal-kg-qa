"""人在环的挂起点：每个待复核任务一个 JSON 文件，落盘即「可恢复」。

用文件而不是数据库，是因为挂起点本质是「一次任务快照」：体积大（含检索结果）、
不需要查询、需要人能直接打开看。目录结构保证进程重启后按 task_id 找得回来。

T25 扩展：状态机多了 ``manual_queue``（超时自动转人工队列），并记录
``deadline_at`` / ``escalated_at`` / ``session_id``（多轮中途挂起）等字段。
旧文件没有这些键，靠 dataclass 默认值直接兼容反序列化。
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.logging_utils import get_logger  # noqa: E402

log = get_logger("checkpoint")


@dataclass
class Checkpoint:
    task_id: str
    query: str
    mode: str
    status: str  # awaiting_review | manual_queue | completed
    reason: str
    top1_similarity: float
    coverage: float
    retrieval: dict[str, Any] = field(default_factory=dict)
    answer: str | None = None
    resolution: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    # ---- T25：超时与多轮 ----
    deadline_at: float | None = None
    escalated_at: float | None = None
    assigned_to: str | None = None
    session_id: str | None = None
    turn_no: int | None = None
    dialog_state: dict[str, Any] | None = None
    #: 任务归属（`X-Client-Id`）。空串 = 本地演示，不校验归属。
    owner: str = ""

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    def state_hash(self) -> str:
        """当前状态的规范化 sha256，审计链用它来判断「这条记录对应哪一版状态」。"""
        raw = json.dumps(self.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CheckpointStore:
    def __init__(self, root: str | Path | None = None, clock: Callable[[], float] | None = None) -> None:
        if root is None:
            from config import settings

            root = settings.checkpoint_path
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._clock = clock

    def _now(self) -> float:
        return (self._clock or time.time)()

    def _path(self, task_id: str) -> Path:
        return self.root / f"{task_id}.json"

    def save(self, ckpt: Checkpoint) -> Path:
        ckpt.updated_at = self._now()
        path = self._path(ckpt.task_id)
        path.write_text(json.dumps(ckpt.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
        return path

    def load(self, task_id: str) -> Checkpoint | None:
        path = self._path(task_id)
        if not path.exists():
            return None
        blob = json.loads(path.read_text(encoding="utf-8"))
        return Checkpoint(**blob)

    def list(self, status: str | None = None) -> list[Checkpoint]:
        out = []
        for path in sorted(self.root.glob("*.json")):
            try:
                ckpt = Checkpoint(**json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
            if status is None or ckpt.status == status:
                out.append(ckpt)
        return out
