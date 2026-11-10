"""图路真实贡献评估（LW-O4）：不再用一个总体 R@5 代替全部证据。

一条命令同时给出三层口径：

1. **检索命中**：`text` / `graph` / `hybrid` 三路各自的 recall@5 与 all@5；
2. **路径正确**：实体链接是否命中金标场景、概念链是否完整（与检索命中分开报）；
3. **最终答案正确**：只在有缓存离线 B6 结果的题上报告，并标明来源，不冒充本次 live 重跑。

再逐题回答「图路到底救了什么」：

* ``text_failed_graph_recovered`` —— 文本路 top5 漏金标、hybrid top5 补齐；
* ``graph_only_candidates`` —— 出现在图路但不在文本路候选里的条文，逐条判定
  ``necessary``（金标必需，属图路独有）/ ``relevant_not_necessary``（与查询概念相连但非金标）/
  ``noise``（既不金标也不连概念）；
* 按 ``two_hop`` / ``three_hop`` / ``cross_law`` / ``entity_linking_failure`` 分桶，
  每项都给样本量与 Wilson 95% 区间；不显著就如实写不显著。

默认在 **140 条题级留出集** 上跑（最终确认集，不用于调阈值），
``--gold data/gold/gold_60.jsonl`` 可切到母集做对照。

    python scripts/eval_graph_contribution.py
    python scripts/eval_graph_contribution.py --gold data/gold/gold_60.jsonl --no-rerank

输出：``data/eval_out/graph_contribution.json`` + 同名 ``.md`` 摘要。
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__004__retrieval.__005__retriever import get_retriever  # noqa: E402
from common.eval_meta import graph_build_id as _graph_build_id  # noqa: E402
from common.eval_meta import split_role as _split_role  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from common.path_utils import read_jsonl, write_json  # noqa: E402

log = get_logger("eval_graph_contribution")

TOP_K = 5
ANSWER_CACHE = "data/eval_out/ablation_holdout_60.json"


# --------------------------------------------------------------------------------
# 统计工具
# --------------------------------------------------------------------------------
def wilson(k: int, n: int, z: float = 1.96) -> list[float]:
    """二项比例的 Wilson 区间：小样本下比正态近似可靠，n=0 时返回 [0, 0]。"""
    if n <= 0:
        return [0.0, 0.0]
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


def rate(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if not n:
        return {"n": 0, "mean": 0.0, "ci95": [0.0, 0.0]}
    return {
        "n": n,
        "mean": round(sum(values) / n, 4),
        "ci95": wilson(round(sum(values)), n) if set(values) <= {0.0, 1.0} else [None, None],
    }


def bool_rate(values: list[bool]) -> dict[str, Any]:
    return rate([1.0 if v else 0.0 for v in values])


# --------------------------------------------------------------------------------
# 单题证据
# --------------------------------------------------------------------------------
def split_expected_path(q: dict) -> tuple[str, set[str], set[str]]:
    """把 expected_path 拆成 (场景, 概念集合, 条文集合)。"""
    scenario = ""
    concepts: set[str] = set()
    articles: set[str] = set()
    gold_articles = set(q.get("expected_articles") or [])
    for node in q.get("expected_path") or []:
        if node.startswith("sc:"):
            scenario = node[3:]
        elif node in gold_articles:
            articles.add(node)
        else:
            concepts.add(node)
    return scenario, concepts, articles


def evaluate_one(
    retriever: Any,
    q: dict,
    use_rerank: bool,
    article_concepts: dict[str, set[str]],
    level: str | None,
) -> dict[str, Any]:
    gold = set(q.get("expected_articles") or [])
    concept_ids = {c["id"] for c in q.get("concepts") or []}
    scenario_id, path_concepts, _ = split_expected_path(q)
    want_concepts = concept_ids or path_concepts

    outs = {
        mode: retriever.retrieve(q["question"], mode=mode, level=level, use_rerank=use_rerank)
        for mode in ("text", "graph", "hybrid")
    }
    tops = {mode: o.article_ids[:TOP_K] for mode, o in outs.items()}
    hybrid = outs["hybrid"]
    linked_concepts = {c.id for c in hybrid.linking.concepts}
    linked_scenarios = {s.id for s in hybrid.linking.scenarios}

    def recall(ids: list[str]) -> float:
        return len(gold & set(ids)) / max(len(gold), 1)

    text_top, graph_top, hybrid_top = tops["text"], tops["graph"], tops["hybrid"]
    text_hit = gold <= set(text_top)
    graph_hit = gold <= set(graph_top)
    hybrid_hit = gold <= set(hybrid_top)
    text_failed = not text_hit
    recovered = text_failed and hybrid_hit

    graph_only = [a for a in dict.fromkeys(graph_top + hybrid_top) if a not in set(text_top)]
    verdicts: list[dict[str, Any]] = []
    for aid in graph_only:
        if aid in gold:
            verdict = "necessary"
        elif article_concepts.get(aid, set()) & want_concepts:
            verdict = "relevant_not_necessary"
        else:
            verdict = "noise"
        item = next((i for i in hybrid.items if i.article_id == aid), None)
        verdicts.append(
            {
                "article_id": aid,
                "verdict": verdict,
                "in_gold": aid in gold,
                "graph_coverage": round(float(getattr(item, "coverage", 0.0)), 4) if item else 0.0,
                "path": list(getattr(item, "path", []))[:6] if item else [],
                "in_text_candidates": aid in set(text_top),
            }
        )

    scenario_hit = bool(scenario_id) and scenario_id in linked_scenarios
    concept_hit = len(linked_concepts & want_concepts) / max(len(want_concepts), 1)
    path_ok = scenario_hit and concept_hit >= 1.0
    linking_failure = concept_hit == 0.0

    missing = sorted(gold - set(hybrid_top))
    return {
        "_question": q,
        "id": q["id"],
        "hop_type": q.get("hop_type"),
        "n_gold": len(gold),
        "text_top5": text_top,
        "graph_top5": graph_top,
        "hybrid_top5": hybrid_top,
        "text_recall@5": round(recall(text_top), 4),
        "graph_recall@5": round(recall(graph_top), 4),
        "hybrid_recall@5": round(recall(hybrid_top), 4),
        "text_hit@5": text_hit,
        "graph_hit@5": graph_hit,
        "hybrid_hit@5": hybrid_hit,
        "text_failed": text_failed,
        "text_failed_graph_recovered": recovered,
        "path_scenario_hit": scenario_hit,
        "path_concept_recall": round(concept_hit, 4),
        "path_ok": path_ok,
        "entity_linking_failure": linking_failure,
        "graph_only_candidates": verdicts,
        "missing_in_hybrid": missing,
        "text_top1_similarity": round(float(getattr(outs["text"], "top1_similarity", 0.0)), 4),
        "low_confidence": bool(hybrid.low_confidence),
        "graph_build_id": outs["text"].graph_build_id,
    }


# --------------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------------
def bucket_of(row: dict, q: dict) -> list[str]:
    out: list[str] = []
    hop = row.get("hop_type")
    if hop in ("two_hop", "three_hop"):
        out.append(hop)
    if hop == "cross_law" or len(q.get("laws") or []) > 1:
        out.append("cross_law")
    if row["entity_linking_failure"]:
        out.append("entity_linking_failure")
    return out


def failure_reason(row: dict) -> str:
    """hybrid 未命中的确定性归因：实体链接 → 路径 → 置信度 → 排序丢失。"""
    if row.get("entity_linking_failure"):
        return "entity_linking_failure"
    if not row.get("path_ok"):
        return "graph_path_incomplete"
    if row.get("low_confidence"):
        return "low_confidence"
    return "ranking_miss"


def summarize(rows: list[dict], answer_by_id: dict[str, dict]) -> dict[str, Any]:
    def block(subset: list[dict]) -> dict[str, Any]:
        n = len(subset)
        candidates = [c for r in subset for c in r["graph_only_candidates"]]
        with_graph_only = [r for r in subset if r["graph_only_candidates"]]
        necessary_qs = [r for r in with_graph_only if any(c["verdict"] == "necessary" for c in r["graph_only_candidates"])]
        answered = [r for r in subset if r["id"] in answer_by_id]
        answer_correct = [
            bool(answer_by_id[r["id"]].get("grounded") == 1.0 and (answer_by_id[r["id"]].get("cite_recall") or 0) >= 1.0)
            for r in answered
        ]
        return {
            "n": n,
            "text_hit@5": bool_rate([r["text_hit@5"] for r in subset]),
            "graph_hit@5": bool_rate([r["graph_hit@5"] for r in subset]),
            "hybrid_hit@5": bool_rate([r["hybrid_hit@5"] for r in subset]),
            "text_recall@5": rate([r["text_recall@5"] for r in subset]),
            "hybrid_recall@5": rate([r["hybrid_recall@5"] for r in subset]),
            "path_ok": bool_rate([r["path_ok"] for r in subset]),
            "path_scenario_hit": bool_rate([r["path_scenario_hit"] for r in subset]),
            "path_concept_recall": rate([r["path_concept_recall"] for r in subset]),
            "text_failed_graph_recovered": bool_rate([r["text_failed_graph_recovered"] for r in subset]),
            "text_failed_n": sum(1 for r in subset if r["text_failed"]),
            "recovered_n": sum(1 for r in subset if r["text_failed_graph_recovered"]),
            "graph_only_candidates": {
                "total": len(candidates),
                "necessary": sum(1 for c in candidates if c["verdict"] == "necessary"),
                "relevant_not_necessary": sum(1 for c in candidates if c["verdict"] == "relevant_not_necessary"),
                "noise": sum(1 for c in candidates if c["verdict"] == "noise"),
                "questions_with_any": len(with_graph_only),
            },
            "necessary_path_rate": bool_rate([bool(r["graph_only_candidates"]) and any(
                c["verdict"] == "necessary" for c in r["graph_only_candidates"]) for r in with_graph_only])
            if with_graph_only
            else {"n": 0, "mean": 0.0, "ci95": [0.0, 0.0]},
            "answer_correct": {
                **bool_rate(answer_correct),
                "source_note": "来自缓存离线 B6 结果，非本次 live 重跑",
                "source": ANSWER_CACHE,
            }
            if answered
            else {"n": 0, "mean": None, "ci95": [None, None], "source_note": "该子集没有可用的离线答案结果"},
            "necessary_questions": len(necessary_qs),
            "failure_reasons": dict(Counter(failure_reason(r) for r in subset if not r["hybrid_hit@5"])),
        }

    buckets: dict[str, list[dict]] = {}
    for r in rows:
        q = r["_question"]
        for name in bucket_of(r, q):
            buckets.setdefault(name, []).append(r)
    return {
        "overall": block(rows),
        "by_bucket": {name: block(subset) for name, subset in sorted(buckets.items())},
    }


def build_markdown(report: dict) -> str:
    v = report["versions"]
    lines = [
        "# 图路真实贡献评估（LW-O4）",
        "",
        f"- 图版本：`{v['graph_build_id']}`；留出集：`{report['gold_path']}`（{report['split_role']}，n={report['n']}）",
        f"- 精排：{'开启' if report['use_rerank'] else '关闭'}；取回：top{TOP_K}",
        "- 口径：检索命中、路径正确、答案正确三层分开；图路独有候选逐条判定必要/相关非必要/噪声。",
        "",
        "## 总体",
        "",
        "| 指标 | 值 | 区间 (95%) | n |",
        "| --- | --- | --- | --- |",
    ]

    def row(name: str, cell: dict, key: str = "mean") -> None:
        lines.append(f"| {name} | {cell.get(key)} | {cell.get('ci95')} | {cell.get('n')} |")

    o = report["overall"]
    row("文本路 hit@5", o["text_hit@5"])
    row("图路 hit@5", o["graph_hit@5"])
    row("hybrid hit@5", o["hybrid_hit@5"])
    row("路径正确率（场景命中且概念链完整）", o["path_ok"])
    row("其中：场景命中", o["path_scenario_hit"])
    row("其中：概念链召回", o["path_concept_recall"])
    row("文本失败→图路补齐率", o["text_failed_graph_recovered"])
    row("答案正确率（离线缓存）", o["answer_correct"])
    lines += [
        "",
        f"- 文本失败 {o['text_failed_n']} 题，其中图路补齐 {o['recovered_n']} 题。",
        f"- 图路独有候选 {o['graph_only_candidates']['total']} 条：必要 "
        f"{o['graph_only_candidates']['necessary']}、相关非必要 "
        f"{o['graph_only_candidates']['relevant_not_necessary']}、噪声 "
        f"{o['graph_only_candidates']['noise']}。",
        "",
        "## 分桶",
        "",
        "| 桶 | n | 文本 hit@5 | hybrid hit@5 | 路径正确 | 图路补齐 | 答案正确(缓存) |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name, b in report["by_bucket"].items():
        lines.append(
            f"| {name} | {b['n']} | {b['text_hit@5']['mean']} | {b['hybrid_hit@5']['mean']} | "
            f"{b['path_ok']['mean']} | {b['text_failed_graph_recovered']['mean']} | {b['answer_correct']['mean']} |"
        )
    lines += [
        "",
        "## 失败归因（hybrid 未命中）",
        "",
        "| 桶 | 未命中 | 实体链接失败 | 图路径不完整 | 低置信 | 排序丢失 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, b in report["by_bucket"].items():
        reasons = b["failure_reasons"]
        lines.append(
            f"| {name} | {sum(reasons.values())} | {reasons.get('entity_linking_failure', 0)} | "
            f"{reasons.get('graph_path_incomplete', 0)} | {reasons.get('low_confidence', 0)} | "
            f"{reasons.get('ranking_miss', 0)} |"
        )
    answered = report["overall"]["answer_correct"].get("n") or 0
    lines += [
        "",
        "## 样本",
        "",
        f"- 图路补齐案例：`recoveries` 共 {len(report['recoveries'])} 条（含文本漏掉的金标与图路径）。",
        f"- hybrid 仍未命中：`failures` 共 {len(report['failures'])} 条，每条带 `failure_reason`。",
        f"- 图路独有条文清单：`graph_only_candidates` 共 {len(report['graph_only_candidates'])} 条。",
        f"- 答案层只有 {answered}/{report['n']} 题可用（缓存离线 B6），"
        f"其余 {report['n'] - answered} 题**只报检索与路径，不报答案正确率**。",
        "",
        "机器可读明细见同目录 `graph_contribution.json`。",
        "",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------
def load_answer_cache(tier: str) -> dict[str, dict]:
    path = Path(ANSWER_CACHE)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    rows = (data.get("rows") or {}).get(tier) or []
    return {r["id"]: r for r in rows}


def build_article_concepts(retriever: Any, gold: list[dict]) -> dict[str, set[str]]:
    """只反查金标涉及的概念，避免全量扫描；用于判定「相关但非必要」。"""
    store = retriever.graph.store
    concept_ids = sorted({c["id"] for q in gold for c in (q.get("concepts") or [])})
    out: dict[str, set[str]] = {}
    for cid in concept_ids:
        for row in store.concept_articles([cid], limit=800):
            out.setdefault(row["article_id"], set()).add(cid)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="图路独特贡献评估（检索/路径/答案三层 + 分桶）")
    ap.add_argument("--gold", default="data/gold/gold_140_holdout.jsonl")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-rerank", action="store_true", help="关闭共享精排，作为独立评价路径")
    ap.add_argument("--no-llm", action="store_true", help="关闭 LLM 路由（离线复算，不产生付费调用）")
    ap.add_argument("--answer-tier", default="A_gates", help="离线答案缓存使用的档位")
    ap.add_argument("--out", default="data/eval_out/graph_contribution.json")
    args = ap.parse_args(argv)

    if args.no_llm:
        from config import settings

        settings.llm_api_key = ""

    gold = read_jsonl(args.gold)
    if args.limit:
        gold = gold[: args.limit]
    retriever = get_retriever()
    article_concepts = build_article_concepts(retriever, gold)
    answers = load_answer_cache(args.answer_tier)
    if not answers:
        log.warning("未找到离线答案缓存 %s，答案层标记为不可用", ANSWER_CACHE)

    rows: list[dict[str, Any]] = []
    for i, q in enumerate(gold, 1):
        row = evaluate_one(retriever, q, use_rerank=not args.no_rerank, article_concepts=article_concepts, level=None)
        rows.append(row)
        if i % 20 == 0:
            log.info("已评估 %d/%d", i, len(gold))

    summary = summarize(rows, answers)
    recoveries = [
        {
            "id": r["id"],
            "question": next(q["question"] for q in gold if q["id"] == r["id"]),
            "text_top5": r["text_top5"],
            "hybrid_top5": r["hybrid_top5"],
            "text_failed_on": sorted(set(next(q["expected_articles"] for q in gold if q["id"] == r["id"])) - set(r["text_top5"])),
            "graph_paths": {c["article_id"]: c["path"] for c in r["graph_only_candidates"] if c["in_gold"]},
            "text_top1_similarity": r["text_top1_similarity"],
        }
        for r in rows
        if r["text_failed_graph_recovered"]
    ]
    failures = [
        {
            "id": r["id"],
            "question": next(q["question"] for q in gold if q["id"] == r["id"]),
            "missing_in_hybrid": r["missing_in_hybrid"],
            "hybrid_top5": r["hybrid_top5"],
            "text_top5": r["text_top5"],
            "path_ok": r["path_ok"],
            "entity_linking_failure": r["entity_linking_failure"],
            "low_confidence": r["low_confidence"],
            "failure_reason": failure_reason(r),
        }
        for r in rows
        if not r["hybrid_hit@5"]
    ]
    candidates = [
        {"id": r["id"], **c} for r in rows for c in r["graph_only_candidates"]
    ]

    for r in rows:
        r.pop("_question", None)
    report = {
        "versions": {
            "graph_build_id": _graph_build_id(),
            "gold_path": args.gold,
            "split_role": _split_role(args.gold),
            "use_rerank": not args.no_rerank,
            "router_llm": not args.no_llm,
            "top_k": TOP_K,
            "answer_tier": args.answer_tier,
        },
        "gold_path": args.gold,
        "split_role": _split_role(args.gold),
        "n": len(rows),
        "use_rerank": not args.no_rerank,
        "answer_layer_available": bool(answers),
        "answer_layer_note": "答案层来自缓存离线 B6 结果，仅覆盖 gold_60_holdout 的 60 题；其余题只报检索/路径。",
        **summary,
        "recoveries": recoveries,
        "failures": failures,
        "graph_only_candidates": candidates,
        "rows": rows,
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, report)
    md = out.with_suffix(".md")
    md.write_text(build_markdown(report), encoding="utf-8")
    o = report["overall"]
    print(
        f"[graph-contribution] n={report['n']} graph={report['versions']['graph_build_id']}\n"
        f"  文本 hit@5={o['text_hit@5']['mean']} hybrid hit@5={o['hybrid_hit@5']['mean']} "
        f"路径正确={o['path_ok']['mean']}\n"
        f"  文本失败={o['text_failed_n']} 图路补齐={o['recovered_n']} "
        f"图路独有候选={o['graph_only_candidates']['total']}"
        f"（必要 {o['graph_only_candidates']['necessary']}）\n"
        f"  → {out} / {md}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
