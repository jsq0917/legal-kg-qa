"""离线重算引用指标：把被「引用长度」压住的 cite_precision 拆开。

对每个档位、每条题：
  must_cite_recall      = |答案引用 ∩ 金标引用| / |金标引用|
  precision_at_must     = |答案前 |金标| 条引用 ∩ 金标引用| / min(|答案引用|, |金标引用|)
                          （需要答案引用顺序；旧产物没存顺序时为 null，不外推）
  precision_at_must_set = |答案引用 ∩ 金标引用| / min(|答案引用|, |金标引用|)
                          （集合口径；多引时等于 must_cite_recall，只用于交叉校验）
  over_citation_rate    = max(|答案引用| - |金标引用|, 0) / |金标引用|
  cite_precision        = |答案引用 ∩ 金标引用| / |答案引用|   （原指标，受长度影响）

若 ablation JSON 带 answer_pre_repair 拆分字段，同时汇总：
  grounded_before_repair / grounded_after_repair / out_of_scope_cites_*

不调用任何模型或 API；只读 data/eval_out 与 gold。
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.path_utils import read_jsonl, write_json  # noqa: E402

METRIC_KEYS = (
    "must_cite_recall",
    "precision_at_must",
    "precision_at_must_set",
    "over_citation_rate",
    "cite_precision",
)
SPLIT_KEYS = ("grounded_before_repair", "grounded_after_repair", "out_of_scope_cites_raw", "out_of_scope_cites_post")


def mean(vals: list[float]) -> float:
    return round(statistics.mean(vals), 4) if vals else 0.0


def ci(vals: list[float], rng: random.Random, n: int = 1000) -> list[float]:
    if not vals:
        return [0.0, 0.0]
    boots = [statistics.mean(rng.choices(vals, k=len(vals))) for _ in range(n)]
    boots.sort()
    return [round(boots[int(0.025 * n)], 4), round(boots[int(0.975 * n) - 1], 4)]


def row_metrics(cites: set[str], gold_cites: set[str], ordered: list[str] | None) -> dict:
    hit = len(cites & gold_cites)
    n_ans, n_must = len(cites), len(gold_cites)
    assert n_must > 0, "金标引用为空，无法计算召回"
    m = {
        "must_cite_recall": round(hit / n_must, 4),
        "precision_at_must": None,
        "precision_at_must_set": round(hit / max(min(n_ans, n_must), 1), 4),
        "over_citation_rate": round(max(n_ans - n_must, 0) / n_must, 4),
        "cite_precision": round(hit / max(n_ans, 1), 4),
        "n_cites": n_ans,
        "n_must_cite": n_must,
    }
    if ordered is not None:
        k = min(n_must, len(ordered))
        m["precision_at_must"] = round(len(set(ordered[:k]) & gold_cites) / k, 4) if k else 0.0
    return m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ablation", default="data/eval_out/ablation.json")
    ap.add_argument("--gold", default="data/gold/gold_60.jsonl")
    ap.add_argument("--out", default="data/eval_out/citation_metrics.json")
    ap.add_argument("--seed", type=int, default=20261029)
    args = ap.parse_args()

    gold = {r["id"]: r for r in read_jsonl(args.gold)}
    report = json.loads(Path(args.ablation).read_text(encoding="utf-8"))
    rng = random.Random(args.seed)

    out: dict = {
        "source": args.ablation,
        "gold": args.gold,
        "definitions": {
            "must_cite_recall": "命中金标引用 / 金标引用数（=原 cite_recall）",
            "precision_at_must": "答案前 |金标| 条引用里的命中率；不惩罚多引；需要引用顺序",
            "precision_at_must_set": "集合口径；多引时等于 must_cite_recall，仅作交叉校验",
            "over_citation_rate": "max(答案引用数-金标引用数,0) / 金标引用数",
            "cite_precision": "命中 / 答案引用数（原指标，受长度影响）",
        },
        "tiers": {},
    }

    for tier, rows in report["rows"].items():
        detail: list[dict] = []
        for r in rows:
            g = gold[r["id"]]
            gold_cites = set(g["article_citations"])
            cites = set(r.get("citations") or [])
            ordered = r.get("citations_ordered")
            m = row_metrics(cites, gold_cites, ordered)
            # 与产物里的原值交叉校验，偏差超 0.001 说明两边口径不一致
            if "cite_precision" in r:
                assert abs(m["cite_precision"] - r["cite_precision"]) <= 0.001, (
                    f"{tier}/{r['id']} cite_precision 不一致：{m['cite_precision']} vs {r['cite_precision']}"
                )
            m["id"] = r["id"]
            m["hop_type"] = r["hop_type"]
            m["citations"] = sorted(cites)
            m["citations_ordered"] = ordered
            m["gold_citations"] = sorted(gold_cites)
            for k in SPLIT_KEYS:
                if k in r:
                    m[k] = r[k]
            detail.append(m)

        summary: dict = {"n": len(detail), "order_available": all(d["precision_at_must"] is not None for d in detail)}
        for k in METRIC_KEYS:
            vals = [d[k] for d in detail if d.get(k) is not None]
            summary[k] = mean(vals) if vals else None
            if vals:
                summary[f"{k}_ci95"] = ci(vals, rng)
        summary["n_cites_mean"] = mean([d["n_cites"] for d in detail])
        summary["n_must_cite_mean"] = mean([d["n_must_cite"] for d in detail])
        split_available = all(k in detail[0] for k in SPLIT_KEYS) if detail else False
        summary["grounded_split_available"] = split_available
        if split_available:
            for k in SPLIT_KEYS:
                summary[f"{k}_mean"] = mean([d[k] for d in detail])
        summary["by_hop"] = {}
        for hop in ("two_hop", "three_hop", "cross_law"):
            sub = [d for d in detail if d["hop_type"] == hop]
            if sub:
                summary["by_hop"][hop] = {
                    k: (mean([d[k] for d in sub if d.get(k) is not None]) if any(d.get(k) is not None for d in sub) else None)
                    for k in METRIC_KEYS
                }
        out["tiers"][tier] = {"summary": summary, "rows": detail}

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    write_json(args.out, out)
    print(
        json.dumps(
            {t: {k: v for k, v in s["summary"].items() if k != "by_hop"} for t, s in out["tiers"].items()},
            ensure_ascii=False,
            indent=1,
        )
    )
    print("写出", args.out)


if __name__ == "__main__":
    main()
