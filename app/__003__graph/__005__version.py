"""图版本与内容指纹（LW-O1）。

为什么需要：图会被重建，而 API、检索评测、缓存、trace 各自持有一份旧结果时，
阅读者无法判断两组数字是否来自同一张图。这里用**内容指纹**而不是时间戳：
只要语料、抽取产物、术语库、边类型定义有一处变化，``content_hash`` 就变，
``graph_build_id`` 随之变；反之同一份输入永远得到同一个 ID，可复算、可 diff。

指纹覆盖（顺序无关，全部排序后序列化）：
* 三部法律的条文正文与章节路径（源语料）；
* Article→Concept 的边类型 / provenance / 置信度 / cue（抽取规则产物）；
* 概念表、别名、场景映射、REQUIRES / SPECIAL_OF / CITES（术语库与结构边）；
* :data:`SCHEMA_VERSION`（边类型语义变更时手动 +1，强制全链失效）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: 边类型语义版本。B7 拆分 DEFINES/REFERS_TO、T0x 收紧排序规则时递增，
#: 保证「同一个 content_hash 一定对应同一套查询语义」。
SCHEMA_VERSION = 2
#: 抽取/建图代码的语义版本（规则改动但产物恰好未变时仍能区分）。
EXTRACTOR_SCHEMA_VERSION = "2026-11-11"

MANIFEST_NAME = "graph_manifest.json"


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _mention_row(m: dict) -> list:
    return [
        m["article_id"],
        m["concept_id"],
        m.get("edge_type", "DEFINES"),
        m.get("provenance", "rule"),
        round(float(m.get("confidence", 0.8)), 6),
        round(float(m.get("defines_score", 1.0)), 6),
        m.get("cue") or "",
        int(m.get("count", 1) or 1),
        sorted(m.get("aliases", []) or []),
    ]


def compute_content_hash(
    articles: list[dict],
    concepts: list[dict],
    scenarios: list[dict],
    requires: list[dict],
    special_of: list[dict],
    mentions: list[dict],
    cites: list[dict],
) -> str:
    """对建图输入做规范化哈希；同输入必得同值。"""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "extractor_schema_version": EXTRACTOR_SCHEMA_VERSION,
        "articles": sorted(
            [
                a["article_id"],
                a.get("citation", ""),
                a.get("chapter_path", ""),
                a.get("content", ""),
            ]
            for a in articles
        ),
        "concepts": sorted(
            [
                c["id"],
                c.get("name", ""),
                sorted(c.get("aliases", []) or []),
                c.get("type", ""),
                sorted(c.get("laws", []) or []),
                c.get("parent") or "",
            ]
            for c in concepts
        ),
        "scenarios": sorted(
            [
                s["id"],
                s.get("name", ""),
                sorted(s.get("aliases", []) or []),
                list(s.get("concepts", []) or []),
            ]
            for s in scenarios
        ),
        "requires": sorted(
            [
                r["source"],
                r["target"],
                round(float(r.get("confidence", 0.9)), 6),
                r.get("provenance", "manual"),
            ]
            for r in requires
        ),
        "special_of": sorted(
            [
                r["child"],
                r["parent"],
                round(float(r.get("confidence", 0.95)), 6),
                r.get("provenance", "manual"),
            ]
            for r in special_of
        ),
        "mentions": sorted(_mention_row(m) for m in mentions),
        "cites": sorted(
            [
                c["source"],
                c["target"],
                round(float(c.get("confidence", 0.8)), 6),
                c.get("provenance", "regex"),
            ]
            for c in cites
        ),
    }
    return _digest(payload)


def compute_graph_fingerprint(graph) -> str:
    """从已建好的图反算指纹（不含源文件），用于校验 pkl 与 manifest 是否同一版本。"""
    nodes = []
    for nid, data in graph.nodes(data=True):
        nodes.append(
            [
                nid,
                data.get("label", ""),
                data.get("name", ""),
                sorted(data.get("aliases", []) or []) if isinstance(data.get("aliases"), list) else data.get("aliases", ""),
                data.get("content", ""),
                data.get("chapter_path", ""),
            ]
        )
    edges = []
    for u, v, key, data in graph.edges(keys=True, data=True):
        edges.append(
            [
                u,
                v,
                data.get("relation", key),
                data.get("provenance", ""),
                round(float(data.get("confidence", data.get("weight", 1.0)) or 1.0), 6),
                round(float(data.get("score", 0.0) or 0.0), 6),
                data.get("cue") or "",
                int(data.get("count", 1) or 1),
            ]
        )
    return _digest({"schema_version": SCHEMA_VERSION, "nodes": sorted(nodes), "edges": sorted(edges)})


def build_manifest(
    *,
    content_hash: str,
    stats: dict[str, Any],
    source_files: dict[str, int] | None = None,
) -> dict[str, Any]:
    return {
        "graph_build_id": f"g{content_hash[:12]}",
        "content_hash": content_hash,
        "schema_version": SCHEMA_VERSION,
        "extractor_schema_version": EXTRACTOR_SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "nodes": stats.get("nodes"),
        "edges": stats.get("edges"),
        "labels": stats.get("labels"),
        "relationships": stats.get("relationships"),
        "source_counts": source_files or {},
    }


def manifest_path(graph_dir: Path | str) -> Path:
    return Path(graph_dir) / MANIFEST_NAME


def read_manifest(graph_dir: Path | str | None = None) -> dict[str, Any] | None:
    if graph_dir is None:
        from config import settings

        graph_dir = settings.graph_dir
    p = manifest_path(graph_dir)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_manifest(manifest: dict[str, Any], graph_dir: Path | str | None = None) -> Path:
    from common.path_utils import write_json

    if graph_dir is None:
        from config import settings

        graph_dir = settings.graph_dir
    p = manifest_path(graph_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(p, manifest)
    return p


def manifest_diff(manifest: dict[str, Any] | None, graph_stats: dict[str, Any]) -> dict[str, Any]:
    """manifest 与实际图统计的差异；空 dict 表示一致。"""
    if not manifest:
        return {"manifest": "missing"}
    out: dict[str, Any] = {}
    for key in ("nodes", "edges"):
        if manifest.get(key) != graph_stats.get(key):
            out[key] = {"manifest": manifest.get(key), "actual": graph_stats.get(key)}
    if graph_stats.get("graph_build_id") and manifest.get("graph_build_id") != graph_stats["graph_build_id"]:
        out["graph_build_id"] = {
            "manifest": manifest.get("graph_build_id"),
            "actual": graph_stats["graph_build_id"],
        }
    return out
