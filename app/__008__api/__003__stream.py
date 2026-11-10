"""SSE 流式编排：检索 → **逐 token 生成** → 五闸校验 → 结束。

## 流式与五闸的顺序冲突：选「流式输出 + 末尾附校验结果」

* **为什么不选「先流后校正」**：五闸（``run_gates``）是确定性规则，但它只能在完整答案
  之后判定。若要「校正」就得在用户已经看到半段文字后再整段替换，前端会闪一下，
  而且替换用的确定性重建与 ``flows.py::repair`` 是同一套逻辑——在本任务里复制一遍
  等于埋一份会漂移的副本。边界又禁止改 ``flows.py``，所以不做文本覆盖。
* **选了之后是什么行为**：token 照常流出；结束时推一个 ``check`` 事件，带五道闸的
  逐项结果与 ``grounded``；``done`` 里再给一次总账。前端用「校验徽标」兜底，
  不做静默改写。代价：已经流出的错误文字收不回来（见 ``reports/api.md`` 的取舍段）。

## 与 ``/ask`` 的差异（必须写清楚，否则两套数字会被当成同一口径）

流式路径把 ``plan`` 节点降级为确定性摘要（不调 LLM）：C 档的 plan 是一次完整生成，
实测会把首个 token 推到 3–5 s，直接违反「首 token ≤ 2 s」。因此流式只保留
``retrieve → answer`` 两段，校验仍在末尾照做；非流式的 ``/ask`` 维持完整三节点。
"""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator as _Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.__007__agents.__001__gates import run_gates  # noqa: E402
from app.__007__agents.__002__flows import (  # noqa: E402
    ANSWER_SYSTEM,
    _deterministic_answer,
    _fmt_items,
    normalize_tier,
)
from app.__008__api.__002__cache import get_cache, make_key, model_version  # noqa: E402
from common import prompt_guard  # noqa: E402
from common.llm import USAGE, get_llm, llm_status  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from config import settings  # noqa: E402

log = get_logger("api.stream")

_TOKEN_CHUNK = 24  # 确定性答案回放时每片字数（只为让前端有「流」的节奏）

# 进程内记住鉴权失败的 key：``LLMClient.chat`` 成功后会记住可用 key，但流式路径
# 自己建 client，不复用那份记忆，于是每个请求都先打一次 401（实测 0.3–1.0 s）。
_BAD_KEYS: set[str] = set()


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunks(text: str, size: int = _TOKEN_CHUNK) -> _Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _plain_path_note(items: list[dict[str, Any]]) -> str:
    """与 ``flows.py::plan`` 的无 LLM 分支同一句法，保证两套路径的可比性。"""
    if not items:
        return "无候选条文"
    note = f"先看 {items[0]['citation']}"
    if len(items) > 1:
        note += f"，再核 {items[1]['citation']}"
    return note


def _open_stream(messages: list[dict[str, str]], key: str, *, max_tokens: int, temperature: float):
    from openai import OpenAI

    llm = get_llm()
    client = OpenAI(
        api_key=key, base_url=llm.base_url, timeout=settings.llm_timeout, max_retries=0
    )
    kwargs: dict[str, Any] = {
        "model": llm.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }
    try:
        return client.chat.completions.create(**kwargs, stream_options={"include_usage": True})
    except Exception:
        # 有些 OpenAI 兼容端点不认 stream_options；去掉再试一次，仍失败就抛给上层
        return client.chat.completions.create(**kwargs)


def _key_order() -> list[str]:
    """优先用已证明可用的 key，跳过进程内已鉴权失败的 key。"""
    llm = get_llm()
    keys = llm.candidate_keys()
    good = [k for k in keys if k not in _BAD_KEYS]
    return good or keys


def iter_llm_tokens(
    messages: list[dict[str, str]], *, max_tokens: int = 480, temperature: float = 0.0
) -> _Iterator[str]:
    """逐 token 产出。鉴权失败会换备用 key；**一旦已经吐出内容就绝不重试**（会重复半段）。"""
    llm = get_llm()
    if not llm.enabled:
        return
    for key in _key_order():
        try:
            stream = _open_stream(messages, key, max_tokens=max_tokens, temperature=temperature)
        except Exception as exc:
            if type(llm)._is_auth_error(exc):
                _BAD_KEYS.add(key)
                log.warning("流式调用鉴权失败（尾号 %s），换下一个 key", key[-4:])
                continue
            USAGE.failures += 1
            log.warning("流式调用失败: %s", exc)
            return
        usage = None
        try:
            for chunk in stream:
                if getattr(chunk, "usage", None) is not None:
                    usage = chunk.usage
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0].delta, "content", None)
                if delta:
                    yield delta
        except Exception as exc:
            USAGE.failures += 1
            log.warning("流式中断（已输出的部分保留）: %s", exc)
            return
        if usage is not None:
            USAGE.add(
                getattr(usage, "prompt_tokens", 0) or 0,
                getattr(usage, "completion_tokens", 0) or 0,
            )
        else:
            USAGE.add(0, 0)
        return


