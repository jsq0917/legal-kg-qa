"""人在环流水线：低置信挂起 -> 人工修正 -> 恢复生成。

判定「低置信」用的是两个可解释信号（与配置项一致）：
* ``top1_similarity`` —— 稠密检索最高分，衡量「问题与法律语料是否对口」；
* ``coverage``         —— 图路路径覆盖度，衡量「概念链是否真的在图上拼起来」。
两者**同时**低于阈值才挂起：单看任何一个都会把「图没覆盖但文本很准」的正常问题误挂。

T25 把「挂起-恢复」扩成生产可用状态机（状态全部落 JSON）：
* ``awaiting_review`` --截止到 ``deadline_at``（默认 24 h）--> ``manual_queue``
  （``sweep_timeouts`` 超时自动转人工队列，**不删任务**，转队列后仍可恢复）；
* 每次状态变更写审计链（who / when / 变更前后状态哈希），见 ``__004__audit``；
* ``start_turn`` / ``resume`` 支持多轮对话中途挂起：挂起时槽位快照进
  ``dialog_state``，恢复后答案回写到会话，下一轮继续沿用槽位。

域外拒答（``assess_domain``）发生在检索之后、生成之前：先看问题有没有链接到
「劳动法专属概念」，再看 ``top1_similarity`` 是否低于 ``OFF_DOMAIN_THRESHOLD``；
任一不满足就直接返回固定话术、``citations=[]``、``refused=True``，**不调 LLM**。
之所以不能只用分数：语料 1465 条里 1260 条是民法典，「离婚财产分割」实测
top1=0.675，比金标中位数还高，纯阈值挡不住。

修正类型（会落库统计分布）：
* ``accept``          —— 人工确认系统结果，不改内容；
* ``edit_articles``   —— 换掉引用条文后重新生成答案；
* ``edit_answer``     —— 直接改答案文本；
* ``provide_answer``  —— 人工直接给最终答案（系统检索完全不可用时）。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.__004__retrieval.__005__retriever import get_retriever  # noqa: E402
from app.__006__hitl.__000__generator import generate_answer  # noqa: E402
from app.__006__hitl.__001__checkpoint import Checkpoint, CheckpointStore  # noqa: E402
from app.__006__hitl.__002__correction import CorrectionStore  # noqa: E402
from app.__006__hitl.__004__audit import AuditLog  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402

log = get_logger("hitl")

VALID_ACTIONS = ("accept", "edit_articles", "edit_answer", "provide_answer")

#: 默认挂起 SLA：24 小时。可由 ``HITL_SLA_HOURS`` 覆盖（不写进 config.py，避免和 T05 撞文件）。
DEFAULT_SLA_HOURS = 24.0


def _default_sla_seconds() -> float:
    try:
        return float(os.environ.get("HITL_SLA_HOURS", DEFAULT_SLA_HOURS)) * 3600.0
    except ValueError:
        return DEFAULT_SLA_HOURS * 3600.0


#: 域外问题的固定话术。不含任何条号，也不引用具体法律，避免「拒答了但还在引条文」的伪拒答。
REFUSAL_ANSWER = "该问题超出劳动法咨询范围，建议咨询其他领域专业人士。"

LABOR_LAWS = frozenset({"中华人民共和国劳动法", "中华人民共和国劳动合同法"})


@lru_cache(maxsize=1)
def labor_concept_ids() -> frozenset[str]:
    """只属于劳动法域的概念 id（``laws`` 全部落在劳动法/劳动合同法内）。

    为什么要区分「专属」：``liquidated_damages``（违约金）同时挂民法典和劳动合同法，
    一个房屋买卖合同纠纷也能链接到它；只有 ``work_injury``/``non_compete`` 这类
    只由劳动法定义的概念，才能说明「这是一个劳动关系问题」。
    """
    from config import settings

    path = settings.lexicon_dir / "concepts.jsonl"
    if not path.exists():
        log.warning("概念表缺失，域内判定退化为纯阈值: %s", path)
        return frozenset()
    ids = set()
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            c = json.loads(line)
            laws = set(c.get("laws") or [])
            if laws and laws <= LABOR_LAWS:
                ids.add(c["id"])
    return frozenset(ids)


def assess_domain(retrieval: dict[str, Any], threshold: float | None = None) -> dict[str, Any]:
    """域内外判定：劳动法专属概念命中（硬闸）+ 检索最高分（软闸）。

    只用 top1 相似度不够：语料 1465 条里 1260 条是民法典，实测「离婚财产分割」的
    top1=0.675、「房屋买卖合同违约」=0.711，比金标中位数（0.681）还高 —— 任何
    纯阈值都会把域外题判成域内。所以先用概念链接把「劳动关系问题」筛出来，
    再用金标 5% 分位卡证据强度。
    """
    from config import settings

    thr = settings.off_domain_threshold if threshold is None else threshold
    concepts = [c.get("id") for c in ((retrieval.get("linked") or {}).get("concepts") or [])]
    hits = [c for c in concepts if c in labor_concept_ids()]
    top = float(retrieval.get("top1_similarity") or 0.0)
    # text 模式不跑实体链接，概念闸无从判断，退化为纯分数闸（否则整档都会被误拒）。
    linkable = retrieval.get("mode", "hybrid") != "text"

    reasons = []
    if linkable and not hits:
        reasons.append("未链接到劳动法域概念")
    if top < thr:
        reasons.append(f"top1={top:.4f} < 阈值 {thr}")
    return {
        "refused": bool(reasons),
        "reason": "；".join(reasons),
        "retrieval_top_score": round(top, 4),
        "off_domain_threshold": thr,
        "domain_concepts": hits,
        "linkable": linkable,
    }


class HitlPipeline:
    def __init__(
        self,
        retriever: Any | None = None,
        checkpoints: CheckpointStore | None = None,
        corrections: CorrectionStore | None = None,
        use_llm: bool | None = None,
        off_domain_threshold: float | None = None,
        clock: Callable[[], float] | None = None,
        audit: AuditLog | None = None,
        sessions: Any | None = None,
        sla_seconds: float | None = None,
    ) -> None:
        self._retriever = retriever
        self._clock = clock
        self.checkpoints = checkpoints or CheckpointStore(clock=self._now)
        self.corrections = corrections or CorrectionStore()
        self.audit = audit or AuditLog(clock=self._now)
        self._sessions = sessions
        self.use_llm = use_llm
        self.off_domain_threshold = off_domain_threshold
        self.sla_seconds = _default_sla_seconds() if sla_seconds is None else sla_seconds

    def _now(self) -> float:
        return (self._clock or time.time)()

    @property
    def retriever(self) -> Any:
        if self._retriever is None:
            self._retriever = get_retriever()
        return self._retriever

    @property
    def sessions(self) -> Any:
        if self._sessions is None:
            from app.__005__dialog.__001__session_store import SessionStore

            self._sessions = SessionStore()
        return self._sessions

    def _audit(
        self,
        ckpt: Checkpoint,
        *,
        actor: str,
        action: str,
        from_status: str,
        from_hash: str | None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.audit.record(
            task_id=ckpt.task_id,
            actor=actor,
            action=action,
            from_status=from_status,
            to_status=ckpt.status,
            from_state_hash=from_hash,
            to_state_hash=ckpt.state_hash(),
            detail=detail,
        )

    # ---------------- 入口 ----------------
    def start(self, query: str, mode: str = "hybrid", force_review: bool = False, owner: str = "") -> dict[str, Any]:
        task_id = uuid.uuid4().hex[:10]
        retrieval = self.retriever.retrieve(query, mode=mode).as_dict()

        # 域外先于挂起：不合域的问题没有「人工确认」的价值，直接拒答，且不调 LLM。
        gate = assess_domain(retrieval, self.off_domain_threshold)
        if gate["refused"]:
            self.corrections.log_task(task_id, "refused")
            log.info("域外拒答: %s (%s)", query[:20], gate["reason"])
            return {
                "task_id": task_id,
                "status": "refused",
                "refused": True,
                "answer": REFUSAL_ANSWER,
                "citations": [],
                "used_llm": False,
                "reason": gate["reason"],
                "retrieval_top_score": gate["retrieval_top_score"],
                "off_domain_threshold": gate["off_domain_threshold"],
                "retrieval": retrieval,
            }

        low = retrieval["low_confidence"]

        if low or force_review:
            reason = (
                f"top1={retrieval['top1_similarity']:.3f} < {self.retriever.settings.refuse_threshold}"
                f" 且 coverage={retrieval['coverage']:.3f} < {self.retriever.settings.path_coverage_threshold}"
                if low
                else "人工强制复核"
            )
            now = self._now()
            ckpt = Checkpoint(
                task_id=task_id,
                query=query,
                mode=mode,
                status="awaiting_review",
                reason=reason,
                top1_similarity=retrieval["top1_similarity"],
                coverage=retrieval["coverage"],
                retrieval=retrieval,
                created_at=now,
                updated_at=now,
                deadline_at=now + self.sla_seconds,
                owner=owner,
            )
            self.checkpoints.save(ckpt)
            self._audit(
                ckpt,
                actor="system",
                action="suspend",
                from_status="new",
                from_hash=None,
                detail={"reason": reason},
            )
            self.corrections.log_task(task_id, "awaiting_review")
            return {
                "task_id": task_id,
                "status": "awaiting_review",
                "refused": False,
                "reason": reason,
                "deadline_at": ckpt.deadline_at,
                "retrieval_top_score": gate["retrieval_top_score"],
                "off_domain_threshold": gate["off_domain_threshold"],
                "retrieval": retrieval,
            }

        gen = generate_answer(query, retrieval, use_llm=self.use_llm)
        self.corrections.log_task(task_id, "completed")
        return {
            "task_id": task_id,
            "status": "completed",
            "refused": False,
            "answer": gen["answer"],
            "citations": gen["citations"],
            "used_llm": gen["used_llm"],
            "retrieval_top_score": gate["retrieval_top_score"],
            "off_domain_threshold": gate["off_domain_threshold"],
            "retrieval": retrieval,
        }

    def resume(
        self, task_id: str, action: dict[str, Any], actor: str | None = None, client_id: str = ""
    ) -> dict[str, Any]:
        ckpt = self.checkpoints.load(task_id)
        if ckpt is None:
            raise KeyError(f"找不到挂起任务 {task_id}")
        if ckpt.owner and client_id and ckpt.owner != client_id:
            raise PermissionError("该任务归属其他客户端")
        if ckpt.status == "completed" and ckpt.answer:
            return {
                "task_id": task_id,
                "status": "completed",
                "answer": ckpt.answer,
                "resolution": ckpt.resolution,
                "already_done": True,
            }

        ctype = action.get("type", "accept")
        if ctype not in VALID_ACTIONS:
            raise ValueError(f"未知修正类型: {ctype}")
        who = actor or action.get("actor") or "human:anonymous"

        from_status = ckpt.status
        from_hash = ckpt.state_hash()
        answer, citations = ckpt.answer, ckpt.retrieval.get("items", [])
        used_llm = False
        if ctype == "accept":
            gen = generate_answer(ckpt.query, ckpt.retrieval, use_llm=self.use_llm)
            answer, citations, used_llm = gen["answer"], gen["citations"], gen["used_llm"]
        elif ctype == "edit_articles":
            answer, citations, used_llm = self._rebuild_with_articles(ckpt, action.get("article_ids") or [])
        elif ctype in ("edit_answer", "provide_answer"):
            answer = action.get("answer") or ""
            if not answer:
                raise ValueError("edit_answer/provide_answer 需要提供 answer")
            citations = action.get("citations") or citations

        now = self._now()
        ckpt.status = "completed"
        ckpt.answer = answer
        ckpt.resolution = {"type": ctype, "payload": action, "at": now, "actor": who}
        self.checkpoints.save(ckpt)
        self._audit(
            ckpt,
            actor=who,
            action=f"resolve:{ctype}",
            from_status=from_status,
            from_hash=from_hash,
            detail={"type": ctype, "was_escalated": bool(ckpt.escalated_at), "deadline_at": ckpt.deadline_at},
        )
        self.corrections.log_correction(task_id, ctype, {**action, "actor": who})
        self.corrections.log_task(task_id, "completed")

        # 多轮中途挂起：把人工确认后的答案回写到会话，下一轮才能接着聊。
        if ckpt.session_id:
            self.sessions.append_turn(
                ckpt.session_id,
                "assistant",
                answer,
                {
                    "status": "answered",
                    "task_id": task_id,
                    "resolution": ctype,
                    "used_llm": used_llm,
                    "resumed": True,
                },
            )

        return {
            "task_id": task_id,
            "status": "completed",
            "answer": answer,
            "citations": citations,
            "used_llm": used_llm,
            "resolution": ckpt.resolution,
            "already_done": False,
            "session_id": ckpt.session_id,
            "turn_no": ckpt.turn_no,
            "escalated": bool(ckpt.escalated_at),
        }

    # ---------------- 超时：自动转人工队列 ----------------
    def sweep_timeouts(self, now: float | None = None) -> list[dict[str, Any]]:
        """把超过 SLA 的挂起任务转进人工队列（``manual_queue``），并写审计。

        不删除任务：转队列只是升级处理人，之后照样能 ``resume``。
        旧 checkpoint 没有 ``deadline_at`` 时，按 ``created_at + sla_seconds`` 补算。
        """
        t = self._now() if now is None else now
        escalated: list[dict[str, Any]] = []
        for ckpt in self.checkpoints.list("awaiting_review"):
            deadline = (
                ckpt.deadline_at if ckpt.deadline_at is not None else ckpt.created_at + self.sla_seconds
            )
            if t < deadline:
                continue
            from_status = ckpt.status
            from_hash = ckpt.state_hash()
            ckpt.status = "manual_queue"
            ckpt.escalated_at = t
            ckpt.assigned_to = "human_queue"
            self.checkpoints.save(ckpt)
            overdue = round(t - deadline, 3)
            self._audit(
                ckpt,
                actor="system:sla",
                action="timeout_escalate",
                from_status=from_status,
                from_hash=from_hash,
                detail={
                    "queue": "human",
                    "deadline_at": deadline,
                    "overdue_s": overdue,
                    "sla_seconds": self.sla_seconds,
                },
            )
            self.corrections.log_task(ckpt.task_id, "manual_queue")
            escalated.append(
                {
                    "task_id": ckpt.task_id,
                    "deadline_at": deadline,
                    "escalated_at": t,
                    "overdue_s": overdue,
                    "queue": "human",
                }
            )
        if escalated:
            log.info("超时转人工队列 %d 条", len(escalated))
        return escalated

    # ---------------- 多轮：中途挂起 ----------------
    def start_turn(
        self,
        session_id: str,
        text: str,
        mode: str = "hybrid",
        force_review: bool = False,
        actor: str = "user",
        owner: str = "",
    ) -> dict[str, Any]:
        """多轮对话的一轮：槽位继承与追问沿用 B4 的规则，低置信时挂起整轮。

        挂起时只写 user 轮，不写 assistant 轮；恢复后由 ``resume`` 回写答案，
        所以会话历史里不会出现「挂起了一半的假答案」。
        """
        from app.__005__dialog.__000__slots import (
            SLOT_QUESTIONS,
            SlotState,
            detect_intent,
            extract_slots,
            missing_slots,
        )
        from app.__005__dialog.__002__dialog_manager import ASK_MARKERS, DialogManager, is_followup

        state = SlotState.from_json(self.sessions.load_state(session_id))
        prev = dict(state.values)
        followup = is_followup(text) and bool(prev)
        intent = detect_intent(text)
        asking = any(m in text for m in ASK_MARKERS)
        if state.intent != "other" and not asking:
            intent = state.intent
        elif intent == "other" and state.intent != "other":
            intent = state.intent
        extracted = extract_slots(text)
        inherited = [k for k in prev if k not in extracted] if followup else []
        state.intent = intent
        state.merge(extracted)

        task_id = uuid.uuid4().hex[:10]
        self.sessions.save_state(session_id, state.to_json())
        self.sessions.append_turn(
            session_id, "user", text, {"intent": intent, "extracted": extracted, "inherited": inherited}
        )
        turn_no = sum(1 for t in self.sessions.turns(session_id) if t["role"] == "user")

        missing = missing_slots(state.values, intent)
        if missing:
            question = SLOT_QUESTIONS[missing[0]]
            self.sessions.append_turn(
                session_id, "assistant", question, {"status": "input_required", "missing": missing}
            )
            return {
                "task_id": task_id,
                "session_id": session_id,
                "turn_no": turn_no,
                "status": "input_required",
                "intent": intent,
                "slots": dict(state.values),
                "missing": missing,
                "question": question,
                "inherited": inherited,
            }

        retrieval = self.retriever.retrieve(DialogManager.compose_query(text, state), mode=mode).as_dict()
        gate = assess_domain(retrieval, self.off_domain_threshold)
        if gate["refused"]:
            self.sessions.append_turn(
                session_id, "assistant", REFUSAL_ANSWER, {"status": "refused", "reason": gate["reason"]}
            )
            self.corrections.log_task(task_id, "refused")
            return {
                "task_id": task_id,
                "session_id": session_id,
                "turn_no": turn_no,
                "status": "refused",
                "refused": True,
                "answer": REFUSAL_ANSWER,
                "citations": [],
                "reason": gate["reason"],
                "retrieval_top_score": gate["retrieval_top_score"],
                "retrieval": retrieval,
                "inherited": inherited,
            }

        low = retrieval["low_confidence"]
        if low or force_review:
            reason = (
                f"top1={retrieval['top1_similarity']:.3f} < {self.retriever.settings.refuse_threshold}"
                f" 且 coverage={retrieval['coverage']:.3f} < {self.retriever.settings.path_coverage_threshold}"
                if low
                else "人工强制复核"
            )
            now = self._now()
            ckpt = Checkpoint(
                task_id=task_id,
                query=text,
                mode=mode,
                status="awaiting_review",
                reason=reason,
                top1_similarity=retrieval["top1_similarity"],
                coverage=retrieval["coverage"],
                retrieval=retrieval,
                created_at=now,
                updated_at=now,
                deadline_at=now + self.sla_seconds,
                session_id=session_id,
                turn_no=turn_no,
                dialog_state=state.to_json(),
                owner=owner,
            )
            self.checkpoints.save(ckpt)
            self._audit(
                ckpt,
                actor=actor,
                action="suspend_turn",
                from_status="new",
                from_hash=None,
                detail={"session_id": session_id, "turn_no": turn_no, "reason": reason},
            )
            self.corrections.log_task(task_id, "awaiting_review")
            return {
                "task_id": task_id,
                "session_id": session_id,
                "turn_no": turn_no,
                "status": "awaiting_review",
                "refused": False,
                "reason": reason,
                "deadline_at": ckpt.deadline_at,
                "slots": dict(state.values),
                "retrieval_top_score": gate["retrieval_top_score"],
                "retrieval": retrieval,
            }

        gen = generate_answer(text, retrieval, use_llm=self.use_llm)
        self.sessions.append_turn(
            session_id,
            "assistant",
            gen["answer"],
            {"status": "answered", "task_id": task_id, "used_llm": gen["used_llm"]},
        )
        self.corrections.log_task(task_id, "completed")
        return {
            "task_id": task_id,
            "session_id": session_id,
            "turn_no": turn_no,
            "status": "completed",
            "refused": False,
            "answer": gen["answer"],
            "citations": gen["citations"],
            "used_llm": gen["used_llm"],
            "slots": dict(state.values),
            "retrieval_top_score": gate["retrieval_top_score"],
            "retrieval": retrieval,
            "inherited": inherited,
        }

    def _rebuild_with_articles(
        self, ckpt: Checkpoint, article_ids: list[str]
    ) -> tuple[str, list[dict], bool]:
        text = self.retriever.text
        items = []
        for aid in article_ids:
            art = text.articles.get(aid)
            if not art:
                continue
            items.append(
                {
                    "article_id": aid,
                    "citation": art["citation"],
                    "law_name": art["law_name"],
                    "content": art["content"],
                    "score": 1.0,
                    "source": "human",
                }
            )
        retrieval = {**ckpt.retrieval, "items": items}
        gen = generate_answer(ckpt.query, retrieval, use_llm=self.use_llm)
        return gen["answer"], gen["citations"], gen["used_llm"]

    # ---------------- 统计 ----------------
    def pending(self) -> list[dict[str, Any]]:
        # 懒扫描：任何人来看待办，都先把超过 SLA 的转进人工队列，避免「无人催办」。
        self.sweep_timeouts()
        return [c.to_json() for c in self.checkpoints.list("awaiting_review")]

    def queue(self) -> list[dict[str, Any]]:
        """人工队列：超时升级后待处理的任务。"""
        self.sweep_timeouts()
        return [c.to_json() for c in self.checkpoints.list("manual_queue")]

    def stats(self) -> dict[str, Any]:
        all_ckpts = self.checkpoints.list()
        stats = self.corrections.stats(len(all_ckpts))
        stats["pending"] = sum(1 for c in all_ckpts if c.status == "awaiting_review")
        stats["manual_queue"] = sum(1 for c in all_ckpts if c.status == "manual_queue")
        stats["completed"] = sum(1 for c in all_ckpts if c.status == "completed")
        return stats
