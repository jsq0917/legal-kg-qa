"""图查询门面：统一 Neo4j 与 NetworkX 两种后端，对上层只暴露「查询计划」。

上层（检索/Agent）永远不写 Cypher，只调用 ``concept_articles`` / ``scenario_concepts`` 等方法；
后端切换只影响执行层，评测口径因此可比。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.__003__graph.__000__schema import META_LABEL, QueryPlan, scenario_node_id, to_cypher  # noqa: E402
from app.__003__graph.__001__networkx_store import NetworkXGraphStore, build_and_save  # noqa: E402
from app.__003__graph.__005__version import read_manifest  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from common.neo4j_manager import get_neo4j_manager  # noqa: E402

log = get_logger("graph_store")


class Neo4jGraphStore:
    backend = "neo4j"

    def __init__(self) -> None:
        self.mgr = get_neo4j_manager()
        if not self.mgr.connect():
            raise ConnectionError("Neo4j 不可用")
        self.query_log: list[dict[str, Any]] = []
        self.graph_build_id: str = ""
        self.content_hash: str = ""
        self.degraded_reason: str | None = None
        self.graph_build_id = self._read_build_id()

    def _read_build_id(self) -> str:
        try:
            rows = self.mgr.run_query("MATCH (m:GraphMeta) RETURN m.graph_build_id AS id, m.content_hash AS h LIMIT 1")
        except Exception:  # 旧库没有 GraphMeta，按「未标版本」处理
            return ""
        if not rows:
            return ""
        self.content_hash = rows[0].get("h") or ""
        return rows[0].get("id") or ""

    def execute(self, plan: QueryPlan) -> list[dict]:
        cypher, params = to_cypher(plan)
        ok, msg = self.mgr.validate_cypher(cypher, params)
        if not ok:
            raise ValueError(f"Cypher 预检失败: {msg}")
        t0 = time.perf_counter()
        rows = self.mgr.run_query(cypher, params)
        self.query_log.append({"plan": plan.name, "ms": round((time.perf_counter() - t0) * 1000, 2), "rows": len(rows)})
        return rows

    def concept_articles(self, concept_ids: list[str], limit: int = 20) -> list[dict]:
        return self.execute(QueryPlan("concept_articles", {"concept_ids": concept_ids, "limit": limit}))

    def scenario_concepts(self, scenario_ids: list[str]) -> list[dict]:
        return self.execute(
            QueryPlan("scenario_concepts", {"scenario_ids": [scenario_node_id(s) for s in scenario_ids]})
        )

    def concept_neighbors(
        self, concept_ids: list[str], relations: list[str] | None = None, direction: str = "out", limit: int = 50
    ) -> list[dict]:
        return self.execute(
            QueryPlan(
                "concept_neighbors",
                {"concept_ids": concept_ids, "relations": relations or ["REQUIRES", "SPECIAL_OF"], "direction": direction, "limit": limit},
            )
        )

    def article_cites(self, article_ids: list[str]) -> list[dict]:
        return self.execute(QueryPlan("article_cites", {"article_ids": article_ids}))

    def concept_scenarios(self, concept_ids: list[str], limit: int = 20) -> list[dict]:
        return self.execute(QueryPlan("concept_scenarios", {"concept_ids": concept_ids, "limit": limit}))

    def article_detail(self, ids: list[str]) -> list[dict]:
        return self.execute(QueryPlan("article_detail", {"ids": ids}))

    def stats(self) -> dict[str, Any]:
        meta = self.mgr.export_metadata_to_json()
        labels = {k: v for k, v in meta["labels"].items() if k != META_LABEL}
        return {
            "backend": "neo4j",
            "graph_build_id": self.graph_build_id,
            "content_hash": self.content_hash,
            "degraded_reason": self.degraded_reason,
            "nodes": sum(labels.values()),
            "edges": sum(meta["relationships"].values()),
            "labels": labels,
            "relationships": meta["relationships"],
        }

    def export_json(self, path: Path) -> None:  # 与 NetworkX 后端保持同一接口
        nodes = self.mgr.run_query("MATCH (n) RETURN labels(n) AS labels, properties(n) AS props")
        edges = self.mgr.run_query(
            "MATCH (a)-[r]->(b) RETURN a.id AS source, b.id AS target, type(r) AS relation, properties(r) AS props"
        )
        from common.path_utils import write_json

        write_json(path, {"nodes": nodes, "edges": edges})


class GraphStore:
    """后端选择器。``auto``：能连 Neo4j 就用，否则用离线镜像（并自动建镜像）。"""

    def __init__(self, backend: str | None = None) -> None:
        from config import settings

        want = backend or settings.graph_backend
        self.requested_backend = want
        self.degraded_reason: str | None = None
        self.impl: Neo4jGraphStore | NetworkXGraphStore | None = None
        manifest = read_manifest(settings.graph_dir)
        if want in ("auto", "neo4j"):
            try:
                impl = Neo4jGraphStore()
                mismatch = self._version_mismatch(impl, manifest)
                if mismatch:
                    if want == "neo4j":
                        raise RuntimeError(mismatch)
                    self.degraded_reason = mismatch
                    log.warning("%s；降级到 NetworkX 离线镜像", mismatch)
                else:
                    self.impl = impl
                    log.info("图后端: neo4j (%s)", impl.graph_build_id or "未标版本")
            except Exception as exc:
                if want == "neo4j":
                    raise
                self.degraded_reason = self.degraded_reason or f"neo4j_unavailable: {exc}"
                log.info("Neo4j 不可用（%s），切换 NetworkX 离线镜像", exc)
        if self.impl is None:
            pkl = settings.graph_dir / "lexweave_graph.pkl"
            if not pkl.exists():
                log.info("离线镜像不存在，现场构建")
                build_and_save(out_path=pkl)
            self.impl = NetworkXGraphStore(pkl)
            if self.impl.degraded_reason:
                self.degraded_reason = self.degraded_reason or self.impl.degraded_reason
            log.info("图后端: networkx (%s)", pkl)

    @staticmethod
    def _version_mismatch(impl: Neo4jGraphStore, manifest: dict[str, Any] | None) -> str | None:
        """Neo4j 库里的图版本与本地 manifest 不一致时返回原因，一致/无法判定返回 None。"""
        if not manifest:
            return None
        expected = manifest.get("graph_build_id")
        actual = impl.graph_build_id
        if not actual:
            return f"neo4j_graph_version_unknown: manifest={expected}（旧库未写入 GraphMeta）"
        if actual != expected:
            return f"neo4j_graph_version_mismatch: manifest={expected} neo4j={actual}"
        return None

    @property
    def graph_build_id(self) -> str:
        return getattr(self.impl, "graph_build_id", "") if self.impl else ""

    @property
    def backend(self) -> str:
        return self.impl.backend if self.impl else "none"

    def health(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "requested_backend": self.requested_backend,
            "graph_build_id": self.graph_build_id,
            "degraded": bool(self.degraded_reason),
            "degraded_reason": self.degraded_reason,
        }

    def execute(self, plan: QueryPlan) -> list[dict]:
        plan.validate()
        return self.impl.execute(plan)

    # ---------------- 便捷方法 ----------------
    def concept_articles(self, concept_ids: list[str], limit: int = 20) -> list[dict]:
        if self.impl is None:
            return []
        return self.impl.concept_articles(concept_ids, limit)

    def scenario_concepts(self, scenario_ids: list[str]) -> list[dict]:
        if self.impl is None:
            return []
        return self.impl.scenario_concepts(scenario_ids)

    def concept_neighbors(
        self, concept_ids: list[str], relations: list[str] | None = None, direction: str = "out", limit: int = 50
    ) -> list[dict]:
        if self.impl is None:
            return []
        return self.impl.concept_neighbors(concept_ids, relations, direction, limit)

    def article_cites(self, article_ids: list[str]) -> list[dict]:
        if self.impl is None:
            return []
        return self.impl.article_cites(article_ids)

    def concept_scenarios(self, concept_ids: list[str], limit: int = 20) -> list[dict]:
        if self.impl is None:
            return []
        return self.impl.concept_scenarios(concept_ids, limit)

    def article_detail(self, ids: list[str]) -> list[dict]:
        if self.impl is None:
            return []
        return self.impl.article_detail(ids)

    def stats(self) -> dict[str, Any]:
        return self.impl.stats() if self.impl else {}

    def export_json(self, path: Path) -> None:
        if self.impl is not None:
            self.impl.export_json(path)


_store: GraphStore | None = None


def get_graph_store() -> GraphStore:
    global _store
    if _store is None:
        _store = GraphStore()
    return _store
