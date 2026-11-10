"""FastAPI 服务：把检索 / 图问答消融 / 多轮对话 / 人在环暴露成 HTTP 接口。

存在的意义不是「再包一层」，而是让每个验收项都有一个可被第三方复现的入口：
* ``POST /retrieve``  —— B3 的 text / graph / hybrid 三档，返回 items + 图路径 + 路由决策；
* ``POST /ask``       —— B6 的 A / B / C 三档，返回答案、五道闸逐项结果、LLM 调用数与耗时；
* ``GET  /ask/stream``—— 同一次编排的 SSE 版本，**逐 token** 推送 ``meta -> token* -> check -> done``；
* ``GET  /cache/stats`` / ``GET /rate-limit/stats`` —— 缓存与限流的可核对口径；
* ``POST /chat``      —— B4 的多轮槽位会话，``session_id`` 相同即共享状态；
* ``GET  /hitl`` ...  —— B5 的挂起列表与人工修正接口。

所有重对象（检索器、图、模型）都是懒加载且进程内单例，首请求慢、后续快；
``/health`` 明确回报当前真实后端（Neo4j 还是 NetworkX 离线镜像），不粉饰。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.__007__agents.__000__state import normalize_tier  # noqa: E402
from app.__008__api.__001__ratelimit import RateLimitMiddleware, limiter_stats  # noqa: E402
from app.__008__api.__002__cache import get_cache, make_key, model_version  # noqa: E402
from app.__008__api.__003__stream import stream_answer  # noqa: E402
from app.__008__api.__005__trace import (  # noqa: E402
    install_llm_hook,
    measure_gate_ms,
    recent_traces,
    summarize_traces,
    trace_request,
    write_trace,
)
from common import prompt_guard  # noqa: E402
from common.llm import llm_status  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from config import settings  # noqa: E402

log = get_logger("api")

install_llm_hook()

app = FastAPI(title="LexWeave", version="1.0.0", description="劳动法知识图谱双路检索 + 多 Agent 校验问答")
app.add_middleware(RateLimitMiddleware)

_MAX_ITEMS = 5


# --------------------------------------------------------------------------------
# 懒加载单例
# --------------------------------------------------------------------------------
def _retriever():
    from app.__004__retrieval.__005__retriever import get_retriever

    return get_retriever()


_FLOWS: dict[tuple[str, bool], Any] = {}


def _flow(tier: str, use_llm: bool = True):
    from app.__007__agents.__002__flows import make_flow

    key = (normalize_tier(tier), use_llm)
    if key not in _FLOWS:
        ret = _retriever()
        _FLOWS[key] = make_flow(
            ret, ret.text.articles, key[0], use_llm=use_llm, art_concepts=_art_concepts()
        )
    return _FLOWS[key]


_ART_CONCEPTS: dict[str, dict[str, set[str]]] = {}


def _art_concepts() -> dict[str, set[str]]:
    """条文 -> 概念反查表。建一次要打全量概念，必须单例复用，不能每请求重建。"""
    from app.__007__agents.__001__gates import build_article_concepts

    if "v" not in _ART_CONCEPTS:
        _ART_CONCEPTS["v"] = build_article_concepts(_retriever().graph.store)
    return _ART_CONCEPTS["v"]


def _hitl():
    from app.__006__hitl.__003__pipeline import HitlPipeline

    return HitlPipeline(retriever=_retriever())


def _dialog():
    from app.__005__dialog.__002__dialog_manager import DialogManager

    return DialogManager(retriever=_retriever())


# --------------------------------------------------------------------------------
# 请求体
# --------------------------------------------------------------------------------
class RetrieveReq(BaseModel):
    query: str = Field(min_length=1)
    mode: str = Field(default="hybrid", pattern="^(text|graph|hybrid)$")
    level: str | None = Field(default=None, pattern="^(simple|medium|complex)$")
    top_k: int | None = Field(default=None, ge=1, le=20)


class AskReq(BaseModel):
    query: str = Field(min_length=1)
    mode: str = Field(default="hybrid", pattern="^(text|graph|hybrid)$")
    tier: str = Field(default="A_gates", pattern="^(?:[ABCabc]|[Aa]_gates)$")
    llm: bool = True


class ChatReq(BaseModel):
    session_id: str = ""
    text: str = Field(min_length=1)
    mode: str = Field(default="hybrid", pattern="^(text|graph|hybrid)$")


class HitlStartReq(BaseModel):
    query: str = Field(min_length=1)
    mode: str = Field(default="hybrid", pattern="^(text|graph|hybrid)$")
    force_review: bool = False


class HitlResolveReq(BaseModel):
    type: str = Field(pattern="^(accept|edit_articles|edit_answer|provide_answer)$")
    article_ids: list[str] | None = None
    answer: str | None = None
    citations: list[str] | None = None


# --------------------------------------------------------------------------------
# 基础
# --------------------------------------------------------------------------------
@app.get("/health")
def health() -> dict[str, Any]:
    ret = _retriever()
    try:
        backend = ret.graph.store.backend
    except Exception:  # 图未构建时也要能返回 200，便于前端展示「先跑 build_graph」
        backend = "uninitialized"
    return {
        "ok": True,
        "graph_backend": backend,
        "corpus_articles": len(ret.text.articles),
        "llm": llm_status(),
    }


@app.get("/meta/laws")
def laws() -> dict[str, Any]:
    idx = _retriever().text.articles
    counts: dict[str, int] = {}
    for art in idx.values():
        counts[art["law_name"]] = counts.get(art["law_name"], 0) + 1
    return {"total": len(idx), "by_law": counts}


# --------------------------------------------------------------------------------
# B3：三档检索
# --------------------------------------------------------------------------------
@app.post("/retrieve")
def retrieve(req: RetrieveReq) -> dict[str, Any]:
    out = _retriever().retrieve(req.query, mode=req.mode, level=req.level, top_k=req.top_k).as_dict()
    out["items"] = out["items"][: max(req.top_k or _MAX_ITEMS, _MAX_ITEMS)]
    return out


# --------------------------------------------------------------------------------
# B6：三档编排
# --------------------------------------------------------------------------------
def _ask(req: AskReq) -> dict[str, Any]:
    flow = _flow(req.tier, req.llm)
    t0 = time.perf_counter()
    state = flow(req.query, mode=req.mode)
    state["latency_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    state["tier"] = normalize_tier(req.tier)
    state["retrieval"]["items"] = state["retrieval"]["items"][:_MAX_ITEMS]
    return state


def _measure_gates(state: dict[str, Any]) -> float:
    """单独量一次五闸的耗时（毫秒），实现与口径统一在 ``__005__trace.py``。

    ``flows.py`` 的 ``check`` 节点不返回耗时，而任务边界禁止改 ``flows.py`` /
    ``gates.py``；五闸是纯函数，用同一份 state 再跑一次即可得到真实耗时。
    """
    return measure_gate_ms(state, _retriever().text.articles, _art_concepts())


@app.post("/ask")
def ask(req: AskReq, response: Response) -> dict[str, Any]:
    """同步编排 + 响应级缓存：同问同参第二次直接命中（key 含 tier 与模型版本）。

    ``trace`` 是请求级账本（耗时 / token / 成本），字段口径见 ``__005__trace.py``；
    命中缓存时不产生 token 与闸门耗时，``cache=hit`` 如实标注。

    刻意**不用 ``BaseHTTPMiddleware`` 统一落盘**：``api.md`` §6 已记录它会缓冲 SSE，
    加一层就把 ``/ask/stream`` 的首 token 打回整段到达。这里在端点内直接写，
    只影响 ``/ask``，流式路径完全不走这条代码。
    """
    cache = get_cache()
    key = make_key(
        req.query, req.mode, req.tier, req.llm, model_version(), prompt_guard.guard_enabled()
    )
    llm_on = bool(req.llm) and bool(llm_status()["enabled"])
    model = settings.llm_model if llm_on else None
    t0 = time.perf_counter()
    cached = cache.get(key)
    if cached is not None:
        latency = round((time.perf_counter() - t0) * 1000, 2)
        with trace_request(
            "/ask", query=req.query, tier=req.tier, mode=req.mode, cache="hit",
            llm_enabled=llm_on, model=model,
        ) as tr:
            trace = tr.finish(cached, total_ms=latency, cache="hit")
        cached["cache"] = "hit"
        cached["latency_ms"] = latency
        cached["trace"] = trace
        write_trace(trace)
        response.headers["X-Trace-Id"] = trace["request_id"]
        return cached

    with trace_request(
        "/ask", query=req.query, tier=req.tier, mode=req.mode, cache="miss",
        llm_enabled=llm_on, model=model,
    ) as tr:
        state = _ask(req)
        gate_ms = _measure_gates(state) if normalize_tier(req.tier) in ("C", "A_gates") else None
        trace = tr.finish(state, cache="miss", gate_ms=gate_ms)
    state["cache"] = "miss"
    cache.set(key, {k: v for k, v in state.items() if k != "trace"})
    state["trace"] = trace
    write_trace(trace)
    response.headers["X-Trace-Id"] = trace["request_id"]
    return state


@app.get("/ask/stream")
def ask_stream(
    query: str = Query(min_length=1),
    mode: str = Query(default="hybrid", pattern="^(text|graph|hybrid)$"),
    tier: str = Query(default="A_gates", pattern="^(?:[ABCabc]|[Aa]_gates)$"),
    llm: bool = Query(default=True),
) -> StreamingResponse:
    """SSE：逐 token 推送答案，末尾附五闸校验结果（取舍见 ``__003__stream.py`` 模块注释）。

    与旧版「按节点推」不同：旧版的 ``answer`` 事件要等整段 LLM 生成完才出现，
    首 token 实测 8–10 s；新版首 token 由检索 + LLM 首字决定（热启动实测 ~1 s）。
    """
    ret = _retriever()
    return StreamingResponse(
        stream_answer(query, mode, tier, ret, ret.text.articles, _art_concepts(), llm),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/cache/stats")
def cache_stats() -> dict[str, Any]:
    return get_cache().snapshot()


@app.get("/rate-limit/stats")
def rate_limit_stats() -> dict[str, Any]:
    return limiter_stats()


# --------------------------------------------------------------------------------
# 可观测性：trace 汇总 / 最近请求（成本看板的数据源）
# --------------------------------------------------------------------------------
@app.get("/observability/summary")
def observability_summary(limit: int = Query(default=200, ge=1, le=2000)) -> dict[str, Any]:
    return summarize_traces(limit=limit)


@app.get("/observability/recent")
def observability_recent(n: int = Query(default=20, ge=1, le=200)) -> dict[str, Any]:
    return {"rows": recent_traces(n=n)}


# --------------------------------------------------------------------------------
# Demo：两个可演示场景（只读闸门层，不改闸门/检索逻辑）
# --------------------------------------------------------------------------------
class DemoGuardReq(BaseModel):
    case_id: str = Field(default="i01", min_length=1)


def _injection_cases() -> dict[str, dict[str, Any]]:
    from common.path_utils import load_jsonl_indexed

    path = settings.data_dir / "security" / "injection_20.jsonl"
    if not path.exists():
        return {}
    return load_jsonl_indexed(path)


@app.post("/demo/guard")
def demo_guard(req: DemoGuardReq) -> dict[str, Any]:
    """把 T28 的间接注入样本喂给 ``prompt_guard``，返回净化前后的对照。

    这是**防护层本身**的可视化：展示「检索文本里的指令被替换成占位符 + 包进数据边界」，
    不调用 LLM，也不改任何闸门。直接注入（指令在用户问题里）不在这一层的职责范围，
    这里如实返回 ``blocked=false`` 并说明。
    """
    case = _injection_cases().get(req.case_id)
    if case is None:
        raise HTTPException(status_code=404, detail=f"没有这条注入用例: {req.case_id}")
    doc = case.get("injected_doc")
    guard_on = prompt_guard.guard_enabled()
    if not doc:
        return {
            "case_id": req.case_id,
            "category": case.get("category"),
            "query": case.get("query"),
            "blocked": False,
            "guard_enabled": guard_on,
            "detail": "该用例是直接注入（指令写在用户问题里）；本层只净化「检索到的法条文本」，不处理用户消息。",
        }
    original = str(doc.get("content") or "")
    sanitized, hits = prompt_guard.sanitize_with_stats(original)
    boundary = prompt_guard.guard_block([doc])
    return {
        "case_id": req.case_id,
        "category": case.get("category"),
        "query": case.get("query"),
        "article_id": doc.get("article_id"),
        "law_name": doc.get("law_name"),
        "guard_enabled": guard_on,
        "blocked": bool(hits) and sanitized != original,
        "hits": hits,
        "placeholder": prompt_guard.INSTRUCTION_PLACEHOLDER,
        "removed_chars": len(original) - len(sanitized),
        "original": original,
        "sanitized": sanitized,
        "boundary_preview": boundary[:1200],
    }


@app.get("/demo/off-domain")
def demo_off_domain(query: str = Query(default="离婚财产分割怎么办？", min_length=1)) -> dict[str, Any]:
    """域外拒答的判定过程（复用 T05 的 ``assess_domain``，不写挂起任务、不调 LLM）。"""
    from app.__006__hitl.__003__pipeline import REFUSAL_ANSWER, assess_domain

    retrieval = _retriever().retrieve(query).as_dict()
    gate = assess_domain(retrieval)
    return {
        "query": query,
        "refused": gate["refused"],
        "reason": gate["reason"],
        "retrieval_top_score": gate["retrieval_top_score"],
        "off_domain_threshold": gate["off_domain_threshold"],
        "domain_concepts": gate["domain_concepts"],
        "answer": REFUSAL_ANSWER if gate["refused"] else "",
        "citations": [],
        "top_items": [
            {"citation": i["citation"], "law_name": i.get("law_name"), "score": i.get("score")}
            for i in retrieval.get("items", [])[:3]
        ],
    }


# --------------------------------------------------------------------------------
# B4：多轮
# --------------------------------------------------------------------------------
@app.post("/chat")
def chat(req: ChatReq) -> dict[str, Any]:
    dm = _dialog()
    sid = req.session_id or dm.new_session()
    return dm.handle(sid, req.text, mode=req.mode).as_dict()


@app.get("/chat/{session_id}/history")
def chat_history(session_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "turns": _dialog().history(session_id)}


# --------------------------------------------------------------------------------
# B5：人在环
# --------------------------------------------------------------------------------
@app.post("/hitl")
def hitl_start(req: HitlStartReq) -> dict[str, Any]:
    return _hitl().start(req.query, mode=req.mode, force_review=req.force_review)


@app.get("/hitl")
def hitl_list(status: str | None = None) -> dict[str, Any]:
    return {"pending": _hitl().pending(), "status_filter": status, "stats": _hitl().stats()}


@app.post("/hitl/{task_id}/resolve")
def hitl_resolve(task_id: str, req: HitlResolveReq) -> dict[str, Any]:
    try:
        action = {"type": req.type}
        if req.article_ids:
            action["article_ids"] = req.article_ids
        if req.answer:
            action["answer"] = req.answer
        if req.citations:
            action["citations"] = req.citations
        return _hitl().resume(task_id, action)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.__008__api.__000__server:app", host="127.0.0.1", port=8000, reload=False)
