"""独立复算脚本（只读 data/eval_out 与 data/gold，不写仓库产物）。

复算内容：
1. retrieval_holdout_140(.no_rerank)：从 predicted × gold 重算 R@5 / all@5 / 分桶，
   并用原实现同款配对 bootstrap（seed 20261029, n=1000）复算 CI；另用 20 个种子看 CI 稳定性。
2. graph_contribution：从逐题 top5 × gold 重算 hit/recovered/failure/候选计数与判定一致性。
3. ablation_holdout_60（当前版与 pre_graphver 版）：从逐题 rows 重算 grounded / 调用 / token /
   延迟 / 修复 / 拒答，并复算 grounded 等配对 bootstrap。
"""

from __future__ import annotations

import json
import random
import statistics
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]  # <REPO_ROOT>
OUT = Path(__file__).resolve().parents[1] / "outputs"
WORK = Path(__file__).resolve().parent


def load(p: str):
    return json.loads((ROOT / p).read_text(encoding="utf-8"))


def gold_map(path: str):
    rows = [json.loads(line) for line in (ROOT / path).read_text(encoding="utf-8").splitlines() if line.strip()]
    return {r["id"]: r for r in rows}


def paired_bootstrap_rows(ra, rb, key, n=1000, seed=20261029):
    """完全照 scripts/eval_retrieval.py / eval_ablation.py 的实现复算（同下标重采样）。"""
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
    }


results = {}


# ---------------------------------------------------------------- 1. 检索留出集
def check_retrieval(path: str, label: str):
    d = load(path)
    gold = gold_map(d["gold_path"])
    rep = {"graph_build_id": d["graph_build_id"], "split_role": d["split_role"], "gold_n": len(gold)}
    hops = Counter(q["hop_type"] for q in gold.values())
    rep["gold_hops"] = dict(hops)
    for mode, res in d["results"].items():
        rows = res["rows"]
        ids = [r["id"] for r in rows]
        rep.setdefault("id_set_ok", {})[mode] = set(ids) == set(gold) and len(ids) == len(set(ids))
        mism = []
        for r in rows:
            g = set(gold[r["id"]]["expected_articles"])
            pred = r["predicted"][:5]
            recall = len(g & set(pred)) / max(len(g), 1)
            all5 = float(g <= set(pred))
            if abs(recall - r["recall@5"]) > 1e-4 or abs(all5 - r["all@5"]) > 1e-4:
                mism.append({"id": r["id"], "gold": sorted(g), "pred": pred,
                             "calc": [round(recall, 4), all5], "reported": [r["recall@5"], r["all@5"]]})
        rep.setdefault("row_mismatch", {})[mode] = mism
        rep.setdefault("recomputed", {})[mode] = {
            "n": len(rows),
            "recall@5": round(statistics.mean(r["recall@5"] for r in rows), 4),
            "all@5": round(statistics.mean(r["all@5"] for r in rows), 4),
            "latency_median_ms": round(statistics.median(r["latency_ms"] for r in rows), 1),
            "by_hop": {
                h: {
                    "n": sum(1 for r in rows if r["hop_type"] == h),
                    "recall@5": round(statistics.mean(r["recall@5"] for r in rows if r["hop_type"] == h), 4),
                    "all@5": round(statistics.mean(r["all@5"] for r in rows if r["hop_type"] == h), 4),
                }
                for h in ("two_hop", "three_hop", "cross_law")
            },
        }
        rep.setdefault("reported", {})[mode] = {
            "n": res["n"], "recall@5": res["recall@5"], "all@5": res["all@5"],
            "latency_median_ms": res["latency_median_ms"],
            "by_hop": {h: {"recall@5": v["recall@5"], "all@5": v["all@5"]} for h, v in res["by_hop"].items()},
        }
        # bootstrap 复算：metric 用逐题值
        for metric in ("recall@5", "all@5"):
            key = metric
            ra = d["results"]["text"]["rows"]
            rb = d["results"][mode]["rows"]
            if mode != "text":
                rep.setdefault("bootstrap_recompute", {})[metric + ":" + mode] = paired_bootstrap_rows(ra, rb, key)
        # 20 个独立种子看 CI 波动
        if mode != "text":
            for metric in ("recall@5", "all@5"):
                cis = []
                for s in range(1, 21):
                    b = paired_bootstrap_rows(d["results"]["text"]["rows"], d["results"][mode]["rows"], metric, seed=s)
                    cis.append(b["ci95"])
                rep.setdefault("bootstrap_seed_sweep", {})[metric + ":" + mode] = {
                    "min_lo": min(c[0] for c in cis), "max_lo": max(c[0] for c in cis),
                    "min_hi": min(c[1] for c in cis), "max_hi": max(c[1] for c in cis),
                }
    rep["reported_bootstrap"] = d["paired_bootstrap"]
    results[label] = rep


check_retrieval("data/eval_out/retrieval_holdout_140.json", "retrieval_holdout_140")
check_retrieval("data/eval_out/retrieval_holdout_140_no_rerank.json", "retrieval_holdout_140_no_rerank")


