"""T11（n 任务）图路多跳评测：PPR / 2-hop + 排除推断边对照。

用法：
    # 只算，不写 retrieval.json（结果落到 --raw）
    python scripts/eval_multihop.py --raw data/eval_out/retrieval_multihop_raw.json

    # 追加两档到 data/eval_out/retrieval.json（先备份；不覆盖已有 key）
    python scripts/eval_multihop.py --append

口径与 ``scripts/eval_retrieval.py`` 一致：60 条 gold_60，``recall@5`` 取金标必需条文的覆盖率，
``path_recall`` 取实体链接概念占金标概念链比例，延迟为单题端到端（含实体链接）。
额外输出：每档边使用量、参数、bootstrap 95% CI、以及「排除 194 条 inferred 边」对照。

边界：本脚本只读图与金标，不重建图、不改 lexicon、不跑 LLM；全程 CPU（调用方需设
``EMBEDDING_DEVICE=cpu``，避免与另一个抢 GPU 的会话冲突）。
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

from app.__004__retrieval.__001__entity_linker import EntityLinker  # noqa: E402
from app.__004__retrieval.__002__graph_retrieval import GraphRetriever  # noqa: E402
from app.__004__retrieval.graph_2hop import TwoHopRetriever  # noqa: E402
from app.__004__retrieval.graph_ppr import PPRRetriever  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from common.path_utils import read_jsonl, write_json  # noqa: E402

log = get_logger("eval_multihop")

HOP_TYPES = ("two_hop", "three_hop", "cross_law")


def bootstrap_ci(values: list[float], n_boot: int = 1000, seed: int = 42) -> tuple[float, float]:
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(n_boot):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.mean(sample))
    means.sort()
    lo = means[int(0.025 * n_boot)]
    hi = means[int(0.975 * n_boot) - 1]
    return round(lo, 4), round(hi, 4)


def evaluate(runner, gold: list[dict], label: str, diagnostics: bool) -> dict:
    rows: list[dict] = []
    edge_totals: dict[str, int] = {}
    for q in gold:
        expected = set(q["expected_articles"])
        chain = [c["id"] for c in q["concepts"]]
        if diagnostics:
            out, diag = runner(q["question"])  # type: ignore[misc]
            latency = diag["latency_ms"]
            for k, v in diag.get("edges", {}).items():
                edge_totals[k] = edge_totals.get(k, 0) + int(v)
        else:
            t0 = time.perf_counter()
            out = runner(q["question"])
            latency = (time.perf_counter() - t0) * 1000
        top5 = [h.article_id for h in out.hits[:5]]
        recall5 = len(expected & set(top5)) / max(len(expected), 1)
        all5 = float(expected <= set(top5))
        linked = {c.id for c in out.linking.concepts}
        path_recall = len(linked & set(chain)) / max(len(chain), 1)
        rows.append(
            {
                "id": q["id"],
                "hop_type": q["hop_type"],
                "recall@5": round(recall5, 4),
                "all@5": all5,
                "path_recall": round(path_recall, 4),
                "latency_ms": round(latency, 1),
                "predicted": top5,
            }
        )

    def agg(key: str, subset: list[dict] | None = None) -> float:
        vs = [r[key] for r in (subset or rows)]
        return round(statistics.mean(vs), 4) if vs else 0.0

    ci_lo, ci_hi = bootstrap_ci([r["recall@5"] for r in rows])
    res = {
        "mode": label,
        "level_override": "custom",
        "n": len(rows),
        "recall@5": agg("recall@5"),
        "recall@5_ci95": [ci_lo, ci_hi],
        "all@5": agg("all@5"),
        "path_recall": agg("path_recall"),
        "latency_median_ms": round(statistics.median([r["latency_ms"] for r in rows]), 1),
        "latency_p90_ms": round(sorted(r["latency_ms"] for r in rows)[int(0.9 * len(rows)) - 1], 1),
        "by_hop": {
            t: {
                "n": sum(1 for r in rows if r["hop_type"] == t),
                "recall@5": agg("recall@5", [r for r in rows if r["hop_type"] == t]),
                "all@5": agg("all@5", [r for r in rows if r["hop_type"] == t]),
                "path_recall": agg("path_recall", [r for r in rows if r["hop_type"] == t]),
            }
            for t in HOP_TYPES
        },
        "edges_used": {
            "per_query_avg": {k: round(v / len(rows), 1) for k, v in edge_totals.items()},
            "total_60_queries": edge_totals,
        },
        "rows": rows,
    }
    log.info(
        "%s recall@5=%.4f CI=%s median=%.1fms edges=%s",
        label, res["recall@5"], res["recall@5_ci95"], res["latency_median_ms"], res["edges_used"]["per_query_avg"],
    )
    return res


def params_of(name: str) -> dict:
    if name == "graph_ppr":
        return {
            "algorithm": "personalised_pagerank",
            "damping": PPRRetriever.DAMPING,
            "max_iter": PPRRetriever.MAX_ITER,
            "tol": PPRRetriever.TOL,
            "defines_edge_weight": PPRRetriever.DEFINES_WEIGHT,
            "refers_to_edge_weight": PPRRetriever.REFERS_TO_WEIGHT,
            "concept_idf": PPRRetriever.USE_IDF,
            "canon_boost": PPRRetriever.CANON_BOOST,
            "walked_relations": ["REQUIRES", "SPECIAL_OF"],
            "projection_relations": ["DEFINES", "REFERS_TO"],
        }
    return {
        "algorithm": "2hop_pruning_rrf",
        "max_hops": TwoHopRetriever.MAX_HOPS,
        "w_requires": TwoHopRetriever.W_REQUIRES,
        "w_special_of": TwoHopRetriever.W_SPECIAL_OF,
        "w_reverse": TwoHopRetriever.W_REVERSE,
        "max_frontier_per_hop": TwoHopRetriever.MAX_FRONTIER,
        "max_project_concepts": TwoHopRetriever.MAX_PROJECT_CONCEPTS,
        "candidates_per_concept": TwoHopRetriever.CANDIDATES_PER_CONCEPT,
        "rrf_k": TwoHopRetriever.RRF_K,
        "coverage_boost": TwoHopRetriever.COVERAGE_BOOST,
        "concept_idf": TwoHopRetriever.USE_IDF,
        "walked_relations": ["REQUIRES", "SPECIAL_OF"],
        "projection_relations": ["DEFINES", "REFERS_TO"],
        "pruning_rule": "硬性 2 跳；每跳前沿按权重截断 60；投影每概念只取规范度+边分前 20 条",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="data/gold/gold_60.jsonl")
    ap.add_argument("--raw", default="data/eval_out/retrieval_multihop_raw.json")
    ap.add_argument("--append", action="store_true", help="把 graph_ppr / graph_2hop 两档追加进 retrieval.json")
    ap.add_argument("--retrieval", default="data/eval_out/retrieval.json")
    ap.add_argument("--backup-dir", default="data/eval_out/_backup_n_20261031")
    ap.add_argument("--with-baseline", action="store_true", help="同进程复算 graph 基线（仅用于延迟对照）")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    from config import settings

    settings.llm_api_key = ""  # 不触发任何 LLM

    gold = read_jsonl(args.gold)
    if args.limit:
        gold = gold[: args.limit]

    linker = EntityLinker()
    ppr = PPRRetriever(linker=linker)
    h2 = TwoHopRetriever(linker=linker)

    def ppr_runner(q: str):
        return ppr.retrieve(q, top_k=5, keep_inferred=True, diagnostics=True)

    def ppr_noinf_runner(q: str):
        return ppr.retrieve(q, top_k=5, keep_inferred=False, diagnostics=True)

    def h2_runner(q: str):
        return h2.retrieve(q, top_k=5, keep_inferred=True, diagnostics=True)

    def h2_noinf_runner(q: str):
        return h2.retrieve(q, top_k=5, keep_inferred=False, diagnostics=True)

    # 预热：把模型/图索引/PPR 图构建成本排除在单题延迟之外
    for fn in (ppr_runner, ppr_noinf_runner, h2_runner, h2_noinf_runner):
        fn(gold[0]["question"])

    output: dict = {
        "gold": args.gold,
        "device": "cpu",
        "baseline_reference": {
            "recall@5": 0.4389,
            "latency_median_ms": 301.5,
            "source": "data/eval_out/retrieval_post_m_rebuild.json（60 条，networkx 离线镜像）",
        },
        "runs": {},
    }

    main_ppr = evaluate(ppr_runner, gold, "graph_ppr", diagnostics=True)
    main_ppr["params"] = params_of("graph_ppr")
    main_h2 = evaluate(h2_runner, gold, "graph_2hop", diagnostics=True)
    main_h2["params"] = params_of("graph_2hop")

    ctrl_ppr = evaluate(ppr_noinf_runner, gold, "graph_ppr_no_inferred", diagnostics=True)
    ctrl_ppr["params"] = {**params_of("graph_ppr"), "keep_inferred": False}
    ctrl_h2 = evaluate(h2_noinf_runner, gold, "graph_2hop_no_inferred", diagnostics=True)
    ctrl_h2["params"] = {**params_of("graph_2hop"), "keep_inferred": False}

    if args.with_baseline:
        base = GraphRetriever(linker=linker)
        baseline = evaluate(
            lambda q: base.retrieve(q, top_k=5, hops=2, use_cites=True),
            gold, "graph_baseline_recheck", diagnostics=False,
        )
        output["baseline_recheck"] = baseline

    main_ppr["no_inferred_control"] = {
        "recall@5": ctrl_ppr["recall@5"],
        "recall@5_ci95": ctrl_ppr["recall@5_ci95"],
        "latency_median_ms": ctrl_ppr["latency_median_ms"],
        "by_hop": ctrl_ppr["by_hop"],
        "edges_used": ctrl_ppr["edges_used"],
    }
    main_h2["no_inferred_control"] = {
        "recall@5": ctrl_h2["recall@5"],
        "recall@5_ci95": ctrl_h2["recall@5_ci95"],
        "latency_median_ms": ctrl_h2["latency_median_ms"],
        "by_hop": ctrl_h2["by_hop"],
        "edges_used": ctrl_h2["edges_used"],
    }

    output["runs"]["graph_ppr"] = {
        "summary": {k: main_ppr[k] for k in
                    ("recall@5", "recall@5_ci95", "all@5", "path_recall", "latency_median_ms", "latency_p90_ms", "by_hop", "edges_used", "params")},
        "no_inferred_control": main_ppr["no_inferred_control"],
    }
    output["runs"]["graph_2hop"] = {
        "summary": {k: main_h2[k] for k in
                    ("recall@5", "recall@5_ci95", "all@5", "path_recall", "latency_median_ms", "latency_p90_ms", "by_hop", "edges_used", "params")},
        "no_inferred_control": main_h2["no_inferred_control"],
    }

    out_path = Path(args.raw)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_path, output)
    log.info("写出 %s", out_path)

    if args.append:
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = Path(args.backup_dir) / f"retrieval.{ts}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        original = Path(args.retrieval).read_bytes()
        backup.write_bytes(original)
        payload = json.loads(original.decode("utf-8"))
        results = payload.setdefault("results", {})
        for key, res in (("graph_ppr", main_ppr), ("graph_2hop", main_h2)):
            if key in results:
                log.warning("覆盖已有 key %s（原值先备份到 %s）", key, backup)
            results[key] = res
        write_json(args.retrieval, payload)
        log.info("已追加 graph_ppr / graph_2hop 到 %s（备份 %s）", args.retrieval, backup)


if __name__ == "__main__":
    main()
