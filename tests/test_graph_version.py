"""图版本、manifest 与一致性比较器的离线单测（不依赖 Neo4j / 模型）。"""

from __future__ import annotations

import json
import pickle
from pathlib import Path

import pytest

from app.__003__graph.__001__networkx_store import NetworkXGraphStore, build_and_save
from app.__003__graph.__005__version import compute_content_hash, read_manifest, write_manifest
from scripts.check_graph_parity import diff_rows, selftest_detector


def _inputs():
    articles = [
        {
            "article_id": "lcl#10",
            "law_name": "劳动合同法",
            "article_no": "第十条",
            "article_num": 10,
            "chapter_path": "第二章 劳动合同的订立",
            "content": "建立劳动关系，应当订立书面劳动合同。",
            "citation": "劳动合同法第十条",
            "chars": 20,
        },
        {
            "article_id": "cc#733",
            "law_name": "民法典",
            "article_no": "第七百三十三条",
            "article_num": 733,
            "chapter_path": "第十四章 租赁合同",
            "content": "租赁期限届满，承租人应当返还租赁物。",
            "citation": "民法典第七百三十三条",
            "chars": 18,
        },
    ]
    concepts = [
        {
            "id": "written_form",
            "name": "书面形式",
            "aliases": ["书面合同"],
            "type": "制度",
            "laws": ["劳动合同法"],
            "parent": None,
        }
    ]
    scenarios = [
        {
            "id": "contract",
            "name": "未签书面合同",
            "aliases": [],
            "description": "",
            "concepts": ["written_form"],
        }
    ]
    mentions = [
        {
            "article_id": "lcl#10",
            "concept_id": "written_form",
            "edge_type": "DEFINES",
            "provenance": "rule",
            "confidence": 0.9,
            "defines_score": 1.0,
            "cue": "应当",
            "count": 1,
            "aliases": ["书面劳动合同"],
        }
    ]
    return articles, concepts, scenarios, mentions


def _write_inputs(root: Path, mentions=None) -> tuple[Path, Path]:
    articles, concepts, scenarios, base_mentions = _inputs()
    extracted = root / "extracted"
    lexicon = root / "lexicon"
    extracted.mkdir(parents=True)
    lexicon.mkdir(parents=True)

    def dump(path: Path, rows: list[dict]) -> None:
        path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    dump(extracted / "articles.jsonl", articles)
    dump(extracted / "mentions_typed.jsonl", base_mentions if mentions is None else mentions)
    dump(extracted / "cites.jsonl", [])
    dump(lexicon / "concepts.jsonl", concepts)
    dump(lexicon / "scenarios.jsonl", scenarios)
    dump(lexicon / "requires.jsonl", [])
    dump(lexicon / "special_of.jsonl", [])
    return extracted, lexicon


def test_content_hash_is_deterministic_and_sensitive():
    articles, concepts, scenarios, mentions = _inputs()
    h1 = compute_content_hash(articles, concepts, scenarios, [], [], mentions, [])
    h2 = compute_content_hash(articles, concepts, scenarios, [], [], mentions, [])
    assert h1 == h2

    changed = [dict(articles[0], content=articles[0]["content"] + "（修订）"), articles[1]]
    h3 = compute_content_hash(changed, concepts, scenarios, [], [], mentions, [])
    assert h3 != h1


def test_build_writes_manifest_and_store_matches(tmp_path):
    extracted, lexicon = _write_inputs(tmp_path)
    out = tmp_path / "graph" / "lexweave_graph.pkl"
    stats = build_and_save(extracted_dir=extracted, lexicon_dir=lexicon, out_path=out)

    manifest = read_manifest(out.parent)
    assert manifest is not None
    assert manifest["graph_build_id"] == stats["graph_build_id"]
    assert manifest["nodes"] == stats["nodes"]
    assert manifest["edges"] == stats["edges"]

    store = NetworkXGraphStore(out)
    assert store.graph_build_id == manifest["graph_build_id"]
    assert store.degraded_reason is None
    assert store.stats()["graph_build_id"] == manifest["graph_build_id"]