# ---------------------------------------------------------------- 2. 图路贡献
def check_graph_contribution():
    d = load("data/eval_out/graph_contribution.json")
    gold = gold_map(d["gold_path"])
    rows = d["rows"]
    rep = {"graph_build_id": d["versions"]["graph_build_id"], "split_role": d["split_role"], "n": d["n"],
           "id_set_ok": set(r["id"] for r in rows) == set(gold)}

    def hit(ids, gid):
        g = set(gold[gid]["expected_articles"])
        return g <= set(ids)

    calc = {"text_failed_n": 0, "recovered_n": 0, "graph_only_total": 0, "verdicts": Counter(),
            "necessary_in_hybrid_top5": 0, "necessary_only_in_graph_top5": 0,
            "in_text_candidates_true": 0}
    for r in rows:
        gid = r["id"]
        g = set(gold[gid]["expected_articles"])
        tf = not hit(r["text_top5"], gid)
        rec = tf and hit(r["hybrid_top5"], gid)
        calc["text_failed_n"] += tf
        calc["recovered_n"] += rec
        if tf != r["text_failed"] or rec != r["text_failed_graph_recovered"]:
            calc.setdefault("row_flag_mismatch", []).append(gid)
        for c in r["graph_only_candidates"]:
            calc["graph_only_total"] += 1
            calc["verdicts"][c["verdict"]] += 1
            if c["in_text_candidates"]:
                calc["in_text_candidates_true"] += 1
            if c["verdict"] == "necessary":
                if c["article_id"] in r["hybrid_top5"]:
                    calc["necessary_in_hybrid_top5"] += 1
                else:
                    calc["necessary_only_in_graph_top5"] += 1
    calc["verdicts"] = dict(calc["verdicts"])
    rep["recomputed"] = calc
    rep["reported"] = d["overall"]
    rep["recoveries_n"] = len(d["recoveries"])
    rep["recovery_detail"] = [
        {"id": x["id"], "missing_from_text": x["text_failed_on"],
         "paths": x["graph_paths"], "all_paths_nonempty": all(bool(v) for v in x["graph_paths"].values()),
         "recovered_in_hybrid": hit(x["hybrid_top5"], x["id"])}
        for x in d["recoveries"]
    ]
    rep["failures_n"] = len(d["failures"])
    rep["failure_reasons_recomputed"] = dict(Counter(
        ("entity_linking_failure" if r["entity_linking_failure"] else
         "graph_path_incomplete" if not r["path_ok"] else
         "low_confidence" if r["low_confidence"] else "ranking_miss")
        for r in rows if not hit(r["hybrid_top5"], r["id"])))
    # 判定一致性：necessary 必须 in_gold；noise 必须非金标
    bad_verdict = [c for r in rows for c in r["graph_only_candidates"]
                   if (c["verdict"] == "necessary" and not c["in_gold"])
                   or (c["verdict"] == "noise" and c["in_gold"])]
    rep["verdict_consistency_violations"] = len(bad_verdict)
    results["graph_contribution"] = rep


check_graph_contribution()


# ---------------------------------------------------------------- 3. 消融
def check_ablation(path: str, label: str):
    d = load(path)
    rep = {"graph_build_id": d.get("graph_build_id"), "split_role": d.get("split_role"),
           "gold_path": d.get("gold_path"), "bootstrap_n": d.get("bootstrap_n"), "tiers": {}}
    for tier, rows in d["rows"].items():
        n = len(rows)
        def mean_of(key, cast=float):
            vals = [r[key] for r in rows if key in r and r[key] is not None]
            return round(statistics.mean(cast(v) for v in vals), 4) if vals else None
        rep["tiers"][tier] = {
            "n": n,
            "recall@5": mean_of("recall@5"),
            "path_accuracy": mean_of("path_accuracy"),
            "cite_precision": mean_of("cite_precision"),
            "cite_recall": mean_of("cite_recall"),
            "precision_at_must": mean_of("precision_at_must"),
            "grounded": mean_of("grounded"),
            "grounded_before_repair": mean_of("grounded_before_repair"),
            "grounded_after_repair": mean_of("grounded_after_repair"),
            "gate_all_pass": mean_of("gate_all_pass"),
            "repaired_rate": mean_of("repaired", bool),
            "refused_rate": mean_of("refused", bool),
            "llm_calls_mean": mean_of("llm_calls"),
            "tokens_mean": round(statistics.mean(r["tokens"] for r in rows), 1),
            "latency_median_ms": round(statistics.median(r["latency_ms"] for r in rows), 1),
            "llm_calls_sum": sum(r["llm_calls"] for r in rows),
            "tokens_sum": sum(r["tokens"] for r in rows),
            "out_of_scope_raw_sum": sum(r.get("out_of_scope_cites_raw", 0) for r in rows),
            "out_of_scope_post_sum": sum(r.get("out_of_scope_cites_post", 0) for r in rows),
            "ids_sha_head": sorted(r["id"] for r in rows)[:3],
        }
    rep["reported_tiers"] = d["tiers"]
    rep["reported_cost_table"] = d["cost_table"]
    # 复算 bootstrap（grounded / cite_precision / llm_calls / latency_ms）
    rep["bootstrap_recompute"] = {}
    tiers = list(d["rows"])
    for i, a in enumerate(tiers):
        for b in tiers[i + 1:]:
            ra, rb = d["rows"][a], d["rows"][b]
            ia = {r["id"]: r for r in ra}
            ib = {r["id"]: r for r in rb}
            ids = sorted(set(ia) & set(ib))
            A = [ia[k] for k in ids]
            B = [ib[k] for k in ids]
            for key in ("grounded", "cite_precision", "llm_calls", "latency_ms"):
                rep["bootstrap_recompute"][f"{b}-{a}:{key}"] = paired_bootstrap_rows(A, B, key, n=d.get("bootstrap_n", 100), seed=20261029)
    rep["reported_bootstrap"] = d["bootstrap"]
    # 同一批题、检索同源核对
    ids0 = {t: set(r["id"] for r in d["rows"][t]) for t in d["rows"]}
    rep["same_question_sets"] = all(v == ids0[tiers[0]] for v in ids0.values())
    base_recall = {r["id"]: r["recall@5"] for r in d["rows"][tiers[0]]}
    rep["same_recall_across_tiers"] = all(
        {r["id"]: r["recall@5"] for r in d["rows"][t]} == base_recall for t in tiers)
    results[label] = rep


