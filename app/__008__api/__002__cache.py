"""响应级缓存：**同问、同参数、同模型版本**直接返回，不打 LLM。

key 口径（六个字段一起进 sha256，任何一项变了就换 key）：
``query``（归一化后）· ``mode`` · ``tier`` · ``llm`` 开关 · ``模型版本`` · ``提示词/闸门版本``
另外把 ``prompt_guard`` 开关也计入，否则 T28 的防护前后会互相污染缓存。

归一化只做「去首尾空白 + 内部空白折叠 + 英文小写」——**不做同义改写**，
避免把两个实质不同的问题折叠成同一条缓存（那会变成另一种「自证」）。

存储是**进程内 LRU + TTL**，不引入 Redis：多副本部署时需要换集中式缓存，
但本模块只暴露 ``get/set``，替换时只改这一处。值的写入与读取都做 deepcopy，
防止调用方原地修改（例如 server 会裁掉 items 再写回）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

# 提示词/闸门逻辑变更时递增：旧缓存自动失效，不用手工清
PROMPT_VERSION = "2026-10-30.1"
KEY_VERSION = "v1"


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def normalise_query(query: str) -> str:
    return " ".join(query.split()).lower()


def model_version() -> str:
    """缓存 key 里的「模型版本」。本地 Ollama 与 DeepSeek 都走这个函数，口径统一。"""
    try:
        from common.llm import get_llm

        llm = get_llm()
        if not llm.enabled:
            return "deterministic"
        return f"{llm.model}@{llm.base_url}"
    except Exception:  # 配置缺失时也要能算出 key
        return "unknown"


def make_key(
    query: str,
    mode: str,
    tier: str,
    llm: bool,
    version: str,
    guard: bool,
) -> str:
    payload = {
        "kv": KEY_VERSION,
        "prompt": PROMPT_VERSION,
        "q": normalise_query(query),
        "mode": mode.lower(),
        "tier": tier.upper(),
        "llm": bool(llm),
        "model": version,
        "guard": bool(guard),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class CacheEntry:
    value: dict[str, Any]
    created_at: float
    expires_at: float


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    sets: int = 0
    evictions: int = 0
    expired: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def snapshot(self, entries: int, max_entries: int, ttl_s: float) -> dict[str, Any]:
        total = self.hits + self.misses
        return {
            "entries": entries,
            "max_entries": max_entries,
            "ttl_s": ttl_s,
            "hits": self.hits,
            "misses": self.misses,
            "sets": self.sets,
            "evictions": self.evictions,
            "expired": self.expired,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
        }


class ResponseCache:
    """线程安全的 LRU + TTL 缓存。``max_entries <= 0`` 视为关闭。"""

    def __init__(self, max_entries: int = 256, ttl_s: float = 3600.0, enabled: bool = True) -> None:
        self.max_entries = max_entries
        self.ttl_s = ttl_s
        self.enabled = enabled and max_entries > 0 and ttl_s > 0
        self._data: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self.stats = CacheStats()

    def get(self, key: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                with self.stats._lock:
                    self.stats.misses += 1
                return None
            if entry.expires_at <= now:
                self._data.pop(key, None)
                with self.stats._lock:
                    self.stats.expired += 1
                    self.stats.misses += 1
                return None
            self._data.move_to_end(key)
            with self.stats._lock:
                self.stats.hits += 1
            return copy.deepcopy(entry.value)

    def set(self, key: str, value: dict[str, Any]) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        with self._lock:
            self._data[key] = CacheEntry(
                value=copy.deepcopy(value), created_at=now, expires_at=now + self.ttl_s
            )
            self._data.move_to_end(key)
            with self.stats._lock:
                self.stats.sets += 1
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)
                with self.stats._lock:
                    self.stats.evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            with self.stats._lock:
                return self.stats.snapshot(len(self._data), self.max_entries, self.ttl_s)


_CACHE: ResponseCache | None = None


def get_cache() -> ResponseCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = ResponseCache(
            max_entries=_env_int("CACHE_MAX_ENTRIES", 256),
            ttl_s=_env_float("CACHE_TTL_S", 3600.0),
            enabled=_env_int("CACHE_ENABLED", 1) != 0,
        )
    return _CACHE