def test_store_flags_manifest_mismatch(tmp_path):
    extracted, lexicon = _write_inputs(tmp_path)
    out = tmp_path / "graph" / "lexweave_graph.pkl"
    build_and_save(extracted_dir=extracted, lexicon_dir=lexicon, out_path=out)

    manifest = read_manifest(out.parent)
    manifest["graph_build_id"] = "gdeadbeef0000"
    write_manifest(manifest, out.parent)

    store = NetworkXGraphStore(out)
    assert store.degraded_reason and "manifest_mismatch" in store.degraded_reason


def test_store_without_manifest_is_explicitly_degraded(tmp_path):
    extracted, lexicon = _write_inputs(tmp_path)
    out = tmp_path / "graph" / "lexweave_graph.pkl"
    build_and_save(extracted_dir=extracted, lexicon_dir=lexicon, out_path=out)
    (out.parent / "graph_manifest.json").unlink()

    store = NetworkXGraphStore(out)
    assert store.degraded_reason == "networkx_manifest_missing"


def test_parity_detector_flags_injected_row():
    rows = [{"source": "a", "target": "b", "relation": "REQUIRES", "confidence": 0.9}]
    tampered = [dict(rows[0]), {"source": "a", "target": "c", "relation": "REQUIRES", "confidence": 0.9}]
    assert diff_rows("concept_neighbors", rows, tampered)
    # 只改浮点尾数也要算差异（防止四舍五入掩盖）
    near = [dict(rows[0], confidence=0.9001)]
    assert diff_rows("concept_neighbors", rows, near)


def test_parity_selftest_detects_every_plan(tmp_path):
    extracted, lexicon = _write_inputs(tmp_path)
    out = tmp_path / "graph" / "lexweave_graph.pkl"
    build_and_save(extracted_dir=extracted, lexicon_dir=lexicon, out_path=out)
    store = NetworkXGraphStore(out)
    plans = [_plan(name) for name in ("concept_articles", "scenario_concepts", "concept_neighbors", "article_cites", "concept_scenarios", "article_detail")]
    result = selftest_detector(store, plans)
    # 有返回行的计划必须全部被检出；没有行的计划单列，不算「检出成功」
    assert result["ok"], result
    assert result["detected"], result


def _plan(name: str):
    from app.__003__graph.__000__schema import QueryPlan

    params = {
        "concept_articles": {"concept_ids": ["written_form"], "limit": 8},
        "scenario_concepts": {"scenario_ids": ["contract"]},
        "concept_neighbors": {"concept_ids": ["written_form"], "relations": ["REQUIRES"], "limit": 5},
        "article_cites": {"article_ids": ["lcl#10"]},
        "concept_scenarios": {"concept_ids": ["written_form"], "limit": 5},
        "article_detail": {"ids": ["lcl#10"]},
    }[name]
    return QueryPlan(name, params)


def test_pkl_is_readable_and_stats_consistent(tmp_path):
    extracted, lexicon = _write_inputs(tmp_path)
    out = tmp_path / "graph" / "lexweave_graph.pkl"
    build_and_save(extracted_dir=extracted, lexicon_dir=lexicon, out_path=out)
    with open(out, "rb") as f:
        g = pickle.load(f)
    assert g.graph["graph_build_id"].startswith("g")
    assert g.graph["schema_version"] == 2
    assert g.graph["graph_fingerprint"]


@pytest.mark.parametrize("plan_name", ["concept_articles", "scenario_concepts", "concept_neighbors", "article_cites", "concept_scenarios", "article_detail"])
def test_every_plan_is_executable(tmp_path, plan_name):
    extracted, lexicon = _write_inputs(tmp_path)
    out = tmp_path / "graph" / "lexweave_graph.pkl"
    build_and_save(extracted_dir=extracted, lexicon_dir=lexicon, out_path=out)
    rows = NetworkXGraphStore(out).execute(_plan(plan_name))
    assert isinstance(rows, list)
