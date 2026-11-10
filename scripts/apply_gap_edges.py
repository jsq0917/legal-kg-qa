"""把 m 的语义推断边（gap_edges_inferred.jsonl）作为**后处理**并入 NetworkX 镜像。

为什么是后处理、而不是改 `build_graph.py`：
`build_and_save()` 只读 `data/lexicon/{requires,special_of,scenarios}.jsonl`（机械层 706 条），
不读 `gap_edges_inferred.jsonl`。为了注入 194 条推断边而改建图核心逻辑，会让「机械层」
与「推断层」混成一个不可分的产物；因此这里只做一次可复现的加边 + 打标，并把核对结果写到
`data/eval_out/gap_edges_mark_check.json`。**重建本身未改，标记是后处理加的。**

标记约定：
- 边 key = `REQUIRES_INFERRED` / `SPECIAL_OF_INFERRED`，与机械层的 `REQUIRES` / `SPECIAL_OF`
  在 MultiDiGraph 里并存，不会覆盖机械边；
- 边属性 `inferred=True`、`provenance="inferred"`、`confidence=0.6`（medium 的数值映射，
  原字符串保留在 `confidence_label`），`evidence_type` / `article` / `note` 原样带入。

用法：
    python scripts/apply_gap_edges.py
    python scripts/apply_gap_edges.py --check-only
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__003__graph.__001__networkx_store import NetworkXGraphStore  # noqa: E402
from common.path_utils import read_jsonl, write_json  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402

log = get_logger("scripts.apply_gap_edges")

GRAPH_DIR = Path("data/graph")
PKL = GRAPH_DIR / "lexweave_graph.pkl"
GAP = Path("data/lexicon/gap_edges_inferred.jsonl")
CHECK_JSON = Path("data/eval_out/gap_edges_mark_check.json")

MECHANICAL_RELS = ("REQUIRES", "SPECIAL_OF", "APPLIES_TO")
INFERRED_KEY = {"REQUIRES": "REQUIRES_INFERRED", "SPECIAL_OF": "SPECIAL_OF_INFERRED"}
CONF_NUMERIC = {"medium": 0.6, "high": 0.8, "low": 0.4}


def _mechanical_counts(g) -> dict[str, int]:
    """机械层：key 等于关系名本身的那批边。"""
    return {
        rel: sum(1 for _, _, k in g.edges(keys=True) if k == rel)
        for rel in MECHANICAL_RELS
    }


def _mechanical_marked(g) -> int:
    """机械层里被误打 inferred 标记的条数（应为 0）。"""
    return sum(
        1
        for _, _, k, d in g.edges(keys=True, data=True)
        if k in MECHANICAL_RELS and d.get("inferred")
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkl", default=str(PKL))
    ap.add_argument("--gap", default=str(GAP))
    ap.add_argument("--check-json", default=str(CHECK_JSON))
    ap.add_argument("--check-only", action="store_true", help="只核对，不写图")
    args = ap.parse_args()

    store = NetworkXGraphStore(Path(args.pkl))
    g = store.g
    rows = read_jsonl(Path(args.gap))

    nodes = set(g.nodes)
    added, skipped_existing, bad_relation, missing_node = 0, 0, 0, []
    rel_counter: Counter[str] = Counter()

    for r in rows:
        rel = r.get("relation")
        key = INFERRED_KEY.get(rel)
        if not key:
            bad_relation += 1
            continue
        u, v = r["source"], r["target"]
        if u not in nodes or v not in nodes:
            missing_node.append((u, v, rel, r.get("article")))
            continue
        if g.has_edge(u, v, key=key):
            skipped_existing += 1
            continue
        if args.check_only:
            added += 1
            rel_counter[rel] += 1
            continue
        g.add_edge(
            u,
            v,
            key=key,
            relation=rel,
            inferred=True,
            evidence_type=r.get("evidence_type"),
            confidence=CONF_NUMERIC.get(r.get("confidence"), 0.6),
            confidence_label=r.get("confidence"),
            provenance="inferred",
            article=r.get("article"),
            article_no=r.get("article_no"),
            cue=r.get("cue"),
            score=r.get("score"),
            note=r.get("note"),
            source_name=r.get("source_name"),
            target_name=r.get("target_name"),
        )
        added += 1
        rel_counter[rel] += 1

    inferred_in_graph = sum(
        1 for _, _, _, d in g.edges(keys=True, data=True) if d.get("inferred")
    )
    check = {
        "gap_file": str(args.gap),
        "graph_file": str(args.pkl),
        "gap_rows": len(rows),
        "added_this_run": added,
        "skipped_already_present": skipped_existing,
        "bad_relation_rows": bad_relation,
        "missing_node_rows": len(missing_node),
        "missing_node_samples": missing_node[:10],
        "added_by_relation": dict(rel_counter),
        "inferred_edges_in_graph": inferred_in_graph,
        "mechanical_edges": _mechanical_counts(g),
        "mechanical_with_inferred_flag": _mechanical_marked(g),
        "check_only": args.check_only,
    }
    write_json(Path(args.check_json), check)
    log.info("核对结果 %s", check)

    if not args.check_only:
        # 先把加边后的图写回 pkl，再导出 graph.json、跑 stats；顺序不能反，
        # 否则 stats 读到的还是旧 pkl（曾出现 graph.json 有 194 条、stats 仍报 5632 的不一致）。
        import pickle

        with open(args.pkl, "wb") as f:
            pickle.dump(g, f, protocol=pickle.HIGHEST_PROTOCOL)
        store = NetworkXGraphStore(Path(args.pkl))
        store.export_json(GRAPH_DIR / "graph.json")
        from app.__003__graph.__004__graph_stats import main as graph_stats_main

        graph_stats_main()
        log.info("已写回 pkl、重新导出 graph.json 并刷新 stats.json")

    if args.check_only:
        return
    problems = []
    if inferred_in_graph != len(rows):
        problems.append(f"图中 inferred 边 {inferred_in_graph} != 文件 {len(rows)}")
    if _mechanical_marked(g):
        problems.append(f"机械层被误标 {_mechanical_marked(g)} 条")
    if missing_node:
        problems.append(f"{len(missing_node)} 条端点不存在")
    if problems:
        raise SystemExit("核对未过: " + "; ".join(problems))


if __name__ == "__main__":
    main()
