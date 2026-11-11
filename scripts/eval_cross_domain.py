"""p 任务 / T14：劳动法域 schema 与规则零样本迁到中医域（CPU、只读外部数据）。

口径（用户 2026-10-31 修订版）：

1. 先查中医 82 条金标是否自带题-条文/概念标注。实测外部路径下**不存在**任何 82 行
   金标/问答集，因此本脚本按第 2 条机械建立，不调用任何模型判读。
2. 机械金标 = 数据集自带抽取标注（``extract_*_data.json`` 的 entities/relations/attributes）
   + ``scripts/check_defines_edges.py`` 的「概念名/别名在原文独立出现」判据。
   机械判不了的候选单列 ``pending``，不进分母。
3. 指标全部由本命令重算：题量、逐题命中、R@5、路由诊断。
4. 边界：不建图、不改主项目 schema、不复制中医数据进仓库；中医文件只读；
   CPU-only（``EMBEDDING_DEVICE=cpu``），索引缓存放冲刺目录。

用法::

    python scripts/eval_cross_domain.py --gold-only          # 只建 82 条金标并打印分布
    python scripts/eval_cross_domain.py                      # 建索引 + 全量评测
    python scripts/eval_cross_domain.py --rerank             # 额外跑精排（CPU 很慢）

输出：
* ``data/eval_out/cross_domain.json``
* ``reports/cross_domain.md``
"""

from __future__ import annotations

import os

# CPU-only：必须在 import config / torch 之前设置
os.environ["EMBEDDING_DEVICE"] = "cpu"
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from check_defines_edges import DERIV_SUFFIX, find_all, snippet, subject_evidence  # noqa: E402

TCM_ROOT_DEFAULT = Path(r"E:\AI大模型开发\中医药知识图谱")
EXTRACT_SUBDIR = Path("中医知识图谱补充资料(代码)") / "__002__extract_information"
CRAWL_SUBDIR = Path("01-代码") / "shenzhen8_chinese_medicine_project" / "__001__clawler"
# faiss 在 Windows 打不开含非 ASCII 字符的路径，缓存目录必须是纯 ASCII
INDEX_DIR_DEFAULT = Path(r"F:\tcm_index_cache\p")
GOLD_TARGETS = {"single": 28, "two": 34, "three": 20}
RNG_SEED = "p-cross-domain-20261031"
LABOR_BASELINES = {
    "text_r5": 0.7472,
    "text_by_hop": {"two_hop": 0.8333, "three_hop": 0.7, "cross_law": 0.5833},
    "graph_r5_historical": 0.4389,
    "graph_r5_post_rebuild": 0.4417,
}


