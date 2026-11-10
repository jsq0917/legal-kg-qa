"""令牌桶限流：**IP 与 API Key 双维度**，纯 ASGI 中间件（不缓冲 SSE）。

为什么是纯 ASGI 而不是 ``BaseHTTPMiddleware``：
``/ask/stream`` 是长连接 SSE，基类中间件会把响应包进额外的 task 组，
一旦下游 ``StreamingResponse`` 的生成器是同步的，容易在客户端侧表现为「一次性吐出」。
直接处理 ``http.response.start`` 不会改变分块时序。

口径（可被环境变量覆盖，见 :class:`LimitConfig`）：
* 每 IP 一个桶、每个 ``X-API-Key`` 一个桶，**两个维度都放行**才通过；
* 拒绝时返回 ``429`` + ``Retry-After``（秒，向上取整）+ ``X-RateLimit-Dimension``；
* 桶按需创建并定期清扫，避免长尾 IP/Key 把进程内存打爆；
* **不引入 Redis**：单进程演示够用；多副本部署需要换集中式限流（见 ``reports/api.md``）。
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any

# 健康检查与文档页不占配额，否则探活会把自己限流掉
EXEMPT_PATHS = frozenset({"/health", "/docs", "/redoc", "/openapi.json", "/rate-limit/stats"})


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class LimitConfig:
    """限流参数。``ip_rps`` 单位是「每秒补充令牌数」，``*_burst`` 是桶容量。"""

    enabled: bool = True
    ip_rps: float = 5.0
    ip_burst: int = 10
    key_rps: float = 10.0
    key_burst: int = 20

    @classmethod
    def from_env(cls) -> LimitConfig:
        return cls(
            enabled=_env_int("RATE_LIMIT_ENABLED", 1) != 0,
            ip_rps=_env_float("RATE_LIMIT_IP_RPS", 5.0),
            ip_burst=_env_int("RATE_LIMIT_IP_BURST", 10),
            key_rps=_env_float("RATE_LIMIT_KEY_RPS", 10.0),
            key_burst=_env_int("RATE_LIMIT_KEY_BURST", 20),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "ip_rps": self.ip_rps,
            "ip_burst": self.ip_burst,
            "key_rps": self.key_rps,
            "key_burst": self.key_burst,
        }


class TokenBucket:
    """单桶：容量 ``capacity``，每秒补 ``rate`` 个令牌。"""

    __slots__ = ("capacity", "rate", "tokens", "updated", "_lock")

    def __init__(self, capacity: int, rate: float, now: float | None = None) -> None:
        self.capacity = max(1, int(capacity))
        self.rate = max(float(rate), 1e-6)
        self.tokens = float(self.capacity)
        self.updated = time.monotonic() if now is None else now
        self._lock = threading.Lock()

    def take(self, now: float | None = None) -> tuple[bool, float]:
        """取一个令牌。返回 ``(是否放行, 需等待秒数)``。"""
        now = time.monotonic() if now is None else now
        with self._lock:
            self.tokens = min(self.capacity, self.tokens + max(0.0, now - self.updated) * self.rate)
            self.updated = now
            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return True, 0.0
            return False, (1.0 - self.tokens) / self.rate

    def remaining(self) -> int:
        return max(0, int(self.tokens))


class RateLimiter:
    """双维度限流器：``{(维度, 标识): 桶}``，加锁保证线程安全。"""

    def __init__(self, config: LimitConfig | None = None) -> None:
        self._config = config or LimitConfig.from_env()
        self._buckets: dict[tuple[str, str], TokenBucket] = {}
        self._lock = threading.Lock()
        self._stats = {"allowed": 0, "denied": 0, "denied_ip": 0, "denied_key": 0}
        self._last_sweep = time.monotonic()

    @property
    def config(self) -> LimitConfig:
        return self._config

    def configure(self, config: LimitConfig) -> None:
        """运行期换参数（基准脚本用）；换参数即清空旧桶，避免新旧速率混算。"""
        with self._lock:
            self._config = config
            self._buckets.clear()
            self._last_sweep = time.monotonic()

    def _bucket(self, dimension: str, ident: str, rate: float, burst: int, now: float) -> TokenBucket:
        key = (dimension, ident)
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(burst, rate, now)
                self._buckets[key] = bucket
            return bucket

    def _sweep(self, now: float, every_s: float = 60.0, idle_s: float = 600.0) -> None:
        """定期清掉长期未用的桶（满桶 + 10 分钟没动过 = 删掉也无损）。"""
        with self._lock:
            if now - self._last_sweep < every_s:
                return
            self._last_sweep = now
            stale = [
                k
                for k, b in self._buckets.items()
                if now - b.updated > idle_s and b.tokens >= b.capacity
            ]
            for k in stale:
                self._buckets.pop(k, None)

    def allow(self, ip: str, api_key: str | None = None) -> tuple[bool, float, str]:
        """返回 ``(放行?, 需等待秒数, 命中的维度)``。"""
        cfg = self._config
        if not cfg.enabled:
            return True, 0.0, ""
        now = time.monotonic()
        self._sweep(now)
        ok, wait = self._bucket("ip", ip, cfg.ip_rps, cfg.ip_burst, now).take(now)
        if not ok:
            with self._lock:
                self._stats["denied"] += 1
                self._stats["denied_ip"] += 1
            return False, wait, "ip"
        if api_key:
            ok, wait = self._bucket("key", api_key, cfg.key_rps, cfg.key_burst, now).take(now)
            if not ok:
                with self._lock:
                    self._stats["denied"] += 1
                    self._stats["denied_key"] += 1
                return False, wait, "key"
        with self._lock:
            self._stats["allowed"] += 1
        return True, 0.0, ""

    def stats(self) -> dict[str, Any]:
        with self._lock:
            out = dict(self._stats)
            out["buckets"] = len(self._buckets)
        return out


def client_ip(scope: dict[str, Any]) -> str:
    """优先取 ``X-Forwarded-For`` 首段（部署在反向代理后），否则取连接地址。"""
    for raw_name, raw_value in scope.get("headers") or []:
        if raw_name == b"x-forwarded-for":
            first = raw_value.decode("latin-1").split(",")[0].strip()
            if first:
                return first
    client = scope.get("client")
    return client[0] if client else "unknown"


def header_value(scope: dict[str, Any], name: str) -> str | None:
    name_b = name.lower().encode()
    for raw_name, raw_value in scope.get("headers") or []:
        if raw_name == name_b:
            value = raw_value.decode("latin-1").strip()
            return value or None
    return None


class RateLimitMiddleware:
    """ASGI 中间件：命中限流时不调用下游，直接回 429。"""

    def __init__(self, app: Any, limiter: RateLimiter | None = None) -> None:
        self.app = app
        self.limiter = limiter or LIMITER

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cfg = self.limiter.config
        if (
            not cfg.enabled
            or scope.get("method") == "OPTIONS"
            or scope.get("path", "") in EXEMPT_PATHS
        ):
            await self.app(scope, receive, send)
            return

        allowed, wait, dimension = self.limiter.allow(
            client_ip(scope), header_value(scope, "x-api-key")
        )
        if not allowed:
            retry_after = max(1, math.ceil(wait))
            body = json.dumps(
                {"detail": "rate limit exceeded", "dimension": dimension, "retry_after": retry_after},
                ensure_ascii=False,
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"retry-after", str(retry_after).encode()),
                        (b"x-ratelimit-dimension", dimension.encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await self.app(scope, receive, send)


LIMITER = RateLimiter(LimitConfig.from_env())


def limiter_stats() -> dict[str, Any]:
    return {"config": LIMITER.config.as_dict(), **LIMITER.stats()}
