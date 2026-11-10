"""B7 边类型拆分：把原来的 ``mentions``（Article→Concept 一条边）拆成
``DEFINES``（该条在定义/正面规制这个概念）与 ``REFERS_TO``（只是顺带提到）。

为什么必须拆：原 ``mentions`` 把「提及」当「定义」，例如「合同履行」挂在民法典第 926 条
（委托合同第三人关系）、「诉讼时效中断」挂在第 199 条（撤销权期间）——概念名在正文出现，
但该条不是在定义它。B1 定义级抽检 75% 未达 85% 的根因就在这里。

判定规则（全部可复现，不调 LLM）：
1. ``definition_pattern``：概念名紧跟 ``是指 / 系指 / 即指 / 指的是 / 所称 / 包括 / 分为 / 统称``，
   或概念名前面 12 字内出现 ``本法所称 / 本条所称 / 本条例所称 / 以下称``；
2. ``copula_definition``：概念名出现在句首 6 字内，且紧跟裸系词 ``是``（排除「是否」）——
   中文法律的定义句常写成「买卖合同是……的合同」，不带「是指」；
3. ``normative_subject``：概念名位于**分句开头**（主语位）、其后紧跟模态词，且别名后第一个字不是
   「的 / 与 / 和 / 及 / 或 / 、」（避免把「劳动合同的解除应当……」误判成对「劳动合同」的规制）；
   另有宽口径分支：概念名只要求落在条文首个分句内（句首 20 字内）且该分句含模态词；
4. 其余一律 ``REFERS_TO``。

**``DEFINES_STRICT=1`` 严口径开关**（默认关闭，关闭时行为与 B7 完全一致）：
杀掉上面那条「首分句内出现即算」的宽口径分支，只保留「别名起于分句首」，并加同分句闸门——
别名结尾到模态词之间不得出现 ``，、；``。理由：宽口径把「订立借款合同，借款人应当……」这类
宾语位提及也算成定义，盲判里约 37% 是这种顺带提及。开关只影响 ``classify_occurrence``，
``_COPULA``（``X 是……``）与「所称号」两个真定义分支在任何模式下都保留。

边界：一条 mention 可能在一段里出现多次，任一次命中规则 1/2 即判 DEFINES（取最强的那次），
并把命中的别名、字符偏移、±60 字窗口与 cue 落盘，供抽检复核。

**``DEFINES_GUARD=1`` 守卫开关**（默认关闭，隐含开启 STRICT）：
在严口径之上再否掉两类假边，依据是 100 条人工盲标里 51 条假边的实际形态：
5. ``maximal_term``：命中串被同一位置的更长词吞掉，且该长词属于**另一个**概念
   （「债权人」里的「债权」、「担保物权」里的「担保」）；或命中串后面紧跟主体后缀
   （人 / 方 / 者 / 单位 / 机构 / 部门 / 委员会）。51 条假边中 23 条属此类；
6. ``alias_fidelity``：别名不保留概念名的中心语（「业主」之于「物业服务合同」），
   或主体名词充当非 entity 类概念的别名（「受托人」之于「委托合同」）。10 条假边属此类；
   另有 15 条假边只靠严口径的同分句闸门即可拦下；
7. ``carry_over``：承前省略补判——别名位于分句首、模态词在下一分句，但两分句之间到模态词
   只剩「则 / 也 / 均 / 仍 / 还 / 都 / 即 / 亦」或什么都不剩时，主语仍是该概念
   （「定作人提供材料的，应当按照约定提供材料」）；若中间冒出新的主语则照旧否掉
   （「定作人提供材料的，承揽人应当及时检验」）。样本里救回 2 条真边，全量新增 130 条。

用法：
    .venv\\Scripts\\python.exe app/__002__extract/__003__edge_typing.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.path_utils import read_jsonl, write_json, write_jsonl  # noqa: E402

WINDOW = 60
DEFINES = "DEFINES"
REFERS_TO = "REFERS_TO"

_SENT_END = re.compile(r"[。；\n]")
_MODAL = ("应当", "不得", "可以", "有权", "必须")
_AFTER_CUE = re.compile(r"\s*[（(，,：:]?\s*(是指|系指|即指|指的是|所称|包括|分为|统称)")
_BEFORE_CUE = re.compile(r"(本法|本条|本条例|本法例|以下|下)\s*所称\s*$")
_COPULA = re.compile(r"^[，,]?\s*是(?!否)")
_CLAUSE_END = re.compile(r"[，、；。：\n]")
_BAD_AFTER_ALIAS = ("的", "与", "和", "及", "或", "、")
_CLAUSE_BREAK = "，、；"
# 承前省略：分句边界之后到模态词之间只剩这些字，说明主语沿用上一分句（「定作人提供材料的，应当……」）
_CARRY_OVER = {"", "则", "也", "均", "仍", "还", "都", "即", "亦", "并"}
# 主体名词后缀：概念名后紧跟这些字，说明命中的是「X 人」而不是 X
_AGENT_SUFFIX = ("人", "方", "者")
_AGENT_WORDS = ("单位", "机构", "部门", "委员会")


def _strict_enabled() -> bool:
    """``DEFINES_STRICT=1`` 启用「分句主语位 + 同分句」严口径；默认关闭＝B7 旧行为。

    开关集中在这一个函数里，方便评测侧用一行环境变量切换，也方便单测里 monkeypatch。
    """
    return os.environ.get("DEFINES_STRICT", "").strip().lower() in {"1", "true", "yes", "on"}


def _guard_enabled() -> bool:
    """``DEFINES_GUARD=1`` 在严口径之上再叠最长词守卫、别名中心语守卫与承前省略补判。

    默认关闭，关闭时 ``classify_occurrence`` 行为与 B7/STRICT 完全一致。
    """
    return os.environ.get("DEFINES_GUARD", "").strip().lower() in {"1", "true", "yes", "on"}


def _carry_over_subject(text: str, end: int, modal_pos: int) -> bool:
    """别名之后跨过分句边界时，判断模态词是否仍以该别名为主语（承前省略）。

    「定作人提供材料的，应当按照约定提供材料」→ 边界后到模态词之间为空 → True；
    「定作人提供材料的，承揽人应当及时检验」→ 中间出现「承揽人」→ False。
    """
    seg_start = end
    for m in re.finditer(r"[，、；。：\n]", text[end:modal_pos]):
        seg_start = end + m.end()
    return text[seg_start:modal_pos].strip("，、；。： \u3000") in _CARRY_OVER


def _sentence_start(text: str, pos: int) -> int:
    start = 0
    for m in _SENT_END.finditer(text, 0, pos):
        start = m.end()
    return start


def _first_clause(text: str, sent_start: int) -> str:
    """条文首句的第一个分句（到第一个逗号/顿号/分号/句号为止）。"""
    m = re.search(r"[，、；。\n]", text[sent_start:])
    end = sent_start + m.start() if m else len(text)
    return text[sent_start:end]


def _clauses(text: str, sent_start: int) -> list[tuple[int, str]]:
    """把首句切成 (起始偏移, 分句文本) 列表。"""
    sent_end = len(text)
    m = _SENT_END.search(text, sent_start)
    if m:
        sent_end = m.start()
    spans, start = [], sent_start
    for m in _CLAUSE_END.finditer(text, sent_start, sent_end):
        spans.append((start, text[start : m.start()]))
        start = m.end()
    spans.append((start, text[start:sent_end]))
    return spans


def classify_occurrence(text: str, start: int, end: int) -> tuple[str, str] | None:
    """判定单次出现；命中返回 (edge_type, cue)，否则 None。"""
    before = text[max(0, start - 12) : start]
    after = text[end : end + 12]
    if _BEFORE_CUE.search(before):
        return DEFINES, "所称号"
    m = _AFTER_CUE.match(after)
    if m:
        return DEFINES, m.group(1)
    sent_start = _sentence_start(text, start)
    if start - sent_start <= 6 and _COPULA.match(after):
        return DEFINES, "系词定义"
    strict = _strict_enabled() or _guard_enabled()
    if not strict:
        clause = _first_clause(text, sent_start)
        in_first_clause = start - sent_start <= 20 and start < sent_start + len(clause)
        if in_first_clause:
            for modal in _MODAL:
                if modal in clause and text.find(modal, sent_start) >= end:
                    return DEFINES, f"normative:{modal}"
    for cstart, ctext in _clauses(text, sent_start):
        if start != cstart:
            continue
        if text[end : end + 1] in _BAD_AFTER_ALIAS:
            continue
        tail = ctext[len(text[start:end]) :]
        for modal in _MODAL:
            if modal not in tail:
                continue
            if strict:
                modal_pos = text.find(modal, end)
                if modal_pos < 0:
                    continue
                # 同分句闸门：别名结尾到模态词之间出现分句标点，说明模态词属于另一个分句。
                if any(p in text[end:modal_pos] for p in _CLAUSE_BREAK):
                    continue
            return DEFINES, f"normative_subject:{modal}"
        if not strict or not _guard_enabled():
            continue
        # 承前省略：模态词落在后续分句，且两分句之间没有出现新的主语，主语仍是本概念。
        sent_end = len(text)
        m_end = _SENT_END.search(text, sent_start)
        if m_end:
            sent_end = m_end.start()
        for modal in _MODAL:
            modal_pos = text.find(modal, end, sent_end)
            if modal_pos < 0 or not any(p in text[end:modal_pos] for p in _CLAUSE_BREAK):
                continue
            if _carry_over_subject(text, end, modal_pos):
                return DEFINES, f"normative_subject:carry_over:{modal}"
    return None


def classify_mention(article: dict, mention: dict) -> dict:
    text = article["content"]
    aliases = [a for a in (mention.get("aliases") or []) if a]
    best: tuple[str, str, str, int] | None = None  # (type, cue, alias, offset)
    fallback: tuple[str, str, int] | None = None
    for alias in aliases:
        for m in re.finditer(re.escape(alias), text):
            hit = classify_occurrence(text, m.start(), m.end())
            if hit:
                if best is None or hit[0] == DEFINES:
                    best = (hit[0], hit[1], alias, m.start())
            elif fallback is None:
                fallback = (alias, m.start(), m.group(0))
    if best:
        edge_type, cue, alias, offset = best
    elif fallback:
        alias, offset, _ = fallback
        edge_type, cue = REFERS_TO, "no_cue"
    else:  # 别名在正文里找不到（理论上不会发生），保守判 REFERS_TO
        alias, offset = (aliases[0] if aliases else ""), 0
        edge_type, cue = REFERS_TO, "alias_not_found"
    end = offset + len(alias)
    return {
        **mention,
        "edge_type": edge_type,
        "cue": cue,
        "matched_alias": alias,
        "offset": offset,
        "defines_score": 1.0 if edge_type == DEFINES else 0.45,
        "window": text[max(0, offset - WINDOW) : min(len(text), end + WINDOW)],
    }


def type_mentions(articles: list[dict], mentions: list[dict]) -> list[dict]:
    by_id = {a["article_id"]: a for a in articles}
    rows = []
    for m in mentions:
        art = by_id.get(m["article_id"])
        if art is None:
            continue
        rows.append(classify_mention(art, m))
    return rows


MANUAL_MENTIONS = "manual_mentions.jsonl"


def load_manual_mentions(lexicon_dir: Path) -> list[dict]:
    """读取人工补的 Article→Concept 边（``provenance=manual``）。

    为什么需要人工补边：别名词典是字面匹配，若正确条文里没有概念名/别名（例如「休息日」
    在劳动法第三十八条写成了「每周至少休息一日」），这条本来正确的映射在图上就不存在。
    这类缺口按法律含义逐条补边、写清理由，不靠放宽匹配规则去凑数。
    """
    path = lexicon_dir / MANUAL_MENTIONS
    if not path.exists():
        return []
    rows = []
    for r in read_jsonl(path):
        edge_type = r.get("edge_type", DEFINES)
        rows.append(
            {
                "article_id": r["article_id"],
                "concept_id": r["concept_id"],
                "aliases": r.get("aliases", []),
                "count": r.get("count", 1),
                "confidence": float(r.get("confidence", 0.95)),
                "provenance": "manual",
                "edge_type": edge_type,
                "cue": "manual",
                "matched_alias": (r.get("aliases") or [""])[0],
                "offset": -1,
                "defines_score": 1.0 if edge_type == DEFINES else 0.45,
                "window": "",
                "note": r.get("note", ""),
            }
        )
    return rows


def merge_manual(rows: list[dict], manual: list[dict]) -> list[dict]:
    """人工边优先：同一 (article, concept) 若规则已抽出，则用人工边的类型与说明覆盖属性。"""
    index = {(r["article_id"], r["concept_id"]): i for i, r in enumerate(rows)}
    for m in manual:
        key = (m["article_id"], m["concept_id"])
        if key in index:
            rows[index[key]] = {**rows[index[key]], **m}
        else:
            index[key] = len(rows)
            rows.append(m)
    return rows


def _maximal_term_reason(
    text: str, start: int, alias: str, concept_id: str,
    terms: dict[str, str], order: list[str],
) -> str | None:
    """命中的是不是另一个更长的词的一部分（债权→债权人、担保→担保物权）。

    ``terms`` 是词→concept_id，只有吞并它的长词属于**另一个**概念时才否掉：
    「劳务派遣单位」里的「派遣单位」仍是本概念，不能因此丢边。
    """
    end = start + len(alias)
    for t in order:
        if len(t) > len(alias) and t.startswith(alias) and text[start : start + len(t)] == t:
            if terms.get(t) != concept_id:
                return f"右侧被:{t}"
    for t in order:
        if len(t) <= len(alias):
            continue
        i = t.find(alias)
        if i <= 0:
            continue
        st = start - i
        if st >= 0 and text[st : st + len(t)] == t and terms.get(t) != concept_id:
            return f"左侧被:{t}"
    nxt = text[end : end + 1]
    if nxt and nxt in _AGENT_SUFFIX:
        return f"接主体后缀:{nxt}"
    for w in _AGENT_WORDS:
        if text[end : end + len(w)] == w:
            return f"接主体后缀:{w}"
    return None


def _alias_fidelity_reason(concept: dict, alias: str) -> str | None:
    """别名是否还指着同一个概念。

    两条硬约束：① 别名必须保留概念名的中心语（后两字），「业主」不是「物业服务合同」；
    ② 主体名词（X 人 / X 单位）不能充当非 entity 类概念的别名，「受托人」不是「委托合同」。
    """
    name = concept.get("name") or ""
    if alias == name:
        return None
    head = name[-2:] if len(name) >= 2 else name
    if head not in alias:
        return f"别名不含中心语:{head}"
    typ = (concept.get("type") or "").lower()
    if (alias.endswith(_AGENT_SUFFIX) or alias.endswith(_AGENT_WORDS)) and typ != "entity":
        return f"主体名词别名:{alias}"
    return None


def apply_defines_guards(rows: list[dict], articles: list[dict], concepts: list[dict]) -> list[dict]:
    """把「最长词吞并」「别名不可信」两类假边从 DEFINES 降为 REFERS_TO。

    只降不升，且跳过 ``provenance=manual`` 的人工边——人工边是逐条读过条文补的，不该被规则否掉。
    """
    by_article = {a["article_id"]: a for a in articles}
    by_cid = {c["id"]: c for c in concepts}
    terms: dict[str, str] = {}
    for c in concepts:
        terms.setdefault(c["name"], c["id"])
        for a in c.get("aliases") or []:
            if a:
                terms.setdefault(a, c["id"])
    order = sorted(terms, key=len, reverse=True)

    out = []
    for r in rows:
        if r.get("edge_type") != DEFINES or r.get("provenance") == "manual":
            out.append(r)
            continue
        art, concept = by_article.get(r["article_id"]), by_cid.get(r["concept_id"])
        alias, start = r.get("matched_alias") or "", r.get("offset", -1)
        if art is None or concept is None or start < 0 or not alias:
            out.append(r)
            continue
        reason = _maximal_term_reason(art["content"], start, alias, r["concept_id"], terms, order) or (
            _alias_fidelity_reason(concept, alias)
        )
        if reason:
            r = {**r, "edge_type": REFERS_TO, "cue": f"guard:{reason}", "defines_score": 0.45}
        out.append(r)
    return out


def load_typed(extracted_dir: Path) -> list[dict]:
    """优先读 mentions_typed.jsonl；缺失时回落旧 mentions.jsonl，并全部标 DEFINES。

    回落是为了让「只更新代码、不重跑抽取」的场景仍能建图，不会静默丢边。
    无论走哪条路径，都会合并 ``data/lexicon/manual_mentions.jsonl`` 里的人工补边。
    """
    typed = extracted_dir / "mentions_typed.jsonl"
    if typed.exists():
        rows = read_jsonl(typed)
    else:
        rows = read_jsonl(extracted_dir / "mentions.jsonl")
        for r in rows:
            r.setdefault("edge_type", DEFINES)
            r.setdefault("cue", "legacy")
            r.setdefault("defines_score", 1.0)
    return merge_manual(rows, load_manual_mentions(extracted_dir.parent / "lexicon"))


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    extracted = root / "data" / "extracted"
    articles = read_jsonl(extracted / "articles.jsonl")
    mentions = read_jsonl(extracted / "mentions.jsonl")
    rows = type_mentions(articles, mentions)
    if _guard_enabled():
        rows = apply_defines_guards(rows, articles, read_jsonl(root / "data" / "lexicon" / "concepts.jsonl"))
    rows = merge_manual(rows, load_manual_mentions(root / "data" / "lexicon"))
    write_jsonl(extracted / "mentions_typed.jsonl", rows)

    types = Counter(r["edge_type"] for r in rows)
    cues = Counter(r["cue"] for r in rows)
    stats = {
        "total": len(rows),
        "rule_total": len(mentions),
        "manual_total": sum(1 for r in rows if r.get("provenance") == "manual"),
        "edge_type": dict(types),
        "defines_ratio": round(types[DEFINES] / max(len(rows), 1), 4),
        "cue_distribution": dict(cues.most_common()),
        "window_chars": WINDOW,
        "rule": (
            "定义式（是指/系指/即指/指的是/所称/包括/分为/统称，或前置『本法所称』）、"
            "句首裸系词『X 是……』、或分句主语位 + 应当/不得/可以/有权/必须 → DEFINES；其余 → REFERS_TO"
        ),
    }
    write_json(root / "data" / "extracted" / "mentions_typed_stats.json", stats)
    print(json.dumps(stats, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
