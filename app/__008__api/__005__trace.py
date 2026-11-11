"""请求级 trace：一次 `/ask` 的结构化耗时 / Token / 成本账本。

刻意**不引入 OpenTelemetry**（一个人维护不动，也不值得为演示拉起 collector）：
这里只有三样东西——``contextvars`` 传递当前请求、一个 ``dict`` 装字段、
一行 jsonl 落盘。开销只在本进程内做几次算术，不拖慢检索与生成。

口径（与报告 ``reports/observability.md`` 一一对应）：

* ``stages.retrieve_ms`` —— 直接取 ``RetrievalOutput.latency_ms``（检索器自己量的，
  不在外面再包一层计时器，避免「量出来的时间不是检索的时间」）；
* ``stages.llm_ms``      —— ``common.llm`` 的耗时钩子累计，覆盖该请求内全部
  ``LLMClient.chat`` 调用（plan + answer；repair 是确定性规则，不打 LLM）；
* ``stages.gate_ms``     —— 优先用 :func:`measure_gate_ms` 对同一份 state 重跑一次五闸得到的
  **实测值**（``gate_ms_source="measured"``）；未传时退回「总耗时 − 检索 − LLM」的
  **推导值**（``gate_ms_source="derived"``，含 LangGraph 编排开销）。A/B 档不跑闸门，
  记 0 并标 ``n/a``；残余未归因时间进 ``stages.overhead_ms``；
* ``llm.total_tokens``   —— 逐请求累计（钩子按 contextvar 归到当前请求，不读全局
  ``USAGE`` 差值，所以并发时也不会把别人的 token 算进来）；
* ``cost.cny``           —— 由 token 折算，不是账单金额，口径见
  :data:`COST_CNY_PER_1K_TOKENS`。
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from threading import Lock
from typing import Any

from app.__007__agents.__000__state import normalize_tier
from common.llm import reset_degradation_hook, reset_model_hook, set_degradation_hook, set_model_hook
from common.path_utils import append_jsonl, read_jsonl

TRACE_DIR = os.getenv("TRACE_DIR", "data/observability")
TRACE_PATH = os.path.join(TRACE_DIR, "trace.jsonl")

#: 成本折算口径（**不是官方价目表**）。来源：A6 实测 C 档 1,110 tok/query、
#: DeepSeek 每千次查询 ¥1.418 → ¥1.418 / 1,110 tok ≈ ¥0.0012775 / 1k tokens。
#: 可用环境变量 ``LLM_COST_CNY_PER_1K_TOKENS`` 覆盖；本地模型不计 API 费用。
COST_CNY_PER_1K_TOKENS = float(os.getenv("LLM_COST_CNY_PER_1K_TOKENS", "0.0012775"))
COST_BASIS = "A6 实测：C 档 1110 tok/query，DeepSeek ¥1.418/1k query → ¥0.0012775/1k tok"

_CURRENT: ContextVar[Trace | None] = ContextVar("lexweave_trace", default=None)
_WRITE_LOCK = Lock()


def model_is_paid(model: str | None) -> bool:
    """只有走 DeepSeek 这类付费 API 才计钱；本地 Ollama 记 0（电费不进这条链路）。"""
    return bool(model) and "deepseek" in model.lower()


def cost_cny(total_tokens: int) -> float:
    return round(total_tokens / 1000.0 * COST_CNY_PER_1K_TOKENS, 6)


class Trace:
    """单请求账本。字段只增不改，``finish`` 时一次性算派生量。"""

    def __init__(
        self,
        path: str,
        *,
        query: str = "",
        tier: str = "",
        mode: str = "",
        cache: str = "miss",
        llm_enabled: bool = True,
        model: str | None = None,
        graph_build_id: str = "",
    ) -> None:
        self.request_id = uuid.uuid4().hex[:12]
        self.path = path
        self.query = query
        self.tier = (tier or "").upper()
        self.mode = mode
        self.cache = cache
        self.llm_enabled = llm_enabled
        self.model = model
        self.graph_build_id = graph_build_id
        self.answer_version: int = 0
        self.superseded_versions: list[int] = []
        self.degradation: dict[str, Any] | None = None
        self.t0 = time.perf_counter()
        self.retrieve_ms: float | None = None
        self.llm_ms: float = 0.0
        self.llm_calls: int = 0
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.events: list[dict[str, Any]] = []

    # ---------------- 记账 ----------------
    def record_retrieval(self, latency_ms: float | None) -> None:
        if latency_ms is not None:
            self.retrieve_ms = round(float(latency_ms), 1)

    def record_llm(self, elapsed_ms: float, prompt_tokens: int, completion_tokens: int) -> None:
        self.llm_ms += float(elapsed_ms)
        self.llm_calls += 1
        self.prompt_tokens += int(prompt_tokens or 0)
        self.completion_tokens += int(completion_tokens or 0)

    def event(self, name: str, **payload: Any) -> None:
        """给前端用的轻量事件（如 ``guard_block`` / ``cache_hit``），不上时间轴。"""
        self.events.append({"name": name, **payload})

    def record_degradation(self, reason: str, detail: str = "", model: str | None = None) -> None:
        """记录一次「降级答案」的成因：无 Key / 上游失败 / 输出非法 / 低置信。"""
        self.degradation = {"reason": reason, "detail": detail[:300], "model": model}

    # ---------------- 收口 ----------------
    def finish(
        self,
        state: dict[str, Any] | None = None,
        *,
        total_ms: float | None = None,
        cache: str | None = None,
        status: str = "ok",
        gate_ms: float | None = None,
    ) -> dict[str, Any]:
        state = state or {}
        total = round(
            float(total_ms) if total_ms is not None else (time.perf_counter() - self.t0) * 1000, 1
        )
        retrieval = state.get("retrieval") or {}
        if self.retrieve_ms is None:
            self.record_retrieval(retrieval.get("latency_ms"))
        checks = state.get("checks") or []
        tier = normalize_tier(state.get("tier") or self.tier)
        is_c = tier in ("C", "A_gates")
        cached = cache or self.cache

        if cached == "hit":
            # 命中缓存时这次请求什么都没跑：阶段耗时全部记 0，
            # 不能把缓存里上一次的耗时搬过来当成「本次检索耗时」。
            retrieve = 0.0
            llm = 0.0
            gate = 0.0
            gate_source = "cache_hit"
            overhead = 0.0
        else:
            retrieve = self.retrieve_ms or 0.0
            llm = round(self.llm_ms, 1)
            if not is_c:
                gate = 0.0
                gate_source = "n/a"
            elif gate_ms is not None:
                gate = round(float(gate_ms), 1)
                gate_source = "measured"
            else:
                gate = round(max(0.0, total - retrieve - llm), 1)
                gate_source = "derived"
            overhead = round(max(0.0, total - retrieve - llm - gate), 1)
        total_tokens = self.prompt_tokens + self.completion_tokens
        paid = model_is_paid(self.model)
        cost = cost_cny(total_tokens) if paid else 0.0

        trace = {
            "request_id": self.request_id,
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "path": self.path,
            "query": self.query,
            "graph_build_id": state.get("graph_build_id") or self.graph_build_id,
            "tier": tier,
            "mode": state.get("mode") or self.mode,
            "cache": cached,
            "status": status,
            "answer_version": state.get("answer_version", self.answer_version),
            "superseded_versions": state.get("superseded_versions", self.superseded_versions),
            "degradation": self.degradation,
            "latency_ms": total,
            "stages": {
                "retrieve_ms": retrieve,
                "llm_ms": llm,
                "gate_ms": gate,
                "overhead_ms": overhead,
                "gate_ms_is_derived": gate_source == "derived",
                "gate_ms_source": gate_source,
            },
            "llm": {
                "enabled": self.llm_enabled,
                "model": self.model,
                "calls": state.get("llm_calls", self.llm_calls),
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": total_tokens,
            },
            "cost": {
                "cny": cost,
                "cny_per_1k_tokens": COST_CNY_PER_1K_TOKENS,
                "paid_model": paid,
                "basis": COST_BASIS if paid else "确定性/本地路径，不计 API 费用",
            },
            "gates": {
                "count": len(checks),
                "all_pass": bool(checks) and all(c.get("ok") for c in checks),
                "failed": [c.get("gate") for c in checks if not c.get("ok")],
                "items": checks,
            },
            "refused": bool(state.get("refused", False)),
            "events": self.events,
        }
        return trace


@contextmanager
def trace_scope(trace: Trace) -> Iterator[Trace]:
    """把已有 trace 绑定到**当前**同步上下文（set/reset 必须同进同出）。

    流式生成器由 FastAPI 在线程池里逐次 ``next()`` 迭代，每次调用都跑在复制过的
    Context 里；跨 ``yield`` 持有 ContextVar token 会在结束时到另一个 Context 复位，
    抛 ``ValueError: ... created in a different Context``。因此流式路径不在这里长期绑定，
    而是由迭代器在**每一次** ``next()`` 内部进出 :func:`trace_scope`。
    """
    token = _CURRENT.set(trace)
    hook_token = set_degradation_hook(trace.record_degradation)
    model_token = set_model_hook(_record_actual_model)
    try:
        yield trace
    finally:
        reset_model_hook(model_token)
        reset_degradation_hook(hook_token)
        _CURRENT.reset(token)


@contextmanager
def trace_request(
    path: str,
    *,
    query: str = "",
    tier: str = "",
    mode: str = "",
    cache: str = "miss",
    llm_enabled: bool = True,
    model: str | None = None,
    graph_build_id: str = "",
) -> Iterator[Trace]:
    trace = Trace(
        path,
        query=query,
        tier=tier,
        mode=mode,
        cache=cache,
        llm_enabled=llm_enabled,
        model=model,
        graph_build_id=graph_build_id,
    )
    with trace_scope(trace):
        yield trace


def _record_actual_model(model: str | None, degraded_reason: str | None = None) -> None:
    """把 trace 里的 ``model`` 换成真正返回内容的模型（而不是配置里的主模型）。"""
    trace = _CURRENT.get()
    if trace is None or not model:
        return
    trace.model = model
    if degraded_reason:
        trace.record_degradation(degraded_reason, f"主模型失败，实际使用 {model}", model)


def record_llm_timing(elapsed_ms: float, prompt_tokens: int, completion_tokens: int) -> None:
    """``common.llm`` 的钩子入口：只有处在 trace 上下文里才记账，其余请求零开销。"""
    trace = _CURRENT.get()
    if trace is not None:
        trace.record_llm(elapsed_ms, prompt_tokens, completion_tokens)


def measure_gate_ms(
    state: dict[str, Any], article_index: dict[str, dict], art_concepts: dict[str, set[str]]
) -> float:
    """用同一份 state 再跑一次五闸，只为了拿到耗时。

    ``flows.py::check`` 不返回耗时，而任务边界禁止改 ``flows.py`` / ``gates.py``；
    五闸是纯函数、无副作用，重复调用一次（毫秒级）得到的就是这一轮闸门的真实耗时。
    """
    from app.__007__agents.__001__gates import run_gates

    t0 = time.perf_counter()
    run_gates(state.get("answer", ""), state.get("retrieval", {}), article_index, art_concepts)
    return (time.perf_counter() - t0) * 1000


_HOOK_INSTALLED = False


def install_llm_hook() -> None:
    """把耗时钩子挂到 ``LLMClient``。幂等，进程内只需一次。"""
    global _HOOK_INSTALLED
    if _HOOK_INSTALLED:
        return
    from common.llm import set_timing_hook

    set_timing_hook(record_llm_timing)
    _HOOK_INSTALLED = True


def write_trace(trace: dict[str, Any], path: str = TRACE_PATH) -> None:
    with _WRITE_LOCK:
        append_jsonl(path, trace)


# --------------------------------------------------------------------------------
# 看板汇总
# --------------------------------------------------------------------------------
def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 1)
    pos = (len(ordered) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(ordered) - 1)
    value = ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)
    return round(value, 1)


def summarize_traces(path: str = TRACE_PATH, limit: int = 200) -> dict[str, Any]:
    """读最后 ``limit`` 条 trace，聚合出「请求数 / token / 成本 / 延迟 / 缓存命中」。

    ``limit`` 是为了看板刷新速度；不是全量统计，报告里必须写清抽样窗口。
    """
    try:
        rows = read_jsonl(path)
    except FileNotFoundError:
        rows = []
    rows = rows[-limit:]

    def bucket() -> dict[str, Any]:
        return {"n": 0, "latency_ms": [], "total_tokens": 0, "cost_cny": 0.0, "llm_calls": 0}

    overall = bucket()
    by_tier: dict[str, dict[str, Any]] = {}
    by_path: dict[str, dict[str, Any]] = {}
    for row in rows:
        tier = str(row.get("tier") or "-")
        path_key = str(row.get("path") or "-")
        for target in (overall, by_tier.setdefault(tier, bucket()), by_path.setdefault(path_key, bucket())):
            target["n"] += 1
            target["latency_ms"].append(float(row.get("latency_ms") or 0.0))
            target["total_tokens"] += int((row.get("llm") or {}).get("total_tokens") or 0)
            target["cost_cny"] += float((row.get("cost") or {}).get("cny") or 0.0)
            target["llm_calls"] += int((row.get("llm") or {}).get("calls") or 0)

    def finalize(item: dict[str, Any]) -> dict[str, Any]:
        lat = item["latency_ms"]
        return {
            "n": item["n"],
            "p50_ms": _percentile(lat, 0.5),
            "p95_ms": _percentile(lat, 0.95),
            "total_tokens": item["total_tokens"],
            "cost_cny": round(item["cost_cny"], 6),
            "llm_calls": item["llm_calls"],
        }

    hits = sum(1 for r in rows if r.get("cache") == "hit")
    return {
        "window": {"rows": len(rows), "limit": limit, "path": path},
        "overall": finalize(overall),
        "cache_hit_rate": round(hits / len(rows), 4) if rows else 0.0,
        "by_tier": {k: finalize(v) for k, v in by_tier.items()},
        "by_path": {k: finalize(v) for k, v in by_path.items()},
        "cost_cny_per_1k_tokens": COST_CNY_PER_1K_TOKENS,
        "cost_basis": COST_BASIS,
        "last_ts": rows[-1].get("ts") if rows else None,
    }


def recent_traces(path: str = TRACE_PATH, n: int = 20) -> list[dict[str, Any]]:
    try:
        rows = read_jsonl(path)
    except FileNotFoundError:
        return []
    return rows[-n:][::-1]