check_ablation("data/eval_out/ablation_holdout_60.json", "ablation_holdout_60")
check_ablation("data/eval_out/ablation_holdout_60_pre_graphver.json", "ablation_holdout_60_pre_graphver")
check_ablation("data/eval_out/ablation.json", "ablation_dev_20261104")

(WORK / "verify_metrics_result.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")

# 打印关键摘要
print("=== retrieval_holdout_140 ===")
r = results["retrieval_holdout_140"]
print("id_set_ok", r["id_set_ok"], "gold_hops", r["gold_hops"])
for m in r["recomputed"]:
    print(m, r["recomputed"][m]["recall@5"], r["recomputed"][m]["all@5"],
          "row_mismatch", len(r["row_mismatch"][m]))
print("bootstrap recompute", json.dumps(r["bootstrap_recompute"], ensure_ascii=False))
print("bootstrap reported", json.dumps(r["reported_bootstrap"], ensure_ascii=False))
print("seed sweep", json.dumps(r["bootstrap_seed_sweep"], ensure_ascii=False))
print("=== no_rerank ===")
r2 = results["retrieval_holdout_140_no_rerank"]
print({m: (v["recall@5"], v["all@5"]) for m, v in r2["recomputed"].items()})
print("bootstrap recompute", json.dumps(r2["bootstrap_recompute"], ensure_ascii=False))
print("bootstrap reported", json.dumps(r2["reported_bootstrap"], ensure_ascii=False))
print("seed sweep", json.dumps(r2["bootstrap_seed_sweep"], ensure_ascii=False))
print("=== graph_contribution ===")
g = results["graph_contribution"]
print("calc", json.dumps(g["recomputed"], ensure_ascii=False))
print("reported", json.dumps(g["reported"], ensure_ascii=False))
print("recoveries", json.dumps(g["recovery_detail"], ensure_ascii=False))
print("failures", g["failures_n"], "reasons", g["failure_reasons_recomputed"], "verdict_violations", g["verdict_consistency_violations"])
print("=== ablation current ===")
a = results["ablation_holdout_60"]
for t, v in a["tiers"].items():
    print(t, {k: v[k] for k in ("recall@5", "grounded", "grounded_before_repair", "gate_all_pass", "repaired_rate", "refused_rate", "llm_calls_mean", "tokens_mean", "latency_median_ms", "llm_calls_sum", "tokens_sum", "out_of_scope_raw_sum", "out_of_scope_post_sum")})
print("sum calls", sum(v["llm_calls_sum"] for v in a["tiers"].values()), "sum tokens", sum(v["tokens_sum"] for v in a["tiers"].values()))
print("same sets", a["same_question_sets"], "same recall", a["same_recall_across_tiers"])
print("bootstrap", json.dumps({k: a["bootstrap_recompute"][k] for k in a["bootstrap_recompute"] if k.split(":")[1] in ("grounded", "llm_calls")}, ensure_ascii=False))
print("=== ablation dev 2026-11-04 ===")
d = results["ablation_dev_20261104"]
for t, v in d["tiers"].items():
    print(t, {k: v[k] for k in ("recall@5", "grounded", "llm_calls_mean", "tokens_mean", "latency_median_ms")})
print("=== ablation pre_graphver ===")
p = results["ablation_holdout_60_pre_graphver"]
for t, v in p["tiers"].items():
    print(t, {k: v[k] for k in ("recall@5", "grounded", "grounded_before_repair", "grounded_after_repair", "gate_all_pass", "repaired_rate", "llm_calls_mean", "latency_median_ms", "out_of_scope_raw_sum")})
