"""B6 消融：A（单链路）/ B（多 Agent）/ C（多 Agent + 五道闸）/ A_gates（单链路 + 五道闸）x 60 条。

同一检索器、同一模型、同一批题，只动编排与校验。
A_gates 是 T04 补的第四档：回答后直接过五道闸门，跳过 B/C 的 plan 节点，
用来回答「闸门收益是否依赖多 Agent 规划」，且它是唯一「1 次 LLM + 闸门」的组合。
指标口径：
* ``recall@5``      —— 检索前 5 条覆盖金标必需条文的比例（四档同源，预期不变，用来证明差异不是检索带来的）；
* ``path_accuracy`` —— 实体链接命中的概念占金标概念链的比例；
* ``cite_precision``—— 答案里引用的条号有多少落在金标引用集合内（越界引用即扣分）；
* ``cite_recall``   —— 金标引用被答案覆盖了多少；
* ``grounded``      —— 答案引用是否全部来自本轮检索结果（与档位无关的独立复算，供 C 与 A/B 对比）；
* ``llm_calls``     —— 单题 LLM 调用次数（USAGE 计数差值，真实值）；
* ``latency``       —— 单题端到端耗时（毫秒）。
置信区间用**配对 bootstrap 100 次**（同一重采样下标同时作用于两档），只报区间是否跨 0。
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
from app.__007__agents.__001__gates import build_article_concepts, cited_citations  # noqa: E402
from app.__007__agents.__002__flows import make_flow  # noqa: E402
from common.eval_meta import graph_build_id as _graph_build_id  # noqa: E402
from common.eval_meta import split_role as _split_role  # noqa: E402
from common.llm import USAGE  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from common.path_utils import read_jsonl, write_json  # noqa: E402

log = get_logger("eval_ablation")

TIERS = ("A", "B", "C", "A_gates")
BOOTSTRAP_N = 100


def metrics_of(state: dict, gold: dict, latency_ms: float, calls: int, tokens: int, known: set[str]) -> dict:
    ret = state["retrieval"]
    expected = set(gold["expected_articles"])
    chain = [c["id"] for c in gold["concepts"]]
    top5 = [i["article_id"] for i in ret["items"][:5]]
    answer_list = cited_citations(state.get("answer", ""), known)
    answer_cites = set(answer_list)
    raw_cites = set(cited_citations(state.get("answer_pre_repair") or state.get("answer", ""), known))
    gold_cites = set(gold["article_citations"])
    retrieved_cites = {i["citation"] for i in ret["items"]}
    n_ans, n_must = len(answer_cites), len(gold_cites)
    k = min(n_must, len(answer_list))
    prec_at_must = len(set(answer_list[:k]) & gold_cites) / k if k else 0.0
    return {
        "id": gold["id"],
        "hop_type": gold["hop_type"],
        "recall@5": round(len(expected & set(top5)) / max(len(expected), 1), 4),
        "path_accuracy": round(len({c["id"] for c in ret["linked"]["concepts"]} & set(chain)) / max(len(chain), 1), 4),
        "cite_precision": round(len(answer_cites & gold_cites) / max(n_ans, 1), 4),
        "cite_recall": round(len(answer_cites & gold_cites) / max(n_must, 1), 4),
        # 定长精度：只看答案前 |金标| 条引用，不惩罚多引
        "precision_at_must": round(prec_at_must, 4),
        # 集合口径（多引时退化为 cite_recall，仅用于交叉校验，不作精度解读）
        "precision_at_must_set": round(len(answer_cites & gold_cites) / max(min(n_ans, n_must), 1), 4),
        # 超额引用：只统计多引的部分，和定长精度互补
        "over_citation_rate": round(max(n_ans - n_must, 0) / max(n_must, 1), 4),
        "n_cites": n_ans,
        "n_cites_raw": len(raw_cites),
        "n_must_cite": n_must,
        "grounded": float(answer_cites <= retrieved_cites) if answer_cites else 1.0,
        # grounded 拆分：repair 前/后 + 越界引用条数；A/B 无 repair 时 before == after
        "grounded_before_repair": float(raw_cites <= retrieved_cites) if raw_cites else 1.0,
        "grounded_after_repair": float(answer_cites <= retrieved_cites) if answer_cites else 1.0,
        "out_of_scope_cites_raw": len(raw_cites - retrieved_cites),
        "out_of_scope_cites_post": len(answer_cites - retrieved_cites),
        "gate_all_pass": float(all(c["ok"] for c in state.get("checks", []))) if state.get("checks") else None,
        "repaired": bool(state.get("repaired")),
        "refused": bool(state.get("refused")),
        "llm_calls": calls,
        "tokens": tokens,
        "latency_ms": round(latency_ms, 1),
        "citations": sorted(answer_cites),
        "citations_ordered": answer_list,
        "citations_raw": sorted(raw_cites),
    }


def agg(rows: list[dict], key: str) -> float:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(statistics.mean(vals), 4) if vals else 0.0


def summarize(rows: list[dict]) -> dict:
    gate_vals = [r["gate_all_pass"] for r in rows if r.get("gate_all_pass") is not None]
    return {
        "n": len(rows),
        "recall@5": agg(rows, "recall@5"),
        "path_accuracy": agg(rows, "path_accuracy"),
        "cite_precision": agg(rows, "cite_precision"),
        "cite_recall": agg(rows, "cite_recall"),
        "precision_at_must": agg(rows, "precision_at_must"),
        "over_citation_rate": agg(rows, "over_citation_rate"),
        "n_cites_mean": agg(rows, "n_cites"),
        "n_must_cite_mean": agg(rows, "n_must_cite"),
        "grounded": agg(rows, "grounded"),
        "grounded_before_repair": agg(rows, "grounded_before_repair"),
        "grounded_after_repair": agg(rows, "grounded_after_repair"),
        "out_of_scope_cites_raw_mean": agg(rows, "out_of_scope_cites_raw"),
        "out_of_scope_cites_post_mean": agg(rows, "out_of_scope_cites_post"),
        # A/B 档没有闸门，这一项应为 null，不能拿 0 冒充
        "gate_all_pass": round(statistics.mean(gate_vals), 4) if gate_vals else None,
        "repaired_rate": round(sum(1 for r in rows if r["repaired"]) / len(rows), 4),
        "refused_rate": round(sum(1 for r in rows if r["refused"]) / len(rows), 4),
        "llm_calls_mean": agg(rows, "llm_calls"),
        "tokens_mean": int(agg(rows, "tokens")),
        "latency_median_ms": round(statistics.median([r["latency_ms"] for r in rows]), 1),
        "by_hop": {
            t: {
                "recall@5": agg([r for r in rows if r["hop_type"] == t], "recall@5"),
                "cite_precision": agg([r for r in rows if r["hop_type"] == t], "cite_precision"),
            }
            for t in ("two_hop", "three_hop", "cross_law")
        },
    }


def paired_bootstrap(ra: list[dict], rb: list[dict], key: str, n: int = BOOTSTRAP_N, seed: int = 20261029) -> dict:
    """同一批下标重采样两档，得到配对差值分布的 95% 区间。"""
    rng = random.Random(seed)
    diffs = []
    N = len(ra)
    for _ in range(n):
        idx = [rng.randrange(N) for _ in range(N)]
        va = statistics.mean(ra[i][key] for i in idx)
        vb = statistics.mean(rb[i][key] for i in idx)
        diffs.append(vb - va)
    diffs.sort()
    lo, hi = diffs[int(0.025 * n)], diffs[int(0.975 * n) - 1]
    return {
        "delta": round(statistics.mean(rb[i][key] - ra[i][key] for i in range(N)), 4),
        "ci95": [round(lo, 4), round(hi, 4)],
        "excludes_zero": bool(lo > 0 or hi < 0),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiers", nargs="+", default=list(TIERS))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-llm", action="store_true", help="确定性生成，用于离线复现（不计真实 token）")
    ap.add_argument("--mode", default="hybrid")
    ap.add_argument("--gold", default="data/gold/gold_60.jsonl", help="金标题库；留出集请指向 gold_60_holdout.jsonl")
    ap.add_argument("--out", default="data/eval_out/ablation.json", help="输出 JSON；留出集不要覆盖 in-sample 产物")
    args = ap.parse_args()

    gold = read_jsonl(args.gold)
    if args.limit:
        gold = gold[: args.limit]

    retriever = get_retriever()
    article_index = retriever.text.articles
    known_citations = {a["citation"] for a in article_index.values()}
    art_concepts = build_article_concepts(retriever.graph.store)
    results: dict[str, list[dict]] = {}
    summaries: dict[str, dict] = {}

    for tier in args.tiers:
        flow = make_flow(retriever, article_index, tier, use_llm=False if args.no_llm else None, art_concepts=art_concepts)
        rows = []
        for q in gold:
            before = USAGE.snapshot()
            t0 = time.perf_counter()
            state = flow(q["question"], mode=args.mode)
            latency = (time.perf_counter() - t0) * 1000
            after = USAGE.snapshot()
            calls = after["calls"] - before["calls"]
            tokens = (after["total_tokens"] - before["total_tokens"])
            rows.append(metrics_of(state, q, latency, calls, tokens, known_citations))
            print(f"{tier} {q['id']} recall={rows[-1]['recall@5']:.2f} cite_p={rows[-1]['cite_precision']:.2f} "
                  f"calls={calls} {latency:.0f}ms", flush=True)
        results[tier] = rows
        summaries[tier] = summarize(rows)
        log.info(
            "%s 档完成 recall@5=%.3f cite_p=%.3f grounded=%.3f calls=%.2f tokens=%d median=%.0fms",
            tier,
            summaries[tier]["recall@5"],
            summaries[tier]["cite_precision"],
            summaries[tier]["grounded"],
            summaries[tier]["llm_calls_mean"],
            summaries[tier]["tokens_mean"],
            summaries[tier]["latency_median_ms"],
        )

    # ---------------- 配对 bootstrap ----------------
    boot: dict[str, dict] = {}
    for key in ("recall@5", "cite_precision", "cite_recall", "grounded", "llm_calls", "latency_ms"):
        for a, b in (("A", "B"), ("B", "C"), ("A", "C"), ("A", "A_gates"), ("C", "A_gates"), ("B", "A_gates")):
            if a in results and b in results and len(results[a]) == len(results[b]):
                boot[f"{b}-{a}:{key}"] = paired_bootstrap(results[a], results[b], key)

    # ---------------- 成本表 ----------------
    base = summaries.get("A") or next(iter(summaries.values()))
    cost_table = {
        tier: {
            "llm_calls_per_query": round(s["llm_calls_mean"], 2),
            "tokens_per_query": s["tokens_mean"],
            "latency_median_ms": s["latency_median_ms"],
            "latency_x_vs_A": round(s["latency_median_ms"] / max(base["latency_median_ms"], 1e-9), 2),
            "gate_all_pass_rate": s["gate_all_pass"],
            "repaired_rate": s["repaired_rate"],
        }
        for tier, s in summaries.items()
    }

    report = {
        "llm_mode": "deterministic" if args.no_llm else "llm",
        "retrieval_mode": args.mode,
        "graph_build_id": _graph_build_id(),
        "gold_path": args.gold,
        "split_role": _split_role(args.gold),
        "n": len(gold),
        "bootstrap_n": BOOTSTRAP_N,
        "tiers": summaries,
        "cost_table": cost_table,
        "bootstrap": boot,
        "rows": results,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, report)
    log.info("写出 %s", out)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "by_hop"} for k, v in summaries.items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
