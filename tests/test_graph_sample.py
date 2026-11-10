"""图查询 50 条抽检（slow）。

**这是自检不是精度**。它只回答一个问题：金标里的场景 -> 概念 -> 条文在图上是不是真查得到、
概念链是不是真连通。它不证明检索效果，也不替代 B3 的 R@5 评测；任何精度数字以
``data/eval_out/`` 里的评测产物为准。

标记 ``slow`` 的原因：要先加载离线图镜像（必要时现场构建），不适合放进日常快跑。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__003__graph.__003__graph_store import get_graph_store  # noqa: E402
from common.path_utils import read_jsonl  # noqa: E402

pytestmark = pytest.mark.slow

N_SAMPLE = 50
ARTICLE_PREFIXES = ("lcl#", "ll#", "cc#")

# gold 自身判为非必要、且正文无概念字面名的「相关但非定义」锁定条文。
# 它们不要求 DEFINES 边；若未来 gold 改判为必要，这里会因不在预期集合而报错。
KNOWN_RELEVANCE_ONLY = {
    ("g26", "lease_contract", "cc#733"),
    ("g45", "lease_contract", "cc#733"),
}


def _pick(row: dict, *keys: str) -> Any:
    for key in keys:
        if key in row:
            return row[key]
    return None


def _sample_gold() -> list[dict]:
    """等步长抽 50 条（gold_60 共 60 条），顺序稳定、不依赖随机种子。"""
    rows = read_jsonl("data/gold/gold_60.jsonl")
    if len(rows) <= N_SAMPLE:
        return rows
    step = len(rows) / N_SAMPLE
    return [rows[int(i * step)] for i in range(N_SAMPLE)]


def _concept_ids(row: dict) -> list[str]:
    return [p for p in row["expected_path"] if not p.startswith("sc:") and not p.startswith(ARTICLE_PREFIXES)]


def test_graph_sample_50_queries_reachable() -> None:
    store = get_graph_store()
    assert store.stats().get("nodes", 0) > 0

    rows = _sample_gold()
    assert len(rows) == N_SAMPLE, f"抽样应为 {N_SAMPLE} 条，实际 {len(rows)}"

    failures: list[str] = []
    relevance_only: set[tuple[str, str, str]] = set()
    for row in rows:
        rid = row["id"]

        # 1) 场景 -> 首个概念
        scenario_concepts = {_pick(r, "concept_id", "id") for r in store.scenario_concepts([row["scenario_id"]])}
        concepts = _concept_ids(row)
        if not concepts or concepts[0] not in scenario_concepts:
            failures.append(f"{rid}: {row['scenario_id']} -> {concepts[0] if concepts else '<空>'}")

        # 2) 概念 -> 锁定条文
        # 注意：concept_articles 是「该概念在这道题里锁定的代表条文」，不等于 DEFINES 边。
        # 若 gold 自身把该条文判为非必要（necessity_by_article=False），要求图上存在
        # DEFINES 边就是错误断言（典型：g26/g45 的 lease_contract -> cc#733，733 讲的是
        # 租赁期限届满返还租赁物，正文无「租赁合同」字面，三票均判非必要）。
        # 因此这里只对「gold 判为必要」的锁定条文要求图边；非必要的记录为 relevance-only，
        # 并校验它确实被 gold 标为非必要，避免把真正的丢边悄悄放过。
        mapping = row.get("concept_articles") or {
            c["id"]: a for c, a in zip(row["concepts"], row["expected_articles"], strict=False)
        }
        necessity = (row.get("validation") or {}).get("necessity_by_article") or {}
        for cid, aid in mapping.items():
            got = {r["article_id"] for r in store.concept_articles([cid], limit=100)}
            if aid in got:
                continue
            if necessity.get(aid) is False:
                relevance_only.add((rid, cid, aid))
                continue
            failures.append(f"{rid}: {cid} -> {aid}")

        # 3) 概念链连通（REQUIRES / SPECIAL_OF，任一方向）
        for left, right in zip(concepts, concepts[1:], strict=False):
            linked = False
            for direction in ("out", "in"):
                neighbors = {
                    _pick(r, "concept_id", "id", "target", "source")
                    for r in store.concept_neighbors(
                        [left], ["REQUIRES", "SPECIAL_OF"], direction=direction, limit=200
                    )
                }
                if right in neighbors:
                    linked = True
                    break
            if not linked:
                failures.append(f"{rid}: {left} -> {right}")

    unexpected = relevance_only - KNOWN_RELEVANCE_ONLY
    assert not unexpected, f"出现未登记的非定义锁定条文（需人工确认）：{sorted(unexpected)}"
    assert not failures, f"图抽检失败（共 {len(failures)} 处）：\n  - " + "\n  - ".join(failures[:15])
