"""问答流水线的唯一业务实现：``/ask``（同步）与 ``/stream``（SSE）都调用这里。

为什么要抽出来：两条路径此前各写一份「拼 prompt → 生成 → 跑闸 → 修复」，
改一处忘另一处就会出现「同一个问题两条路径结论不同」。现在：

* :func:`answer_sync` 与 :func:`answer_tokens` 共用同一份 prompt 与 fallback 规则；
* :func:`check_gates` / :func:`repair_state` 共用同一套闸门与确定性重建；
* 档位（A / B / C / A_gates）决定的是**调用几次**，而不是**另一套逻辑**。

不做的事：这里不碰网络、不写缓存、不发 SSE，只把「业务一步」写成纯函数，
便于单测直接断言中间结果。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.__007__agents.__001__gates import cited_patterns, run_gates  # noqa: E402
from common import prompt_guard  # noqa: E402
from common.llm import get_llm  # noqa: E402

PLAN_SYSTEM = "你是劳动法检索规划器。只根据给定概念与候选法条，用两三句话写出本案的推理链路（先看哪条、再看哪条、为什么）。不要编造条号。"
ANSWER_SYSTEM = (
    "你是劳动法咨询助手。只能使用提供的法条作答，不得引用未提供的法条，不得编造条号。"
    "回答分两部分：1) 结论（2-3 句）；2) 依据（逐行以「- 」开头，写出条号、法名与该条原文的关键片段）。"
)

MAX_ATTEMPTS = 2
#: 给 answer 节点的 token 预算；太小会把正文挤成空串（思维链共享配额）。
ANSWER_MAX_TOKENS = 480
PLAN_MAX_TOKENS = 220


def fmt_items(items: list[dict]) -> str:
    """拼接层（唯一的 prompt 组装点）：检索条文进 prompt 前必须过数据边界。"""
    if prompt_guard.guard_enabled():
        return prompt_guard.guard_block(
            [
                {
                    "article_id": i["citation"],
                    "source": "corpus",
                    "law_name": i["law_name"],
                    "content": i.get("content", "")[:220],
                }
                for i in items
            ]
        )
    return "\n".join(f"{i['citation']}（{i['law_name']}）：{i.get('content', '')[:220]}" for i in items)


def note_cites_ok(note: str, items: list[dict]) -> bool:
    """规划节点是 LLM 写的，可能顺手带出不存在的条号；确定性答案不能把它原样搬进正文。"""
    cats = [i["citation"] for i in items]
    return all(any(p in c for c in cats) for p in cited_patterns(note))


def deterministic_answer(query: str, retrieval: dict[str, Any], path_note: str = "") -> str:
    items = retrieval.get("items", [])[:4]
    if not items:
        return "没有检索到直接相关的法条，无法给出有依据的结论，建议补充事实或转人工。"
    head = items[0]
    lines = [f"结论：根据{head['citation']}（{head['law_name']}），相关行为应按该条规定处理。"]
    if path_note and note_cites_ok(path_note, items):
        lines.append(f"推理链路：{path_note}")
    lines.append("依据：")
    lines += [f"- {i['citation']}（{i['law_name']}）：{i.get('content', '')[:160]}" for i in items]
    return "\n".join(lines)


def plain_path_note(items: list[dict], top_n: int = 2) -> str:
    """无 LLM 时的确定性链路摘要；同步与流式共用同一句法。"""
    if not items:
        return "无候选条文"
    note = f"先看 {items[0]['citation']}"
    if len(items) > 1:
        note += f"，再核 {items[1]['citation']}"
    return note


def plan_messages(query: str, retrieval: dict[str, Any]) -> list[dict[str, str]]:
    linked = [c["name"] for c in retrieval.get("linked", {}).get("concepts", [])]
    items = retrieval.get("items", [])[:4]
    return [
        {"role": "system", "content": PLAN_SYSTEM},
        {
            "role": "user",
            "content": f"问题：{query}\n命中概念：{'、'.join(linked) or '无'}\n候选法条：\n{fmt_items(items)}",
        },
    ]


def plan_state(query: str, retrieval: dict[str, Any], *, ask_llm: bool) -> dict[str, Any]:
    """规划一步。返回 ``path_note`` 与 ``llm_calls`` 增量（0/1）。"""
    items = retrieval.get("items", [])[:4]
    if ask_llm:
        text = get_llm().try_chat(
            plan_messages(query, retrieval), default="", temperature=0.0, max_tokens=PLAN_MAX_TOKENS
        )
        if text.strip():
            return {"path_note": text.strip(), "llm_calls": 1}
    return {"path_note": plain_path_note(items), "llm_calls": 0}


def answer_messages(query: str, retrieval: dict[str, Any], path_note: str = "") -> list[dict[str, str]]:
    items = retrieval.get("items", [])[:4]
    return [
        {"role": "system", "content": ANSWER_SYSTEM},
        {
            "role": "user",
            "content": (
                f"用户问题：{query}\n推理链路参考：{path_note}\n\n"
                f"可用法条：\n{fmt_items(items)}\n\n请作答。"
            ),
        },
    ]


def answer_sync(query: str, retrieval: dict[str, Any], path_note: str, *, ask_llm: bool) -> dict[str, Any]:
    """同步生成一步。LLM 失败/空正文时回落确定性答案，并照常返回 citations。"""
    items = retrieval.get("items", [])[:4]
    calls = 0
    if ask_llm and items:
        text = get_llm().try_chat(
            answer_messages(query, retrieval, path_note), default="", temperature=0.0, max_tokens=ANSWER_MAX_TOKENS
        )
        if text.strip():
            calls = 1
            return {"answer": text.strip(), "citations": [i["citation"] for i in items], "llm_calls": calls}
    return {
        "answer": deterministic_answer(query, retrieval, path_note),
        "citations": [i["citation"] for i in items],
        "llm_calls": calls,
    }


def answer_tokens(query: str, retrieval: dict[str, Any], path_note: str) -> Iterator[str]:
    """流式生成：只产出 token；失败由调用方决定是否回落确定性答案。

    抛出 :class:`common.llm.LLMDisabled` / ``LLMError``，调用方据此记录降级原因。
    """
    yield from get_llm().chat_stream(
        answer_messages(query, retrieval, path_note), max_tokens=ANSWER_MAX_TOKENS, temperature=0.0
    )


def check_gates(
    answer: str, retrieval: dict[str, Any], article_index: dict[str, dict], art_concepts: dict[str, set[str]]
) -> list[dict]:
    return run_gates(answer, retrieval, article_index, art_concepts)


def repair_state(
    *,
    query: str,
    retrieval: dict[str, Any],
    checks: list[dict],
    path_note: str,
    art_concepts: dict[str, set[str]],
    prior_answer: str,
    attempts: int,
) -> dict[str, Any]:
    """闸门失败时的确定性重建：剔除没有图连接/证据的条文，并显式给出原文片段。"""
    gates_ok = {c["gate"]: c["ok"] for c in checks}
    items = retrieval.get("items", [])
    if not gates_ok.get("path_support", True):
        linked = {c["id"] for c in retrieval.get("linked", {}).get("concepts", [])}
        kept = [i for i in items if art_concepts.get(i["article_id"], set()) & linked]
        items = kept or items
    sub = {**retrieval, "items": items}
    answer = deterministic_answer(query, sub, path_note)
    if not gates_ok.get("low_conf_handoff", True):
        answer = (
            "该问题检索证据不足（top1 相似度与图路径覆盖均偏低），无法确定结论，建议补充材料或转人工复核。\n" + answer
        )
    return {
        "answer": answer,
        "answer_pre_repair": prior_answer,
        "citations": [i["citation"] for i in items[:4]],
        "attempts": attempts + 1,
        "repaired": True,
    }


def after_check(checks: list[dict], attempts: int) -> str:
    if all(c["ok"] for c in checks):
        return "done"
    if attempts < MAX_ATTEMPTS:
        return "repair"
    return "done"


def is_refused(answer: str) -> bool:
    return "无法确定" in answer or "无法给出" in answer
