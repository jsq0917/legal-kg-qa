"""pass_b 缓存命中路径的回归测试（T07 修复）。

背景：``pass_b`` 未命中缓存时返回 ``{int: int}``，命中缓存时 JsonCache 里存的是
json 序列化后的字符串键（``"0".."n"``）。修复前只把 value 转 int、键仍是 str，
调用方 ``labels_b1 = [b1.get(i, 0) for i in range(len(order))]`` 按 int 取键会全部拿到 0。
2026-10-30 复核实证：g01 的 ``pass_b_run1`` / ``pass_b_run2`` 都是 ``[0,0,0,0]``，而 ``pass_a`` 是 ``[1,1,0,0]``。

这个测试不碰网络、不碰真实金标：只往临时 JsonCache 里塞一条与 ``pass_b`` 完全同 key 的缓存，
断言返回字典的键是 int、且标签不是全 0。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from build_gold import pass_b  # noqa: E402
from eval.common import JsonCache  # noqa: E402

QUESTION = "我被劳务公司派到别的公司上班出了纠纷，该找劳务公司还是上班那家公司负责？"
CANDIDATES = ["lcl#58", "lcl#92", "lcl#59"]
ARTICLES = {
    "lcl#58": {"citation": "中华人民共和国劳动合同法第五十八条", "content": "劳务派遣单位是本法所称用人单位……"},
    "lcl#92": {"citation": "中华人民共和国劳动合同法第九十二条", "content": "劳务派遣单位违反本法规定的……"},
    "lcl#59": {"citation": "中华人民共和国劳动合同法第五十九条", "content": "劳务派遣单位派遣劳动者应当与……"},
}


def _listing(candidates: list[str]) -> str:
    return "\n".join(f"[{i + 1}] {ARTICLES[a]['citation']}\n{ARTICLES[a]['content']}" for i, a in enumerate(candidates))


@pytest.mark.parametrize("salt", ["a", "b"])
def test_pass_b_cached_labels_use_int_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, salt: str) -> None:
    cache = JsonCache(tmp_path / "cache.json")
    listing = _listing(CANDIDATES)
    key = cache.key("passb", salt, QUESTION, listing)
    # 模拟真实缓存：json 落盘后键一定是字符串；标签为 [1, 1, 0]
    cache.set(key, {"labels": {"0": 1, "1": 1, "2": 0}, "raw": {"necessary": [1, 2]}})

    # 若缓存没命中，会走真实 LLM；这里直接让调用失败，确保测试只验证缓存路径
    monkeypatch.setattr("build_gold.get_llm", lambda: pytest.fail("缓存未命中：pass_b 的 key 与写入时不一致"))

    labels = pass_b(QUESTION, CANDIDATES, ARTICLES, cache, True, salt)

    assert set(labels.keys()) == {0, 1, 2}, f"键必须是 int，实际 {labels}"
    assert [labels.get(i, 0) for i in range(len(CANDIDATES))] == [1, 1, 0], "缓存命中的标签不能读成全 0"