def _summary(retrieval: dict[str, Any], top_n: int = 5) -> dict[str, Any]:
    return {
        "level": retrieval.get("level"),
        "top1_similarity": retrieval.get("top1_similarity"),
        "coverage": retrieval.get("coverage"),
        "low_confidence": retrieval.get("low_confidence"),
        "items": retrieval.get("items", [])[:top_n],
        "linked": retrieval.get("linked"),
    }


def stream_answer(
    query: str,
    mode: str,
    tier: str,
    retriever: Any,
    article_index: dict[str, dict],
    art_concepts: dict[str, set[str]],
    use_llm: bool = True,
) -> _Iterator[str]:
    """SSE 生成器。同步生成器由 FastAPI 在线程池里迭代，不阻塞事件循环。"""
    tier_u = normalize_tier(tier)
    want_llm = bool(use_llm) and bool(llm_status()["enabled"])
    guard = prompt_guard.guard_enabled()
    key = make_key(query, mode, tier_u, want_llm, model_version(), guard)
    cache = get_cache()
    t0 = time.perf_counter()

    def elapsed_ms() -> float:
        return round((time.perf_counter() - t0) * 1000, 1)

    hit = cache.get(key)
    if hit is not None:
        yield sse("meta", {"cache": "hit", "retrieval": hit.get("retrieval"), "retrieval_ms": 0.0})
        first_token: float | None = None
        for piece in _chunks(hit.get("answer", "")):
            if first_token is None:
                first_token = elapsed_ms()
            yield sse("token", {"text": piece})
        checks = hit.get("checks") or []
        if checks:
            yield sse("check", {"checks": checks, "grounded": all(c["ok"] for c in checks), "tier": tier_u})
        yield sse(
            "done",
            {
                "answer": hit.get("answer", ""),
                "citations": hit.get("citations", []),
                "refused": hit.get("refused", False),
                "checks": checks,
                "ttft_ms": first_token if first_token is not None else elapsed_ms(),
                "total_ms": elapsed_ms(),
                "cache": "hit",
            },
        )
        return

    retrieval = retriever.retrieve(query, mode=mode).as_dict()
    items = retrieval.get("items", [])[:4]
    yield sse(
        "meta",
        {
            "cache": "miss",
            "query": query,
            "mode": mode,
            "tier": tier_u,
            "retrieval": _summary(retrieval),
            "retrieval_ms": retrieval.get("latency_ms"),
        },
    )

    path_note = _plain_path_note(items)
    answer = ""
    first_token_ms: float | None = None
    if want_llm and items:
        messages = [
            {"role": "system", "content": ANSWER_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"用户问题：{query}\n推理链路参考：{path_note}\n\n"
                    f"可用法条：\n{_fmt_items(items)}\n\n请作答。"
                ),
            },
        ]
        parts: list[str] = []
        for tok in iter_llm_tokens(messages):
            if first_token_ms is None:
                first_token_ms = elapsed_ms()
            parts.append(tok)
            yield sse("token", {"text": tok})
        answer = "".join(parts).strip()

    if not answer:
        # 无 LLM / 无候选条文 / 流式失败 → 确定性答案，仍按分片回放
        answer = _deterministic_answer(query, retrieval, path_note)
        for piece in _chunks(answer):
            if first_token_ms is None:
                first_token_ms = elapsed_ms()
            yield sse("token", {"text": piece})

    checks = run_gates(answer, retrieval, article_index, art_concepts) if tier_u in ("C", "A_gates") else []
    if checks:
        yield sse("check", {"checks": checks, "grounded": all(c["ok"] for c in checks), "tier": tier_u})

    refused = "无法确定" in answer or "无法给出" in answer
    citations = [i.get("citation") for i in items]
    cache.set(
        key,
        {
            "answer": answer,
            "citations": citations,
            "checks": checks,
            "refused": refused,
            "tier": tier_u,
            "retrieval": _summary(retrieval),
        },
    )
    yield sse(
        "done",
        {
            "answer": answer,
            "citations": citations,
            "refused": refused,
            "checks": checks,
            "ttft_ms": first_token_ms if first_token_ms is not None else elapsed_ms(),
            "total_ms": elapsed_ms(),
            "cache": "miss",
        },
    )
