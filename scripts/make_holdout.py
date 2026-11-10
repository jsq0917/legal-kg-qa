"""从 gold_200 生成留出集：140 条 B3 留出 + 60 条 B6 分层抽样。

- gold_60（母本子集）是调参/校准用 dev，**不参与**这里的抽样；
- 140 = 200 - 60，按原顺序写出，供 eval_retrieval.py --gold 使用；
- B6 的 60 条从 140 里按 hop_type 分层抽 30/20/10，seed 固定，可复现。
"""

from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.path_utils import read_jsonl, write_jsonl  # noqa: E402

DEV = "data/gold/gold_60.jsonl"
ALL = "data/gold/gold_200.jsonl"
HOLDOUT_140 = "data/gold/gold_140_holdout.jsonl"
HOLDOUT_60 = "data/gold/gold_60_holdout.jsonl"
QUOTA = {"two_hop": 30, "three_hop": 20, "cross_law": 10}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20261110)
    args = ap.parse_args()

    dev = read_jsonl(DEV)
    all_rows = read_jsonl(ALL)
    dev_ids = {r["id"] for r in dev}
    all_ids = {r["id"] for r in all_rows}
    missing = dev_ids - all_ids
    assert not missing, f"dev 有 {len(missing)} 条不在 gold_200：{sorted(missing)[:5]}"

    holdout = [r for r in all_rows if r["id"] not in dev_ids]
    assert len(holdout) == 140, f"留出集应为 140，实际 {len(holdout)}"

    rng = random.Random(args.seed)
    picked: list[dict] = []
    for hop, quota in QUOTA.items():
        pool = [r for r in holdout if r["hop_type"] == hop]
        assert len(pool) >= quota, f"{hop} 只有 {len(pool)} 条，不够抽 {quota}"
        picked.extend(rng.sample(pool, quota))
    order = {r["id"]: i for i, r in enumerate(holdout)}
    picked.sort(key=lambda r: order[r["id"]])

    write_jsonl(HOLDOUT_140, holdout)
    write_jsonl(HOLDOUT_60, picked)
    print("140 holdout:", len(holdout), dict(Counter(r["hop_type"] for r in holdout)))
    print("60 holdout :", len(picked), dict(Counter(r["hop_type"] for r in picked)))
    print("ids60:", ",".join(r["id"] for r in picked))
    print("seed:", args.seed)
    print("paths:", Path(HOLDOUT_140), Path(HOLDOUT_60))


if __name__ == "__main__":
    main()
