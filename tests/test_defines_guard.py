"""``DEFINES_GUARD`` 守卫规则的样例级断言。

为什么需要：100 条人工盲标里 51 条假边只集中在两类形态——别名不是这个概念、命中串被更长
的词吞掉；另有 7 条真边被「同分句」闸门误杀。这里把四类代表句钉死，规则一改就红灯。

样例覆盖：最长词吞并（左/右）、主体后缀、别名中心语、别名主体名词、承前省略（真/假）。
"""

from __future__ import annotations

import pytest

import app.__002__extract.__003__edge_typing as et

CONCEPTS = [
    {"id": "creditor_right", "name": "债权", "type": "right", "aliases": ["债权"]},
    {"id": "creditor", "name": "债权人", "type": "entity", "aliases": ["债权人"]},
    {"id": "security_right", "name": "担保物权", "type": "right", "aliases": ["担保物权", "担保"]},
    {"id": "property_service", "name": "物业服务合同", "type": "contract", "aliases": ["物业服务合同", "业主"]},
    {"id": "entrust", "name": "委托合同", "type": "contract", "aliases": ["委托合同", "受托人"]},
    {"id": "work_contract", "name": "承揽合同", "type": "contract", "aliases": ["承揽合同"]},
    {"id": "orderer", "name": "定作人", "type": "entity", "aliases": ["定作人"]},
    {"id": "contractor", "name": "承揽人", "type": "entity", "aliases": ["承揽人"]},
    {"id": "labor_dispatch", "name": "劳务派遣单位", "type": "entity", "aliases": ["劳务派遣单位", "派遣单位"]},
    {"id": "borrow", "name": "借款合同", "type": "contract", "aliases": ["借款合同"]},
]

ARTICLES = [{"article_id": "t#1", "content": "占位"}]


def _article(text: str) -> dict:
    return {"article_id": "t#1", "content": text}


def _classify(text: str, alias: str, concept_id: str, concept: dict, offset: int | None = None) -> str:
    """跑一遍分类 + 守卫，返回最终 edge_type。"""
    if offset is None:
        offset = text.find(alias)
    art = _article(text)
    m = {"article_id": "t#1", "concept_id": concept_id, "aliases": concept["aliases"]}
    row = et.classify_mention(art, m)
    row["matched_alias"], row["offset"] = alias, offset
    if not et._guard_enabled():  # 开关关闭时守卫不介入，用于对照默认行为
        return row["edge_type"], row["cue"]
    out = et.apply_defines_guards([row], [art], CONCEPTS)
    return out[0]["edge_type"], out[0]["cue"]


@pytest.fixture(autouse=True)
def _guard_on(monkeypatch):
    monkeypatch.setenv("DEFINES_GUARD", "1")
    monkeypatch.delenv("DEFINES_STRICT", raising=False)


@pytest.mark.parametrize(
    "text,alias,cid,name,expect",
    [
        # 债权 命中在「债权人」里 → 被更长的另一个概念吞掉 → 不成立
        ("债务人不履行到期债务，债权人可以留置动产。", "债权", "creditor_right", "债权", "REFERS_TO"),
        # 担保 命中在「担保人」里 → 接主体后缀 → 不成立
        ("主合同解除后，担保人对债务人仍应当承担担保责任。", "担保", "security_right", "担保物权", "REFERS_TO"),
        # 业主 不是 物业服务合同 的别名 → 不成立
        ("业主应当依法予以配合。", "业主", "property_service", "物业服务合同", "REFERS_TO"),
        # 受托人 是主体名词，不是 委托合同 → 不成立
        ("受托人应当继续处理委托事务。", "受托人", "entrust", "委托合同", "REFERS_TO"),
        # 借款 不是 借款合同（宾语位，且被 订立 支配）→ 不成立
        ("订立借款合同，借款人应当提供真实情况。", "借款合同", "borrow", "借款合同", "REFERS_TO"),
        # 承前省略：分句后直接接模态词，主语仍是承揽人 → 成立
        ("承揽人完成工作的，应当向定作人交付工作成果。", "承揽人", "contractor", "承揽人", "DEFINES"),
        # 承前省略不成立：分句后冒出新的主语 → 不成立
        ("定作人提供材料的，承揽人应当及时检验。", "定作人", "orderer", "定作人", "REFERS_TO"),
        # 派遣单位 是本概念的简称 → 不能被「劳务派遣单位」误判成吞并 → 成立
        ("劳务派遣单位应当与被派遣劳动者订立二年以上的劳动合同。", "派遣单位", "labor_dispatch", "劳务派遣单位", "DEFINES"),
    ],
)
def test_guard_cases(text, alias, cid, name, expect):
    concept = next(c for c in CONCEPTS if c["id"] == cid)
    assert name == concept["name"]
    got, cue = _classify(text, alias, cid, concept, offset=text.find(alias))
    assert got == expect, f"{name} @ {alias} → {got} ({cue})"


def test_guard_off_keeps_old_behaviour(monkeypatch):
    """关掉开关时「担保／担保物权」这类假边照旧放行，证明默认行为没变。"""
    monkeypatch.delenv("DEFINES_GUARD", raising=False)
    concept = next(c for c in CONCEPTS if c["id"] == "security_right")
    text = "主合同解除后，担保人对债务人仍应当承担担保责任。"
    got, _ = _classify(text, "担保", "security_right", concept)
    assert got == "DEFINES"
