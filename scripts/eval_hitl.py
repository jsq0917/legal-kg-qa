"""B5 E2E：3 条域内 + 2 条域外用例，覆盖「挂起 -> 人工确认 -> 恢复 -> 继续生成」与「域外拒答」。

用例设计（域内题直接取自 data/gold/gold_60.jsonl，避免自拟题导致「域内题其实不域内」）::

    域内 in-1 工伤认定   g55  挂起 -> accept        -> 继续生成
    域内 in-2 经济补偿   g08  挂起 -> accept        -> 继续生成
    域内 in-3 竞业限制   g05  挂起 -> edit_articles -> 换引用后继续生成
    域外 out-1 离婚财产分割       -> refused, citations == [], 不调 LLM
    域外 out-2 房屋买卖合同违约   -> refused, citations == [], 不调 LLM

拒答判定在 ``HitlPipeline.start`` 里（检索之后、生成之前），所以 ``/hitl``、``/ask``
之外的入口也共享同一道门；脚本只负责断言，不自己判断域内外。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__006__hitl.__001__checkpoint import CheckpointStore  # noqa: E402
from app.__006__hitl.__002__correction import CorrectionStore  # noqa: E402
from app.__006__hitl.__003__pipeline import REFUSAL_ANSWER, HitlPipeline  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from common.path_utils import write_json  # noqa: E402

log = get_logger("eval_hitl")

GOLD = Path("data/gold/gold_60.jsonl")
CITATION_NO = re.compile(r"第[〇零一二三四五六七八九十百千0-9]+条")

#: (用例 id, 主题, 金标 id, 人工动作)
IN_DOMAIN = [
    ("in-1", "工伤认定", "g55", {"type": "accept"}),
    ("in-2", "经济补偿", "g08", {"type": "accept"}),
    ("in-3", "竞业限制补偿", "g05", {"type": "edit_articles", "article_ids": ["lcl#23", "lcl#24"]}),
]

OUT_DOMAIN = [
    ("out-1", "离婚财产分割", "夫妻离婚时房子和存款怎么分割？"),
    ("out-2", "房屋买卖合同违约", "签了房屋买卖合同，卖方违约不配合过户，我能要违约金吗？"),
]


def load_gold() -> dict[str, dict]:
    return {g["id"]: g for g in (json.loads(l) for l in GOLD.open(encoding="utf-8") if l.strip())}


def run_in_domain(pipe: HitlPipeline, gold: dict[str, dict]) -> list[dict]:
    cases = []
    for cid, topic, gid, action in IN_DOMAIN:
        query = gold[gid]["question"]
        started = pipe.start(query, force_review=True)
        suspended = started["status"] == "awaiting_review"
        resumed = pipe.resume(started["task_id"], action) if suspended else {}
        answer = resumed.get("answer") or ""
        cites = list(resumed.get("citations") or [])
        swapped = action["type"] != "edit_articles" or all(
            gold_cite in cites for gold_cite in gold[gid]["article_citations"]
        )
        cases.append(
            {
                "id": cid,
                "topic": topic,
                "gold_id": gid,
                "query": query,
                "suspended": suspended,
                "resumed": resumed.get("status") == "completed",
                "resolution": action["type"],
                "answer_len": len(answer),
                "answer_head": answer[:80],
                "citations": cites,
                "citations_match_gold": swapped,
                "used_llm": resumed.get("used_llm"),
                "refused": False,
                "retrieval_top_score": started["retrieval_top_score"],
                "off_domain_threshold": started["off_domain_threshold"],
                "ok": bool(suspended and resumed.get("status") == "completed" and answer and not started["refused"]),
            }
        )
    return cases


def run_out_domain(pipe: HitlPipeline) -> list[dict]:
    cases = []
    for cid, topic, query in OUT_DOMAIN:
        out = pipe.start(query)
        answer = out.get("answer") or ""
        cites = list(out.get("citations") or [])
        no_citation = not CITATION_NO.search(answer)
        cases.append(
            {
                "id": cid,
                "topic": topic,
                "query": query,
                "suspended": out["status"] == "awaiting_review",
                "resumed": False,
                "refused": bool(out.get("refused")),
                "status": out["status"],
                "answer": answer,
                "answer_has_article_no": not no_citation,
                "citations": cites,
                "used_llm": out.get("used_llm"),
                "reason": out.get("reason"),
                "retrieval_top_score": out["retrieval_top_score"],
                "off_domain_threshold": out["off_domain_threshold"],
                "ok": bool(out.get("refused") and cites == [] and no_citation and answer == REFUSAL_ANSWER),
            }
        )
    return cases


def reset_state(ckpt_root: Path, db: Path) -> None:
    """清空上次运行的挂起点与修正库，保证 stats 可复现。

    用 ``rmtree`` 而不是逐文件 ``unlink``：Windows 上文件被扫描器/编辑器短暂占用时
    ``unlink`` 会抛异常或留下残file，导致 ``total_tasks`` 把上一轮的算进来。
    """
    import shutil

    if ckpt_root.exists():
        shutil.rmtree(ckpt_root, ignore_errors=True)
    if db.exists():
        try:
            db.unlink()
        except OSError:
            shutil.rmtree(db, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="B5 E2E：3 条域内 + 2 条域外")
    ap.add_argument(
        "--llm",
        action="store_true",
        help="域内题走真实 LLM 生成（默认走确定性模板，保证重跑逐字节一致）",
    )
    args = ap.parse_args()

    ckpt_root = Path("data/sessions/checkpoints_eval")
    db = Path("data/sessions/hitl_eval.sqlite3")
    reset_state(ckpt_root, db)

    checkpoints = CheckpointStore(ckpt_root)
    corrections = CorrectionStore(db)
    # 默认 use_llm=False：确定性模板生成，保证「重跑两次结果一致」可逐字节校验。
    # 加 --llm 则走真实生成（答案文本会因采样波动，结构字段仍应一致）。
    pipe = HitlPipeline(checkpoints=checkpoints, corrections=corrections, use_llm=True if args.llm else False)
    gold = load_gold()

    cases = run_in_domain(pipe, gold) + run_out_domain(pipe)

    stats = pipe.stats()
    report = {
        "cases": cases,
        "all_pass": all(c["ok"] for c in cases),
        "stats": stats,
        "checkpoint_files": len(list(ckpt_root.glob("*.json"))),
        "off_domain_threshold": cases[0]["off_domain_threshold"],
        "generation": "llm" if args.llm else "deterministic",
    }
    out = Path("data/eval_out/hitl.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(out, report)
    log.info("E2E 全部通过=%s 统计=%s", report["all_pass"], json.dumps(stats, ensure_ascii=False))


if __name__ == "__main__":
    main()
