"""B3 路由评测 v2（T13）：规则快路 → 语义层 → LLM 兜底，三条口径同台对比。

标签来源（先写清楚，避免「自造标签再自测」）：
* ``medium``  —— ``gold_200.jsonl`` 的 100 条 two_hop（两概念两法条）；
* ``complex`` —— 66 条 three_hop + 34 条 cross_law（三概念或跨法）；
* ``simple``  —— ``gold_200`` 里**没有**单概念题，沿用 B3 的模板口径：从 280 个概念里
  按 id 等步长抽 60 个，问「X 是怎么规定的」。这 60 条是合成标签，报告中单列，
  不与真实金标混算结论。

评测口径：
* 规则 / 旧 LR（5 维特征，留一 CV）作为 B3 基线复现；
* 语义层（5 维特征 + bge-large-zh 句向量，5 折分层 CV）作为第二级；
* LLM 只做两件事：① 全量判一遍，给出「纯文本可达到的上限」；② 在语义层 margin 低时兜底。
  LLM 判定全部落 ``data/eval_out/router_llm_cache.json``，重跑不再产生费用。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__004__retrieval.__001__entity_linker import EntityLinker  # noqa: E402
from app.__004__retrieval.__004__router import (  # noqa: E402
    FEATURE_KEYS,
    ROUTE_LLM_SYSTEM_PROMPT,
    SEMANTIC_MARGIN_THRESHOLD,
    SemanticRouter,
    features_of,
    rule_fast,
    rule_route,
)
from common.logging_utils import get_logger  # noqa: E402
from common.path_utils import read_jsonl, write_json  # noqa: E402

log = get_logger("eval_router")

LABELS = ("simple", "medium", "complex")
SIMPLE_TEMPLATES = ("{name}是怎么规定的？", "法律对{name}有哪些规定？")
GOLD_PATH = Path("data/gold/gold_200.jsonl")
LLM_CACHE_PATH = Path("data/eval_out/router_llm_cache.json")


# --------------------------------------------------------------------------------------
# 数据集
# --------------------------------------------------------------------------------------
def hash_query(query: str) -> str:
    return hashlib.sha1(query.encode("utf-8")).hexdigest()


def load_gold() -> list[dict]:
    gold = read_jsonl(GOLD_PATH)
    if len(gold) != 200:
        raise SystemExit(f"前置不满足：{GOLD_PATH} 应为 200 行，实际 {len(gold)} 行")
    bad = [g["id"] for g in gold if not (g.get("validation") or {}).get("leakage_pass", False)]
    if bad:
        raise SystemExit(f"前置不满足：{len(bad)} 条金标未通过泄漏校验（如 {bad[:3]}）")
    return gold


def build_dataset(linker: EntityLinker, n_simple: int) -> list[dict]:
    gold = load_gold()
    rows: list[dict] = []

    concepts = sorted(read_jsonl("data/lexicon/concepts.jsonl"), key=lambda c: c["id"])
    step = max(len(concepts) // n_simple, 1)
    for i, c in enumerate(concepts[::step][:n_simple]):
        rows.append(
            {
                "id": f"s{i + 1:02d}",
                "query": SIMPLE_TEMPLATES[i % 2].format(name=c["name"]),
                "label": "simple",
                "src": "synthetic_simple",
            }
        )
    for g in gold:
        rows.append(
            {
                "id": g["id"],
                "query": g["question"],
                "label": "medium" if g["hop_type"] == "two_hop" else "complex",
                "src": g["hop_type"],
                "hops": g.get("hops"),
                "n_expected": len(g["expected_articles"]),
                "expected_articles": g["expected_articles"],
            }
        )

    for r in rows:
        linking = linker.link(r["query"])
        r["n_concepts"] = len(linking.concepts)
        r["n_scenarios"] = len(linking.scenarios)
        r["features"] = features_of(r["query"], r["n_concepts"], r["n_scenarios"])
        r["rule"], r["rule_reason"] = rule_route(r["features"])
        r["rule_fast"], r["rule_fast_reason"] = rule_fast(r["features"])
    return rows


# --------------------------------------------------------------------------------------
# 各层预测
# --------------------------------------------------------------------------------------
def stratified_folds(y: np.ndarray, folds: int, seed: int = 42) -> list[np.ndarray]:
    from sklearn.model_selection import StratifiedKFold

    return list(StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed).split(np.zeros(len(y)), y))


def oof_lr(matrix: np.ndarray, y: np.ndarray, folds: list[np.ndarray]) -> np.ndarray:
    """返回每个样本的 out-of-fold 三类概率矩阵。"""
    from sklearn.linear_model import LogisticRegression

    proba = np.zeros((len(y), len(LABELS)))
    for tr, te in folds:
        clf = LogisticRegression(max_iter=3000)
        clf.fit(matrix[tr], y[tr])
        p = clf.predict_proba(matrix[te])
        for j, cls in enumerate(clf.classes_):
            proba[te, LABELS.index(cls)] = p[:, j]
    return proba


def loo_accuracy(matrix: np.ndarray, y: np.ndarray) -> float:
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import LeaveOneOut, cross_val_predict

    pred = cross_val_predict(LogisticRegression(max_iter=1000), matrix, y, cv=LeaveOneOut())
    return float((pred == y).mean())


def knn_loo(emb: np.ndarray, y: np.ndarray, k: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """语义相似度对照：k=7 的余弦 kNN（排除自身），返回 (pred, 邻域一致度)。"""
    from sklearn.neighbors import NearestNeighbors

    nn = NearestNeighbors(n_neighbors=k + 1, metric="cosine").fit(emb)
    dist, idx = nn.kneighbors(emb)
    pred = np.empty(len(y), dtype=object)
    conf = np.zeros(len(y))
    for i in range(len(y)):
        neighbors = idx[i][1:]
        sims = 1.0 - dist[i][1:]
        votes: Counter = Counter()
        weights: Counter = Counter()
        for j, s in zip(neighbors, sims, strict=True):
            votes[y[j]] += 1
            weights[y[j]] += max(float(s), 0.0)
        best = max(weights, key=weights.get)
        pred[i] = best
        conf[i] = weights[best] / sum(weights.values()) if sum(weights.values()) else 0.0
    return pred, conf


def llm_verdicts(rows: list[dict], allow_llm: bool) -> dict[str, str]:
    """LLM 三分类判定，按查询文本哈希缓存；返回 ``{query_hash: level}``。"""
    cache: dict[str, dict] = {}
    if LLM_CACHE_PATH.exists():
        cache = json.loads(LLM_CACHE_PATH.read_text(encoding="utf-8"))

    from common.llm import get_llm

    llm = get_llm()
    missing = [r for r in rows if hash_query(r["query"]) not in cache or not cache[hash_query(r["query"])]]
    if missing and allow_llm and llm.enabled:
        log.info("LLM 判定缺失 %s 条，开始调用（其余命中缓存）", len(missing))
        t0 = time.perf_counter()
        for i, r in enumerate(missing):
            out = llm.try_json(
                [{"role": "system", "content": ROUTE_LLM_SYSTEM_PROMPT}, {"role": "user", "content": r["query"]}],
                default=None,
                temperature=0,
                max_tokens=64,
            )
            cache[hash_query(r["query"])] = out if isinstance(out, dict) else {}
            if i % 20 == 0:
                LLM_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
                log.info("LLM 进度 %s/%s", i, len(missing))
        log.info("LLM 调用完成，用时 %.0f s", time.perf_counter() - t0)
        LLM_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    out = {}
    for r in rows:
        entry = cache.get(hash_query(r["query"])) or {}
        level = entry.get("level")
        out[hash_query(r["query"])] = level if level in LABELS else "medium"
    return out


# --------------------------------------------------------------------------------------
# 指标
# --------------------------------------------------------------------------------------
def accuracy(pred: list[str] | np.ndarray, y: list[str] | np.ndarray, mask: np.ndarray | None = None) -> float:
    pred, y = np.asarray(pred), np.asarray(y)
    m = np.ones(len(y), dtype=bool) if mask is None else mask
    return float((pred[m] == y[m]).mean())


def bootstrap_ci(pred: np.ndarray, y: np.ndarray, mask: np.ndarray | None = None, n: int = 2000, seed: int = 42):
    rng = np.random.default_rng(seed)
    idx = np.where(np.ones(len(y), dtype=bool) if mask is None else mask)[0]
    vals = np.empty(n)
    for i in range(n):
        s = rng.choice(idx, size=len(idx), replace=True)
        vals[i] = (pred[s] == y[s]).mean()
    return round(float(np.percentile(vals, 2.5)), 4), round(float(np.percentile(vals, 97.5)), 4)


def confusion(pred: np.ndarray, y: np.ndarray) -> dict[str, dict[str, int]]:
    conf = {t: {p: 0 for p in LABELS} for t in LABELS}
    for p, t in zip(pred, y, strict=True):
        conf[str(t)][str(p)] += 1
    return conf


def per_class(pred: np.ndarray, y: np.ndarray) -> dict[str, dict[str, float]]:
    out = {}
    for level in LABELS:
        tp = int(((pred == level) & (y == level)).sum())
        fp = int(((pred == level) & (y != level)).sum())
        fn = int(((pred != level) & (y == level)).sum())
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        out[level] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": int((y == level).sum()),
        }
    return out


def asymmetric_costs(conf: dict[str, dict[str, int]]) -> dict:
    """错判方向不对称：complex→simple 掉召回（危险），simple→complex 只是慢（可接受）。"""
    to_simple = conf["complex"]["simple"] + conf["medium"]["simple"]
    to_complex = conf["simple"]["complex"] + conf["simple"]["medium"]
    return {
        "dangerous_complex_to_simple": conf["complex"]["simple"] + conf["medium"]["simple"],
        "medium_to_simple": conf["medium"]["simple"],
        "expensive_simple_to_complex": conf["simple"]["complex"] + conf["simple"]["medium"],
        "comment": (
            "complex/medium 被降级到 simple 会丢多跳条文（掉召回）；simple 被升级到 medium/complex "
            "只是多取几条、慢一点。两者不能用一个「错判数」合并报。"
        ),
        "to_simple_total": to_simple,
        "to_complex_total": to_complex,
    }


def measure_retrieval_cost(sample: list[dict], modes: tuple[str, ...] = ("graph", "text", "hybrid")) -> dict:
    """反事实检索：同一批复杂题分别按 simple / medium / complex 跑，量 R@5 与延迟。

    这是「路由到 simple 掉召回」的直接证据；模型显存不足时逐模式降级并如实记录 error。
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from app.__004__retrieval.__005__retriever import get_retriever

    retriever = get_retriever(use_router=False)
    out: dict = {"n": len(sample), "modes": {}}
    for mode in modes:
        mode_out: dict = {"levels": {}}
        try:
            for level in ("simple", "medium", "complex"):
                recalls, latencies = [], []
                for row in sample:
                    res = retriever.retrieve(row["query"], mode=mode, level=level)
                    hit = set(res.article_ids[:5]) & set(row["expected_articles"])
                    recalls.append(len(hit) / max(len(row["expected_articles"]), 1))
                    latencies.append(res.latency_ms)
                mode_out["levels"][level] = {
                    "recall@5": round(statistics.mean(recalls), 4),
                    "latency_median_ms": round(statistics.median(latencies), 1),
                }
        except Exception as exc:  # 显存/内存不足时不硬扛，记录降级原因
            mode_out["error"] = f"{type(exc).__name__}: {exc}"
            log.warning("反事实检索 %s 模式失败: %s", mode, exc)
        mode_out["delta_complex_minus_simple"] = round(
            mode_out["levels"].get("complex", {}).get("recall@5", 0.0)
            - mode_out["levels"].get("simple", {}).get("recall@5", 0.0),
            4,
        ) if mode_out["levels"].get("complex") and mode_out["levels"].get("simple") else None
        out["modes"][mode] = mode_out
    return out


