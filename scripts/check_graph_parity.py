"""图一致性检查（LW-N1）：同一份建图输入在 NetworkX 与 Neo4j 上必须给出同一批结果。

检查三层：
1. **版本层**：``data/graph/graph_manifest.json`` 与离线镜像的实际统计是否一致；
2. **语义层**：六类 :class:`QueryPlan` 在两种后端上的返回行是否一致（先去噪排序再比较）；
3. **检测器自检**：人为构造一条差异边，确认比较器会失败（防止「永远 0 差异」的假绿）。

Neo4j 未启动时默认 skipped 并照常比较版本层；加 ``--require-neo4j`` 则整体失败。
输出机器可读 JSON，便于 CI 与文档引用。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__003__graph.__000__schema import QueryPlan  # noqa: E402
from app.__003__graph.__001__networkx_store import NetworkXGraphStore  # noqa: E402
from app.__003__graph.__003__graph_store import Neo4jGraphStore  # noqa: E402
from app.__003__graph.__005__version import read_manifest  # noqa: E402
from common.eval_meta import graph_build_id  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from common.path_utils import write_json  # noqa: E402

log = get_logger("graph_parity")

PLANS: list[QueryPlan] = [
    QueryPlan("concept_articles", {"concept_ids": ["labor_contract", "lease_contract", "network_tort"], "limit": 8}),
    QueryPlan("scenario_concepts", {"scenario_ids": ["unpaid_wages", "lease_dispute", "labor_dispatch"]}),
    QueryPlan(
        "concept_neighbors",
        {"concept_ids": ["labor_relationship", "labor_dispatch"], "relations": ["REQUIRES", "SPECIAL_OF"], "limit": 20},
    ),
    QueryPlan("article_cites", {"article_ids": ["lcl#14", "lcl#25", "cc#577"]}),
    QueryPlan("concept_scenarios", {"concept_ids": ["labor_dispatch", "annual_leave"], "limit": 10}),
    QueryPlan("article_detail", {"ids": ["lcl#10", "cc#733", "cc#577"]}),
]

ROUND = 4


def _r(x: Any) -> Any:
    return round(float(x), ROUND) if isinstance(x, (int, float)) else x


def normalise(plan_name: str, rows: list[dict]) -> list[dict]:
    """把两种后端的行规范化成可比较的形态：字段取交集、浮点定长、按确定规则排序。"""
    out: list[dict] = []
    for row in rows:
        item = {k: (sorted(v) if isinstance(v, list) else _r(v)) for k, v in row.items()}
        out.append(item)
    key = {
        "concept_articles": lambda r: (r.get("article_id", ""),),
        "scenario_concepts": lambda r: (r.get("scenario_id", ""), r.get("concept_id", "")),
        "concept_neighbors": lambda r: (r.get("source", ""), r.get("target", ""), r.get("relation", "")),
        "article_cites": lambda r: (r.get("source", ""), r.get("target", "")),
        "concept_scenarios": lambda r: (r.get("scenario_id", ""), r.get("concept_id", "")),
        "article_detail": lambda r: (r.get("id", ""),),
    }[plan_name]
    out.sort(key=key)
    return out


def diff_rows(plan_name: str, left: list[dict], right: list[dict]) -> dict[str, Any]:
    a = normalise(plan_name, left)
    b = normalise(plan_name, right)
    if a == b:
        return {}
    only_a = [r for r in a if r not in b]
    only_b = [r for r in b if r not in a]
    return {
        "n_left": len(a),
        "n_right": len(b),
        "only_left": only_a[:5],
        "only_right": only_b[:5],
    }


def compare_backends(nx_store: NetworkXGraphStore, other: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for plan in PLANS:
        left = nx_store.execute(plan)
        right = other.execute(plan)
        d = diff_rows(plan.name, left, right)
        out[plan.name] = {"ok": not d, **({"diff": d} if d else {"rows": len(left)})}
    return out


def selftest_detector(nx_store: NetworkXGraphStore, plans: list[QueryPlan] | None = None) -> dict[str, Any]:
    """对每条计划人为改一行，比较器必须报出差异；无返回行的计划单列，不算检出。"""
    detected: list[str] = []
    no_rows: list[str] = []
    missed: list[str] = []
    for plan in plans or PLANS:
        rows = nx_store.execute(plan)
        if not rows:
            no_rows.append(plan.name)
            continue
        tampered = json.loads(json.dumps(rows))
        tampered[0]["__injected__"] = "diff-edge"
        if diff_rows(plan.name, rows, tampered):
            detected.append(plan.name)
        else:
            missed.append(plan.name)
    return {"detected": detected, "no_rows": no_rows, "missed": missed, "ok": not missed}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/eval_out/graph_parity.json")
    ap.add_argument("--require-neo4j", action="store_true", help="Neo4j 不可用/版本不一致时直接失败")
    args = ap.parse_args()

    from config import settings

    pkl = settings.graph_dir / "lexweave_graph.pkl"
    manifest = read_manifest(settings.graph_dir)
    nx_store = NetworkXGraphStore(pkl)
    nx_stats = nx_store.stats()

    version_diff: dict[str, Any] = {}
    if not manifest:
        version_diff["manifest"] = "missing"
    else:
        for k in ("nodes", "edges"):
            if manifest.get(k) != nx_stats.get(k):
                version_diff[k] = {"manifest": manifest.get(k), "actual": nx_stats.get(k)}
        if manifest.get("graph_build_id") != nx_stats.get("graph_build_id"):
            version_diff["graph_build_id"] = {
                "manifest": manifest.get("graph_build_id"),
                "actual": nx_stats.get("graph_build_id"),
            }

    report: dict[str, Any] = {
        "graph_build_id": graph_build_id(),
        "manifest_present": bool(manifest),
        "version_diff": version_diff,
        "networkx": {k: nx_stats.get(k) for k in ("nodes", "edges", "labels", "relationships")},
        "detector_selftest": selftest_detector(nx_store),
        "neo4j": {"status": "skipped"},
        "plans": {},
    }

    neo4j_ok = False
    try:
        neo = Neo4jGraphStore()
        neo4j_ok = True
        if manifest and neo.graph_build_id != manifest.get("graph_build_id"):
            report["neo4j"] = {
                "status": "version_mismatch",
                "graph_build_id": neo.graph_build_id,
                "expected": manifest.get("graph_build_id"),
            }
            if args.require_neo4j:
                neo4j_ok = False
        else:
            report["neo4j"] = {"status": "ok", "graph_build_id": neo.graph_build_id, "stats": {k: neo.stats().get(k) for k in ("nodes", "edges", "labels", "relationships")}}
            report["plans"] = compare_backends(nx_store, neo)
    except Exception as exc:
        report["neo4j"] = {"status": "unavailable", "reason": str(exc)[:200]}
        if args.require_neo4j:
            neo4j_ok = False

    detector_ok = report["detector_selftest"]["ok"]
    plans_ok = all(v["ok"] for v in report["plans"].values()) if report["plans"] else True
    report["ok"] = not version_diff and detector_ok and plans_ok and (neo4j_ok or not args.require_neo4j)
    report["checks"] = {
        "manifest_consistent": not version_diff,
        "detector_detects_injected_diff": detector_ok,
        "backends_consistent": plans_ok,
        "neo4j_required_met": neo4j_ok or not args.require_neo4j,
    }
    write_json(Path(args.out), report)

    print(json.dumps({k: report[k] for k in ("graph_build_id", "version_diff", "neo4j", "checks", "ok")}, ensure_ascii=False, indent=1))
    # 汇总到终端，失败时给出定位信息
    if version_diff:
        log.error("manifest 与实际图不一致: %s", version_diff)
    bad = [name for name, v in report["plans"].items() if not v["ok"]]
    if bad:
        log.error("后端结果不一致的计划: %s", bad)
    if not detector_ok:
        log.error("比较器未能检出人为注入的差异边: %s", report["detector_selftest"])
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
