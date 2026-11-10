"""``DEFINES_STRICT`` 开关的 10 条样例对照断言。

为什么需要：``classify_occurrence`` 被边类型化、抽检、测量脚本共用，改了判据却没有样例级
断言的话，「默认关闭＝旧行为」这句话只能靠人肉记忆保证。这里把 10 条代表句的**旧判据结果**
钉死，再单独钉严口径结果，任何一侧漂移都会红灯。

样例覆盖：定义式（所称 / 是指风格）、裸系词、分句主语位、宾语位、``X 的 …`` 偏正结构、
跨分句模态词、别名落在词中间（「被派遣劳动者」里的「劳动者」）。
"""

from __future__ import annotations

import pytest

from app.__002__extract.__003__edge_typing import _strict_enabled, classify_occurrence

# (条文, 别名, 别名起始偏移, 旧判据期望, 严口径期望)
CASES = [
    (
        "借款合同是借款人向贷款人借款，到期返还借款并支付利息的合同。",
        "借款合同",
        0,
        ("DEFINES", "系词定义"),
        ("DEFINES", "系词定义"),
    ),
    (
        "本法所称个人信息，是指以电子或者其他方式记录的能够单独识别特定自然人的各种信息。",
        "个人信息",
        4,
        ("DEFINES", "所称号"),
        ("DEFINES", "所称号"),
    ),
    (
        "用人单位应当依法建立和完善劳动规章制度，保障劳动者享有劳动权利。",
        "用人单位",
        0,
        ("DEFINES", "normative:应当"),
        ("DEFINES", "normative_subject:应当"),
    ),
    (
        "劳动合同的解除应当依照法律规定执行。",
        "劳动合同",
        0,
        ("DEFINES", "normative:应当"),
        None,
    ),
    (
        "土地承包经营权人可以自主决定依法采取出租、入股或者其他方式流转土地经营权。",
        "土地承包经营权",
        0,
        ("DEFINES", "normative:可以"),
        ("DEFINES", "normative_subject:可以"),
    ),
    (
        "定作人提供材料的，承揽人应当及时检验。",
        "定作人",
        0,
        None,
        None,
    ),
    (
        "出卖人应当履行向买受人交付标的物或者交付提取标的物的单证的义务。",
        "出卖人",
        0,
        ("DEFINES", "normative:应当"),
        ("DEFINES", "normative_subject:应当"),
    ),
    (
        "被派遣劳动者有权在劳务派遣单位或者用工单位依法参加或者组织工会。",
        "劳动者",
        3,
        ("DEFINES", "normative:有权"),
        None,
    ),
    (
        "用人单位与劳动者应当按照劳动合同的约定，全面履行各自的义务。",
        "用人单位",
        0,
        ("DEFINES", "normative:应当"),
        None,
    ),
    (
        "买受人应当按照约定的数额支付价款。",
        "买受人",
        0,
        ("DEFINES", "normative:应当"),
        ("DEFINES", "normative_subject:应当"),
    ),
]


def _run(case, strict: bool, monkeypatch):
    text, alias, offset, _, _ = case
    assert text[offset : offset + len(alias)] == alias, "样例偏移写错了"
    if strict:
        monkeypatch.setenv("DEFINES_STRICT", "1")
    else:
        monkeypatch.delenv("DEFINES_STRICT", raising=False)
    return classify_occurrence(text, offset, offset + len(alias))


@pytest.mark.parametrize("case", CASES, ids=[c[1] for c in CASES])
def test_switch_off_matches_b7(case, monkeypatch):
    assert _run(case, False, monkeypatch) == case[3]


@pytest.mark.parametrize("case", CASES, ids=[c[1] for c in CASES])
def test_switch_on_applies_strict(case, monkeypatch):
    assert _run(case, True, monkeypatch) == case[4]


def test_strict_flag_parsing(monkeypatch):
    monkeypatch.delenv("DEFINES_STRICT", raising=False)
    assert _strict_enabled() is False
    for value in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("DEFINES_STRICT", value)
        assert _strict_enabled() is True
    for value in ("0", "", "no", "off"):
        monkeypatch.setenv("DEFINES_STRICT", value)
        assert _strict_enabled() is False
