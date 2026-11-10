"""机械复核 DEFINES 边：概念串是否在条文原文字面出现、以什么形态出现、命中在哪。

纯本地、无 LLM、确定性输出。对每条 DEFINES 边给出四个机械事实：

1. ``literal_offsets``：概念名在条文原文中**原样**出现的字符偏移；
2. ``derived_offsets``：概念名只在派生形式（X 人 / X 权 / X 物 / X 方 / X 者）中出现；
3. ``alias_hits``：概念名不出现时，由哪些别名命中、命中在哪、命中串外面套着什么更长的词；
4. ``bucket``：四桶机械归类——
   ``absent``（概念串完全未出现）、``derived_only``（仅派生形式）、
   ``pending_semantic``（字面出现但机械判据给不出主语位/定义式证据）、
   ``normal``（字面出现且落在定义式或分句主语位）。

四桶只是**可复核事实的归类**，不冒充语义判定；``pending_semantic`` 一律标为「待语义判定」。
``--alias-fix`` 用当前术语库（已删掉误配别名）重算每条边的支撑，给出删边后的口径。

用法：
    python scripts/check_defines_edges.py --json data/eval_out/defines_edges_check.json
    python scripts/check_defines_edges.py --baseline data/eval_out/defines_human_labels.csv \
        --json data/eval_out/defines_edges_check_100.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DERIV_SUFFIX = ("人", "权", "物", "方", "者")
MODALS = ("应当", "不得", "可以", "有权", "必须")
DEF_CUES = ("是指", "系指", "即指", "指的是", "所称", "包括", "分为", "统称")
CLAUSE_BREAK = "，、；。：\n"
SENT_BREAK = "。；\n"


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def find_all(text: str, needle: str) -> list[int]:
    """非重叠、从左到右，确定性。"""
    out = []
    i = text.find(needle)
    while i != -1:
        out.append(i)
        i = text.find(needle, i + len(needle))
    return out


def clause_start(text: str, i: int) -> bool:
    """分句开头＝概念串紧跟在标点之后（或条文首位），而不是「上一个标点之后任意位置」。"""
    return i == 0 or text[i - 1] in CLAUSE_BREAK


def subject_evidence(text: str, i: int, name: str) -> str | None:
    """机械判定该次出现是否落在定义式或分句主语位；给不出证据就返回 None。

    只复刻 edge_typing 的「定义式 / 裸系词 / 分句主语位+模态词」三条可机械化的分支，
    不做任何语义推断。返回证据名便于抽查定位。
    """
    after = text[i + len(name) : i + len(name) + 10]
    m = re.match(r"\s*[（(，,：:]?\s*(是指|系指|即指|指的是|所称|包括|分为|统称)", after)
    if m:
        return f"definition_cue_after:{m.group(1)}"
    before = text[max(0, i - 12) : i]
    if re.search(r"所称\s*$", before):
        return "definition_suo_cheng_before"
    if not clause_start(text, i):
        return None
    cut = len(after)
    for k, ch in enumerate(after):
        if ch in CLAUSE_BREAK:
            cut = k
            break
    seg = after[:cut]
    if seg[:1] in ("的", "与", "和", "及", "或", "、"):
        return None
    for modal in MODALS:
        pos = seg.find(modal)
        while pos != -1:
            # 「所有权」「债权」里的「有权」不是模态词
            if not (modal == "有权" and pos > 0 and seg[pos - 1] == "所"):
                return f"clause_subject_modal:{modal}"
            pos = seg.find(modal, pos + 1)
    if seg.startswith("是") and not text[i + len(name) + 1 : i + len(name) + 2] == "否":
        return "clause_subject_copula"
    return None


def snippet(text: str, i: int, n: int, pad: int = 12) -> str:
    return text[max(0, i - pad) : i + n + pad]


def enclosing_terms(text: str, i: int, n: int, term_index: dict[str, set[str]]) -> list[dict]:
    """命中串外面若套着更长的术语（另一个概念名/别名），返回这些更长术语。

    这是「别名误配」的机械证据：别名本身是通用词，真正被条文规制的是外层术语
    （「承包」之于「承包人」/「总承包人」，「保管」之于「保管人」）。
    """
    out = []
    for term, owners in term_index.items():
        if len(term) <= n:
            continue
        start = text.find(term)
        while start != -1:
            if start <= i and i + n <= start + len(term) and text[i : i + n] != term:
                out.append({"term": term, "term_offset": start, "owners": sorted(owners)})
                break
            start = text.find(term, start + 1)
    return out


def classify(text: str, concept: dict, matched_alias: str | None, term_index: dict[str, set[str]]) -> dict:
    name = concept["name"]
    literal = find_all(text, name)
    derived = [i for i in literal if text[i + len(name) : i + len(name) + 1] in DERIV_SUFFIX]
    standalone = [i for i in literal if i not in derived]

    aliases = [a for a in concept.get("aliases", []) if a != name]
    alias_hits = []
    for a in aliases:
        for i in find_all(text, a):
            nxt = text[i + len(a) : i + len(a) + 1]
            others = sorted(n for n in term_index.get(a, set()) if n != name)
            alias_hits.append(
                {
                    "alias": a,
                    "offset": i,
                    "snippet": snippet(text, i, len(a)),
                    "is_substring_of_concept": a in name,
                    "followed_by_suffix": nxt in DERIV_SUFFIX,
                    "other_concepts_sharing_alias": others,
                    "enclosed_by": enclosing_terms(text, i, len(a), term_index),
                }
            )

    evidence = []
    for i in standalone:
        ev = subject_evidence(text, i, name)
        evidence.append({"offset": i, "snippet": snippet(text, i, len(name)), "evidence": ev})

    if not literal:
        bucket = "absent"
    elif not standalone:
        bucket = "derived_only"
    elif any(e["evidence"] for e in evidence):
        bucket = "normal"
    else:
        bucket = "pending_semantic"

    alias_hit_for_edge = None
    if matched_alias and matched_alias != name:
        for h in alias_hits:
            if h["alias"] == matched_alias:
                alias_hit_for_edge = h
                break

    return {
        "concept_id": concept["id"],
        "concept_name": name,
        "bucket": bucket,
        "literal_offsets": literal,
        "derived_offsets": derived,
        "standalone_offsets": standalone,
        "standalone_evidence": evidence,
        "alias_hits": alias_hits,
        "matched_alias": matched_alias,
        "matched_alias_hit": alias_hit_for_edge,
    }


def edge_supported(rec: dict, concept: dict, text_len: int) -> bool:
    """当前术语库下这条边是否还有任何字面支撑（概念名或剩余别名出现）。"""
    if rec["literal_offsets"]:
        return True
    return any(h["offset"] < text_len for h in rec["alias_hits"])


def load(root: Path):
    articles = {a["article_id"]: a for a in read_jsonl(root / "data" / "extracted" / "articles.jsonl")}
    concepts = read_jsonl(root / "data" / "lexicon" / "concepts.jsonl")
    by_id = {c["id"]: c for c in concepts}
    by_name: dict[str, list[dict]] = defaultdict(list)
    for c in concepts:
        by_name[c["name"]].append(c)
    term_index: dict[str, set[str]] = defaultdict(set)
    for c in concepts:
        for t in c["aliases"]:
            term_index[t].add(c["name"])
    edges = [e for e in read_jsonl(root / "data" / "extracted" / "mentions_typed.jsonl") if e["edge_type"] == "DEFINES"]
    return articles, by_id, by_name, dict(term_index), edges


def summarize(records: list[dict]) -> dict:
    buckets = Counter(r["bucket"] for r in records)
    n = len(records)
    absent_by_alias = Counter()
    for r in records:
        if r["bucket"] == "absent":
            alias = r["matched_alias"] or "<none>"
            absent_by_alias[alias] += 1
    return {
        "n": n,
        "buckets": {k: buckets.get(k, 0) for k in ("absent", "derived_only", "pending_semantic", "normal")},
        "absent_rate": round(buckets.get("absent", 0) / n, 4) if n else None,
        "absent_by_matched_alias_top": absent_by_alias.most_common(15),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--baseline", default=None, help="100 条基准 CSV（只读，不解释 human_label）")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--dump-rows", action="store_true", help="把 819 条逐边事实也写进 JSON")
    args = ap.parse_args()

    root = Path(args.root)
    articles, by_id, by_name, term_index, edges = load(root)

    records = []
    for e in edges:
        concept = by_id.get(e["concept_id"])
        article = articles.get(e["article_id"])
        if not concept or not article:
            records.append(
                {
                    "article_id": e["article_id"],
                    "concept_id": e["concept_id"],
                    "concept_name": concept["name"] if concept else None,
                    "bucket": "unresolved",
                    "matched_alias": e.get("matched_alias"),
                    "literal_offsets": [],
                    "derived_offsets": [],
                    "standalone_offsets": [],
                    "standalone_evidence": [],
                    "alias_hits": [],
                    "matched_alias_hit": None,
                }
            )
            continue
        rec = classify(article["content"], concept, e.get("matched_alias"), term_index)
        rec["article_id"] = e["article_id"]
        rec["citation"] = article.get("citation")
        rec["provenance"] = e.get("provenance")
        rec["edge_supported"] = edge_supported(rec, concept, len(article["content"]))
        records.append(rec)

    result = {
        "root": str(root),
        "source_edges": "data/extracted/mentions_typed.jsonl#DEFINES",
        "defines_total": len(records),
        "rules": {
            "derived_suffix": list(DERIV_SUFFIX),
            "modals": list(MODALS),
            "definition_cues": list(DEF_CUES),
            "pending_semantic": "字面出现、但无定义式/分句主语位机械证据；不判对错",
        },
        "all": summarize(records),
        # 当前术语库下，概念名与剩余别名都未在条文中出现 → 这条边没有任何字面支撑
        "edges_without_any_support": sum(1 for r in records if not r.get("edge_supported", True)),
        # 只统计仍有字面支撑的边：删别名后失去支撑的边不再计入分母
        "supported_only": summarize([r for r in records if r.get("edge_supported", True)]),
    }
    if args.dump_rows:
        result["rows"] = records

    if args.baseline:
        baseline = list(csv.DictReader((root / args.baseline).open(encoding="utf-8")))
        brows = []
        for row in baseline:
            cands = by_name.get(row["concept_name"], [])
            if not cands:
                brows.append({"concept_name": row["concept_name"], "article_id": row["article_id"], "bucket": "name_unmatched"})
                continue
            # 同名概念（如「违约金」有两个 id）按边集合定位；找不到就取第一个
            edge_ids = {e["concept_id"] for e in edges if e["article_id"] == row["article_id"]}
            concept = next((c for c in cands if c["id"] in edge_ids), cands[0])
            article = articles[row["article_id"]]
            matched = next(
                (
                    e.get("matched_alias")
                    for e in edges
                    if e["article_id"] == row["article_id"] and e["concept_id"] == concept["id"]
                ),
                None,
            )
            rec = classify(article["content"], concept, matched, term_index)
            rec["article_id"] = row["article_id"]
            rec["citation"] = row.get("citation")
            rec["ai_prefill_label"] = row.get("human_label")
            rec["ai_prefill_note"] = row.get("note")
            rec["edge_supported"] = edge_supported(rec, concept, len(article["content"]))
            brows.append(rec)
        result["baseline_100"] = summarize(brows)
        result["baseline_100_supported"] = summarize([r for r in brows if r.get("edge_supported", True)])
        result["baseline_vs_ai_prefill"] = {
            "note": "ai_prefill_label 是 AI 预填，不是人工标注，仅作并列参考",
            "cross_tab": dict(
                Counter(f"{r.get('bucket')}|label={r.get('ai_prefill_label')}" for r in brows)
            ),
        }
        result["baseline_rows"] = brows

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=False), encoding="utf-8")
        print(f"wrote {out}")
    print(json.dumps({k: result[k] for k in ("defines_total", "all", "supported_only", "edges_without_any_support")}, ensure_ascii=False, indent=1))
    if "baseline_100" in result:
        print(json.dumps({"baseline_100": result["baseline_100"], "cross_tab": result["baseline_vs_ai_prefill"]["cross_tab"]}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
