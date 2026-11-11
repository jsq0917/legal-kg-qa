"""复查：独立复算图路贡献 307 / 6 / 1，并核对新口径边界。

只读。做法：
1. 不采信 graph_contribution.json 的任何聚合数字，逐题重跑 text(top30, no rerank)、
   graph(top10)、hybrid(top5)、text(top5)；
2. 用与 HybridRetriever 相同的候选池定义（text top30 ∪ graph top10）重新计算
   「图路独有候选」「necessary / relevant / noise」「hybrid 补回」「真正图路救回」；
3. 与产物逐题对照 top5，定位任何不一致；
4. 输出 necessary 明细（path / in_graph_top5 / coverage），检查判据是否被放宽。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.__004__retrieval.__005__retriever import get_retriever  # noqa: E402
from config import settings  # noqa: E402

TOP_K = 5
TEXT_POOL_K = 30
GRAPH_POOL_K = 10


def split_expected_path(q: dict) -> tuple[str, set[str], set[str]]:
    scenario = ""
    concepts: set[str] = set()
    articles: set[str] = set()
    gold_articles = set(q.get("expected_articles") or [])
    for node in q.get("expected_path") or []:
        if node.startswith("sc:"):
            scenario = node[3:]
        elif node in gold_articles:
            articles.add(node)
        else:
            concepts.add(node)
    return scenario, concepts, articles


def build_article_concepts(retriever, gold: list[dict]) -> dict[str, set[str]]:
    store = retriever.graph.store
    concept_ids = sorted({c["id"] for q in gold for c in (q.get("concepts") or [])})
    out: dict[str, set[str]] = {}
    for cid in concept_ids:
        for row in store.concept_articles([cid], limit=800):
            out.setdefault(row["article_id"], set()).add(cid)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--gold", default="data/gold/gold_140_holdout.jsonl")
    ap.add_argument("--artifact", default="data/eval_out/graph_contribution.json")
    ap.add_argument("--out", default="2026-11-11/acceptance/work/recheck_graph_contribution.json")
    args = ap.parse_args()

    settings.llm_api_key = ""  # 不产生付费调用；路由走规则/语义

    gold = [json.loads(line) for line in (ROOT / args.gold).read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        gold = gold[: args.limit]
    art = json.loads((ROOT / args.artifact).read_text(encoding="utf-8"))
    art_rows = {r["id"]: r for r in art["rows"]}

    retriever = get_retriever()
    article_concepts = build_article_concepts(retriever, gold)

    rows = []
    mismatches = []
    for i, q in enumerate(gold, 1):
        gold_set = set(q.get("expected_articles") or [])
        concept_ids = {c["id"] for c in q.get("concepts") or []}
        _, path_concepts, _ = split_expected_path(q)
        want_concepts = concept_ids or path_concepts

        decision = retriever.decide(q["question"])
        text_pool_hits = retriever.text.search(q["question"], top_k=TEXT_POOL_K, use_rerank=False)
        text_pool = {h.article_id for h in text_pool_hits}
        graph_res = retriever.graph.retrieve(
            q["question"], top_k=GRAPH_POOL_K, hops=decision.hops, use_cites=True
        )
        graph_pool = {h.article_id for h in graph_res.hits}
        graph_top = [h.article_id for h in graph_res.hits[:TOP_K]]

        text_out = retriever.retrieve(q["question"], mode="text")
        hybrid_out = retriever.retrieve(q["question"], mode="hybrid")
        text_top = text_out.article_ids[:TOP_K]
        hybrid_top = hybrid_out.article_ids[:TOP_K]

        a = art_rows.get(q["id"])
        if a is not None:
            for name, mine, theirs in (
                ("text_top5", text_top, a["text_top5"]),
                ("graph_top5", graph_top, a["graph_top5"]),
                ("hybrid_top5", hybrid_top, a["hybrid_top5"]),
            ):
                if mine != theirs:
                    mismatches.append({"id": q["id"], "field": name, "mine": mine, "artifact": theirs})

        text_hit = gold_set <= set(text_top)
        hybrid_hit = gold_set <= set(hybrid_top)
        missing_in_text = sorted(gold_set - set(text_top))
        hybrid_recovered = (not text_hit) and hybrid_hit
        graph_rescued = (
            hybrid_recovered
            and bool(missing_in_text)
            and all(x in graph_top for x in missing_in_text)
            and all(x not in text_pool for x in missing_in_text)
        )
        graph_only = [x for x in dict.fromkeys(graph_top + hybrid_top) if x not in text_pool]

        verdicts = []
        for aid in graph_only:
            if aid in gold_set:
                verdict = "necessary"
            elif article_concepts.get(aid, set()) & want_concepts:
                verdict = "relevant_not_necessary"
            else:
                verdict = "noise"
            item = next((it for it in hybrid_out.items if it.article_id == aid), None)
            verdicts.append(
                {
                    "article_id": aid,
                    "verdict": verdict,
                    "in_graph_top5": aid in graph_top,
                    "in_graph_pool": aid in graph_pool,
                    "in_hybrid_top5": aid in hybrid_top,
                    "coverage": round(float(getattr(item, "coverage", 0.0)), 4) if item else 0.0,
                    "path": list(getattr(item, "path", []))[:8] if item else [],
                }
            )

        row = {
            "id": q["id"],
            "level": decision.level,
            "hops": decision.hops,
            "gold": sorted(gold_set),
            "text_top5": text_top,
            "graph_top5": graph_top,
            "hybrid_top5": hybrid_top,
            "text_pool": sorted(text_pool),
            "graph_pool": sorted(graph_pool),
            "text_hit": text_hit,
            "hybrid_hit": hybrid_hit,
            "text_failed": not text_hit,
            "hybrid_recovered": hybrid_recovered,
            "graph_rescued": graph_rescued,
            "missing_in_text": missing_in_text,
            "graph_only": verdicts,
        }
        rows.append(row)
        if i % 10 == 0:
            print(f"done {i}/{len(gold)}", flush=True)

    cands = [c for r in rows for c in r["graph_only"]]
    with_any = [r for r in rows if r["graph_only"]]
    nec = [c for c in cands if c["verdict"] == "necessary"]
    dup_within = [
        r["id"] for r in rows if len({c["article_id"] for c in r["graph_only"]}) != len(r["graph_only"])
    ]

    summary = {
        "n": len(rows),
        "text_failed_n": sum(1 for r in rows if r["text_failed"]),
        "recovered_n": sum(1 for r in rows if r["hybrid_recovered"]),
        "rescued_n": sum(1 for r in rows if r["graph_rescued"]),
        "rescued_ids": [r["id"] for r in rows if r["graph_rescued"]],
        "candidates_total": len(cands),
        "necessary": len(nec),
        "relevant_not_necessary": sum(1 for c in cands if c["verdict"] == "relevant_not_necessary"),
        "noise": sum(1 for c in cands if c["verdict"] == "noise"),
        "questions_with_any": len(with_any),
        "necessary_path_rate": (len([r for r in with_any if any(c["verdict"] == "necessary" for c in r["graph_only"])]) / len(with_any))
        if with_any
        else 0.0,
        "duplicate_within_row": dup_within,
        "necessary_detail": [
            {
                "id": r["id"],
                **{k: c[k] for k in ("article_id", "in_graph_top5", "in_graph_pool", "in_hybrid_top5", "coverage", "path")},
            }
            for r in rows
            for c in r["graph_only"]
            if c["verdict"] == "necessary"
        ],
        "artifact_overall": {
            "candidates_total": art["overall"]["graph_only_candidates"]["total"],
            "necessary": art["overall"]["graph_only_candidates"]["necessary"],
            "relevant_not_necessary": art["overall"]["graph_only_candidates"]["relevant_not_necessary"],
            "noise": art["overall"]["graph_only_candidates"]["noise"],
            "questions_with_any": art["overall"]["graph_only_candidates"]["questions_with_any"],
            "text_failed_n": art["overall"]["text_failed_n"],
            "recovered_n": art["overall"]["recovered_n"],
            "rescued_n": art["overall"]["rescued_n"],
            "necessary_path_rate": art["overall"]["necessary_path_rate"],
        },
        "top5_mismatch_count": len(mismatches),
        "top5_mismatches": mismatches,
    }

    out = ROOT / args.out
    out.write_text(json.dumps({**summary, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("necessary_detail", "top5_mismatches")}, ensure_ascii=False, indent=1))
    print("necessary_detail:")
    print(json.dumps(summary["necessary_detail"], ensure_ascii=False, indent=1))
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
