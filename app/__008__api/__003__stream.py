"""SSE 流式编排：与 ``/ask`` 共用同一套流水线，只是把中间状态实时推给前端。

## 语义（与同步路径对齐）

* 档位决定调用次数：A / A_gates 不跑 plan，B / C 跑 plan；两边调用同一个
  :mod:`app.__007__agents.__003__pipeline`。
* **草稿不是终稿**：token 事件带 ``draft: true`` 与 ``answer_version``；
  通过五闸后才发 ``final``。高风险档（C / A_gates）在最终事件之前，正文不会被标记为最终答案。
* **不静默覆盖**：触发确定性修复时先发 ``superseded``（旧版本号 + 原因 + 失败闸门），
  再发新版本的 token，``done.superseded_versions`` 明确列出被替换的版本。
* **取消不留成功记录**：客户端断开（GeneratorExit）→ trace 记 ``cancelled``，不写缓存；
  上游超时 → ``timeout``；未捕获异常 → ``error``。三种情况都不会被记成 ok。

## 事件顺序

``meta`` →（``plan``，B/C）→ ``token``(draft, v1) → ``check`` →（``superseded`` → ``token``(draft, v2) → ``check``）*
→ ``final`` → ``done``；出错时追加 ``error``。只有 ``final`` 携带的正文才是已通过闸门的终稿。
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Iterator as _Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.__007__agents.__000__state import normalize_tier  # noqa: E402
from app.__007__agents.__003__pipeline import (  # noqa: E402
    after_check,
    answer_tokens,
    check_gates,
    deterministic_answer,
    is_refused,
    plan_state,
    repair_state,
)
from app.__008__api.__002__cache import get_cache, make_key, model_version  # noqa: E402
from app.__008__api.__005__trace import trace_request, write_trace  # noqa: E402
from common import prompt_guard  # noqa: E402
from common.llm import (
    LLMDisabled,
    LLMError,
    classify_error,
    llm_status,
    report_degradation,
)  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402

log = get_logger("api.stream")

_TOKEN_CHUNK = 24  # 确定性答案/缓存回放时每片字数（只为让前端有「流」的节奏）
#: 整条流的最长存活时间；超时后不再产出正文，明确告诉前端这次没有完成。
STREAM_TIMEOUT_S = float(os.getenv("STREAM_TIMEOUT_S", "120"))

#: SSE 事件 Schema：每种事件的**必有字段**。契约测试与前端都按这份表校验，
#: 少字段即视为回归（例如缓存回放漏了 ``answer_version``）。
EVENT_SCHEMA: dict[str, set[str]] = {
    "meta": {"cache"},
    "plan": {"path_note", "llm_calls"},
    "token": {"text", "draft", "answer_version"},
    "check": {"checks", "grounded", "tier", "answer_version"},
    "superseded": {"answer_version", "reason", "failed_gates", "next_version"},
    "final": {"answer", "citations", "refused", "checks", "answer_version", "superseded_versions"},
    "done": {"answer", "citations", "refused", "checks", "answer_version", "superseded_versions", "cache", "status"},
    "error": {"status", "detail"},
}
#: 事件顺序约束：这些事件必须出现在 ``final`` 之前。``superseded`` 只会在修复时出现。
BEFORE_FINAL = {"meta", "plan", "token", "check", "superseded"}


def schema_errors(events: list[tuple[str, dict[str, Any]]]) -> list[str]:
    """按 :data:`EVENT_SCHEMA` 校验事件序列；返回人类可读的问题列表（空 = 合法）。"""
    problems: list[str] = []
    names = [n for n, _ in events]
    if "final" not in names:
        problems.append("缺少 final 事件")
    if names.count("final") > 1:
        problems.append(f"final 事件出现 {names.count('final')} 次")
    if "final" in names:
        cut = names.index("final")
        if any(n in BEFORE_FINAL for n in names[cut + 1 :]):
            problems.append("final 之后仍出现中间事件")
    for i, (name, data) in enumerate(events):
        if name not in EVENT_SCHEMA:
            problems.append(f"未知事件 {name!r}（第 {i} 个）")
            continue
        missing = EVENT_SCHEMA[name] - set(data)
        if missing:
            problems.append(f"事件 {name!r} 缺字段 {sorted(missing)}")
    return problems


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _chunks(text: str, size: int = _TOKEN_CHUNK) -> _Iterator[str]:
    for i in range(0, len(text), size):
        yield text[i : i + size]


def _summary(retrieval: dict[str, Any], top_n: int = 5) -> dict[str, Any]:
    return {
        "level": retrieval.get("level"),
        "top1_similarity": retrieval.get("top1_similarity"),
        "coverage": retrieval.get("coverage"),
        "low_confidence": retrieval.get("low_confidence"),
        "items": retrieval.get("items", [])[:top_n],
        "linked": retrieval.get("linked"),
        "graph_build_id": retrieval.get("graph_build_id"),
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
    graph_build_id = ""
    try:
        graph_build_id = retriever.graph.store.graph_build_id
    except Exception:
        pass
    key = make_key(query, mode, tier_u, want_llm, model_version(), guard, graph_build_id=graph_build_id)
    cache = get_cache()
    t0 = time.perf_counter()
    status = "ok"
    cache_state = "miss"
    state: dict[str, Any] = {"tier": tier_u, "mode": mode, "checks": [], "graph_build_id": graph_build_id}

    def elapsed_ms() -> float:
        return round((time.perf_counter() - t0) * 1000, 1)

    with trace_request(
        "/stream",
        query=query,
        tier=tier_u,
        mode=mode,
        cache="miss",
        llm_enabled=want_llm,
        model=model_version(),
        graph_build_id=graph_build_id,
    ) as tr:
        try:
            hit = cache.get(key)
            if hit is not None:
                cache_state = "hit"
                state["retrieval"] = hit.get("retrieval") or {}
                state["graph_build_id"] = (
                    hit.get("graph_build_id") or state["retrieval"].get("graph_build_id") or graph_build_id
                )
                checks = hit.get("checks") or []
                version = hit.get("answer_version", 1)
                path_note = hit.get("path_note", "")
                yield sse(
                    "meta",
                    {
                        "cache": "hit",
                        "graph_build_id": state["graph_build_id"],
                        "retrieval": hit.get("retrieval"),
                        "retrieval_ms": 0.0,
                    },
                )
                if path_note and tier_u in ("B", "C"):
                    state["path_note"] = path_note
                    yield sse("plan", {"path_note": path_note, "llm_calls": 0, "cached": True})
                first_token: float | None = None
                for piece in _chunks(hit.get("answer", "")):
                    if first_token is None:
                        first_token = elapsed_ms()
                    yield sse("token", {"text": piece, "draft": False, "answer_version": version})
                if checks:
                    yield sse(
                        "check",
                        {"checks": checks, "grounded": all(c["ok"] for c in checks), "tier": tier_u, "answer_version": version},
                    )
                final = {
                    "answer": hit.get("answer", ""),
                    "citations": hit.get("citations", []),
                    "refused": hit.get("refused", False),
                    "checks": checks,
                    "answer_version": version,
                    "superseded_versions": hit.get("superseded_versions", []),
                    "graph_build_id": state["graph_build_id"],
                }
                state.update(final)
                yield sse("final", {**final, "ttft_ms": first_token if first_token is not None else elapsed_ms()})
                yield sse(
                    "done",
                    {
                        **final,
                        "ttft_ms": first_token if first_token is not None else elapsed_ms(),
                        "total_ms": elapsed_ms(),
                        "cache": "hit",
                        "status": "ok",
                    },
                )
                return

            retrieval = retriever.retrieve(query, mode=mode).as_dict()
            state["retrieval"] = retrieval
            graph_build_id = retrieval.get("graph_build_id") or graph_build_id
            tr.record_retrieval(retrieval.get("latency_ms"))
            yield sse(
                "meta",
                {
                    "cache": "miss",
                    "query": query,
                    "mode": mode,
                    "tier": tier_u,
                    "graph_build_id": graph_build_id,
                    "retrieval": _summary(retrieval),
                    "retrieval_ms": retrieval.get("latency_ms"),
                },
            )

            # ---- plan（与 /ask 同档位一致）----
            path_note = ""
            if tier_u in ("B", "C"):
                res = plan_state(query, retrieval, ask_llm=want_llm)
                path_note = res["path_note"]
                state["path_note"] = path_note
                yield sse("plan", {"path_note": path_note, "llm_calls": res["llm_calls"]})

            # ---- 生成 v1（草稿）----
            items = retrieval.get("items", [])[:4]
            first_token_ms: float | None = None
            parts: list[str] = []
            if want_llm and items:
                try:
                    for tok in answer_tokens(query, retrieval, path_note):
                        if first_token_ms is None:
                            first_token_ms = elapsed_ms()
                        if (time.perf_counter() - t0) > STREAM_TIMEOUT_S:
                            raise TimeoutError(f"stream 超过 {STREAM_TIMEOUT_S:.0f}s")
                        parts.append(tok)
                        yield sse("token", {"text": tok, "draft": tier_u in ("C", "A_gates"), "answer_version": 1})
                except (LLMDisabled, LLMError) as exc:
                    report_degradation(getattr(exc, "kind", "no_key"), str(exc), model_version())
                    log.warning("流式生成失败，回落确定性答案：%s", exc)
                except TimeoutError:
                    raise
            answer = "".join(parts).strip()
            if not answer:
                answer = deterministic_answer(query, retrieval, path_note)
                for piece in _chunks(answer):
                    if first_token_ms is None:
                        first_token_ms = elapsed_ms()
                    yield sse("token", {"text": piece, "draft": tier_u in ("C", "A_gates"), "answer_version": 1})

            # ---- 校验 →（修复 → 复检）*，与 flows 的有环图同构 ----
            version = 1
            superseded: list[int] = []
            attempts = 0
            checks: list[dict] = []
            while tier_u in ("C", "A_gates"):
                checks = check_gates(answer, retrieval, article_index, art_concepts)
                yield sse(
                    "check",
                    {
                        "checks": checks,
                        "grounded": all(c["ok"] for c in checks),
                        "tier": tier_u,
                        "answer_version": version,
                    },
                )
                if after_check(checks, attempts) != "repair":
                    break
                failed = [c["gate"] for c in checks if not c["ok"]]
                superseded.append(version)
                yield sse(
                    "superseded",
                    {
                        "answer_version": version,
                        "reason": "gate_repair",
                        "failed_gates": failed,
                        "next_version": version + 1,
                    },
                )
                res = repair_state(
                    query=query,
                    retrieval=retrieval,
                    checks=checks,
                    path_note=path_note,
                    art_concepts=art_concepts,
                    prior_answer=answer,
                    attempts=attempts,
                )
                attempts = res["attempts"]
                answer = res["answer"]
                version += 1
                for piece in _chunks(answer):
                    # 修复后的正文同样要等下一轮 check 通过才算终稿，这里只能是草稿。
                    yield sse("token", {"text": piece, "draft": True, "answer_version": version})

            refused = is_refused(answer)
            final = {
                "answer": answer,
                "citations": [i.get("citation") for i in items],
                "refused": refused,
                "checks": checks,
                "answer_version": version,
                "superseded_versions": superseded,
            }
            state.update(final)
            yield sse(
                "final",
                {**final, "ttft_ms": first_token_ms if first_token_ms is not None else elapsed_ms()},
            )

            cache.set(
                key,
                {
                    **final,
                    "tier": tier_u,
                    "graph_build_id": graph_build_id,
                    "path_note": path_note,
                    "retrieval": _summary(retrieval),
                },
            )
            yield sse(
                "done",
                {
                    **final,
                    "ttft_ms": first_token_ms if first_token_ms is not None else elapsed_ms(),
                    "total_ms": elapsed_ms(),
                    "cache": "miss",
                    "status": "ok",
                },
            )
        except GeneratorExit:
            status = "cancelled"
            log.info("客户端断开，流式请求取消（不写缓存、不记成功）")
            raise
        except TimeoutError as exc:
            status = "timeout"
            yield sse("error", {"status": "timeout", "detail": str(exc)})
        except Exception as exc:  # 兜底：任何异常都不能被记成 ok
            status = "error"
            log.warning("流式请求失败: %s", exc)
            yield sse("error", {"status": "error", "kind": classify_error(exc), "detail": str(exc)[:200]})
        finally:
            write_trace(tr.finish(state, cache=cache_state, status=status))
