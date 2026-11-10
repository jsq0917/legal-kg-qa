"""2-hop 剪枝图检索器（独立文件，不接入 ``__005__retriever`` 主流程）。

设计要点（对应 T11「图路多跳改造」的 2-hop 档）：

* **只走概念关系边**：扩展阶段只允许 ``REQUIRES`` / ``SPECIAL_OF``，不走占全图 74% 的
  ``REFERS_TO``；后者只作为「概念 -> 条文」的末梢投影索引，不参与多跳遍历。
* **硬性 2 跳**：种子概念为第 0 跳，最多再扩 2 跳；每一跳的前沿按权重截断（``MAX_FRONTIER``）。
* **双向但不对称**：正向（依赖/细化方向）权重 1.0，反向权重 ``W_REVERSE``，避免把父概念
  的全部子概念平权拉进来。
* **概念稀有度降权**：高频泛概念（如 ``legal_person``）按 ``idf`` 降权，否则它们的条文会
  淹没具体概念对应的首选条款。
* **RRF 合并**：每个概念先按「概念名是否出现在章节名/首句」再按边分排序，取前
  ``CANDIDATES_PER_CONCEPT`` 条，用 ``w/(k+rank)`` 融合；避免单条高置信边直接决定全局排序。

参数全部写在类属性里，评测脚本会原样写进报告，便于复现与调参对照。
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.__003__graph.__000__schema import scenario_node_id  # noqa: E402
from app.__003__graph.__001__networkx_store import NetworkXGraphStore  # noqa: E402
from app.__004__retrieval.__001__entity_linker import EntityLinker  # noqa: E402
from app.__004__retrieval.__002__graph_retrieval import GraphHit, GraphResult  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402

log = get_logger("retrieval_2hop")

CONCEPT_RELS = ("REQUIRES", "SPECIAL_OF")


class TwoHopRetriever:
    """2-hop 剪枝检索：REQUIRES/SPECIAL_OF 上走两跳，DEFINES/REFERS_TO 只做末梢投影。"""

    MAX_HOPS = 2
    W_REQUIRES = 0.62
    W_SPECIAL_OF = 0.48
    W_REVERSE = 0.45
    MAX_FRONTIER = 60
    MAX_PROJECT_CONCEPTS = 60
    CANDIDATES_PER_CONCEPT = 20
    RRF_K = 5
    COVERAGE_BOOST = 0.35
    SCENARIO_SEED = 0.80
    USE_IDF = True

    def __init__(self, linker: EntityLinker | None = None, graph_path: Path | None = None) -> None:
        from config import settings

        path = Path(graph_path) if graph_path else settings.graph_dir / "lexweave_graph.pkl"
        self.loader = NetworkXGraphStore(path)
        self.g = self.loader.g
        self.linker = linker or EntityLinker()

        self.articles: dict[str, dict] = {
            n: d for n, d in self.g.nodes(data=True) if d.get("label") == "Article"
        }
        self.names: dict[str, list[str]] = {}
        self.concept_links: dict[str, list[tuple[str, str, float, bool]]] = {}
        self.concept_out: dict[str, list[tuple[str, str, float, bool]]] = {}
        self.concept_in: dict[str, list[tuple[str, str, float, bool]]] = {}
        self.scenario_concepts: dict[str, list[tuple[str, float]]] = {}

        for n, d in self.g.nodes(data=True):
            if d.get("label") == "Concept":
                self.names[n] = [d.get("name"), *(d.get("aliases") or [])]

        for u, v, key, data in self.g.edges(keys=True, data=True):
            rel = data.get("relation", key)
            inferred = bool(data.get("inferred"))
            if rel in ("DEFINES", "REFERS_TO"):
                conf = float(data.get("score", data.get("confidence", 0.8)))
                self.concept_links.setdefault(v, []).append((u, rel, conf, inferred))
            elif rel in CONCEPT_RELS:
                conf = float(data.get("confidence", 0.9))
                self.concept_out.setdefault(u, []).append((v, rel, conf, inferred))
                self.concept_in.setdefault(v, []).append((u, rel, conf, inferred))
            elif rel == "APPLIES_TO":
                self.scenario_concepts.setdefault(u, []).append((v, float(data.get("weight", 1.0))))

        self.n_concepts = sum(1 for _, d in self.g.nodes(data=True) if d.get("label") == "Concept")
        self.log = log

    # ---------------- 基础分 ----------------
    def _idf(self, cid: str) -> float:
        if not self.USE_IDF:
            return 1.0
        df = max(len(self.concept_links.get(cid, [])), 1)
        return math.log((self.n_concepts + 1) / (df + 1)) + 1.0

    def _canonicality(self, cid: str, article_id: str) -> float:
        art = self.articles.get(article_id) or {}
        text = art.get("content", "")
        chapter = art.get("chapter_path", "")
        best = 0.0
        for name in self.names.get(cid, []):
            if not name:
                continue
            if name in chapter:
                return 1.0
            if name in text[:80]:
                best = max(best, 0.7)
        return best

    # ---------------- 种子与扩展 ----------------
    def _seeds(self, query: str) -> tuple[dict[str, float], dict[str, float], Any, dict[str, int]]:
        linking = self.linker.link(query)
        seeds = {c.id: c.score for c in linking.concepts}
        scenario_seeds = {s.id: s.score for s in linking.scenarios}
        stats = {"scenario_edges": 0}
        for sid, score in scenario_seeds.items():
            for cid, weight in self.scenario_concepts.get(scenario_node_id(sid), []):
                seeds[cid] = max(seeds.get(cid, 0.0), self.SCENARIO_SEED * weight * score)
                stats["scenario_edges"] += 1
        return seeds, scenario_seeds, linking, stats

    def _expand(self, seeds: dict[str, float], keep_inferred: bool) -> tuple[dict[str, float], dict[str, int]]:
        expanded = dict(seeds)
        frontier = dict(seeds)
        stats = {"concept_edges": 0, "requires": 0, "special_of": 0, "reverse": 0, "inferred": 0}
        for _ in range(self.MAX_HOPS):
            nxt: dict[str, float] = {}
            for cid, w in frontier.items():
                for v, rel, conf, inferred in self.concept_out.get(cid, []):
                    if inferred and not keep_inferred:
                        continue
                    weight = self.W_REQUIRES if rel == "REQUIRES" else self.W_SPECIAL_OF
                    nxt[v] = max(nxt.get(v, 0.0), w * weight * conf)
                    stats["concept_edges"] += 1
                    stats["requires" if rel == "REQUIRES" else "special_of"] += 1
                    stats["inferred"] += int(inferred)
                for u, rel, conf, inferred in self.concept_in.get(cid, []):
                    if inferred and not keep_inferred:
                        continue
                    weight = self.W_REQUIRES if rel == "REQUIRES" else self.W_SPECIAL_OF
                    nxt[u] = max(nxt.get(u, 0.0), w * weight * conf * self.W_REVERSE)
                    stats["concept_edges"] += 1
                    stats["requires" if rel == "REQUIRES" else "special_of"] += 1
                    stats["reverse"] += 1
                    stats["inferred"] += int(inferred)
            # 每跳前沿剪枝：只保留权重最高的 MAX_FRONTIER 个概念
            frontier = dict(sorted(nxt.items(), key=lambda kv: -kv[1])[: self.MAX_FRONTIER])
            for cid, w in frontier.items():
                expanded[cid] = max(expanded.get(cid, 0.0), w)
        return expanded, stats

    def _rank(
        self, expanded: dict[str, float], direct: set[str], keep_inferred: bool
    ) -> tuple[list[GraphHit], dict[str, int]]:
        stats = {"projection_edges": 0, "projection_defines": 0, "projection_refers_to": 0, "articles": 0}
        concepts = sorted(expanded.items(), key=lambda kv: -kv[1])[: self.MAX_PROJECT_CONCEPTS]
        rrf: dict[str, float] = {}
        evidence: dict[str, list[str]] = {}
        hit_sets: dict[str, set[str]] = {}
        for cid, weight in concepts:
            rows = [
                (aid, rel, score)
                for aid, rel, score, inferred in self.concept_links.get(cid, [])
                if not (inferred and not keep_inferred)
            ]
            rows.sort(key=lambda x: (-self._canonicality(cid, x[0]), -x[2], x[0]))
            w = weight * self._idf(cid)
            for rank, (aid, rel, _score) in enumerate(rows[: self.CANDIDATES_PER_CONCEPT], start=1):
                rrf[aid] = rrf.get(aid, 0.0) + w / (self.RRF_K + rank)
                evidence.setdefault(aid, []).append(cid)
                hit_sets.setdefault(aid, set()).add(cid)
                stats["projection_edges"] += 1
                stats["projection_defines" if rel == "DEFINES" else "projection_refers_to"] += 1
        for aid in rrf:
            cov = len(hit_sets[aid] & direct) / max(len(direct), 1)
            rrf[aid] *= 1.0 + self.COVERAGE_BOOST * cov
        ranked = sorted(rrf.items(), key=lambda kv: (-kv[1], kv[0]))
        stats["articles"] = len(ranked)
        hits: list[GraphHit] = []
        for aid, score in ranked:
            art = self.articles.get(aid) or {}
            cset = hit_sets.get(aid, set())
            hits.append(
                GraphHit(
                    article_id=aid,
                    score=round(score, 4),
                    concept_ids=sorted(cset),
                    coverage=round(len(cset & direct) / max(len(direct), 1), 4),
                    path=[*sorted(evidence.get(aid, [])), aid],
                    meta={
                        "law_name": art.get("law_name"),
                        "article_no": art.get("article_no"),
                        "chapter_path": art.get("chapter_path"),
                    },
                )
            )
        return hits, stats

    # ---------------- 主入口 ----------------
    def retrieve(
        self, query: str, top_k: int = 5, keep_inferred: bool = True, diagnostics: bool = False
    ) -> GraphResult | tuple[GraphResult, dict[str, Any]]:
        t0 = time.perf_counter()
        seeds, scenario_seeds, linking, seed_stats = self._seeds(query)
        expanded, expand_stats = self._expand(seeds, keep_inferred)
        direct = set(seeds)
        hits, rank_stats = self._rank(expanded, direct, keep_inferred)
        hits = hits[:top_k]

        total_cov = (
            len({c for h in hits for c in h.concept_ids} & direct) / max(len(direct), 1) if direct else 0.0
        )
        plans = [
            f"seeds[{len(seeds)}]",
            f"expand_2hop[{len(expanded)} concepts]",
            f"rrf_projection[{rank_stats['articles']} candidates]",
        ]
        result = GraphResult(hits, linking, round(total_cov, 4), expanded, plans, [])
        if not diagnostics:
            return result
        diag = {
            "latency_ms": (time.perf_counter() - t0) * 1000,
            "seeds": len(seeds),
            "concepts_reached": len(expanded),
            "candidate_articles": rank_stats["articles"],
            "edges": {**seed_stats, **expand_stats, **rank_stats},
        }
        return result, diag
