"""同步 /ask 与流式 /ask/stream 的语义契约检查（LW-N2）。

同一批问题分别走两条路径，解析**最终答案、引用、拒答、五闸、修复版本、缓存状态**，
按字段比较而不是比字符串是否逐字相等；再校验 SSE 事件序列是否符合
:data:`app.__008__api.__003__stream.EVENT_SCHEMA`。

默认 ``fixture`` 模式：用假检索器 + 假 LLM，完全离线、可重复，CI 可跑。
``--live`` 模式：打真实检索器与真实语料，只作冒烟；默认 ``llm=False``，
只有显式加 ``--with-llm`` 才会调付费 API。

一条命令：

    python scripts/check_stream_contract.py
    python scripts/check_stream_contract.py --tiers A_gates,C
    python scripts/check_stream_contract.py --live --limit 10

退出码 0 = 无语义差异；1 = 存在差异或 Schema 违规（可直接作为发布门禁）。
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__007__agents import __002__flows as flows_mod  # noqa: E402
from app.__007__agents import __003__pipeline as pipeline_mod  # noqa: E402
from app.__008__api import __000__server as server  # noqa: E402
from app.__008__api import __003__stream as stream_mod  # noqa: E402
from app.__008__api.__002__cache import (  # noqa: E402
    ANSWER_VERSION,
    POLICY_VERSION,
    PROMPT_VERSION,
    get_cache,
    model_version,
)
from common.llm import LLMDisabled  # noqa: E402
from common.path_utils import write_json  # noqa: E402

TIERS = ("A", "B", "C", "A_gates")
GATED = ("C", "A_gates")
COMPARE_FIELDS = (
    "answer",
    "citations",
    "refused",
    "checks",
    "answer_version",
    "superseded_versions",
    "graph_build_id",
    "plan_present",
)
FAKE_MODEL = "contract-fixture-model"
BUILD_ID = "gcontract0000"

ARTICLE = {
    "article_id": "lcl#50",
    "citation": "中华人民共和国劳动法第五十条",
    "law_name": "劳动法",
    "article_no": "第五十条",
    "content": "工资应当以货币形式按月支付给劳动者本人。不得克扣或者无故拖欠劳动者的工资。",
    "chapter_path": "第五章 工资",
}
BASE_RETRIEVAL = {
    "query": "公司拖欠工资怎么办",
    "mode": "hybrid",
    "graph_build_id": BUILD_ID,
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


def _retrieval(**over: Any) -> dict[str, Any]:
    out = copy.deepcopy(BASE_RETRIEVAL)
    out.update(over)
    return out


GOOD_ANSWER = (
    "结论：工资应按月足额支付，拖欠部分可以要求补发。\n"
    "依据：\n"
    "- 中华人民共和国劳动法第五十条（劳动法）：工资应当以货币形式按月支付给劳动者本人。"
)
BAD_CITE_ANSWER = (
    "结论：根据劳动合同法第九十九条处理。\n"
    "依据：\n- 劳动合同法第九十九条（劳动合同法）：不存在的条文"
)


class FakeLLM:
    """规划与作答分开，便于检查「流式是否漏了 plan」；可配置为直接抛错。"""

    def __init__(
        self,
        plan_text: str = "先核中华人民共和国劳动法第五十条。",
        answer_text: str = GOOD_ANSWER,
        raises: BaseException | None = None,
    ) -> None:
        self.plan_text = plan_text
        self.answer_text = answer_text
        self.raises = raises
        self.plan_calls = 0
        self.answer_calls = 0

    def try_chat(self, messages: list[dict[str, str]], default: str = "", **_: Any) -> str:
        # 与真实 LLMClient 一致：try_* 吞掉异常返回 default，只有 chat_stream 会抛。
        if self.raises is not None:
            return default
        if "规划器" in messages[0]["content"]:
            self.plan_calls += 1
            return self.plan_text
        self.answer_calls += 1
        return self.answer_text

    def chat_stream(self, messages: list[dict[str, str]], *, max_tokens: int = 480, temperature: float = 0.0):
        if self.raises is not None:
            raise self.raises
        self.answer_calls += 1
        for ch in self.answer_text:
            yield ch


class _Out:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._payload)


class _Store:
    graph_build_id = BUILD_ID
    content_hash = "fixture"


class _Graph:
    store = _Store()


class _Text:
    articles = ARTICLE_INDEX


class _Retriever:
    graph = _Graph()
    text = _Text()

    def __init__(self, retrieval: dict[str, Any]) -> None:
        self.retrieval = retrieval
        self.calls = 0

    def retrieve(self, query: str, mode: str = "hybrid") -> _Out:
        self.calls += 1
        return _Out(self.retrieval)


def sync_bundle(state: dict[str, Any]) -> dict[str, Any]:
    checks = {c["gate"]: bool(c["ok"]) for c in state.get("checks") or []}
    return {
        "answer": state.get("answer", ""),
        "citations": list(state.get("citations") or []),
        "refused": bool(state.get("refused", False)),
        "checks": checks,
        "answer_version": int(state.get("answer_version", 1)),
        "superseded_versions": list(state.get("superseded_versions") or []),
        "graph_build_id": state.get("graph_build_id", ""),
        "plan_present": bool(state.get("path_note")),
        "cache": state.get("cache", "miss"),
    }


def stream_bundle(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    plan = None
    final: dict[str, Any] = {}
    done: dict[str, Any] = {}
    error: dict[str, Any] | None = None
    for name, data in events:
        if name == "meta":
            meta = data
        elif name == "plan":
            plan = data
        elif name == "final":
            final = data
        elif name == "done":
            done = data
        elif name == "error":
            error = data
    checks = {c["gate"]: bool(c["ok"]) for c in final.get("checks") or []}
    return {
        "answer": final.get("answer", ""),
        "citations": list(final.get("citations") or []),
        "refused": bool(final.get("refused", False)),
        "checks": checks,
        "answer_version": int(final.get("answer_version", 1)),
        "superseded_versions": list(final.get("superseded_versions") or []),
        "graph_build_id": meta.get("graph_build_id", ""),
        "plan_present": plan is not None,
        "cache": done.get("cache", meta.get("cache", "miss")),
        "status": done.get("status", "error" if error else "missing"),
    }


def diff_bundles(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    """逐字段比较；answer 只在两边都非空时给出最短差异位置，便于定位漂移。"""
    out: dict[str, Any] = {}
    for field in COMPARE_FIELDS:
        a, b = left.get(field), right.get(field)
        if a != b:
            out[field] = {"sync": a, "stream": b}
    return out


def _events(raw: list[str]) -> list[tuple[str, dict[str, Any]]]:
    parsed: list[tuple[str, dict[str, Any]]] = []
    for block in raw:
        name = ""
        data: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data = json.loads(line.split(":", 1)[1].strip())
        if name:
            parsed.append((name, data))
    return parsed


def _structural_problems(
    events: list[tuple[str, dict[str, Any]]], tier: str
) -> list[str]:
    """除了字段一致性，再检查「草稿不得被当成终稿」这类顺序语义。"""
    problems = list(stream_mod.schema_errors(events))
    names = [n for n, _ in events]
    if tier in GATED and "check" not in names:
        problems.append(f"{tier} 档没有任何 check 事件")
    if "final" in names and "check" in names and names.index("check") > names.index("final"):
        problems.append("check 出现在 final 之后")
    first_check = names.index("check") if "check" in names else len(names)
    for i, (name, data) in enumerate(events):
        if name == "token" and i < first_check and tier in GATED and not data.get("draft"):
            problems.append("闸门之前流出的正文被标记为非草稿")
            break
    if "superseded" in names:
        sup = dict(events)["superseded"]
        after = names[names.index("superseded") + 1 :]
        if "token" not in after:
            problems.append("superseded 之后没有新版本正文")
        if sup.get("answer_version") not in dict(events)["done"].get("superseded_versions", []):
            problems.append("superseded 版本未出现在 done.superseded_versions")
    return problems


def run_fixture_case(case: dict[str, Any], tier: str) -> dict[str, Any]:
    retrieval = case["retrieval"]
    fake = FakeLLM(
        plan_text=case.get("plan_text", "先核中华人民共和国劳动法第五十条。"),
        answer_text=case.get("answer_text", GOOD_ANSWER),
        raises=case.get("raises"),
    )
    enabled = bool(case.get("llm", True))
    retriever = _Retriever(retrieval)

    def run_sync() -> dict[str, Any]:
        from fastapi import Response

        req = server.AskReq(query=retrieval["query"], mode="hybrid", tier=tier, llm=enabled)
        return server.ask(req, Response())

    def run_stream() -> list[tuple[str, dict[str, Any]]]:
        raw = list(
            stream_mod.stream_answer(
                retrieval["query"], "hybrid", tier, retriever, ARTICLE_INDEX, ART_CONCEPTS, use_llm=enabled
            )
        )
        return _events(raw)

    patches = [
        patch.object(server, "_retriever", lambda: retriever),
        patch.object(server, "_art_concepts", lambda: ART_CONCEPTS),
        patch.object(server, "model_version", lambda: FAKE_MODEL),
        patch.object(server, "llm_status", lambda: {"enabled": enabled}),
        patch.object(server, "write_trace", lambda *a, **k: None),
        patch.object(stream_mod, "model_version", lambda: FAKE_MODEL),
        patch.object(stream_mod, "llm_status", lambda: {"enabled": enabled}),
        patch.object(stream_mod, "write_trace", lambda *a, **k: None),
        patch.object(pipeline_mod, "get_llm", lambda: fake),
        patch.object(flows_mod, "llm_probe", lambda: enabled),
    ]
    for p in patches:
        p.start()
    try:
        server._FLOWS.clear()
        get_cache().clear()
        sync_miss = sync_bundle(run_sync())

        get_cache().clear()
        events = run_stream()
        stream_miss = stream_bundle(events)

        stream_events_again = run_stream()  # 命中流式自己写的缓存
        stream_hit = stream_bundle(stream_events_again)

        sync_hit = sync_bundle(run_sync())  # 命中流式写入的缓存：跨路径复用必须同 key 同字段
    finally:
        for p in reversed(patches):
            p.stop()
        server._FLOWS.clear()
        get_cache().clear()

    return {
        "case": case["id"],
        "tier": tier,
        "sync_miss": sync_miss,
        "stream_miss": stream_miss,
        "stream_hit": stream_hit,
        "sync_hit": sync_hit,
        "diffs": {
            "sync_vs_stream": diff_bundles(sync_miss, stream_miss),
            "stream_replay": diff_bundles(stream_miss, stream_hit),
            "cross_cache": diff_bundles(sync_miss, sync_hit),
        },
        "schema_problems": _structural_problems(events, tier),
    }


FIXTURE_CASES: list[dict[str, Any]] = [
    {"id": "grounded", "retrieval": _retrieval()},
    {"id": "repair_fake_citation", "retrieval": _retrieval(), "answer_text": BAD_CITE_ANSWER},
    {
        "id": "refuse_empty_items",
        "retrieval": _retrieval(
            items=[], coverage=0.0, top1_similarity=0.0, low_confidence=True, linked={"concepts": [], "scenarios": []}
        ),
        "llm": False,
    },
    {
        "id": "low_conf_handoff",
        "retrieval": _retrieval(top1_similarity=0.41, coverage=0.31, low_confidence=True),
        "llm": False,
    },
    {"id": "llm_unavailable", "retrieval": _retrieval(), "raises": LLMDisabled("no key")},
]


def run_fixture(tiers: list[str]) -> dict[str, Any]:
    rows = [run_fixture_case(case, tier) for case in FIXTURE_CASES for tier in tiers]
    bad = [r for r in rows if any(r["diffs"].values()) or r["schema_problems"]]
    return _report("fixture", tiers, rows, bad)


def _live_questions(limit: int) -> list[str]:
    path = Path("data/gold/gold_60.jsonl")
    if not path.exists():
        return ["公司拖欠工资怎么办"]
    out: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if len(out) >= limit:
                break
            line = line.strip()
            if line:
                out.append(json.loads(line)["question"])
    return out


def run_live(tiers: list[str], limit: int, with_llm: bool) -> dict[str, Any]:
    retriever = server._retriever()
    rows: list[dict[str, Any]] = []
    for question in _live_questions(limit):
        for tier in tiers:
            get_cache().clear()
            state = server.ask(
                server.AskReq(query=question, mode="hybrid", tier=tier, llm=with_llm), server.Response()
            )
            sync = sync_bundle(state)
            get_cache().clear()
            raw = list(
                stream_mod.stream_answer(
                    question,
                    "hybrid",
                    tier,
                    retriever,
                    retriever.text.articles,
                    server._art_concepts(),
                    use_llm=with_llm,
                )
            )
            events = _events(raw)
            stream = stream_bundle(events)
            rows.append(
                {
                    "question": question,
                    "tier": tier,
                    "sync_miss": sync,
                    "stream_miss": stream,
                    "diffs": {"sync_vs_stream": diff_bundles(sync, stream)},
                    "schema_problems": _structural_problems(events, tier),
                }
            )
    bad = [r for r in rows if any(r["diffs"].values()) or r["schema_problems"]]
    return _report("live", tiers, rows, bad, extra={"with_llm": with_llm, "questions": len(rows)})


def _report(mode: str, tiers: list[str], rows: list[dict[str, Any]], bad: list[dict[str, Any]], extra=None) -> dict:
    return {
        "mode": mode,
        "versions": {
            "model": FAKE_MODEL if mode == "fixture" else model_version(),
            "prompt": PROMPT_VERSION,
            "policy": POLICY_VERSION,
            "answer_schema": ANSWER_VERSION,
            "graph_build_id": BUILD_ID if mode == "fixture" else server._graph_build_id(),
        },
        "tiers": tiers,
        "total": len(rows),
        "failed": len(bad),
        "ok": not bad,
        **(extra or {}),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="同步 /ask 与 SSE /ask/stream 语义契约检查")
    ap.add_argument("--live", action="store_true", help="打真实检索器（冒烟），默认离线 fixture")
    ap.add_argument("--with-llm", action="store_true", help="live 模式下启用真实 LLM（会产生费用）")
    ap.add_argument("--limit", type=int, default=5, help="live 模式的问题数")
    ap.add_argument("--tiers", default="A,B,C,A_gates", help="逗号分隔的档位")
    ap.add_argument("--out", default="", help="输出 JSON 路径")
    args = ap.parse_args(argv)

    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    unknown = [t for t in tiers if t not in TIERS]
    if unknown:
        print(f"未知档位: {unknown}（可选 {TIERS}）", file=sys.stderr)
        return 2

    if args.live:
        report = run_live(tiers, args.limit, args.with_llm)
        out = args.out or "data/eval_out/stream_contract_live.json"
    else:
        report = run_fixture(tiers)
        out = args.out or "data/eval_out/stream_contract.json"

    write_json(Path(out), report)
    print(
        f"[{report['mode']}] {report['total']} 组用例，失败 {report['failed']}；"
        f"graph={report['versions']['graph_build_id']} → {out}"
    )
    for row in report["rows"]:
        if not (any(row["diffs"].values()) or row["schema_problems"]):
            continue
        head = row.get("case") or row.get("question", "")[:30]
        print(f"  ✗ {head} / {row['tier']}")
        for name, diff in row["diffs"].items():
            for field, pair in diff.items():
                print(f"    {name}.{field}: sync={pair['sync']!r} stream={pair['stream']!r}")
        for problem in row["schema_problems"]:
            print(f"    schema: {problem}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
