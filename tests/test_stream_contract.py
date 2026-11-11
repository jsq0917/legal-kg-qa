"""同步 /ask 与流式 /stream 的契约测试（LW-N2）：同一问题必须给出同一结论与版本语义。"""

from __future__ import annotations

import copy
import json

import pytest

from app.__007__agents import __001__gates as gates_mod  # noqa: F401  （确保模块加载）
from app.__007__agents import __002__flows as flows_mod
from app.__007__agents import __003__pipeline as pipeline_mod
from app.__008__api import __003__stream as stream_mod
from app.__008__api import __005__trace as trace_mod
from app.__008__api.__002__cache import get_cache, make_key
from common.llm import LLMDisabled

ARTICLE = {
    "article_id": "lcl#50",
    "citation": "中华人民共和国劳动法第五十条",
    "law_name": "劳动法",
    "article_no": "第五十条",
    "content": "工资应当以货币形式按月支付给劳动者本人。不得克扣或者无故拖欠劳动者的工资。",
    "chapter_path": "第五章 工资",
}
RETRIEVAL = {
    "query": "公司拖欠工资怎么办",
    "mode": "hybrid",
    "graph_build_id": "gtest0000000",
    "level": "medium",
    "top1_similarity": 0.91,
    "coverage": 0.9,
    "low_confidence": False,
    "path_coverage_threshold": 0.6,
    "refuse_threshold": 0.62,
    "latency_ms": 5.0,
    "items": [dict(ARTICLE, score=0.91)],
    "linked": {"concepts": [{"id": "wage_payment", "name": "工资支付", "score": 0.9}], "scenarios": []},
}
ARTICLE_INDEX = {"lcl#50": ARTICLE}
ART_CONCEPTS = {"lcl#50": {"wage_payment"}}


class _Out:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def as_dict(self) -> dict:
        return copy.deepcopy(self._payload)


class _Store:
    graph_build_id = "gtest0000000"


class _Retriever:
    class graph:
        store = _Store()

    def __init__(self, retrieval: dict) -> None:
        self.retrieval = retrieval
        self.calls = 0

    def retrieve(self, query: str, mode: str = "hybrid") -> _Out:
        self.calls += 1
        return _Out(self.retrieval)


class _FakeLLM:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def chat_stream(self, messages, *, max_tokens=480, temperature=0.0):
        self.calls += 1
        yield from self.text

    def try_chat(self, messages, default="", **kwargs):
        self.calls += 1
        return self.text


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    get_cache().clear()
    written: list[dict] = []
    rec = lambda trace, path=None: written.append(trace)  # noqa: E731
    monkeypatch.setattr(trace_mod, "write_trace", rec)
    monkeypatch.setattr(stream_mod, "write_trace", rec)
    monkeypatch.setattr(stream_mod, "model_version", lambda: "test-model")
    monkeypatch.setattr(stream_mod, "llm_status", lambda: {"enabled": False})
    monkeypatch.setattr(flows_mod, "llm_probe", lambda: False)
    yield written
    get_cache().clear()


def _events(raw: list[str]) -> list[tuple[str, dict]]:
    out = []
    for block in raw:
        lines = [ln for ln in block.strip().splitlines() if ln.startswith(("event:", "data:"))]
        name = lines[0].split(":", 1)[1].strip()
        data = json.loads(lines[1].split(":", 1)[1].strip())
        out.append((name, data))
    return out


def _run_sync(tier: str) -> dict:
    flow = flows_mod.make_flow(_Retriever(RETRIEVAL), ARTICLE_INDEX, tier, use_llm=False, art_concepts=ART_CONCEPTS)
    return flow("公司拖欠工资怎么办", "hybrid")


def _run_stream(tier: str, retriever: _Retriever | None = None, use_llm: bool = False) -> list[tuple[str, dict]]:
    ret = retriever or _Retriever(RETRIEVAL)
    raw = list(
        stream_mod.stream_answer(
            "公司拖欠工资怎么办", "hybrid", tier, ret, ARTICLE_INDEX, ART_CONCEPTS, use_llm=use_llm
        )
    )
    return _events(raw)


def test_sync_and_stream_agree_on_answer_and_citations():
    sync = _run_sync("A_gates")
    events = _run_stream("A_gates")
    final = dict(events)["final"]
    assert final["answer"] == sync["answer"]
    assert final["citations"] == sync["citations"]
    assert final["refused"] == sync["refused"]
    assert final["answer_version"] == sync["answer_version"] == 1
    assert [c["gate"] for c in final["checks"]] == [c["gate"] for c in sync["checks"]]
    assert all(c["ok"] for c in final["checks"])


def test_gated_tier_never_marks_draft_as_final():
    events = _run_stream("A_gates")
    names = [n for n, _ in events]
    assert names[0] == "meta"
    assert "check" in names and "final" in names
    assert names.index("check") < names.index("final")
    for name, data in events:
        if name == "token":
            assert data["draft"] is True
            assert "final" not in data
    done = dict(events)["done"]
    assert done["status"] == "ok"
    assert done["answer_version"] == 1
    assert done["superseded_versions"] == []


