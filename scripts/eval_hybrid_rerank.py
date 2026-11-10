"""T12（o 任务）hybrid 融合 + reranker 2x2 对照。

口径：60 条 ``data/gold/gold_60.jsonl``，图路输入固定为 n 任务的
``TwoHopRetriever``（R@5 0.6111），文本路为 ``TextIndex``（dense+BM25 RRF）。

四格 = 融合方式（RRF / 加权）× 是否过 reranker。每格报：
``recall@5`` / 95% CI（配对 bootstrap 1000 次，seed 落盘）/ 延迟 median+p90 /
「rerank 前后 top-5 变化条数」。

另外单列上限对照（全部标注为 oracle，不是实测检索）：
1. 融合候选池的 top-5 覆盖上限（池内最优选 5 条能拿到的 recall）；
2. 文本 top5 ∪ 图 top5 的上限；
3. 用金标概念直接作 2-hop 种子的图路上限，以及它与文本路做同等 RRF 融合后的上限。

边界：不改 ``__005__retriever.py``、不改金标、不重建图、不改 ``data/lexicon/``、
不跑 LLM；全程 CPU（``EMBEDDING_DEVICE=cpu``、``GRAPH_BACKEND=networkx``）。

用法：
    # 只算，不写 retrieval.json
    python scripts/eval_hybrid_rerank.py --raw data/eval_out/hybrid_rerank_raw.json
    # 先跑 3 条自检
    python scripts/eval_hybrid_rerank.py --limit 3
    # 追加 results.hybrid_rerank（先备份原文件）
    python scripts/eval_hybrid_rerank.py --append
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from app.__004__retrieval.__000__text_index import TextIndex, rrf_fuse  # noqa: E402
from app.__004__retrieval.__001__entity_linker import EntityLinker  # noqa: E402
from app.__004__retrieval.graph_2hop import TwoHopRetriever  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from common.path_utils import read_jsonl, write_json  # noqa: E402

log = get_logger("eval_hybrid_rerank")

HOP_TYPES = ("two_hop", "three_hop", "cross_law")

# 与生产 hybrid 对齐的候选规模；权重取 LEVELS["medium"]，评测时固定档位，避免路由漂移。
TEXT_CAND = 30
GRAPH_CAND = 10
RERANK_CAND = 25
RRF_K = 60
W_TEXT = 0.55
W_GRAPH = 0.45
TOP_K = 5
BOOT_N = 1000
BOOT_SEED = 20261031
TARGET_RECALL = 0.78


# ---------------------------------------------------------------------------
# 统计工具
# ---------------------------------------------------------------------------
def bootstrap_ci(values: list[float], n_boot: int = BOOT_N, seed: int = BOOT_SEED) -> list[float]:
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(n_boot):
        means.append(statistics.mean(values[rng.randrange(n)] for _ in range(n)))
    means.sort()
    return [round(means[int(0.025 * n_boot)], 4), round(means[int(0.975 * n_boot) - 1], 4)]


def paired_bootstrap_delta(a: list[float], b: list[float], n_boot: int = BOOT_N, seed: int = BOOT_SEED) -> dict:
    """配对 bootstrap：同一组重采样下标同时作用于两个系统，输出 mean(a-b) 的 95% CI。"""
    rng = random.Random(seed)
    n = len(a)
    deltas: list[float] = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        deltas.append(statistics.mean(a[i] - b[i] for i in idx))
    deltas.sort()
    lo = round(deltas[int(0.025 * n_boot)], 4)
    hi = round(deltas[int(0.975 * n_boot) - 1], 4)
    return {
        "delta": round(statistics.mean(x - y for x, y in zip(a, b)), 4),
        "ci95": [lo, hi],
        "excludes_zero": bool(lo > 0 or hi < 0),
    }


def minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


# ---------------------------------------------------------------------------
# 融合
# ---------------------------------------------------------------------------
def fuse_rrf(text_ids: list[str], graph_ids: list[str]) -> list[str]:
    fused = rrf_fuse([text_ids, graph_ids], k=RRF_K, weights=[W_TEXT, W_GRAPH])
    return [a for a, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))][:RERANK_CAND]


def fuse_weighted(text_rows: list[tuple[str, float]], graph_rows: list[tuple[str, float]]) -> list[str]:
    """两路各自 min-max 归一化后加权求和；缺项记 0 分。"""
    nt = dict(zip([a for a, _ in text_rows], minmax([s for _, s in text_rows])))
    ng = dict(zip([a for a, _ in graph_rows], minmax([s for _, s in graph_rows])))
    fused: dict[str, float] = {}
    for aid in set(nt) | set(ng):
        fused[aid] = W_TEXT * nt.get(aid, 0.0) + W_GRAPH * ng.get(aid, 0.0)
    return [a for a, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))][:RERANK_CAND]


# ---------------------------------------------------------------------------
# 评测
# ---------------------------------------------------------------------------
def per_query_recall(top5: list[str], expected: set[str]) -> tuple[float, float]:
    recall = len(expected & set(top5)) / max(len(expected), 1)
    return round(recall, 4), float(expected <= set(top5))


def aggregate(
    name: str,
    fusion: str,
    rerank: bool,
    rows: list[dict],
    change_vs: str | None = None,
    base_rows: list[dict] | None = None,
) -> dict:
    recalls = [r["recall@5"] for r in rows]
    lat = [r["latency_ms"] for r in rows]
    lo, hi = bootstrap_ci(recalls)

    def agg(key: str, subset: list[dict] | None = None) -> float:
        vs = [r[key] for r in (subset or rows)]
        return round(statistics.mean(vs), 4) if vs else 0.0

    out = {
        "mode": name,
        "fusion": fusion,
        "rerank": rerank,
        "level_override": "medium(fixed)",
        "n": len(rows),
        "recall@5": round(statistics.mean(recalls), 4),
        "recall@5_ci95": [lo, hi],
        "all@5": agg("all@5"),
        "latency_median_ms": round(statistics.median(lat), 1),
        "latency_p90_ms": round(sorted(lat)[int(0.9 * len(lat)) - 1], 1),
        "latency_mean_ms": round(statistics.mean(lat), 1),
        "latency_components_median_ms": {
            k: round(statistics.median([r["t_" + k] for r in rows]), 1)
            for k in ("text", "graph", "fuse", "rerank")
            if all(("t_" + k) in r for r in rows)
        },
        "by_hop": {
            t: {
                "n": sum(1 for r in rows if r["hop_type"] == t),
                "recall@5": agg("recall@5", [r for r in rows if r["hop_type"] == t]),
                "all@5": agg("all@5", [r for r in rows if r["hop_type"] == t]),
            }
            for t in HOP_TYPES
        },
        "rows": rows,
    }
    if change_vs is not None and base_rows is not None:
        base = {r["id"]: r for r in base_rows}
        changed_q = 0
        changed_pos = 0
        jacc = []
        for r in rows:
            b = base[r["id"]]["predicted"]
            a = r["predicted"]
            if set(a) != set(b):
                changed_q += 1
            n = min(len(a), len(b))
            changed_pos += sum(1 for i in range(n) if a[i] != b[i]) + abs(len(a) - len(b))
            union = set(a) | set(b)
            jacc.append(len(set(a) & set(b)) / len(union) if union else 1.0)
        out["top5_change_vs"] = change_vs
        out["top5_changed_queries"] = changed_q
        out["top5_changed_positions_total"] = changed_pos
        out["top5_mean_jaccard"] = round(statistics.mean(jacc), 4)
    return out


def run_cells(
    gold: list[dict],
    text: TextIndex,
    h2: TwoHopRetriever,
    rerank_cells: bool,
) -> tuple[dict[str, dict], dict]:
    """按题循环：文本/图路各测一次，四格共用同一组检索组件耗时，融合与精排各格单独计时。"""
    cell_rows: dict[str, list[dict]] = {k: [] for k in ("rrf_norerank", "rrf_rerank", "weighted_norerank", "weighted_rerank")}
    oracle_rows: list[dict] = []

    for q in gold:
        expected = set(q["expected_articles"])
        chain = [c["id"] for c in q["concepts"]]
        question = q["question"]

        # --- 文本路 ---
        t0 = time.perf_counter()
        text_res = text.search(question, top_k=TEXT_CAND, use_rerank=False)
        t_text = (time.perf_counter() - t0) * 1000
        text_rows = [(r.article_id, float(r.score)) for r in text_res]
        text_ids = [a for a, _ in text_rows]

        # --- 图路（2-hop，n 任务输入） ---
        t0 = time.perf_counter()
        graph_res = h2.retrieve(question, top_k=GRAPH_CAND, keep_inferred=True)
        t_graph = (time.perf_counter() - t0) * 1000
        graph_rows = [(h.article_id, float(h.score)) for h in graph_res.hits]
        graph_ids = [a for a, _ in graph_rows]
        linked = {c.id for c in graph_res.linking.concepts}
        path_recall = len(linked & set(chain)) / max(len(chain), 1)

        # --- 两种融合 ---
        t0 = time.perf_counter()
        pools = {"rrf": fuse_rrf(text_ids, graph_ids), "weighted": fuse_weighted(text_rows, graph_rows)}
        t_fuse = (time.perf_counter() - t0) * 1000

        # --- 精排前 ---
        for fusion in ("rrf", "weighted"):
            top5 = pools[fusion][:TOP_K]
            rec, all5 = per_query_recall(top5, expected)
            cell_rows[f"{fusion}_norerank"].append(
                {
                    "id": q["id"],
                    "hop_type": q["hop_type"],
                    "recall@5": rec,
                    "all@5": all5,
                    "path_recall": round(path_recall, 4),
                    "latency_ms": round(t_text + t_graph + t_fuse, 1),
                    "t_text": round(t_text, 1),
                    "t_graph": round(t_graph, 1),
                    "t_fuse": round(t_fuse, 1),
                    "pool_size": len(pools[fusion]),
                    "predicted": top5,
                }
            )

        # --- 精排 ---
        if rerank_cells:
            for fusion in ("rrf", "weighted"):
                pool = pools[fusion]
                docs = [text.articles[a]["content"] for a in pool]
                t0 = time.perf_counter()
                scores = text.reranker.score(question, docs)
                t_rerank = (time.perf_counter() - t0) * 1000
                ranked = sorted(zip(pool, scores), key=lambda kv: (-kv[1], kv[0]))
                top5 = [a for a, _ in ranked[:TOP_K]]
                rec, all5 = per_query_recall(top5, expected)
                cell_rows[f"{fusion}_rerank"].append(
                    {
                        "id": q["id"],
                        "hop_type": q["hop_type"],
                        "recall@5": rec,
                        "all@5": all5,
                        "path_recall": round(path_recall, 4),
                        "latency_ms": round(t_text + t_graph + t_fuse + t_rerank, 1),
                        "t_text": round(t_text, 1),
                        "t_graph": round(t_graph, 1),
                        "t_fuse": round(t_fuse, 1),
                        "t_rerank": round(t_rerank, 1),
                        "pool_size": len(pool),
                        "predicted": top5,
                        "rerank_scores": [round(float(s), 4) for _, s in ranked[:TOP_K]],
                    }
                )

        # --- oracle：金标概念作种子 ---
        gold_seeds = {c["id"]: 1.0 for c in q["concepts"] if c["id"] in h2.g}
        oracle_rows.append(_oracle_row(q, expected, gold_seeds, text_ids, graph_ids, h2))

    cells = {
        "rrf_norerank": aggregate("rrf_norerank", "rrf", False, cell_rows["rrf_norerank"]),
        "weighted_norerank": aggregate("weighted_norerank", "weighted", False, cell_rows["weighted_norerank"]),
    }
    if rerank_cells:
        cells["rrf_rerank"] = aggregate(
            "rrf_rerank", "rrf", True, cell_rows["rrf_rerank"],
            change_vs="rrf_norerank", base_rows=cell_rows["rrf_norerank"],
        )
        cells["weighted_rerank"] = aggregate(
            "weighted_rerank", "weighted", True, cell_rows["weighted_rerank"],
            change_vs="weighted_norerank", base_rows=cell_rows["weighted_norerank"],
        )

    oracles = _oracle_summary(oracle_rows)
    return cells, oracles


def _oracle_row(
    q: dict,
    expected: set[str],
    gold_seeds: dict[str, float],
    text_ids: list[str],
    graph_ids: list[str],
    h2: TwoHopRetriever,
) -> dict:
    # 金标概念作种子的图路
    expanded, _ = h2._expand(gold_seeds, keep_inferred=True)
    hits, _ = h2._rank(expanded, set(gold_seeds), keep_inferred=True)
    oracle_graph_ids = [h.article_id for h in hits[:GRAPH_CAND]]
    rec_graph = len(expected & set(oracle_graph_ids[:TOP_K])) / max(len(expected), 1)
    # 金标种子图路 + 文本 RRF（与正式格同参数）
    fused = rrf_fuse([text_ids, oracle_graph_ids], k=RRF_K, weights=[W_TEXT, W_GRAPH])
    oracle_hybrid_ids = [a for a, _ in sorted(fused.items(), key=lambda kv: (-kv[1], kv[0]))][:TOP_K]
    rec_hybrid = len(expected & set(oracle_hybrid_ids)) / max(len(expected), 1)
    # 文本 top5 ∪ 图 top5 的上限
    rec_union = min(1.0, len(expected & (set(text_ids[:TOP_K]) | set(graph_ids[:TOP_K]))) / max(len(expected), 1))
    return {
        "id": q["id"],
        "hop_type": q["hop_type"],
        "oracle_graph_seed_recall@5": round(rec_graph, 4),
        "oracle_graph_seed_hybrid_recall@5": round(rec_hybrid, 4),
        "union_top5_recall@5": round(rec_union, 4),
    }


def _oracle_summary(rows: list[dict]) -> dict:
    def mean(key: str) -> float:
        return round(statistics.mean(r[key] for r in rows), 4)

    return {
        "note": "全部为 oracle（用金标概念作种子），不是可部署的实测结果",
        "graph_gold_seed_recall@5": mean("oracle_graph_seed_recall@5"),
        "graph_gold_seed_hybrid_recall@5": mean("oracle_graph_seed_hybrid_recall@5"),
        "union_top5_recall@5": mean("union_top5_recall@5"),
        "by_hop": {
            t: {
                "n": sum(1 for r in rows if r["hop_type"] == t),
                "graph_gold_seed_recall@5": round(statistics.mean([r["oracle_graph_seed_recall@5"] for r in rows if r["hop_type"] == t]), 4)
                if any(r["hop_type"] == t for r in rows) else None,
                "graph_gold_seed_hybrid_recall@5": round(statistics.mean([r["oracle_graph_seed_hybrid_recall@5"] for r in rows if r["hop_type"] == t]), 4)
                if any(r["hop_type"] == t for r in rows) else None,
            }
            for t in HOP_TYPES
        },
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="data/gold/gold_60.jsonl")
    ap.add_argument("--raw", default="data/eval_out/hybrid_rerank_raw.json")
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--retrieval", default="data/eval_out/retrieval.json")
    ap.add_argument("--backup-dir", default="data/eval_out/_backup_o_20261031")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--skip-rerank", action="store_true", help="只跑两个 no-rerank 格（自检用）")
    args = ap.parse_args()

    from config import settings

    settings.llm_api_key = ""  # 不触发任何 LLM

    gold = read_jsonl(args.gold)
    if args.limit:
        gold = gold[: args.limit]

    linker = EntityLinker()
    text = TextIndex().load()
    h2 = TwoHopRetriever(linker=linker)

    # 预热：模型加载、图索引、rerank 首包成本不计入单题延迟
    warm = gold[0]["question"]
    text.search(warm, top_k=TEXT_CAND, use_rerank=False)
    h2.retrieve(warm, top_k=GRAPH_CAND, keep_inferred=True)
    if not args.skip_rerank:
        text.reranker.score(warm, [text.articles[h.article_id]["content"] for h in h2.retrieve(warm, top_k=5, keep_inferred=True).hits])

    t_start = time.perf_counter()
    cells, oracles = run_cells(gold, text, h2, rerank_cells=not args.skip_rerank)
    elapsed = round(time.perf_counter() - t_start, 1)

    # 文本基线：沿用 retrieval.json 里官方 text 档的逐题结果（router 档位），用于配对对照
    official = json.loads(Path(args.retrieval).read_text(encoding="utf-8"))["results"]
    text_rows = {r["id"]: r for r in official["text"]["rows"]}
    text_recalls = [text_rows[q["id"]]["recall@5"] for q in gold]

    deltas: dict[str, dict] = {}
    for name, cell in cells.items():
        deltas[f"{name}_minus_text"] = paired_bootstrap_delta(
            [r["recall@5"] for r in cell["rows"]], text_recalls
        )
        deltas[f"{name}_minus_text"]["baseline"] = "retrieval.json#results.text（router 档位）"
    if not args.skip_rerank:
        for fusion in ("rrf", "weighted"):
            deltas[f"{fusion}_rerank_minus_norerank"] = paired_bootstrap_delta(
                [r["recall@5"] for r in cells[f"{fusion}_rerank"]["rows"]],
                [r["recall@5"] for r in cells[f"{fusion}_norerank"]["rows"]],
            )
        for band in ("norerank", "rerank"):
            top = cells[f"weighted_{band}"]["rows"]
            other = cells[f"rrf_{band}"]["rows"]
            deltas[f"weighted_{band}_minus_rrf_{band}"] = paired_bootstrap_delta(
                [r["recall@5"] for r in top], [r["recall@5"] for r in other]
            )

    best_name = max(cells, key=lambda k: cells[k]["recall@5"])
    best = cells[best_name]["recall@5"]

    payload = {
        "mode": "hybrid_rerank",
        "level_override": "medium(fixed)",
        "gold": args.gold,
        "device": "cpu",
        "n": len(gold),
        "bootstrap": {"n_boot": BOOT_N, "seed": BOOT_SEED, "paired": True},
        "target": {
            "recall@5": TARGET_RECALL,
            "reached": bool(best >= TARGET_RECALL),
            "best_cell": best_name,
            "best_recall@5": best,
            "gap_pp": round((best - TARGET_RECALL) * 100, 2),
        },
        "graph_input": {
            "path": "graph_2hop",
            "reference_recall@5": official["graph_2hop"]["recall@5"],
            "reference_ci95": official["graph_2hop"]["recall@5_ci95"],
            "reference_latency_median_ms": official["graph_2hop"]["latency_median_ms"],
        },
        "text_baseline": {
            "source": "retrieval.json#results.text（router 档位）",
            "recall@5": official["text"]["recall@5"],
            "latency_median_ms": official["text"]["latency_median_ms"],
        },
        "params": {
            "text_candidates": TEXT_CAND,
            "graph_candidates": GRAPH_CAND,
            "rerank_candidates": RERANK_CAND,
            "rrf_k": RRF_K,
            "weights": {"text": W_TEXT, "graph": W_GRAPH},
            "reranker": settings.rerank_model_name,
            "reranker_max_length": 512,
            "fusion_weighted": "两路各自 min-max 归一化后加权求和，缺项 0 分",
            "coverage_bonus": 0.0,
            "keep_inferred": True,
            "latency_note": "文本/图路组件每题各测一次（四格共用），融合与精排按格单独计时；不含模型加载与图构建",
        },
        "cells": cells,
        "deltas": deltas,
        "oracles": oracles,
        "elapsed_s": elapsed,
    }

    out_path = Path(args.raw)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(out_path, payload)
    log.info("写出 %s（耗时 %.0fs）", out_path, elapsed)
    for name in cells:
        c = cells[name]
        log.info("%s R@5=%.4f CI=%s median=%.0fms p90=%.0fms", name, c["recall@5"], c["recall@5_ci95"], c["latency_median_ms"], c["latency_p90_ms"])

    if args.append:
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = Path(args.backup_dir) / f"retrieval.{ts}.json"
        backup.parent.mkdir(parents=True, exist_ok=True)
        original = Path(args.retrieval).read_bytes()
        backup.write_bytes(original)
        doc = json.loads(original.decode("utf-8"))
        results = doc.setdefault("results", {})
        if "hybrid_rerank" in results:
            log.warning("覆盖已有 key hybrid_rerank（原值已备份到 %s）", backup)
        results["hybrid_rerank"] = payload
        write_json(args.retrieval, doc)
        log.info("已追加 results.hybrid_rerank 到 %s（备份 %s）", args.retrieval, backup)


if __name__ == "__main__":
    main()
