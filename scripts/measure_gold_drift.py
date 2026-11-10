"""T06 金标题面漂移量化：题面到底离条文原文有多远。

用数字回答「60 条金标题面是不是照着条文原文改的」，不给辩解：
* ``bigram_overlap`` —— 题面二元组有多少落在被锁条文的二元组里（复用 ``eval/common.py``，不另写口径）；
* ``edit_distance``  —— 题面与最接近的那条被锁条文的字符级编辑距离（原始值 + 归一化值）；
* ``article_no``     —— 题面里是否直接出现「第 X 条」或该条文的条号。

只读 ``data/gold/gold_60.jsonl`` 与 ``data/corpus/articles.jsonl``；只写
``data/eval_out/gold_question_drift.json``。不改任何 gold 文件。
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.path_utils import read_jsonl, write_json  # noqa: E402
from eval.common import bigram_overlap  # noqa: E402

#: gold 的 article_id 前缀 -> 法名；与 build_gold / check_gold 的命名一致
ID_PREFIX = {
    "lcl": "中华人民共和国劳动合同法",
    "ll": "中华人民共和国劳动法",
    "cc": "中华人民共和国民法典",
}

#: 建题脚本内部的「抄写」阈值；验收线就是它（题面与任一被锁条文的二元组重合率都低于此值）
BUILD_LEAK = 0.45

ARTICLE_NO_PATTERN = re.compile(r"第[〇零一二三四五六七八九十百千0-9]+条")


def levenshtein(a: str, b: str) -> int:
    """字符级编辑距离（滚动数组，O(min(len(a), len(b))) 空间）。"""
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def load_corpus() -> dict[tuple[str, int], dict[str, Any]]:
    """(法名, 条号数字) -> 条文；用 corpus 而不是 extracted，避免把派生文件当事实源。"""
    corpus: dict[tuple[str, int], dict[str, Any]] = {}
    for row in read_jsonl("data/corpus/articles.jsonl"):
        corpus[(row["law_name"], int(row["article_num"]))] = row
    return corpus


def resolve(article_id: str, corpus: dict[tuple[str, int], dict[str, Any]]) -> dict[str, Any] | None:
    prefix, _, num = article_id.partition("#")
    law = ID_PREFIX.get(prefix)
    if not law or not num.isdigit():
        return None
    return corpus.get((law, int(num)))


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(q * (len(ordered) - 1))))
    return round(ordered[idx], 4)


def main() -> None:
    ap = argparse.ArgumentParser(description="量 60 条金标题面与条文原文的漂移")
    ap.add_argument("--gold-file", default="data/gold/gold_60.jsonl")
    ap.add_argument("--out", default="data/eval_out/gold_question_drift.json")
    args = ap.parse_args()

    gold = read_jsonl(args.gold_file)
    corpus = load_corpus()

    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for q in gold:
        question = q["question"]
        pairs = []
        for aid in q["expected_articles"]:
            article = resolve(aid, corpus)
            if article is None:
                missing.append(f"{q['id']}:{aid}")
                continue
            content = article["content"]
            overlap = round(bigram_overlap(question, content), 4)
            dist = levenshtein(question, content)
            pairs.append(
                {
                    "article_id": aid,
                    "article_no": article["article_no"],
                    "bigram_overlap": overlap,
                    "edit_distance": dist,
                    "edit_distance_norm": round(dist / max(len(question), len(content), 1), 4),
                }
            )

        if not pairs:
            continue
        worst = max(pairs, key=lambda p: p["bigram_overlap"])
        closest = min(pairs, key=lambda p: p["edit_distance"])
        matched_nos = ARTICLE_NO_PATTERN.findall(question)
        locked_nos = {p["article_no"] for p in pairs}
        rows.append(
            {
                "id": q["id"],
                "hop_type": q["hop_type"],
                "question": question,
                "question_chars": len(question),
                "locked_articles": [p["article_id"] for p in pairs],
                # 验收项：这条题的最坏二元组重合率
                "bigram_overlap_max": worst["bigram_overlap"],
                "bigram_overlap_max_article": worst["article_id"],
                "bigram_overlap_mean": round(statistics.mean(p["bigram_overlap"] for p in pairs), 4),
                "edit_distance_min": closest["edit_distance"],
                "edit_distance_min_article": closest["article_id"],
                "edit_distance_norm": closest["edit_distance_norm"],
                "mentions_article_no": bool(matched_nos),
                "article_nos_in_question": matched_nos,
                "mentions_locked_article_no": any(no in question for no in locked_nos),
                "pairs": pairs,
            }
        )

    questions = [r["bigram_overlap_max"] for r in rows]
    at_or_above = [r for r in rows if r["bigram_overlap_max"] >= BUILD_LEAK]
    dists = [r["edit_distance_min"] for r in rows]
    norms = [r["edit_distance_norm"] for r in rows]
    buckets = {
        "[0,0.05)": sum(1 for v in questions if v < 0.05),
        "[0.05,0.10)": sum(1 for v in questions if 0.05 <= v < 0.10),
        "[0.10,0.20)": sum(1 for v in questions if 0.10 <= v < 0.20),
        "[0.20,0.30)": sum(1 for v in questions if 0.20 <= v < 0.30),
        "[0.30,0.45)": sum(1 for v in questions if 0.30 <= v < 0.45),
        ">=0.45": sum(1 for v in questions if v >= BUILD_LEAK),
    }

    # 人工抽检样本：agent 只负责把「该看哪 10 条」摆出来，不替人下结论
    step = max(len(rows) // 10, 1)
    sample = [
        {
            "id": r["id"],
            "question": r["question"],
            "bigram_overlap_max": r["bigram_overlap_max"],
            "edit_distance_min": r["edit_distance_min"],
            "mentions_article_no": r["mentions_article_no"],
            "note": "请人工判断：只读题面，能不能猜到是哪一条法条",
        }
        for r in rows[::step][:10]
    ]

    report = {
        "meta": {
            "gold_file": args.gold_file,
            "corpus_file": "data/corpus/articles.jsonl",
            "metric": "reuse eval/common.py::bigram_overlap（分母为题面二元组数）",
            "threshold": BUILD_LEAK,
            "edit_distance_definition": "字符级 Levenshtein；norm = 距离 / max(题面长度, 条文长度)",
            "missing_articles": missing,
        },
        "summary": {
            "n_questions": len(rows),
            "n_article_pairs": sum(len(r["pairs"]) for r in rows),
            "bigram": {
                "mean_of_max": round(statistics.mean(questions), 4) if questions else 0.0,
                "max": round(max(questions), 4) if questions else 0.0,
                "median": round(statistics.median(questions), 4) if questions else 0.0,
                "p90": percentile(questions, 0.90),
                "all_below_0.45": not at_or_above,
                "n_at_or_above_0.45": len(at_or_above),
                "at_or_above_ids": [r["id"] for r in at_or_above],
                "distribution": buckets,
            },
            "edit_distance": {
                "mean": round(statistics.mean(dists), 1) if dists else 0.0,
                "median": round(statistics.median(dists), 1) if dists else 0.0,
                "min": min(dists) if dists else 0,
                "max": max(dists) if dists else 0,
                "mean_norm": round(statistics.mean(norms), 4) if norms else 0.0,
            },
            "article_no": {
                "questions_mentioning_any_article_no": sum(1 for r in rows if r["mentions_article_no"]),
                "questions_mentioning_locked_article_no": sum(1 for r in rows if r["mentions_locked_article_no"]),
                "ids_mentioning_locked": [r["id"] for r in rows if r["mentions_locked_article_no"]],
            },
        },
        "per_question": rows,
        "manual_spot_check_sample": sample,
        "conclusion": (
            f"{len(rows)} 条题面的最坏二元组重合率最高 {max(questions):.4f}，"
            + (
                f"全部低于 {BUILD_LEAK}。"
                if not at_or_above
                else f"有 {len(at_or_above)} 条 >= {BUILD_LEAK}，不能声称全部低于阈值。"
            )
            + f" 题面直接出现条号的有 {sum(1 for r in rows if r['mentions_article_no'])} 条"
            + f"，出现被锁条号的有 {sum(1 for r in rows if r['mentions_locked_article_no'])} 条。"
        ),
    }
    write_json(args.out, report)

    print(
        json.dumps(
            {
                "n_questions": report["summary"]["n_questions"],
                "bigram_max": report["summary"]["bigram"]["max"],
                "all_below_0.45": report["summary"]["bigram"]["all_below_0.45"],
                "edit_distance_median": report["summary"]["edit_distance"]["median"],
                "mention_locked_article_no": report["summary"]["article_no"][
                    "questions_mentioning_locked_article_no"
                ],
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    print(report["conclusion"])


if __name__ == "__main__":
    main()
