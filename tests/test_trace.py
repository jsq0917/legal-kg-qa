"""T27：trace 字段与成本汇总的口径测试（不加载模型、不调 LLM）。"""

from __future__ import annotations

import json

from app.__008__api.__005__trace import (
    COST_CNY_PER_1K_TOKENS,
    Trace,
    cost_cny,
    measure_gate_ms,
    model_is_paid,
    recent_traces,
    record_llm_timing,
    summarize_traces,
    trace_request,
    write_trace,
)

CHECKS = [
    {"gate": "cite_exists", "ok": True, "detail": "全部条号可查"},
    {"gate": "cite_in_retrieval", "ok": False, "detail": "越界引用"},
    {"gate": "path_support", "ok": True, "detail": "coverage=0.71"},
    {"gate": "evidence_span", "ok": True, "detail": "依据均有原文片段"},
    {"gate": "low_conf_handoff", "ok": True, "detail": "low_confidence=False"},
]


def test_trace_fields_are_derived_consistently() -> None:
    tr = Trace("/ask", query="拖工资", tier="C", mode="hybrid", model="deepseek-chat")
    tr.record_retrieval(400.0)
    tr.record_llm(1200.0, 800, 200)
    state = {
        "tier": "C",
        "mode": "hybrid",
        "llm_calls": 1,
        "retrieval": {"latency_ms": 400.0},
        "checks": CHECKS,
        "refused": False,
    }
    out = tr.finish(state, total_ms=1700.0)

    assert out["stages"]["retrieve_ms"] == 400.0
    assert out["stages"]["llm_ms"] == 1200.0
    assert out["stages"]["gate_ms"] == 100.0
    assert out["stages"]["overhead_ms"] == 0.0
    assert out["stages"]["gate_ms_is_derived"] is True
    assert out["stages"]["gate_ms_source"] == "derived"
    assert out["llm"]["total_tokens"] == 1000
    assert out["llm"]["calls"] == 1
    assert out["cost"]["cny"] == cost_cny(1000)
    assert out["gates"]["count"] == 5
    assert out["gates"]["all_pass"] is False
    assert out["gates"]["failed"] == ["cite_in_retrieval"]
    assert out["cache"] == "miss"


def test_non_c_tier_has_no_derived_gate_time() -> None:
    tr = Trace("/ask", query="q", tier="A", mode="text", model="deepseek-chat")
    tr.record_retrieval(300.0)
    tr.record_llm(900.0, 400, 100)
    out = tr.finish({"tier": "A", "retrieval": {"latency_ms": 300.0}, "checks": []}, total_ms=1300.0)
    assert out["stages"]["gate_ms"] == 0.0
    assert out["stages"]["overhead_ms"] == 100.0
    assert out["stages"]["gate_ms_is_derived"] is False
    assert out["stages"]["gate_ms_source"] == "n/a"


def test_measured_gate_time_overrides_derivation() -> None:
    """传了 ``gate_ms`` 就用实测值，剩余时间进 ``overhead_ms``，不再把编排开销算进闸门。"""
    tr = Trace("/ask", query="q", tier="C", mode="hybrid", model="deepseek-chat")
    tr.record_retrieval(400.0)
    tr.record_llm(1200.0, 800, 200)
    out = tr.finish(
        {"tier": "C", "retrieval": {"latency_ms": 400.0}, "checks": CHECKS},
        total_ms=1700.0,
        gate_ms=80.0,
    )
    assert out["stages"]["gate_ms"] == 80.0
    assert out["stages"]["overhead_ms"] == 20.0
    assert out["stages"]["gate_ms_is_derived"] is False
    assert out["stages"]["gate_ms_source"] == "measured"


def test_measure_gate_ms_uses_state_without_side_effects() -> None:
    """同一份 state 重跑五闸只用于计时：返回值非负，且不改动输入。"""
    state = {"answer": "根据劳动合同法第八十五条，应支付赔偿金。", "retrieval": {"items": [], "coverage": 0.0}}
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)
    ms = measure_gate_ms(state, {}, {})
    assert ms >= 0.0
    assert json.dumps(state, ensure_ascii=False, sort_keys=True) == before


def test_cache_hit_does_not_replay_previous_stage_times() -> None:
    """命中缓存时本次没跑检索/LLM/闸门：阶段耗时必须是 0，不能搬上一次的数。"""
    tr = Trace("/ask", query="q", tier="C", mode="hybrid", model="deepseek-chat")
    tr.record_retrieval(400.0)
    tr.record_llm(1200.0, 800, 200)
    out = tr.finish(
        {"tier": "C", "retrieval": {"latency_ms": 400.0}, "checks": CHECKS},
        total_ms=0.2,
        cache="hit",
    )
    assert out["cache"] == "hit"
    assert out["stages"]["retrieve_ms"] == 0.0
    assert out["stages"]["llm_ms"] == 0.0
    assert out["stages"]["gate_ms"] == 0.0
    assert out["stages"]["overhead_ms"] == 0.0
    assert out["stages"]["gate_ms_source"] == "cache_hit"
    assert out["gates"]["count"] == 5


def test_llm_hook_only_records_inside_context() -> None:
    record_llm_timing(500.0, 10, 20)  # 没有上下文：必须静默丢弃
    with trace_request("/ask", query="q", tier="C", llm_enabled=False) as tr:
        record_llm_timing(700.0, 100, 40)
        record_llm_timing(300.0, 50, 30)
    assert tr.llm_calls == 2
    assert tr.llm_ms == 1000.0
    assert tr.prompt_tokens == 150
    assert tr.completion_tokens == 70


def test_cost_only_for_paid_models() -> None:
    assert model_is_paid("deepseek-chat") is True
    assert model_is_paid("qwen2.5-1.5b-instruct") is False
    assert cost_cny(1110) == round(1110 / 1000 * COST_CNY_PER_1K_TOKENS, 6)


def _row(tier: str, latency: float, tokens: int, cache: str, cost: float) -> dict:
    return {
        "tier": tier,
        "path": "/ask",
        "latency_ms": latency,
        "cache": cache,
        "llm": {"total_tokens": tokens, "calls": 1},
        "cost": {"cny": cost},
    }


def test_summary_aggregates_jsonl(tmp_path) -> None:
    path = tmp_path / "trace.jsonl"
    rows = [
        _row("C", 3000.0, 1200, "miss", 0.001533),
        _row("C", 1000.0, 900, "hit", 0.0),
        _row("A", 2000.0, 500, "miss", 0.000639),
    ]
    for row in rows:
        write_trace(row, str(path))

    summary = summarize_traces(str(path))
    assert summary["window"]["rows"] == 3
    assert summary["overall"]["n"] == 3
    assert summary["overall"]["total_tokens"] == 2600
    assert summary["overall"]["cost_cny"] == 0.002172
    assert summary["cache_hit_rate"] == round(1 / 3, 4)
    assert summary["by_tier"]["C"]["n"] == 2
    assert summary["by_tier"]["A"]["n"] == 1
    assert summary["overall"]["p50_ms"] == 2000.0

    recent = recent_traces(str(path), n=2)
    assert [r["tier"] for r in recent] == ["A", "C"]

    missing = summarize_traces(str(tmp_path / "nope.jsonl"))
    assert missing["overall"]["n"] == 0
    assert json.dumps(missing, ensure_ascii=False)  # 可序列化，前端能直接消费
