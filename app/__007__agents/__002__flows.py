"""LangGraph 编排：A（单链路）/ B（多 Agent）/ C（多 Agent + 五道闸）/ A_gates（单链路 + 五道闸）。

为什么用 LangGraph 而不是手写 if/else：
状态在节点间显式流转，C 档的「校验 -> 修复 -> 复检」天然是一个有环图，
用条件边表达比嵌套函数清楚，也便于把 ``repaired`` / ``attempts`` 落进评测。

四个档位的 LLM 调用结构（成本表的口径）：
* A:       retrieve(0) + answer(1)                        = 1 次
* B:       retrieve(0) + plan(1) + answer(1)               = 2 次
* C:       retrieve(0) + plan(1) + answer(1) + repair(0)*  = 2 次（闸门是规则，不打 LLM）
* A_gates: retrieve(0) + answer(1) + repair(0)*            = 1 次（同上，只多了闸门）

``A_gates`` 是**默认档**（``/ask``、``/stream``、UI 均默认它）：单次生成 + 五道闸，
比 C 少一次 plan 调用。B6 消融里它同时是多 Agent 的对照项。
它回答的问题是「五道闸门的收益是否依赖多 Agent 的规划」——若 A_gates 与 C 的
grounded 接近，说明闸门本身有效；若明显更差，说明 plan 提供的那一跳确实有用。

业务实现（拼 prompt / 生成 / 校验 / 修复）全部在 :mod:`app.__007__agents.__003__pipeline`，
同步 ``/ask`` 与流式 ``/stream`` 共用，本文件只负责把它们连成图。
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.__007__agents.__000__state import QAState  # noqa: E402
from app.__007__agents.__003__pipeline import (  # noqa: E402
    ANSWER_SYSTEM,  # noqa: F401  （兼容旧导入）
    MAX_ATTEMPTS,
    PLAN_SYSTEM,  # noqa: F401
    after_check,
    answer_sync,
    check_gates,
    deterministic_answer,
    fmt_items,
    is_refused,
    plan_state,
    repair_state,
)
from common.llm import llm_probe  # noqa: E402

# 兼容旧名字（测试与外部脚本仍在引用）
_fmt_items = fmt_items
_deterministic_answer = deterministic_answer


def llm_enabled() -> bool:
    return llm_probe()


def make_flow(
    retriever: Any,
    article_index: dict[str, dict],
    tier: str,
    use_llm: bool | None = None,
    art_concepts: dict[str, set[str]] | None = None,
) -> Callable[[str, str], dict]:
    """返回 ``run(query, mode) -> state``。图只编译一次，供 60 题复用。"""
    from langgraph.graph import END, StateGraph

    ask_llm = llm_enabled() if use_llm is None else use_llm
    art_concepts = art_concepts or {}

    # ---------------- 节点：只做状态拼装，业务在 pipeline ----------------
    def retrieve(state: QAState) -> dict:
        out = retriever.retrieve(state["query"], mode=state.get("mode", "hybrid")).as_dict()
        return {"retrieval": out, "llm_calls": 0}

    def plan(state: QAState) -> dict:
        res = plan_state(state["query"], state["retrieval"], ask_llm=ask_llm)
        return {"path_note": res["path_note"], "llm_calls": state.get("llm_calls", 0) + res["llm_calls"]}

    def answer(state: QAState) -> dict:
        res = answer_sync(
            state["query"], state["retrieval"], state.get("path_note", ""), ask_llm=ask_llm
        )
        return {
            "answer": res["answer"],
            "citations": res["citations"],
            "llm_calls": state.get("llm_calls", 0) + res["llm_calls"],
        }

    def check(state: QAState) -> dict:
        return {"checks": check_gates(state.get("answer", ""), state["retrieval"], article_index, art_concepts)}

    def repair(state: QAState) -> dict:
        return repair_state(
            query=state["query"],
            retrieval=state["retrieval"],
            checks=state.get("checks", []),
            path_note=state.get("path_note", ""),
            art_concepts=art_concepts,
            prior_answer=state.get("answer", ""),
            attempts=state.get("attempts", 0),
        )

    def route_after_check(state: QAState) -> str:
        return after_check(state.get("checks", []), state.get("attempts", 0))

    # ---------------- 建图 ----------------
    g = StateGraph(QAState)
    g.add_node("retrieve", retrieve)
    g.add_node("answer", answer)
    g.set_entry_point("retrieve")
    if tier in ("A", "A_gates"):
        g.add_edge("retrieve", "answer")
    else:
        g.add_node("plan", plan)
        g.add_edge("retrieve", "plan")
        g.add_edge("plan", "answer")
    if tier in ("A", "B"):
        g.add_edge("answer", END)
    else:
        g.add_node("check", check)
        g.add_node("repair", repair)
        g.add_edge("answer", "check")
        g.add_conditional_edges("check", route_after_check, {"repair": "repair", "done": END})
        g.add_edge("repair", "check")
    compiled = g.compile()

    def run(query: str, mode: str = "hybrid") -> dict:
        init: QAState = {
            "query": query,
            "mode": mode,
            "attempts": 0,
            "repaired": False,
            "citations": [],
            "checks": [],
        }
        out = compiled.invoke(init)
        out.setdefault("answer", "")
        out["refused"] = is_refused(out["answer"])
        # 每次修复都算一个新版本：v1 是初稿，attempts 次修复后当前版本是 1+attempts。
        # 旧写法固定成 2，遇到「修复后复检仍失败、再修一次」就与流式路径对不上。
        attempts = int(out.get("attempts", 0))
        out["answer_version"] = 1 + attempts
        out["superseded_versions"] = list(range(1, 1 + attempts))
        return dict(out)

    def stream_steps(init: dict) -> Any:
        """SSE 用：按节点产出 ``{node_name: 该节点的增量}``，不改变 run 的语义。"""
        return compiled.stream(init, stream_mode="updates")

    run.stream_steps = stream_steps  # type: ignore[attr-defined]
    return run