def test_repair_publishes_superseded_event(monkeypatch):
    bad = "结论：根据劳动合同法第九十九条规定处理。\n依据：\n- 劳动合同法第九十九条（劳动合同法）：不存在的条文"
    fake = _FakeLLM(bad)
    monkeypatch.setattr(pipeline_mod, "get_llm", lambda: fake)
    monkeypatch.setattr(stream_mod, "llm_status", lambda: {"enabled": True})

    events = _run_stream("A_gates", use_llm=True)
    names = [n for n, _ in events]
    assert "superseded" in names
    sup = dict(events)["superseded"]
    assert sup["answer_version"] == 1
    assert sup["next_version"] == 2
    assert "cite_exists" in sup["failed_gates"] or "cite_in_retrieval" in sup["failed_gates"]
    done = dict(events)["done"]
    assert done["answer_version"] >= 2
    assert 1 in done["superseded_versions"]
    # 最终答案不能还带着假条号
    assert "第九十九条" not in done["answer"]
    assert done["status"] == "ok"


def test_cache_hit_replays_without_retrieval(monkeypatch):
    ret = _Retriever(RETRIEVAL)
    _run_stream("A_gates", ret)
    calls_after_first = ret.calls
    events = _run_stream("A_gates", ret)
    assert ret.calls == calls_after_first  # 第二次没有再检索
    meta = dict(events)["meta"]
    assert meta["cache"] == "hit"
    done = dict(events)["done"]
    assert done["cache"] == "hit"
    assert done["answer_version"] == 1


def test_client_disconnect_is_cancelled_and_not_cached(monkeypatch):
    written: list[dict] = []
    rec = lambda trace, path=None: written.append(trace)  # noqa: E731
    monkeypatch.setattr(stream_mod, "write_trace", rec)
    ret = _Retriever(RETRIEVAL)
    gen = stream_mod.stream_answer(
        "公司拖欠工资怎么办", "hybrid", "A_gates", ret, ARTICLE_INDEX, ART_CONCEPTS, use_llm=False
    )
    next(gen)  # 只消费 meta
    gen.close()

    assert written, "取消也必须留下 trace"
    assert written[-1]["status"] == "cancelled"
    key = make_key(
        "公司拖欠工资怎么办", "hybrid", "A_gates", False, "test-model", stream_mod.prompt_guard.guard_enabled(),
        graph_build_id="gtest0000000",
    )
    assert get_cache().get(key) is None


def test_stream_survives_starlette_threadpool_context_switch(_env):
    """回归：Starlette 用 ``iterate_in_threadpool`` 逐次 ``next()`` 迭代同步生成器。

    每次 ``next()`` 都跑在线程池里、使用复制过的 Context。旧实现把 ``trace_request``
    跨整个生成器：第一个 Context 里 set 的 ContextVar token，在结束时到另一个
    Context reset，必然抛 ``ValueError: ... was created in a different Context``，
    而且 trace 还先被写成 ok。这里走真实传输层机制复现。
    """
    import asyncio

    from starlette.concurrency import iterate_in_threadpool

    ret = _Retriever(RETRIEVAL)
    gen = stream_mod.stream_answer(
        "公司拖欠工资怎么办", "hybrid", "A_gates", ret, ARTICLE_INDEX, ART_CONCEPTS, use_llm=False
    )

    async def consume() -> list[str]:
        return [chunk async for chunk in iterate_in_threadpool(gen)]

    raw = asyncio.run(consume())
    events = _events(raw)
    assert dict(events)["done"]["status"] == "ok"
    assert "error" not in [name for name, _ in events]
    assert _env[-1]["status"] == "ok"


def test_sse_events_follow_schema():
    events = _run_stream("A_gates")
    assert stream_mod.schema_errors(events) == []


def test_multi_repair_versions_align_across_paths():
    """items 为空时两条路径都会修复两次：版本号必须一致（旧实现同步固定报 2）。"""
    empty = dict(RETRIEVAL, items=[], coverage=0.0, top1_similarity=0.0, low_confidence=True)
    flow = flows_mod.make_flow(_Retriever(empty), ARTICLE_INDEX, "A_gates", use_llm=False, art_concepts=ART_CONCEPTS)
    sync = flow("公司拖欠工资怎么办", "hybrid")
    events = _run_stream("A_gates", _Retriever(empty))
    final = dict(events)["final"]
    assert sync["answer_version"] == final["answer_version"] == 3
    assert sync["superseded_versions"] == final["superseded_versions"] == [1, 2]


def test_stream_error_status_is_not_ok(monkeypatch, _env):
    class Boom(_Retriever):
        def retrieve(self, query, mode="hybrid"):
            raise RuntimeError("检索炸了")

    events = _run_stream("A_gates", Boom(RETRIEVAL))
    assert dict(events)["error"]["status"] == "error"
    assert _env[-1]["status"] == "error"


def test_cache_key_separates_graph_and_policy_versions():
    base = dict(query="q", mode="hybrid", tier="A_gates", llm=False, version="m", guard=False)
    k1 = make_key(**base, graph_build_id="g1", policy_version="p1")
    k2 = make_key(**base, graph_build_id="g2", policy_version="p1")
    k3 = make_key(**base, graph_build_id="g1", policy_version="p2")
    assert len({k1, k2, k3}) == 3


def test_llm_disabled_stream_falls_back_deterministically(monkeypatch):
    class DeadLLM:
        def chat_stream(self, *a, **kw):
            raise LLMDisabled("no key")
            yield  # pragma: no cover

    monkeypatch.setattr(pipeline_mod, "get_llm", lambda: DeadLLM())
    monkeypatch.setattr(stream_mod, "llm_status", lambda: {"enabled": True})
    events = _run_stream("A_gates", use_llm=True)
    done = dict(events)["done"]
    assert done["status"] == "ok"
    assert done["answer"]
    assert all(c["ok"] for c in done["checks"])
