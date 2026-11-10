"""只读测量：``DEFINES_STRICT`` 严口径相对 B7 旧口径的规模变化。

**不建图、不写 graph.json、不调 build_and_save**，只读 ``data/extracted/mentions.jsonl``
与 ``articles.jsonl``，把两套判据各跑一遍再对照。

三种口径：

* ``old``：``DEFINES_STRICT`` 不设，即 B7 现行判据（含「首分句内出现即算」的宽口径分支）；
* ``strict``：``DEFINES_STRICT=1``，只保留「别名起于分句首」+ 同分句闸门；
* ``strict_no_gate``：在 strict 基础上把 ``_CLAUSE_BREAK`` 临时置空，用来单独度量闸门
  自身的边际效果（闸门只对「模态词在别的分句」的情况起作用）。

用法::

    .venv\\Scripts\\python.exe scripts/measure_defines_strict.py
    .venv\\Scripts\\python.exe scripts/measure_defines_strict.py --out data/eval_out/defines_strict_preview.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__002__extract import __003__edge_typing as et  # noqa: E402
from common.path_utils import read_jsonl, write_json  # noqa: E402

OUT = "data/eval_out/defines_strict_preview.json"
SAMPLE_CHARS = 46

# 旧 cue → 判据家族，用于「按 kind 分组的保留率」
FAMILY = {
    "系词定义": "copula",
    "所称号": "designation",
}
CUE_FAMILY = {
    "是指": "definition_cue",
    "系指": "definition_cue",
    "即指": "definition_cue",
    "指的是": "definition_cue",
    "包括": "definition_cue",
    "分为": "definition_cue",
    "统称": "definition_cue",
    "所称": "definition_cue",
}


def _family(cue: str) -> str:
    if cue in FAMILY:
        return FAMILY[cue]
    if cue in CUE_FAMILY:
        return CUE_FAMILY[cue]
    if cue.startswith("normative_subject"):
        return "subject_position"
    if cue.startswith("normative"):
        return "first_clause_wide"
    return "other"


def _run(articles: dict[str, dict], mentions: list[dict], *, strict: bool) -> list[dict]:
    if strict:
        os.environ["DEFINES_STRICT"] = "1"
    else:
        os.environ.pop("DEFINES_STRICT", None)
    rows = []
    for m in mentions:
        art = articles.get(m["article_id"])
        if art is None:
            continue
        rows.append(et.classify_mention(art, m))
    os.environ.pop("DEFINES_STRICT", None)
    return rows


def _clause_start(text: str, pos: int) -> int:
    """pos 所在分句的起始偏移（分句分隔符与 ``et._clauses`` 保持一致）。"""
    start = 0
    for m in re.finditer(r"[，、；。：\n]", text[:pos]):
        start = m.end()
    return start


def _snippet(text: str, start: int, end: int) -> str:
    lo = max(0, start - 10)
    seg = text[lo : min(len(text), end + SAMPLE_CHARS)].replace("\n", " ")
    return ("…" if lo else "") + seg


def measure(out_path: str) -> dict:
    root = Path(__file__).resolve().parents[1]
    articles = {a["article_id"]: a for a in read_jsonl(root / "data/extracted/articles.jsonl")}
    mentions = read_jsonl(root / "data/extracted/mentions.jsonl")
    concepts = {c["id"]: c for c in read_jsonl(root / "data/lexicon/concepts.jsonl")}

    old = _run(articles, mentions, strict=False)
    strict = _run(articles, mentions, strict=True)
    saved_break = et._CLAUSE_BREAK
    et._CLAUSE_BREAK = ""
    try:
        no_gate = _run(articles, mentions, strict=True)
    finally:
        et._CLAUSE_BREAK = saved_break

    n = len(old)
    old_d = sum(1 for r in old if r["edge_type"] == et.DEFINES)
    new_d = sum(1 for r in strict if r["edge_type"] == et.DEFINES)
    no_gate_d = sum(1 for r in no_gate if r["edge_type"] == et.DEFINES)

    dropped, gained = [], []
    for o, s in zip(old, strict):
        if o["edge_type"] == et.DEFINES and s["edge_type"] != et.DEFINES:
            dropped.append((o, s))
        elif o["edge_type"] != et.DEFINES and s["edge_type"] == et.DEFINES:
            gained.append((o, s))

    # 按旧 cue 家族统计保留率
    fam_tot: Counter = Counter()
    fam_keep: Counter = Counter()
    for o, s in zip(old, strict):
        if o["edge_type"] != et.DEFINES:
            continue
        f = _family(o["cue"])
        fam_tot[f] += 1
        if s["edge_type"] == et.DEFINES:
            fam_keep[f] += 1
    by_family = {
        f: {
            "old": fam_tot[f],
            "kept": fam_keep[f],
            "dropped": fam_tot[f] - fam_keep[f],
            "retention": round(fam_keep[f] / fam_tot[f], 4) if fam_tot[f] else None,
        }
        for f in sorted(fam_tot)
    }

    # 概念维度
    old_by: Counter = Counter()
    new_by: Counter = Counter()
    for o in old:
        if o["edge_type"] == et.DEFINES:
            old_by[o["concept_id"]] += 1
    for s in strict:
        if s["edge_type"] == et.DEFINES:
            new_by[s["concept_id"]] += 1
    drop_by = {cid: old_by[cid] - new_by[cid] for cid in old_by}
    top_drop = sorted(
        ((cid, d) for cid, d in drop_by.items() if d > 0),
        key=lambda kv: (-kv[1], kv[0]),
    )[:10]

    samples = []
    causes: Counter = Counter()
    for o, _ in dropped:
        art = articles[o["article_id"]]
        text = art["content"]
        end = o["offset"] + len(o["matched_alias"])
        if text[end : end + 1] in et._BAD_AFTER_ALIAS:
            cause = "偏正/并列结构（别名后紧跟 的/与/和/及/或/、）"
        elif o["offset"] != _clause_start(text, o["offset"]):
            cause = "不在分句首（宾语位、介词短语或词内匹配）"
        else:
            cause = "同分句闸门"
        causes[cause] += 1
        samples.append(
            {
                "concept_id": o["concept_id"],
                "concept_name": (concepts.get(o["concept_id"]) or {}).get("name"),
                "article_id": o["article_id"],
                "citation": art.get("citation"),
                "matched_alias": o["matched_alias"],
                "old_cue": o["cue"],
                "drop_cause": cause,
                "new_edge_type": "REFERS_TO",
                "snippet": _snippet(text, o["offset"], end),
            }
        )

    out = {
        "script": "scripts/measure_defines_strict.py",
        "readonly": "只读 measurements：未写 graph.json、未调 build_and_save",
        "switch": "DEFINES_STRICT=1 启用严口径；不设＝B7 旧口径",
        "inputs": {
            "mentions": "data/extracted/mentions.jsonl",
            "articles": "data/extracted/articles.jsonl",
            "rule_mentions": n,
            "manual_mentions_excluded": 5,
        },
        "edges": {
            "old_defines": old_d,
            "strict_defines": new_d,
            "dropped": old_d - new_d,
            "gained": len(gained),
            "drop_ratio": round((old_d - new_d) / old_d, 4) if old_d else None,
            "old_ratio_of_mentions": round(old_d / n, 4),
            "strict_ratio_of_mentions": round(new_d / n, 4),
            "with_manual_old": old_d + 5,
            "with_manual_strict": new_d + 5,
        },
        "gate_marginal": {
            "strict_defines_without_gate": no_gate_d,
            "blocked_by_gate": no_gate_d - new_d,
            "note": "闸门只在「别名起于分句首但模态词落在后面的分句」时起作用；为 0 说明该分支本身已按分句切分，闸门是纯防御性代码",
        },
        "by_family": by_family,
        "concepts": {
            "old_with_defines": len(old_by),
            "strict_with_defines": len(new_by),
            "lost_all_defines": len([c for c in old_by if new_by.get(c, 0) == 0]),
            "total_concepts": len(concepts),
        },
        "top_dropped_concepts": [
            {
                "concept_id": cid,
                "concept_name": (concepts.get(cid) or {}).get("name"),
                "dropped": d,
                "old_defines": old_by[cid],
                "strict_defines": new_by.get(cid, 0),
            }
            for cid, d in top_drop
        ],
        "drop_causes": dict(sorted(causes.items())),
        "dropped_samples": samples,
        "cue_distribution": {
            "old": dict(sorted(Counter(r["cue"] for r in old if r["edge_type"] == et.DEFINES).items())),
            "strict": dict(
                sorted(Counter(r["cue"] for r in strict if r["edge_type"] == et.DEFINES).items())
            ),
        },
    }
    write_json(root / out_path, out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=OUT)
    args = ap.parse_args()
    out = measure(args.out)
    brief = {k: v for k, v in out.items() if k not in ("dropped_samples",)}
    print(json.dumps(brief, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
