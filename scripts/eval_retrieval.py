"""B3 检索评测：60 条金标 x {text, graph, hybrid(+router)}，同一套指标口径。

指标定义（写进 README，不换口径）：
* ``recall@k``     —— 检索结果前 k 条覆盖了多少「金标必需条文」；
* ``all@k``        —— 前 k 条是否把金标必需条文全部召回（严格指标，多跳题很难）；
* ``path_recall``  —— 实体链接命中的概念占概念链的比例（图路可解释性的直接体现）；
* ``citation_hit`` —— 前 k 条里有多少条与金标引用完全一致；
* ``latency``      —— 单题端到端检索耗时（含模型前向）。

所有模式都在同一进程里跑，模型只加载一次，延迟才可比。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__004__retrieval.__005__retriever import get_retriever  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from common.path_utils import read_jsonl, write_json  # noqa: E402

log = get_logger("eval_retrieval")

MODES = ("text", "graph", "hybrid")


def evaluate_mode(retriever, gold: list[dict], mode: str, level: str | None = None) -> dict:
    rows = []
    for q in gold:
        expected = set(q["expected_articles"])
        chain = [c["id"] for c in q["concepts"]]
        out = retriever.retrieve(q["question"], mode=mode, level=level)

        top5 = out.article_ids[:5]
        recall5 = len(expected & set(top5)) / max(len(expected), 1)
        all5 = float(expected <= set(top5))
        linked = {c.id for c in out.linking.concepts}
        path_recall = len(linked & set(chain)) / max(len(chain), 1)
        citations = {i.citation for i in out.items[:5]}
        citation_hit = len(citations & set(q["article_citations"])) / max(len(q["article_citations"]), 1)
        rows.append(
            {
                "id": q["id"],
                "hop_type": q["hop_type"],
                "level": out.decision.level,
                "recall@5": round(recall5, 4),
                "all@5": all5,
                "path_recall": round(path_recall, 4),
                "citation_hit": round(citation_hit, 4),
                "coverage": out.coverage,
                "top1_similarity": round(out.top1_similarity, 4),
                "latency_ms": round(out.latency_ms, 1),
                "low_confidence": out.low_confidence,
                "predicted": top5,
            }
        )

    def agg(key: str, subset: list[dict] | None = None) -> float:
        vs = [r[key] for r in (subset or rows)]
        return round(statistics.mean(vs), 4) if vs else 0.0

    out = {
        "mode": mode,
        "level_override": level or "router",
        "n": len(rows),
        "recall@5": agg("recall@5"),
        "all@5": agg("all@5"),
        "path_recall": agg("path_recall"),
        "citation_hit": agg("citation_hit"),
        "latency_median_ms": round(statistics.median([r["latency_ms"] for r in rows]), 1),
        "low_confidence_rate": round(sum(1 for r in rows if r["low_confidence"]) / len(rows), 4),
        "by_hop": {
            t: {
                "recall@5": agg("recall@5", [r for r in rows if r["hop_type"] == t]),
                "all@5": agg("all@5", [r for r in rows if r["hop_type"] == t]),
                "path_recall": agg("path_recall", [r for r in rows if r["hop_type"] == t]),
                "citation_hit": agg("citation_hit", [r for r in rows if r["hop_type"] == t]),
            }
            for t in ("two_hop", "three_hop", "cross_law")
        },
        "rows": rows,
    }
    return out


def paired_bootstrap(results: dict, metric: str, n: int = 1000, seed: int = 20261029) -> dict:
    """按题配对的 bootstrap：同一道题在两个模式下做差，再重采样题号。"""
    rng = random.Random(seed)
    modes = list(results)
    out: dict = {}
    for i, m1 in enumerate(modes):
        for m2 in modes[i + 1 :]:
            r1 = {r["id"]: r for r in results[m1]["rows"]}
            r2 = {r["id"]: r for r in results[m2]["rows"]}
            ids = sorted(set(r1) & set(r2))
            diffs = [r1[k][metric] - r2[k][metric] for k in ids]
            if not diffs:
                continue
            boots = sorted(statistics.mean(rng.choices(diffs, k=len(diffs))) for _ in range(n))
            out[f"{m1}-{m2}"] = {
                "metric": metric,
                "mean": round(statistics.mean(diffs), 4),
                "ci95": [round(boots[int(0.025 * n)], 4), round(boots[int(0.975 * n) - 1], 4)],
                "n": len(diffs),
            }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", nargs="+", default=list(MODES))
    ap.add_argument("--no-router", action="store_true", help="固定 medium 档，测「无路由」基线")
    ap.add_argument("--no-llm", action="store_true", help="确定性路径：路由不触发 LLM 兜底")
    ap.add_argument("--out", default="data/eval_out/retrieval.json", help="结果输出路径（默认沿用原文件）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--gold", default="data/gold/gold_60.jsonl", help="金标题库；留出集请指向 gold_140_holdout.jsonl")
    args = ap.parse_args()

    if args.no_llm:
        from config import settings

        settings.llm_api_key = ""

    gold = read_jsonl(args.gold)
    if args.limit:
        gold = gold[: args.limit]

    retriever = get_retriever(use_router=not args.no_router)
    results: dict = {}
    for mode in args.modes:
        t0 = time.perf_counter()
        res = evaluate_mode(retriever, gold, mode, level="medium" if args.no_router else None)
        results[mode] = res
        log.info(
            "%s recall@5=%.3f all@5=%.3f path=%.3f cit=%.3f median=%.0fms",
            mode,
            res["recall@5"],
            res["all@5"],
            res["path_recall"],
            res["citation_hit"],
            res["latency_median_ms"],
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    boot = {
        "recall@5": paired_bootstrap(results, "recall@5"),
        "all@5": paired_bootstrap(results, "all@5"),
    }
    write_json(
        out_path,
        {
            "gold_report": json.loads(Path("data/gold/gold_report.json").read_text(encoding="utf-8")),
            "results": results,
            "paired_bootstrap": boot,
        },
    )
    log.info("写出 %s", out_path)


if __name__ == "__main__":
    main()