# --------------------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description="T13 路由三级级联评测")
    ap.add_argument("--no-llm", action="store_true", help="不发起 LLM 调用，只读缓存（CI 用）")
    ap.add_argument("--n-simple", type=int, default=60, help="合成 simple 标签条数")
    ap.add_argument("--margin", type=float, default=SEMANTIC_MARGIN_THRESHOLD, help="语义层敢判门槛")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--retrieval-cost", action="store_true", help="额外跑反事实检索（慢，需要模型）")
    ap.add_argument("--cost-sample", type=int, default=20, help="反事实检索抽多少条复杂题")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 条（调试用，不落盘模型）")
    args = ap.parse_args()

    linker = EntityLinker()
    rows = build_dataset(linker, args.n_simple)
    if args.limit:
        rows = rows[: args.limit]
    y = np.array([r["label"] for r in rows])
    src = np.array([r["src"] for r in rows])
    gold_mask = np.isin(src, ["two_hop", "three_hop", "cross_law"])
    log.info("样本 %s 条：%s", len(rows), Counter(y).most_common())

    feats = np.asarray([[r["features"][k] for k in FEATURE_KEYS] for r in rows], dtype=float)
    semantic = SemanticRouter()
    emb = semantic.encode([r["query"] for r in rows])
    matrix = np.hstack([feats, np.asarray(emb, dtype=float)])

    folds = stratified_folds(y, 5)
    # ---- 基线：规则 / 旧 LR（5 维，留一 CV）----
    rule_pred = np.array([r["rule"] for r in rows])
    lr_loo = loo_accuracy(feats, y)
    lr_pred = np.array(LABELS)[np.argmax(oof_lr(feats, y, folds), axis=1)]
    # 旧 LR 的留一预测（用于混淆矩阵与示例）
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import LeaveOneOut, cross_val_predict

    lr_loo_pred = np.asarray(cross_val_predict(LogisticRegression(max_iter=1000), feats, y, cv=LeaveOneOut()))

    # ---- 语义层：5 维 + 句向量（5 折 OOF）----
    semantic_proba = oof_lr(matrix, y, folds)
    semantic_pred = np.array(LABELS)[np.argmax(semantic_proba, axis=1)]
    margins = np.sort(semantic_proba, axis=1)[:, -1] - np.sort(semantic_proba, axis=1)[:, -2]
    # ---- 语义相似度对照：纯 kNN ----
    knn_pred, knn_conf = knn_loo(np.asarray(emb), y)

    # ---- 规则快路 + 级联 ----
    llm_map = llm_verdicts(rows, allow_llm=not args.no_llm)
    llm_pred = np.array([llm_map[hash_query(r["query"])] for r in rows])

    cascade_pred, cascade_src = [], []
    for i, r in enumerate(rows):
        if r["rule_fast"] is not None:
            cascade_pred.append(r["rule_fast"])
            cascade_src.append("rule")
        elif margins[i] >= args.margin:
            cascade_pred.append(semantic_pred[i])
            cascade_src.append("semantic")
        else:
            cascade_pred.append(llm_pred[i])
            cascade_src.append("llm")
    cascade_pred = np.array(cascade_pred)
    cascade_src = np.array(cascade_src)

    # 标签自洽上界：完美跳数识别器（合成 simple 视为全对）
    hops_oracle = np.array(
        ["simple" if not g else ("complex" if int(r.get("hops") or 0) >= 3 else "medium") for r, g in zip(rows, gold_mask, strict=True)]
    )
    trigger = float((cascade_src == "llm").mean())

    # ---- 指标汇总 ----
    result: dict = {
        "n": len(rows),
        "n_gold": int(gold_mask.sum()),
        "by_label": {k: int((y == k).sum()) for k in LABELS},
        "by_source": {k: int(v) for k, v in Counter(src).items()},
        "label_scheme": {
            "medium": "gold_200 的 two_hop（100 条）",
            "complex": "gold_200 的 three_hop（66）+ cross_law（34）",
            "simple": f"词典模板合成（{args.n_simple} 条，非真实金标，结论单列）",
        },
        "rule_accuracy": round(accuracy(rule_pred, y), 4),
        "lr_loo_accuracy": round(lr_loo, 4),
        "lr_cv5_accuracy": round(accuracy(lr_pred, y), 4),
        "semantic_accuracy": round(accuracy(semantic_pred, y), 4),
        "knn_accuracy": round(accuracy(knn_pred, y), 4),
        "llm_accuracy": round(accuracy(llm_pred, y), 4),
        "cascade_accuracy": round(accuracy(cascade_pred, y), 4),
        "gold_only": {
            "rule": round(accuracy(rule_pred, y, gold_mask), 4),
            "lr_loo": round(accuracy(lr_loo_pred, y, gold_mask), 4),
            "lr_cv5": round(accuracy(lr_pred, y, gold_mask), 4),
            "semantic": round(accuracy(semantic_pred, y, gold_mask), 4),
            "knn": round(accuracy(knn_pred, y, gold_mask), 4),
            "llm": round(accuracy(llm_pred, y, gold_mask), 4),
            "cascade": round(accuracy(cascade_pred, y, gold_mask), 4),
        },
        "ci95": {
            "rule": bootstrap_ci(rule_pred, y, n=args.bootstrap),
            "lr_loo": bootstrap_ci(lr_loo_pred, y, n=args.bootstrap),
            "semantic": bootstrap_ci(semantic_pred, y, n=args.bootstrap),
            "llm": bootstrap_ci(llm_pred, y, n=args.bootstrap),
            "cascade": bootstrap_ci(cascade_pred, y, n=args.bootstrap),
            "cascade_gold_only": bootstrap_ci(cascade_pred, y, gold_mask, n=args.bootstrap),
        },
        "cascade": {
            "margin_threshold": args.margin,
            "llm_trigger_rate": round(trigger, 4),
            "llm_triggered": int((cascade_src == "llm").sum()),
            "source_mix": {k: int(v) for k, v in Counter(cascade_src).items()},
            "llm_cache": str(LLM_CACHE_PATH),
            "llm_cache_hits": int(sum(1 for r in rows if llm_map.get(hash_query(r["query"])))),
            "llm_enabled_in_run": not args.no_llm,
        },
        "confusions": {
            "rule": confusion(rule_pred, y),
            "lr_loo": confusion(lr_loo_pred, y),
            "semantic": confusion(semantic_pred, y),
            "knn": confusion(knn_pred, y),
            "cascade": confusion(cascade_pred, y),
        },
        "per_class_cascade": per_class(cascade_pred, y),
        "asymmetric_cost": asymmetric_costs(confusion(cascade_pred, y)),
        "ceiling": {
            "llm_full_set": round(accuracy(llm_pred, y), 4),
            "llm_gold_only": round(accuracy(llm_pred, y, gold_mask), 4),
            "knn_gold_only": round(accuracy(knn_pred, y, gold_mask), 4),
            "needed_gold_accuracy_for_0_88_overall": round(
                (0.88 * len(y) - float((y == "simple").sum())) / max(int(gold_mask.sum()), 1), 4
            ),
            # 标签自洽上界：假设完美读出 hops（>=3 判 complex，否则 medium），合成 simple 视为全对。
            # 这是「只看题面」的路由不可能跨过的线，用来解释 0.88 为什么不可达。
            "hops_oracle_gold": round(accuracy(hops_oracle, y, gold_mask), 4),
            "hops_oracle_overall": round(accuracy(hops_oracle, y), 4),
        },
        "wrong_examples": {
            "rule": [
                {"id": r["id"], "label": r["label"], "pred": str(p), "query": r["query"]}
                for r, p in zip(rows, rule_pred, strict=True)
                if p != r["label"]
            ][:8],
            "cascade": [
                {"id": r["id"], "label": r["label"], "pred": str(p), "source": str(s), "query": r["query"]}
                for r, p, s in zip(rows, cascade_pred, cascade_src, strict=True)
                if p != r["label"]
            ][:8],
        },
        "rows": [
            {
                "id": r["id"],
                "src": r["src"],
                "label": r["label"],
                "query": r["query"],
                "features": r["features"],
                "rule": str(rule_pred[i]),
                "lr_loo": str(lr_loo_pred[i]),
                "semantic": str(semantic_pred[i]),
                "semantic_margin": round(float(margins[i]), 4),
                "knn": str(knn_pred[i]),
                "knn_conf": round(float(knn_conf[i]), 4),
                "llm": str(llm_pred[i]),
                "cascade": str(cascade_pred[i]),
                "source": str(cascade_src[i]),
            }
            for i, r in enumerate(rows)
        ],
    }

    if args.retrieval_cost:
        sample = [r for r in rows if r["src"] in ("three_hop", "cross_law")][: args.cost_sample]
        result["retrieval_cost"] = measure_retrieval_cost(sample)
        log.info("反事实检索完成：%s", result["retrieval_cost"]["modes"].keys())

    # 全量拟合语义模型，供 Retriever 在线使用（--limit 调试时不写盘）
    if not args.limit:
        info = semantic.fit([r["query"] for r in rows], [r["features"] for r in rows], list(y), embeddings=emb)
        result["semantic_model"] = {**info, "path": str(semantic.model_path)}

    out = Path("data/eval_out/router.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, result)
    log.info(
        "规则 %.3f | 旧LR(LOO) %.3f | 语义 %.3f | LLM %.3f | 级联 %.3f（gold %.3f，触发 %.1f%%）→ %s",
        result["rule_accuracy"],
        result["lr_loo_accuracy"],
        result["semantic_accuracy"],
        result["llm_accuracy"],
        result["cascade_accuracy"],
        result["gold_only"]["cascade"],
        trigger * 100,
        out,
    )


if __name__ == "__main__":
    main()
