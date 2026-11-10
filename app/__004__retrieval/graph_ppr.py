"""Personalised PageRank 图检索器（独立文件，不接入 ``__005__retriever`` 主流程）。

与 2-hop 档的差异：

* **不做硬剪枝**，而是把种子概念的权重作为 PPR 的 personalization 向量，让质量沿
  ``REQUIRES`` / ``SPECIAL_OF`` 与「概念 -> 条文」投影边扩散；条文节点是汇点（没有出边），
  所以最终质量 = 从种子出发、按权重走到该条文的概率。
* **投影边加权**：``DEFINES`` 记 1.0，``REFERS_TO`` 记 0.7（比主检索器的 0.45 略高，
  因为金标首选条文有 72% 来自 REFERS_TO，但仍低于定义边）；再按概念 ``idf`` 降权，
  避免 ``legal_person`` 这类泛概念把质量吸走。
* **规范度微调**：排序后只对直接命中的种子概念做一次 ``1 + 0.3 * canonicality`` 乘子，
  不再叠加覆盖率加成，保持 PPR 分数本身的概率含义。

参数（damping / 迭代数 / 收敛阈值）写在类属性里，评测脚本原样写入报告。
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path
from typing import Any

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.__003__graph.__000__schema import scenario_node_id  # noqa: E402
from app.__003__graph.__001__networkx_store import NetworkXGraphStore  # noqa: E402
from app.__004__retrieval.__001__entity_linker import EntityLinker  # noqa: E402
from app.__004__retrieval.__002__graph_retrieval import GraphHit, GraphResult  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402

log = get_logger("retrieval_ppr")

CONCEPT_RELS = ("REQUIRES", "SPECIAL_OF")


class PPRRetriever:
    """Personalised PageRank：种子概念 -> 概念关系 -> 条文投影。"""

    DAMPING = 0.85
    MAX_ITER = 100
    TOL = 1e-6
    DEFINES_WEIGHT = 1.0
    REFERS_TO_WEIGHT = 0.7
    USE_IDF = True
    CANON_BOOST = 0.3
    SCENARIO_SEED = 0.80

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
        self.n_concepts = 0
        for n, d in self.g.nodes(data=True):
            if d.get("label") == "Concept":
                self.names[n] = [d.get("name"), *(d.get("aliases") or [])]
                self.n_concepts += 1
        for u, v, key, data in self.g.edges(keys=True, data=True):
            rel = data.get("relation", key)
            if rel not in ("DEFINES", "REFERS_TO"):
                continue
            if bool(data.get("inferred")):
                continue
            score = float(data.get("score", data.get("confidence", 0.8)))
            self.concept_links.setdefault(v, []).append((u, rel, score, False))

        self._graphs: dict[bool, tuple[nx.DiGraph, dict[str, int]]] = {}
        self.log = log

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

    def _graph(self, keep_inferred: bool) -> tuple[nx.DiGraph, dict[str, int]]:
        if keep_inferred in self._graphs:
            return self._graphs[keep_inferred]
        graph = nx.DiGraph()
        for n, d in self.g.nodes(data=True):
            graph.add_node(n, label=d.get("label"))
        stats = {"concept_edges": 0, "requires": 0, "special_of": 0, "applies_to": 0,
                 "defines": 0, "refers_to": 0, "inferred_skipped": 0}
        for u, v, key, data in self.g.edges(keys=True, data=True):
            rel = data.get("relation", key)
            inferred = bool(data.get("inferred"))
            if rel in CONCEPT_RELS:
                if inferred and not keep_inferred:
                    stats["inferred_skipped"] += 1
                    continue
                weight = float(data.get("confidence", 0.9))
                if graph.has_edge(u, v):
                    graph[u][v]["weight"] = max(graph[u][v]["weight"], weight)
                else:
                    graph.add_edge(u, v, weight=weight)
                stats["concept_edges"] += 1
                stats["requires" if rel == "REQUIRES" else "special_of"] += 1
            elif rel == "APPLIES_TO":
                graph.add_edge(u, v, weight=float(data.get("weight", 1.0)))
                stats["applies_to"] += 1
            elif rel in ("DEFINES", "REFERS_TO"):
                if inferred:
                    continue
                weight = float(data.get("score", data.get("confidence", 0.8)))
                weight *= self.DEFINES_WEIGHT if rel == "DEFINES" else self.REFERS_TO_WEIGHT
                weight *= self._idf(v)
                if graph.has_edge(v, u):
                    graph[v][u]["weight"] = max(graph[v][u]["weight"], weight)
                else:
                    graph.add_edge(v, u, weight=weight)
                stats["defines" if rel == "DEFINES" else "refers_to"] += 1
        self._graphs[keep_inferred] = (graph, stats)
        return graph, stats

    def _seeds(self, query: str) -> tuple[dict[str, float], dict[str, float], Any, int]:
        linking = self.linker.link(query)
        seeds = {c.id: c.score for c in linking.concepts}
        scenario_seeds = {s.id: s.score for s in linking.scenarios}
        applies = 0
        for sid, score in scenario_seeds.items():
            for _, v, _, data in self.g.out_edges(scenario_node_id(sid), keys=True, data=True):
                if data.get("relation") != "APPLIES_TO":
                    continue
                seeds[v] = max(seeds.get(v, 0.0), self.SCENARIO_SEED * float(data.get("weight", 1.0)) * score)
                applies += 1
        return seeds, scenario_seeds, linking, applies

    def retrieve(
        self, query: str, top_k: int = 5, keep_inferred: bool = True, diagnostics: bool = False
    ) -> GraphResult | tuple[GraphResult, dict[str, Any]]:
        t0 = time.perf_counter()
        graph, edge_stats = self._graph(keep_inferred)
        seeds, scenario_seeds, linking, applies = self._seeds(query)

        personal = {n: 0.0 for n in graph.nodes}
        for cid, w in seeds.items():
            if cid in personal:
                personal[cid] = w
        for sid, score in scenario_seeds.items():
            node = scenario_node_id(sid)
            if node in personal:
                personal[node] = self.SCENARIO_SEED * score
        total = sum(personal.values())

        hits: list[GraphHit] = []
        if total > 0:
            try:
                pr = nx.pagerank(
                    graph,
                    alpha=self.DAMPING,
                    personalization=personal,
                    max_iter=self.MAX_ITER,
                    tol=self.TOL,
                    weight="weight",
                )
            except nx.PowerIterationFailedConvergence:
                pr = nx.pagerank(
                    graph,
                    alpha=self.DAMPING,
                    personalization=personal,
                    max_iter=self.MAX_ITER * 5,
                    tol=self.TOL,
                    weight="weight",
                )
            direct = set(seeds)
            ranked: list[tuple[str, float]] = []
            for n, data in graph.nodes(data=True):
                if data.get("label") != "Article":
                    continue
                value = pr.get(n, 0.0)
                if self.CANON_BOOST and direct:
                    value *= 1.0 + self.CANON_BOOST * max(self._canonicality(c, n) for c in direct)
                ranked.append((n, value))
            ranked.sort(key=lambda kv: (-kv[1], kv[0]))
            for aid, score in ranked[:top_k]:
                art = self.articles.get(aid) or {}
                cids = [c for c in direct if self._canonicality(c, aid) > 0]
                hits.append(
                    GraphHit(
                        article_id=aid,
                        score=round(float(score), 6),
                        concept_ids=sorted(cids),
                        coverage=0.0,
                        path=sorted(cids) + [aid],
                        meta={
                            "law_name": art.get("law_name"),
                            "article_no": art.get("article_no"),
                            "chapter_path": art.get("chapter_path"),
                        },
                    )
                )

        coverage = 0.0
        plans = [f"ppr[alpha={self.DAMPING}, iters<={self.MAX_ITER}]", f"seed_concepts[{len(seeds)}]"]
        result = GraphResult(hits, linking, coverage, dict(seeds), plans, [])
        if not diagnostics:
            return result
        diag = {
            "latency_ms": (time.perf_counter() - t0) * 1000,
            "seeds": len(seeds),
            "candidate_articles": len([n for n, d in graph.nodes(data=True) if d.get("label") == "Article"]),
            "edges": {**edge_stats, "scenario_applies_to": applies},
        }
        return result, diag
