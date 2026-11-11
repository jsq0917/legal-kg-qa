"""核验「图路独有候选」口径：artifact 只排除了 text top5，未排除 text top30 候选池。

HybridRetriever 的候选池 = text.search(top_k=30, use_rerank=False) ∪ graph top10，
再 RRF + 精排取 top_k。因此 hybrid top5 里不在 text top5 的条文，完全可能来自
文本候选池（只是精排后名次变了），不一定是图路带来的。

本脚本只读：对 140 题重跑文本候选池 top30（不调 LLM、不做精排），
统计 532 条「图路独有候选」里有多少其实在文本候选池中。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.__004__retrieval.__005__retriever import get_retriever  # noqa: E402

d = json.loads((ROOT / "data/eval_out/graph_contribution.json").read_text(encoding="utf-8"))
gold = {json.loads(line)["id"]: json.loads(line) for line in (ROOT / "data/gold/gold_140_holdout.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
retriever = get_retriever()
from config import settings  # noqa: E402

settings.llm_api_key = ""

detail = []
origin = Counter()
for i, row in enumerate(d["rows"], 1):
    q = gold[row["id"]]["question"]
    text30 = [r.article_id for r in retriever.text.search(q, top_k=30, use_rerank=False)]
    t30 = set(text30)
    for c in row["graph_only_candidates"]:
        aid = c["article_id"]
        in_graph_top5 = aid in row["graph_top5"]
        in_hybrid_top5 = aid in row["hybrid_top5"]
        if aid in t30:
            origin["text_top30（其实在文本候选池）"] += 1
        elif in_graph_top5:
            origin["真正的图路 top5 候选"] += 1
        else:
            origin["hybrid 独有且不在文本候选池（应为 graph top10）"] += 1
        detail.append({
            "id": row["id"], "article_id": aid, "verdict": c["verdict"],
            "in_text_top30": aid in t30, "in_graph_top5": in_graph_top5,
            "in_hybrid_top5": in_hybrid_top5,
        })
    if i % 20 == 0:
        print(f"done {i}/140", flush=True)

print(json.dumps(dict(origin), ensure_ascii=False, indent=1))
# 必要候选的出身
nec = [x for x in detail if x["verdict"] == "necessary"]
print("necessary n", len(nec), "in_text_top30", sum(x["in_text_top30"] for x in nec),
      "in_hybrid_top5", sum(x["in_hybrid_top5"] for x in nec))
# 4 个 recovery 的细节
rec_ids = {x["id"] for x in d["recoveries"]}
for rid in sorted(rec_ids):
    row = next(r for r in d["rows"] if r["id"] == rid)
    q = gold[rid]["question"]
    text30 = [r.article_id for r in retriever.text.search(q, top_k=30, use_rerank=False)]
    missing = set(gold[rid]["expected_articles"]) - set(row["text_top5"])
    print(rid, "missing", sorted(missing),
          "in_text_top30", {a: (a in text30) for a in missing},
          "hybrid", row["hybrid_top5"], "graph", row["graph_top5"])
    print("  graph_paths", {c["article_id"]: c["path"] for c in row["graph_only_candidates"] if c["in_gold"]})

(Path(__file__).resolve().parents[1] / "work" / "graph_only_origin.json").write_text(
    json.dumps({"origin": dict(origin), "detail": detail}, ensure_ascii=False, indent=1), encoding="utf-8")
