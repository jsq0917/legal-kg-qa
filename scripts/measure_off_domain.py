"""B5 域外拒答阈值的复算脚本（只读，不建图、不写 data/）。

口径与 ``app/__006__hitl/__003__pipeline.py::assess_domain`` 完全一致：

* ``retrieval_top_score`` = ``RetrievalOutput.top1_similarity``（bge-m3 dense 最高余弦）；
* 域内硬闸 = 链接到「劳动法专属概念」（``laws`` 全部落在劳动法/劳动合同法内）；
* ``off_domain_threshold`` = 60 条金标 top1 分布的 5% 分位，向下取两位有效数字。

用法::

    python scripts/measure_off_domain.py               # 全量 60 条，约 4 分钟（CPU）
    python scripts/measure_off_domain.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__004__retrieval.__005__retriever import get_retriever  # noqa: E402
from app.__006__hitl.__003__pipeline import assess_domain, labor_concept_ids  # noqa: E402


def percentile(values: list[float], p: float) -> float:
    s = sorted(values)
    pos = (len(s) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="data/gold/gold_60.jsonl")
    ap.add_argument("--json", default="")
    ap.add_argument("--threshold", type=float, default=None)
    args = ap.parse_args()

    from config import settings

    thr = settings.off_domain_threshold if args.threshold is None else args.threshold
    rows = [json.loads(l) for l in Path(args.gold).open(encoding="utf-8") if l.strip()]
    ret = get_retriever()

    scores: list[float] = []
    detail = []
    for g in rows:
        out = ret.retrieve(g["question"]).as_dict()
        gate = assess_domain(out, thr)
        scores.append(out["top1_similarity"])
        detail.append(
            {
                "id": g["id"],
                "scenario": g["scenario_id"],
                "top1": round(out["top1_similarity"], 4),
                "domain_concepts": gate["domain_concepts"],
                "refused": gate["refused"],
                "reason": gate["reason"],
            }
        )

    dist = {
        "n": len(scores),
        "min": round(min(scores), 4),
        "p05": round(percentile(scores, 0.05), 4),
        "p25": round(percentile(scores, 0.25), 4),
        "median": round(percentile(scores, 0.5), 4),
        "p75": round(percentile(scores, 0.75), 4),
        "max": round(max(scores), 4),
        "mean": round(sum(scores) / len(scores), 4),
    }
    sensitivity = {}
    for t in (round(thr - 0.02, 4), round(thr, 4), round(thr + 0.02, 4)):
        rejected = [d["id"] for d in detail if d["top1"] < t or not d["domain_concepts"]]
        sensitivity[str(t)] = {"rejected": len(rejected), "ids": rejected}

    report = {
        "threshold": thr,
        "labor_concepts": len(labor_concept_ids()),
        "distribution": dist,
        "concept_gate_rejects": sum(1 for d in detail if not d["domain_concepts"]),
        "sensitivity": sensitivity,
        "detail": detail,
    }
    print(json.dumps({k: v for k, v in report.items() if k != "detail"}, ensure_ascii=False, indent=1))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