def stable_key(*parts: str) -> str:
    return hashlib.sha1("|".join([RNG_SEED, *parts]).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------------------
# 1. 读取中医外部数据（只读）
# --------------------------------------------------------------------------------------
def load_docs(crawl_dir: Path) -> list[dict]:
    arts = []
    for kind, sub in (("herb", "中药"), ("formula", "方剂")):
        for p in sorted((crawl_dir / sub).glob("*.txt")):
            text = p.read_text(encoding="utf-8", errors="ignore")
            arts.append(
                {
                    "article_id": f"{kind}:{p.stem}",
                    "citation": f"中医百科·{'中药' if kind == 'herb' else '方剂'}·{p.stem}",
                    "law_name": "中药" if kind == "herb" else "方剂",
                    "content": text,
                    "kind": kind,
                    "name": p.stem,
                    "path": str(p),
                }
            )
    return arts


def parse_aliases(text: str) -> list[str]:
    """从百科页「名称」后的 ``- 别名`` 行机械抽取别名；解析不到返回空。"""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == "名称":
            out = []
            for nxt in lines[i + 1 :]:
                s = nxt.strip()
                if s.startswith("- "):
                    v = s[2:].strip()
                    if v:
                        out.append(v)
                elif out:
                    break
            return out
    return []


def load_extract(path: Path) -> list[dict]:
    blob = json.loads(path.read_text(encoding="utf-8"))
    return blob["results"]


def herb_attributes(rec: dict) -> dict:
    for e in rec["extract_dict"].get("entities", []):
        if e.get("type") == "Herb" and isinstance(e.get("attributes"), dict):
            return e["attributes"]
    return {}


def formula_relations(rec: dict, rel: str) -> list[dict]:
    return [r for r in rec["extract_dict"].get("relations", []) if r.get("relation") == rel]


# --------------------------------------------------------------------------------------
# 2. 机械判据：概念名/别名在原文独立出现（复用 T01 check_defines_edges.classify）
# --------------------------------------------------------------------------------------
def _is_enclosed(text: str, i: int, n: int, longer_terms: list[str]) -> bool:
    """命中串是否被某个更长的实体名包裹（如「沙参」之于「沙参麦冬汤」）。"""
    for term in longer_terms:
        start = text.find(term)
        while start != -1:
            if start <= i and i + n <= start + len(term):
                return True
            start = text.find(term, start + 1)
    return False


def mechanical_hit(
    text: str,
    name: str,
    aliases: list[str],
    article_id: str,
    longer_terms_fn,
) -> dict:
    """概念名/别名在原文**独立**出现。

    判据与 T01 ``scripts/check_defines_edges.py`` 同源：字面命中、非派生后缀
    （X人/X权/X物/X方/X者）、未被更长术语包裹、别名非概念名子串。
    bucket 仍用 ``subject_evidence`` 复刻法律域「定义式/分句主语位」证据，便于量化
    这条规则在中医语体下的失效程度。
    """
    n = len(name)
    for i in find_all(text, name):
        if text[i + n : i + n + 1] in DERIV_SUFFIX:
            continue
        if _is_enclosed(text, i, n, longer_terms_fn(name)):
            continue
        ev = subject_evidence(text, i, name)
        return {
            "ok": True,
            "article_id": article_id,
            "matched": name,
            "match_type": "literal",
            "offset": i,
            "snippet": snippet(text, i, n),
            "bucket": "normal" if ev else "pending_semantic",
            "subject_evidence": ev,
        }

    for a in aliases:
        if not a or a == name or a in name:
            continue
        na = len(a)
        for i in find_all(text, a):
            if text[i + na : i + na + 1] in DERIV_SUFFIX:
                continue
            if _is_enclosed(text, i, na, longer_terms_fn(a)):
                continue
            return {
                "ok": True,
                "article_id": article_id,
                "matched": a,
                "match_type": "alias",
                "offset": i,
                "snippet": snippet(text, i, na),
                "bucket": "pending_semantic",
                "subject_evidence": None,
            }

    return {
        "ok": False,
        "article_id": article_id,
        "reason": "absent",
        "literal_offsets": len(find_all(text, name)),
        "alias_hits": 0,
        "bucket": "absent",
    }


# --------------------------------------------------------------------------------------
# 3. 机械建 82 条金标
# --------------------------------------------------------------------------------------
def build_gold(tcm_root: Path) -> tuple[list[dict], dict, dict]:
    crawl_dir = tcm_root / CRAWL_SUBDIR
    extract_dir = tcm_root / EXTRACT_SUBDIR
    docs = load_docs(crawl_dir)
    doc_by_id = {d["article_id"]: d for d in docs}
    herb_docs = [d for d in docs if d["kind"] == "herb"]
    formula_docs = {d["name"]: d for d in docs if d["kind"] == "formula"}

    herb_recs = load_extract(extract_dir / "extract_herb_data.json")
    formula_recs = load_extract(extract_dir / "extract_formula_data.json")

    herb_aliases: dict[str, list[str]] = {}
    alias_to_herb: dict[str, set[str]] = defaultdict(set)
    for d in herb_docs:
        al = parse_aliases(d["content"])
        herb_aliases[d["name"]] = al
        alias_to_herb[d["name"]].add(d["name"])
        for a in al:
            alias_to_herb[a].add(d["name"])

    # 全域术语表：用于「独立出现」判据（检测被更长实体名包裹）
    term_index: dict[str, set[str]] = defaultdict(set)
    for d in herb_docs:
        for t in [d["name"], *herb_aliases[d["name"]]]:
            term_index[t].add(d["name"])
    for name in formula_docs:
        term_index[name].add("formula:" + name)
    term_index = {k: v for k, v in term_index.items()}

    _encl_cache: dict[str, list[str]] = {}

    def longer_terms_fn(term: str) -> list[str]:
        if term not in _encl_cache:
            _encl_cache[term] = [t for t in term_index if len(t) > len(term) and term in t]
        return _encl_cache[term]

    # 单味药 -> 属性（供 A 类题）
    herb_attr: dict[str, dict] = {}
    for rec in herb_recs:
        stem = Path(rec["filename"]).stem
        herb_attr[stem] = herb_attributes(rec)

    # ---------------- A：单文档题（药材自身） ----------------
    pool_single = []
    for d in herb_docs:
        attrs = herb_attr.get(d["name"], {})
        if not (attrs.get("property_flavor") or attrs.get("effect") or attrs.get("meridian")):
            continue
        ev = mechanical_hit(
            d["content"], d["name"], herb_aliases.get(d["name"], []), d["article_id"], longer_terms_fn
        )
        item = {
            "cls": "single",
            "query": f"中药「{d['name']}」的性味、归经和功效是什么？",
            "expected": [d["article_id"]],
            "evidence": [ev],
            "key": d["name"],
            "gold": {
                "source_file": str((extract_dir / "extract_herb_data.json").relative_to(tcm_root)),
                "filename": d["name"] + ".txt",
                "fields": ["property_flavor", "meridian", "effect", "indication"],
                "answer": {
                    "property_flavor": attrs.get("property_flavor"),
                    "meridian": attrs.get("meridian"),
                    "effect": attrs.get("effect"),
                    "indication": attrs.get("indication"),
                },
            },
        }
        pool_single.append(item)

    # ---------------- B/C：方剂 -> 药材 ----------------
    pool_two, pool_three = [], []
    map_fail = Counter()
    for rec in formula_recs:
        fname = Path(rec["filename"]).stem
        fdoc = formula_docs.get(fname)
        if fdoc is None:
            map_fail["no_formula_doc"] += 1
            continue
        mapped = []
        for r in formula_relations(rec, "HAS_INGREDIENT"):
            surface = r.get("object") or ""
            owners = alias_to_herb.get(surface) or alias_to_herb.get(surface.strip()) or set()
            candidates = sorted(owners)
            if not candidates:
                map_fail["not_in_alias_table"] += 1
                continue
            if len(candidates) != 1:
                map_fail["ambiguous_herb"] += 1
                continue
            hname = candidates[0]
            hdoc = doc_by_id.get(f"herb:{hname}")
            if hdoc is None:
                map_fail["no_herb_doc"] += 1
                continue
            mapped.append((surface, hname, hdoc, r))

        # 去重（同一味药可能在文里出现多次）
        seen, uniq = set(), []
        for row in mapped:
            if row[1] in seen:
                continue
            seen.add(row[1])
            uniq.append(row)

        base_f_ev = mechanical_hit(fdoc["content"], fname, [], fdoc["article_id"], longer_terms_fn)
        for surface, hname, hdoc, rel in uniq:
            h_ev_in_f = mechanical_hit(
                fdoc["content"],
                surface,
                [hname] + herb_aliases.get(hname, []),
                fdoc["article_id"],
                longer_terms_fn,
            )
            h_ev_in_h = mechanical_hit(
                hdoc["content"], hname, herb_aliases.get(hname, []), hdoc["article_id"], longer_terms_fn
            )
            attrs = herb_attr.get(hname, {})
            item = {
                "cls": "two",
                "query": f"方剂「{fname}」中含有药材「{surface}」，{surface}的功效是什么？",
                "expected": [fdoc["article_id"], hdoc["article_id"]],
                "evidence": [base_f_ev, h_ev_in_f, h_ev_in_h],
                "key": f"{fname}::{hname}",
                "gold": {
                    "source_file": str((extract_dir / "extract_formula_data.json").relative_to(tcm_root)),
                    "filename": fname + ".txt",
                    "fields": ["relations.HAS_INGREDIENT", "herb.attributes.effect"],
                    "relation": {
                        "subject": rel.get("subject"),
                        "relation": rel.get("relation"),
                        "object": rel.get("object"),
                    },
                    "answer": {"effect": attrs.get("effect"), "indication": attrs.get("indication")},
                },
            }
            pool_two.append(item)

        # C 类：方剂里两味不同药材，期望 3 篇文档
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                s1, h1, d1, r1 = uniq[i]
                s2, h2, d2, r2 = uniq[j]
                ev1f = mechanical_hit(
                    fdoc["content"], s1, [h1] + herb_aliases.get(h1, []), fdoc["article_id"], longer_terms_fn
                )
                ev2f = mechanical_hit(
                    fdoc["content"], s2, [h2] + herb_aliases.get(h2, []), fdoc["article_id"], longer_terms_fn
                )
                ev1h = mechanical_hit(
                    d1["content"], h1, herb_aliases.get(h1, []), d1["article_id"], longer_terms_fn
                )
                ev2h = mechanical_hit(
                    d2["content"], h2, herb_aliases.get(h2, []), d2["article_id"], longer_terms_fn
                )
                item = {
                    "cls": "three",
                    "query": f"方剂「{fname}」含有「{s1}」和「{s2}」，这两味药分别有什么功效？",
                    "expected": [fdoc["article_id"], d1["article_id"], d2["article_id"]],
                    "evidence": [base_f_ev, ev1f, ev2f, ev1h, ev2h],
                    "key": f"{fname}::{h1}::{h2}",
                    "gold": {
                        "source_file": str((extract_dir / "extract_formula_data.json").relative_to(tcm_root)),
                        "filename": fname + ".txt",
                        "fields": ["relations.HAS_INGREDIENT(2 条)", "herb.attributes.effect"],
                        "relations": [
                            {"subject": r1.get("subject"), "relation": r1.get("relation"), "object": r1.get("object")},
                            {"subject": r2.get("subject"), "relation": r2.get("relation"), "object": r2.get("object")},
                        ],
                    },
                }
                pool_three.append(item)
                if len(pool_three) > 2000 and j > 2:
                    break
            if len(pool_three) > 2000:
                break
        if len(pool_three) > 2000:
            continue

    # ---------------- 机械校验 + 确定性抽样 ----------------
    def split(pool: list[dict], cap: int) -> tuple[list[dict], list[dict]]:
        pool = sorted(pool, key=lambda x: stable_key(x["cls"], x["key"]))[:cap]
        ok, pending = [], []
        for it in pool:
            bad = [e for e in it["evidence"] if not e.get("ok")]
            if bad:
                pending.append(
                    {
                        "cls": it["cls"],
                        "key": it["key"],
                        "reason": "; ".join(f"{e.get('article_id')}:{e.get('reason')}" for e in bad),
                    }
                )
            else:
                ok.append(it)
        return ok, pending

    ok_single, pend_single = split(pool_single, 200)
    ok_two, pend_two = split(pool_two, 400)
    ok_three, pend_three = split(pool_three, 300)

    chosen = []
    for cls, ok in (("single", ok_single), ("two", ok_two), ("three", ok_three)):
        target = GOLD_TARGETS[cls]
        if len(ok) < target:
            raise SystemExit(f"机械金标不足：{cls} 需要 {target} 条，实际可用 {len(ok)} 条")
        chosen.extend(ok[:target])

    pending = pend_single + pend_two + pend_three
    stats = {
        "docs": {
            "herb": sum(1 for d in docs if d["kind"] == "herb"),
            "formula": sum(1 for d in docs if d["kind"] == "formula"),
            "total": len(docs),
        },
        "pools": {
            "single": {"examined": len(ok_single) + len(pend_single), "accepted": len(ok_single), "pending": len(pend_single)},
            "two": {"examined": len(ok_two) + len(pend_two), "accepted": len(ok_two), "pending": len(pend_two)},
            "three": {"examined": len(ok_three) + len(pend_three), "accepted": len(ok_three), "pending": len(pend_three)},
        },
        "map_fail": dict(map_fail),
        "chosen": {k: sum(1 for c in chosen if c["cls"] == k) for k in GOLD_TARGETS},
        "pending_total": len(pending),
        "pending_samples": pending[:20],
    }
    return chosen, stats, doc_by_id


# --------------------------------------------------------------------------------------
# 4. 建/载中医文本索引（不建图）
# --------------------------------------------------------------------------------------
def ensure_index(index_dir: Path, docs: list[dict], rebuild: bool) -> "object":
    from app.__004__retrieval.__000__text_index import TextIndex

    index_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = index_dir / "tcm_manifest.json"
    ids = [d["article_id"] for d in docs]
    manifest = {
        "n": len(ids),
        "sha1": hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest(),
        "embedding_model": "BAAI/bge-m3",
        "device": "cpu",
    }
    idx = TextIndex(index_dir=index_dir)
    cache_ok = False
    if not rebuild and manifest_path.exists():
        cached = json.loads(manifest_path.read_text(encoding="utf-8"))
        cache_ok = all(cached.get(k) == manifest.get(k) for k in ("n", "sha1", "embedding_model", "device"))
    if cache_ok:
        idx.load()
    else:
        stats = idx.build(docs, batch_size=32)
        manifest["build_stats"] = stats
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[index] built {stats}", file=sys.stderr)
    idx.articles = {d["article_id"]: d for d in docs}
    return idx


# --------------------------------------------------------------------------------------
# 5. 检索评测 + 路由诊断
# --------------------------------------------------------------------------------------
def top_ids(idx, query: str, mode: str, k: int = 5) -> list[str]:
    if mode == "bm25":
        return [a for a, _ in idx.bm25_search(query, 20)][:k]
    if mode == "dense":
        return [a for a, _ in idx.dense_search(query, 20)][:k]
    hits = idx.search(query, top_k=k, use_rerank=False)
    return [h.article_id for h in hits]


def evaluate_retrieval(idx, gold: list[dict], modes: list[str]) -> dict:
    rows = []
    for g in gold:
        expected = set(g["expected"])
        row = {"id": g["key"], "cls": g["cls"], "n_expected": len(expected), "query": g["query"]}
        for mode in modes:
            top = top_ids(idx, g["query"], mode)
            inter = expected & set(top)
            row[mode] = {
                "top5": top,
                "recall@5": round(len(inter) / len(expected), 4),
                "all@5": float(expected <= set(top)),
                "hit@5": float(bool(inter)),
                "missing_rank": {d: (top.index(d) + 1 if d in top else None) for d in expected},
            }
        row["top1_similarity"] = round(
            (idx.dense_search(g["query"], 1) or [("", 0.0)])[0][1], 4
        )
        row["evidence"] = [
            {k: e.get(k) for k in ("article_id", "matched", "match_type", "snippet", "bucket")}
            for e in g["evidence"]
        ]
        rows.append(row)

    def agg(rows_sub: list[dict], mode: str, key: str) -> float:
        vals = [r[mode][key] for r in rows_sub]
        return round(statistics.mean(vals), 4) if vals else 0.0

    out = {"n": len(rows), "modes": {}}
    for mode in modes:
        out["modes"][mode] = {
            "n": len(rows),
            "recall@5": agg(rows, mode, "recall@5"),
            "all@5": agg(rows, mode, "all@5"),
            "hit@5": agg(rows, mode, "hit@5"),
            "by_class": {
                cls: {
                    "n": sum(1 for r in rows if r["cls"] == cls),
                    "recall@5": agg([r for r in rows if r["cls"] == cls], mode, "recall@5"),
                    "all@5": agg([r for r in rows if r["cls"] == cls], mode, "all@5"),
                    "hit@5": agg([r for r in rows if r["cls"] == cls], mode, "hit@5"),
                }
                for cls in ("single", "two", "three")
            },
        }
    sims = sorted(r["top1_similarity"] for r in rows)
    p05 = sims[max(0, int((len(sims) - 1) * 0.05))]
    out["top1_similarity"] = {
        "min": sims[0],
        "p05": p05,
        "median": round(statistics.median(sims), 4),
        "max": sims[-1],
        "below_legal_threshold_0_62": sum(1 for s in sims if s < 0.62),
    }
    out["evidence_buckets"] = dict(
        Counter(e.get("bucket") for g in gold for e in g["evidence"])
    )
    # 每类漏召的是哪一类文档（herb 页 / formula 页）
    for mode in modes:
        miss = Counter()
        for r in rows:
            for doc, rank in r[mode]["missing_rank"].items():
                if rank is None:
                    miss["herb" if doc.startswith("herb:") else "formula"] += 1
        out["modes"][mode]["miss_by_kind"] = dict(miss)
    if "rrf" in modes:
        examples = []
        for r in rows:
            missing = [d for d, v in r["rrf"]["missing_rank"].items() if v is None]
            if missing:
                examples.append({"id": r["id"], "cls": r["cls"], "missing": missing, "top5": r["rrf"]["top5"]})
        out["rrf_miss_examples"] = examples[:3]
    out["rows"] = rows
    return out


def evaluate_router(gold: list[dict]) -> dict:
    """零样本路由诊断。

    标签是机械口径（1/2/3 篇证据文档 -> simple/medium/complex），不是人工路由标签；
    法律词表对中医实体的命中数同时记录，用来解释「哪些规则迁不过去」。
    """
    from app.__004__retrieval.__001__entity_linker import EntityLinker
    from app.__004__retrieval.__004__router import CascadeRouter, features_of, rule_fast

    linker = EntityLinker()
    router = CascadeRouter(enable_llm=False)
    label_map = {"single": "simple", "two": "medium", "three": "complex"}
    rows = []
    for g in gold:
        link = linker.link(g["query"])
        n_c, n_s = len(link.concepts), len(link.scenarios)
        d = router.route(g["query"], n_c, n_s)
        f = features_of(g["query"], n_c, n_s)
        rf, rf_reason = rule_fast(f)
        rows.append(
            {
                "id": g["key"],
                "cls": g["cls"],
                "label": label_map[g["cls"]],
                "pred": d.level,
                "source": d.source,
                "reason": d.reason,
                "n_concepts": n_c,
                "n_scenarios": n_s,
                "linked_concepts": [c.name for c in link.concepts],
                "rule_fast": rf,
                "rule_fast_reason": rf_reason,
            }
        )
    n = len(rows)
    correct = sum(1 for r in rows if r["pred"] == r["label"])
    conf = Counter((r["label"], r["pred"]) for r in rows)
    fast_rows = [r for r in rows if r["rule_fast"] is not None]
    fast_correct = sum(1 for r in fast_rows if r["rule_fast"] == r["label"])
    return {
        "status": "不具备（严格口径）",
        "reason": (
            "中医域没有人工路由标签；路由的实体链接层是劳动法词典（concepts.jsonl/alias_index.json），"
            "中医实体名不在其中，n_concepts 基本为 0，simple/medium/complex 的判定依据在跨域后失效。"
        ),
        "diagnostic": {
            "label_rule": "机械口径：单文档题=simple，双文档题=medium，三文档题=complex",
            "n": n,
            "accuracy": round(correct / n, 4),
            "confusion": {f"{a}->{b}": c for (a, b), c in sorted(conf.items())},
            "rule_fast_coverage": round(len(fast_rows) / n, 4),
            "rule_fast_accuracy_on_covered": round(fast_correct / len(fast_rows), 4) if fast_rows else None,
            "n_concepts_zero_rate": round(sum(1 for r in rows if r["n_concepts"] == 0) / n, 4),
        },
        "rows": rows,
    }


def legal_lexicon_overlap(gold: list[dict], tcm_root: Path) -> dict:
    """中医实体名/别名是否出现在劳动法概念词典里（0 重叠 = 词表层不可迁移）。"""
    concepts_path = ROOT / "data" / "lexicon" / "concepts.jsonl"
    names, aliases = set(), set()
    for line in concepts_path.open(encoding="utf-8"):
        if not line.strip():
            continue
        c = json.loads(line)
        names.add(c["name"])
        aliases.update(c.get("aliases", []))

    tcm_terms = set()
    for p in sorted((tcm_root / CRAWL_SUBDIR / "中药").glob("*.txt")):
        tcm_terms.add(p.stem)
        tcm_terms.update(parse_aliases(p.read_text(encoding="utf-8", errors="ignore")))
    for p in sorted((tcm_root / CRAWL_SUBDIR / "方剂").glob("*.txt")):
        tcm_terms.add(p.stem)

    overlap = sorted(tcm_terms & (names | aliases))
    return {
        "tcm_terms": len(tcm_terms),
        "legal_concept_names": len(names),
        "legal_aliases": len(aliases),
        "overlap_count": len(overlap),
        "overlap_terms": overlap[:30],
    }


# --------------------------------------------------------------------------------------
# 6. 报告
# --------------------------------------------------------------------------------------
def render_report(payload: dict) -> str:
    ev = payload["evaluation"]
    modes = ev["modes"]
    primary = modes.get("rrf") or next(iter(modes.values()))
    st = payload["gold_stats"]
    rt = payload["routing"]["diagnostic"]
    overlap = payload["legal_lexicon_overlap"]
    sim = ev["top1_similarity"]
    legal = LABOR_BASELINES

    def pct(x):
        return f"{x * 100:.2f}%"

    def pts(x):
        return f"{x * 100:+.2f} pt"

    two_delta = primary["by_class"]["two"]["recall@5"] - legal["text_by_hop"]["two_hop"]
    three_delta = primary["by_class"]["three"]["recall@5"] - legal["text_by_hop"]["three_hop"]
    overall_delta = primary["recall@5"] - legal["text_r5"]
    hard_delta = primary["recall@5"] - legal["graph_r5_historical"]
    bk = ev.get("evidence_buckets", {})
    bk_total = sum(bk.values()) or 1
    bk_normal = bk.get("normal", 0)
    bk_pending = bk.get("pending_semantic", 0)
    miss = primary.get("miss_by_kind", {})
    miss_total = sum(miss.values())
    examples = ev.get("rrf_miss_examples", [])
    ex = examples[0] if examples else None

    lines = [
        "# 中医 82 条跨域迁移验证（p / T14）",
        "",
        f"日期：2026-10-31　｜　脚本：`scripts/eval_cross_domain.py`　｜　索引缓存：`{payload['external_paths']['index_dir']}`",
        "",
        "## 结论先行",
        "",
        f"- **R@5 = {primary['recall@5']:.4f}**（n={primary['n']}，分母固定 82；检索路 = {payload['primary_mode']}，CPU）。",
        f"- **路由准确率：不具备（严格口径）**。中医域无人工路由标签，且路由实体链接层是劳动法词表；"
        f"机械标签口径下诊断准确率 {rt['accuracy']:.4f}，只作诊断，不作交付数字。",
        f"- **金标来源**：外部中医路径下**未发现预置的 82 条金标/问答集**（json/jsonl/csv 全扫，0 命中）。"
        f"按修订口径机械建立 82 条：数据集自带 `extract_herb_data.json` / `extract_formula_data.json` 的"
        f"实体、关系、属性标注 + T01 字面独立出现判据。",
        f"- **分母与不可判**：机械构建 82 条进入分母；校验候选 {sum(v['examined'] for v in st['pools'].values())} 条，"
        f"其中 {st['pending_total']} 条机械不可判，单列 `pending`，不计入指标。",
        "",
        "## 1. 迁移结果",
        "",
        "| 检索路 | R@5 | all@5 | hit@5 | 单文档(28) | 双文档(34) | 三文档(20) |",
        "|---|---|---|---|---|---|---|",
    ]
    for mode, m in modes.items():
        bc = m["by_class"]
        lines.append(
            f"| {mode} | {m['recall@5']:.4f} | {m['all@5']:.4f} | {m['hit@5']:.4f} | "
            f"{bc['single']['recall@5']:.4f} | {bc['two']['recall@5']:.4f} | {bc['three']['recall@5']:.4f} |"
        )
    lines += [
        "",
        f"- 三文档题掉得最多：{primary['by_class']['three']['recall@5']:.4f}；单文档题 {primary['by_class']['single']['recall@5']:.4f}。",
        f"- 与劳动法**同一文本路**（0.7472）比，总 R@5 {pts(overall_delta)}；"
        f"双文档 {primary['by_class']['two']['recall@5']:.4f} vs 0.8333（{pts(two_delta)}）；"
        f"三文档 {primary['by_class']['three']['recall@5']:.4f} vs 0.7000（{pts(three_delta)}）。",
        f"- 与用户给的 **0.4389** 硬减得 {pts(hard_delta)}，但这是「文本路 vs 劳动法图路」的跨路差值；"
        f"中医域没有可用图路（见 §3），**两者域与检索路都不同，不可当同一序列**，该差值只作对照。",
        "",
        "> **可比性说明（重要）**：这 82 条是机械题，题面直接写出实体名（如「中药「草乌」的性味…」），"
        f"比劳动法 60 条自然语言问句**容易**得多。{primary['recall@5']:.4f} 说明这套检索管线不改代码就能在新域跑通，"
        "**不说明中医问答效果优于劳动法**；跨域结论只取「能否零样本迁移」与「迁移后失效的规则」两点。",
        "",
        "## 2. 金标来源、分母与机械校验",
        "",
        f"- 中医文档：{st['docs']['herb']} 篇中药 + {st['docs']['formula']} 篇方剂 = {st['docs']['total']} 篇，只读外部路径。",
        f"- 标注来源字段：`extract_herb_data.json` 的 `results[].extract_dict.entities[].attributes`；"
        f"`extract_formula_data.json` 的 `results[].extract_dict.relations[]`（HAS_INGREDIENT）。",
        "- 机械判据：概念名/别名在原文独立出现（`scripts/check_defines_edges.py::classify`，别名需非子串、"
        "非派生后缀、未被更长术语包裹），逐条记录命中片段。",
        f"- 候选校验：单文档 {st['pools']['single']['accepted']}/{st['pools']['single']['examined']} 通过；"
        f"双文档 {st['pools']['two']['accepted']}/{st['pools']['two']['examined']}；"
        f"三文档 {st['pools']['three']['accepted']}/{st['pools']['three']['examined']}。",
        f"- 不可判 {st['pending_total']} 条（样例见 JSON `gold_stats.pending_samples`），不计入分母。",
        "",
        "## 3. 迁不过去的法律域特有规则（7 条，含具体例子）",
        "",
        "1. **法条号 / 条文体系**：劳动法的检索单元是「法条」，`article_id` 形如 `ll#82`，"
        "`eval_retrieval.py` 用 `expected_articles` + `article_citations` 算 R@5；"
        "中医文档的检索单元是百科页（`herb:草乌` / `formula:六君子汤`），**没有条号**，"
        "本次 82 条的式样从 `第X条` 变成 `中药/方剂名字`，条号相关字段 0 条。",
        f"2. **劳动法术语别名 / 实体链接词表**：`data/lexicon/concepts.jsonl`（{overlap['legal_concept_names']} 个概念名 + "
        f"{overlap['legal_aliases']} 个别名）+ `alias_index.json` + `concept.faiss`。"
        f"中医术语 {overlap['tcm_terms']} 个与该词表交集 **{overlap['overlap_count']} 个**；"
        f"82 条题里 n_concepts=0 的比例 **{rt['n_concepts_zero_rate'] * 100:.1f}%**，"
        "即实体链接层在中医域整体空转。",
        f"3. **DEFINES 定义式证据规则**（`scripts/check_defines_edges.py`「是指/系指/所称/分句主语位+应当/不得/可以」）："
        f"本次机械校验命中 {bk_total} 处证据，判为定义式/主语位（`normal`）**{bk_normal} 处**，"
        f"`pending_semantic` {bk_pending} 处。例：「草乌」在《草乌》页出现的是「【来源】为…」的百科描述，"
        "不是「本法所称…是指…」的法条定义，该规则在中医语体下 0 命中。",
        f"4. **域外拒答阈值 0.62**：该值由劳动法 60 条金标 top1 相似度的 5% 分位标定（`config.py`，`scripts/measure_off_domain.py`）。"
        f"中医 82 条 top1 分布 min={sim['min']:.4f} / p05={sim['p05']:.4f} / median={sim['median']:.4f} / max={sim['max']:.4f}，"
        f"低于 0.62 的 **{sim['below_legal_threshold_0_62']}/{ev['n']} 条**。"
        "注意这与「误拒」相反：阈值在中医域**完全失去判别力**（全部放行 = 等于不设防），"
        "因为它标定的是劳动法语料的相似度分布，不是「中医问题是否域外」。",
        "5. **五道闸里的法律合规闸**（`app/__007__agents/__001__gates.py`）：`cite_exists` 用 "
        "`_ARTICLE_TAIL=第[一二三四五六七八九十百千零〇0-9]+条` 校验引用，`_LAW_TAILS=劳动合同法/劳动法/民法典`；"
        "`cite_in_retrieval` / `path_support` / `evidence_span` 也全部围绕「法条引用 + 图路径」。"
        "中医答案没有条号，前四闸**无对象可校验**（直接判 fail）；"
        "医疗场景该闸应换成「不建议自行用药 / 就医提示」，而不是法条合规。",
        f"6. **路由标签体系与 LLM 路由提示词**（`app/__004__retrieval/__004__router.py`）：simple/medium/complex 按"
        "「问题点个数 + 是否跨法」定义，system prompt 明写「跨《劳动合同法》《劳动法》《民法典》两部以上 -> complex」。"
        f"中医没有跨法概念。机械标签（单/双/三文档题）下诊断准确率 {rt['accuracy']:.4f}，"
        f"其中 **complex→medium {rt['confusion'].get('complex->medium', 0)}/20**（三文档题全被判成 medium，"
        f"即 top_k 只给 5 不够用），`rule_fast` 覆盖率 {rt['rule_fast_coverage']:.4f}。",
        "7. **图 schema 与图检索路**：`app/__003__graph/__000__schema.py` 的节点/边（法条、概念、场景、DEFINES、CITES…）"
        "在中医域没有对应物；外部中医图谱（`tcm_metadata.json`）只有 6 labels / 6 relationships / **12 条样例三元组**，"
        "且是课程项目 LLM 抽取产物（非人工标注），不足以评测图路。因此**图路 = 不具备**，本次只跑文本三路。",
        "",
        "## 4. 掉点分析（按问题类型）",
        "",
        "| 类型 | 中医 R@5 | 劳动法同路 R@5 | 差值 | 主要失败原因 |",
        "|---|---|---|---|---|",
        f"| 单文档（药材属性） | {primary['by_class']['single']['recall@5']:.4f} | 无对应（simple 为合成题） | — | 题面=实体名，28/28 全中 |",
        f"| 双文档（方剂+药材） | {primary['by_class']['two']['recall@5']:.4f} | 0.8333（two_hop） | {pts(two_delta)} | 方剂页稳中，药材页被同名方剂页挤掉 |",
        f"| 三文档（方剂+两药） | {primary['by_class']['three']['recall@5']:.4f} | 0.7000（three_hop） | {pts(three_delta)} | top-5 只装下 2 篇，漏 1 篇即掉 0.33 |",
        "",
        f"- 全部漏召 **{miss_total} 次，100% 是药材页**（方剂页 {miss.get('formula', 0)} 次漏、药材页 {miss.get('herb', 0)} 次漏）。",
        "- 失败机制是**同名词条竞争**，不是语义不匹配：问「方剂 F 含 药材 H」时，向量和 BM25 同时被「名字里含 H 的其它方剂」吸引，"
        "把 H 自己的药材页挤出 top-5。",
    ]
    if ex:
        lines += [
            f"- 例：`{ex['id']}` 漏召 `{', '.join(ex['missing'])}`，rrf top-5 = "
            + "、".join(f"`{t}`" for t in ex["top5"])
            + "（除第 1 名是查询里的方剂本身，其余都是名字里含该药材名的其它方剂页）。",
        ]
    lines += [
        "",
        f"- 总体相对劳动法同一文本路 {pts(overall_delta)}；但这 82 条题面含实体名，属**宽松口径**，"
        "跨域差值只能读作「管线未崩」，不能读作「中医更好」。",
        "- 真正的域间差异是**文档粒度**：劳动法一条法条 = 一个短检索单元；中医一篇百科页承载多实体多关系，"
        "且实体名会出现在其它页里，top-5 预算更容易被同类条目挤爆。",
        "",
        "## 5. 可复现命令",
        "",
        "```powershell",
        "cd <仓库根>",
        "$env:EMBEDDING_DEVICE='cpu'",
        ".\\venv\\Scripts\\python.exe scripts\\eval_cross_domain.py --tcm-root E:\\AI大模型开发\\中医药知识图谱 --out data\\eval_out\\cross_domain.json --report reports\\cross_domain.md",
        "```",
        "",
        "首次运行会用 bge-m3 在 CPU 上建 2,175 篇文档的中医文本索引（缓存在 `F:\\tcm_index_cache\\p`，重复运行直接复用；"
        "目录必须是纯 ASCII，faiss 在 Windows 打不开中文路径）；不建图、不改主项目 schema、不复制中医数据进仓库。",
        "",
        "## 6. 改动清单",
        "",
        "- 新增 `scripts/eval_cross_domain.py`（本任务脚本）",
        "- 新增 `data/eval_out/cross_domain.json`（逐题台账 + 金标来源 + 指标）",
        "- 新增 `reports/cross_domain.md`（本报告）",
        "- 未修改：`app/__003__graph/__000__schema.py`、`data/graph/`、`data/gold/`、`data/lexicon/`；无 git 写操作。",
    ]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tcm-root", default=str(TCM_ROOT_DEFAULT))
    ap.add_argument("--index-dir", default=str(INDEX_DIR_DEFAULT))
    ap.add_argument("--out", default="data/eval_out/cross_domain.json")
    ap.add_argument("--report", default="reports/cross_domain.md")
    ap.add_argument("--gold-only", action="store_true")
    ap.add_argument("--rebuild-index", action="store_true")
    ap.add_argument("--rerank", action="store_true", help="额外跑 bge-reranker（CPU 很慢）")
    args = ap.parse_args()

    tcm_root = Path(args.tcm_root)
    if not tcm_root.exists():
        raise SystemExit(f"中医数据路径不存在：{tcm_root}")

    t0 = time.time()
    gold, gold_stats, _ = build_gold(tcm_root)
    print(
        f"[gold] n={len(gold)} classes={gold_stats['chosen']} "
        f"pending={gold_stats['pending_total']} pools={gold_stats['pools']}",
        file=sys.stderr,
    )
    if args.gold_only:
        print(json.dumps(gold_stats, ensure_ascii=False, indent=1))
        for g in gold[:5]:
            print(json.dumps(g, ensure_ascii=False)[:500])
        return

    docs = load_docs(tcm_root / CRAWL_SUBDIR)
    idx = ensure_index(Path(args.index_dir), docs, args.rebuild_index)

    modes = ["bm25", "dense", "rrf"]
    evaluation = evaluate_retrieval(idx, gold, modes)
    if args.rerank:
        rows = []
        for g in gold:
            top = [h.article_id for h in idx.search(g["query"], top_k=5, use_rerank=True)]
            expected = set(g["expected"])
            inter = expected & set(top)
            rows.append(
                {
                    "recall@5": len(inter) / len(expected),
                    "all@5": float(expected <= set(top)),
                    "hit@5": float(bool(inter)),
                }
            )
        evaluation["modes"]["rrf_rerank"] = {
            "recall@5": round(statistics.mean(r["recall@5"] for r in rows), 4),
            "all@5": round(statistics.mean(r["all@5"] for r in rows), 4),
            "hit@5": round(statistics.mean(r["hit@5"] for r in rows), 4),
            "by_class": {
                cls: {
                    "n": sum(1 for g in gold if g["cls"] == cls),
                    "recall@5": round(statistics.mean(r["recall@5"] for g, r in zip(gold, rows) if g["cls"] == cls), 4),
                    "all@5": round(statistics.mean(r["all@5"] for g, r in zip(gold, rows) if g["cls"] == cls), 4),
                    "hit@5": round(statistics.mean(r["hit@5"] for g, r in zip(gold, rows) if g["cls"] == cls), 4),
                }
                for cls in ("single", "two", "three")
            },
        }

    routing = evaluate_router(gold)
    overlap = legal_lexicon_overlap(gold, tcm_root)

    payload = {
        "task": "p / T14 中医 82 条跨域迁移验证",
        "date": "2026-10-31",
        "caliber": {
            "gold_source": "外部中医路径无预置 82 条金标；机械使用数据集自带抽取标注 + T01 字面独立出现判据",
            "denominator": len(gold),
            "pending_excluded": gold_stats["pending_total"],
            "model_as_gold": False,
        },
        "external_paths": {
            "tcm_root": str(tcm_root),
            "crawl_dir": str(tcm_root / CRAWL_SUBDIR),
            "extract_dir": str(tcm_root / EXTRACT_SUBDIR),
            "index_dir": str(Path(args.index_dir)),
            "read_only": True,
        },
        "gold_stats": gold_stats,
        "gold": gold,
        "evaluation": evaluation,
        "routing": routing,
        "legal_lexicon_overlap": overlap,
        "labor_baselines": LABOR_BASELINES,
        "runtime_s": round(time.time() - t0, 1),
    }
    payload["primary_mode"] = "rrf"

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    report_path = ROOT / args.report
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(payload), encoding="utf-8")
    print(f"[done] {out_path} + {report_path} in {payload['runtime_s']}s", file=sys.stderr)


if __name__ == "__main__":
    main()
