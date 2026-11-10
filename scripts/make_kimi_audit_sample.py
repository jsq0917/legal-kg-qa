"""从 Kimi 211 条 DEFINES 判定中抽 40 条真人抽检（分层、盲性、可复算）。

抽样设计（固定 seed=20261110，改 seed 会得到不同样本，报告里要写清）：
* Kimi 判「成立」53 条：按句法角色分层抽 20（主语 12 / 主语中心语 8）。
* Kimi 判「不成立」158 条：先纳入 6 条质检异常（4 条证据非原文 + 2 条角色与判定矛盾），
  其余 152 条按角色比例抽 14（宾语 5 / 定语 4 / 复合词内 3 / 其他 1 / 介词宾语 1）。
* 输出表只含判定所需事实（盲标编号 / 概念名 / 条文 / 原文 / 偏移 / 所在句），
  **不含** 模型判定、句法角色、证据、理由、质检标记，也不含抽样层标识。
* 行序随机打乱，避免同层连续出现。

用法：
    .venv\\Scripts\\python.exe scripts\\make_kimi_audit_sample.py
产物：
    data/annotation/audit_kimi_40_20261110.csv

CSV 与现有盲标表同列同序，Excel 可直接双击打开；本机 .venv 未装 openpyxl，
所以不生成带下拉框的 xlsx（如需下拉，用 Excel 打开 CSV 后给「人工判定」列加数据验证即可）。
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = Path("data/annotation")
SRC = BASE / "defines_pending_211_20261110.csv"
KIMI = BASE / "kimi_batch_211_parsed.csv"
OUT_CSV = BASE / "audit_kimi_40_20261110.csv"

SEED = 20261110
COLS = [
    "盲标编号",
    "概念名",
    "条文编号",
    "条文标题",
    "条文原文",
    "概念出现位置（0起字符偏移）",
    "概念所在句",
]


def take(ids: list[str], n: int, rng: random.Random) -> list[str]:
    ids = sorted(ids)
    if len(ids) < n:
        raise SystemExit(f"分层样本不足：需要 {n}，只有 {len(ids)}")
    return rng.sample(ids, n)


def main() -> None:
    src = pd.read_csv(SRC, encoding="utf-8-sig", dtype=str)
    kimi = pd.read_csv(KIMI, encoding="utf-8-sig", dtype=str)
    if len(src) != 211 or len(kimi) != 211:
        raise SystemExit(f"行数不对：src={len(src)} kimi={len(kimi)}")

    merged = kimi.merge(src[["盲标编号"]], on="盲标编号", how="inner", validate="one_to_one")
    if len(merged) != 211:
        raise SystemExit("case_id 对不上，先跑完整性核验")

    rng = random.Random(SEED)
    picked: list[str] = []

    # 1) 成立层：按角色分层
    pos = merged[merged["模型判定"] == "成立"]
    picked += take(pos[pos["句法角色"] == "主语"]["盲标编号"].tolist(), 12, rng)
    picked += take(pos[pos["句法角色"] == "主语中心语"]["盲标编号"].tolist(), 8, rng)

    # 2) 不成立层：质检异常全收，其余按角色比例抽
    neg = merged[merged["模型判定"] == "不成立"]
    flagged = neg[neg["质检标记"].notna()]["盲标编号"].tolist()
    picked += flagged
    rest = neg[neg["质检标记"].isna()]
    quota = {"宾语": 5, "定语": 4, "复合词内": 3, "其他": 1, "介词宾语": 1}
    for role, n in quota.items():
        picked += take(rest[rest["句法角色"] == role]["盲标编号"].tolist(), n, rng)

    if len(picked) != 40 or len(set(picked)) != 40:
        raise SystemExit(f"抽样数不对或有重复：{len(picked)} / {len(set(picked))}")

    audit = src[src["盲标编号"].isin(picked)][COLS].copy()
    audit["人工判定"] = ""
    audit["备注"] = ""
    audit = audit.sample(frac=1.0, random_state=SEED).reset_index(drop=True)
    audit.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    print(f"抽到 {len(audit)} 条：{OUT_CSV}")
    print("成立层 20（主语 12 / 主语中心语 8）；不成立层 20（质检异常 6 + 分层 14）")


if __name__ == "__main__":
    main()
