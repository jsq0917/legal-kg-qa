"""T25 HITL 扩展 E2E：24 h 挂起恢复 / 超时转人工队列 / 审计链 / 多轮中途挂起。

与 ``eval_hitl.py`` 的区别：那份只验「挂起-恢复」的主路径，本脚本验证生产化的
状态机与审计。所有时间走 ``Clock`` 注入，不 sleep，所以 24 h 超时可以在毫秒内复现:

    case-within   挂起 23 h  -> 未超时 -> 恢复成功
    case-expired  挂起 24.5 h -> sweep 自动转 manual_queue -> 仍可恢复（不删任务）
    case-multiturn 多轮第 2 轮挂起 -> 跨 24.5 h 恢复 -> 第 3 轮继续沿用槽位

判定口径：
* 「恢复成功」= status=completed 且 answer 非空；
* 「转人工队列」= checkpoint.status 从 awaiting_review 变 manual_queue，
  且审计里有一条 actor=system:sla 的 timeout_escalate；
* 「审计可追」= 单任务哈希链逐条连续（from_state_hash == 上一条 to_state_hash），
  且每条都有 actor / at_iso / to_state_hash；
* 「状态 JSON 不 pkl」= state 目录下只有 ``.json``，逐个可反序列化。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.__005__dialog.__001__session_store import SessionStore  # noqa: E402
from app.__006__hitl.__001__checkpoint import CheckpointStore  # noqa: E402
from app.__006__hitl.__002__correction import CorrectionStore  # noqa: E402
from app.__006__hitl.__003__pipeline import HitlPipeline  # noqa: E402
from app.__006__hitl.__004__audit import AuditLog  # noqa: E402
from common.logging_utils import get_logger  # noqa: E402
from common.path_utils import write_json  # noqa: E402

log = get_logger("eval_hitl_ext")

GOLD = Path("data/gold/gold_60.jsonl")
STATE_ROOT = Path("data/sessions/hitl_ext_eval")
SLA_HOURS = 24.0
TZ = timezone(timedelta(hours=8))


class Clock:
    """可推进的假时钟：``advance`` 只改读数，不 sleep。"""

    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, hours: float) -> float:
        self.now += hours * 3600.0
        return self.now


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).isoformat(timespec="seconds")


def load_gold() -> dict[str, dict]:
    return {g["id"]: g for g in (json.loads(line) for line in GOLD.open(encoding="utf-8") if line.strip())}


def reset_state() -> None:
    if STATE_ROOT.exists():
        shutil.rmtree(STATE_ROOT, ignore_errors=True)


def build_pipe(clock: Clock) -> tuple[HitlPipeline, AuditLog]:
    ckpt_root = STATE_ROOT / "checkpoints"
    corrections = CorrectionStore(STATE_ROOT / "hitl.sqlite3")
    audit = AuditLog(STATE_ROOT / "audit.jsonl", clock=clock)
    sessions = SessionStore(STATE_ROOT / "sessions.sqlite3")
    pipe = HitlPipeline(
        checkpoints=CheckpointStore(ckpt_root, clock=clock),
        corrections=corrections,
        audit=audit,
        sessions=sessions,
        use_llm=False,
        clock=clock,
        sla_seconds=SLA_HOURS * 3600.0,
    )
    return pipe, audit


def case_within_sla(pipe: HitlPipeline, clock: Clock, gold: dict[str, dict]) -> dict[str, Any]:
    """挂起 23 h（未到 SLA）-> 恢复成功。"""
    started = pipe.start(gold["g55"]["question"], force_review=True)
    task_id = started["task_id"]
    suspended_at = pipe.checkpoints.load(task_id).created_at
    clock.advance(23.0)
    escalated = pipe.sweep_timeouts()
    pending = pipe.pending()
    resumed = pipe.resume(task_id, {"type": "accept"}, actor="reviewer-a@within")
    ckpt = pipe.checkpoints.load(task_id)
    return {
        "id": "case-within-sla",
        "scenario": "挂起 23 h（< 24 h SLA）后人工确认",
        "task_id": task_id,
        "suspended": started["status"] == "awaiting_review",
        "suspended_at": iso(suspended_at),
        "resumed_at": iso(clock.now),
        "elapsed_hours": 23.0,
        "early_sweep_escalations": len(escalated),
        "pending_before_resume": len(pending),
        "status_before_resume": ckpt.status,
        "resolved_status": ckpt.status,
        "answer_len": len(resumed.get("answer") or ""),
        "answer_nonempty": bool(resumed.get("answer")),
        "ok": bool(
            started["status"] == "awaiting_review"
            and not escalated
            and resumed["status"] == "completed"
            and resumed.get("answer")
        ),
    }


def case_expired_24h(pipe: HitlPipeline, clock: Clock, gold: dict[str, dict]) -> dict[str, Any]:
    """挂起 24.5 h -> sweep 转 manual_queue -> 仍可恢复。"""
    started = pipe.start(gold["g08"]["question"], force_review=True)
    task_id = started["task_id"]
    suspended_at = pipe.checkpoints.load(task_id).created_at
    clock.advance(24.5)
    escalated = pipe.sweep_timeouts()
    queued = pipe.queue()
    ckpt_queued = pipe.checkpoints.load(task_id)
    queued_record = next((e for e in escalated if e["task_id"] == task_id), None)
    resumed = pipe.resume(task_id, {"type": "accept"}, actor="reviewer-b@queue")
    ckpt = pipe.checkpoints.load(task_id)
    return {
        "id": "case-expired-24h",
        "scenario": "挂起 24.5 h（> 24 h SLA）→ 自动转人工队列 → 队列中恢复",
        "task_id": task_id,
        "suspended_at": iso(suspended_at),
        "escalated_at": iso(ckpt_queued.escalated_at or clock.now),
        "elapsed_hours": 24.5,
        "overdue_s": queued_record["overdue_s"] if queued_record else None,
        "queue_size_after_sweep": len(queued),
        "status_after_sweep": ckpt_queued.status,
        "assigned_to": ckpt_queued.assigned_to,
        "status_after_resume": ckpt.status,
        "answer_len": len(resumed.get("answer") or ""),
        "answer_nonempty": bool(resumed.get("answer")),
        "escalated_flag_in_result": resumed.get("escalated"),
        "ok": bool(
            started["status"] == "awaiting_review"
            and ckpt_queued.status == "manual_queue"
            and len(queued) == 1
            and resumed["status"] == "completed"
            and resumed.get("answer")
        ),
    }


def case_multiturn(pipe: HitlPipeline, clock: Clock) -> dict[str, Any]:
    """多轮对话第 2 轮中途挂起 -> 超时转队列 -> 恢复后第 3 轮继续。"""
    session_id = f"mt-{uuid.uuid4().hex[:8]}"
    turn1 = pipe.start_turn(session_id, "公司拖欠我工资，从2024年3月开始上班", actor="user")
    turn2_started = pipe.start_turn(
        session_id, "月薪8000元，2024年9月离职，公司还拖欠我两个月工资没发", force_review=True
    )
    task_id = turn2_started["task_id"]
    turns_before_resume = pipe.sessions.turns(session_id)
    clock.advance(24.5)
    escalated = pipe.sweep_timeouts()
    queue_after_sweep = pipe.queue()
    queued = pipe.checkpoints.load(task_id)
    resumed = pipe.resume(task_id, {"type": "accept"}, actor="reviewer-c@multiturn")
    turn3 = pipe.start_turn(session_id, "那如果没签合同呢", actor="user")
    turns_after = pipe.sessions.turns(session_id)
    roles = [t["role"] for t in turns_after]
    resumed_turn = next(
        (t for t in turns_after if t["role"] == "assistant" and (t["payload"] or {}).get("resumed")), None
    )
    return {
        "id": "case-multiturn-mid-suspend",
        "scenario": "多轮第 2 轮挂起，跨 24.5 h 恢复，第 3 轮继续",
        "session_id": session_id,
        "turn1_status": turn1["status"],
        "turn1_missing": turn1.get("missing"),
        "turn2_status": turn2_started["status"],
        "task_id": task_id,
        "turn_no": turn2_started.get("turn_no"),
        "turns_before_resume": len(turns_before_resume),
        "suspended_while_no_assistant_answer": roles[:3] == ["user", "assistant", "user"],
        "status_after_sweep": queued.status,
        "queue_size_after_sweep": len(queue_after_sweep),
        "sweep_escalations": len(escalated),
        "resumed_status": resumed["status"],
        "resumed_answer_len": len(resumed.get("answer") or ""),
        "turn3_status": turn3["status"],
        "turn3_inherited_slots": turn3.get("inherited"),
        "turn3_slots": turn3.get("slots"),
        "turn_count": len(turns_after),
        "roles": roles,
        "resumed_turn_recorded": resumed_turn is not None,
        "ok": bool(
            turn1["status"] == "input_required"
            and turn2_started["status"] == "awaiting_review"
            and queued.status == "manual_queue"
            and resumed["status"] == "completed"
            and resumed.get("answer")
            and turn3["status"] in ("completed", "refused")
            and len(turns_after) == 6
            and resumed_turn is not None
        ),
    }


def check_storage() -> dict[str, Any]:
    ckpt_root = STATE_ROOT / "checkpoints"
    files = sorted(ckpt_root.glob("*"))
    json_files = [p for p in files if p.suffix == ".json"]
    pkl_files = [str(p.relative_to(STATE_ROOT)) for p in STATE_ROOT.rglob("*.pkl")]
    bad = []
    statuses: dict[str, int] = {}
    for path in json_files:
        try:
            blob = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            bad.append({"file": path.name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        status = blob.get("status", "?")
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "state_dir": str(STATE_ROOT),
        "checkpoint_files": len(files),
        "json_files": len(json_files),
        "non_json_files": [p.name for p in files if p.suffix != ".json"],
        "pkl_files": pkl_files,
        "unreadable": bad,
        "statuses": statuses,
        "ok": bool(json_files) and not bad and not pkl_files and len(json_files) == len(files),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="T25 HITL 扩展：24h 挂起 / 超时 / 审计 / 多轮")
    ap.parse_args()

    reset_state()
    clock = Clock(datetime(2026, 10, 31, 9, 0, tzinfo=TZ).timestamp())
    started_at = clock.now
    pipe, audit = build_pipe(clock)
    gold = load_gold()

    case_a = case_within_sla(pipe, clock, gold)
    case_b = case_expired_24h(pipe, clock, gold)
    case_c = case_multiturn(pipe, clock)

    task_ids = [case_a["task_id"], case_b["task_id"], case_c["task_id"]]
    verifications = {tid: audit.verify(tid) for tid in task_ids}
    all_entries = audit.entries()
    storage = check_storage()
    stats = pipe.stats()

    audit_report = {
        "entries": len(all_entries),
        "tasks": len(task_ids),
        "all_chains_ok": all(v["ok"] for v in verifications.values()),
        "all_fields_present": all(
            bool(e.get("actor") and e.get("at_iso") and e.get("to_state_hash")) for e in all_entries
        ),
        "by_task": verifications,
    }

    checks = {
        "within_sla_recovered": case_a["ok"],
        "expired_24h_to_human_queue": case_b["status_after_sweep"] == "manual_queue"
        and case_b["queue_size_after_sweep"] == 1,
        "expired_24h_still_recoverable": case_b["status_after_resume"] == "completed"
        and case_b["answer_nonempty"],
        "audit_chain_ok": audit_report["all_chains_ok"],
        "audit_who_when_hash": audit_report["all_fields_present"],
        "state_json_not_pkl": storage["ok"],
        "multiturn_mid_suspend_resumed": case_c["ok"],
    }

    report = {
        "schema": "t25-hitl-ext/v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sla_hours": SLA_HOURS,
        "clock": {
            "start": iso(started_at),
            "end": iso(clock.now),
            "elapsed_hours": round((clock.now - started_at) / 3600, 1),
        },
        "generation": "deterministic",
        "cases": [case_a, case_b, case_c],
        "audit": audit_report,
        "storage": storage,
        "stats": stats,
        "checks": checks,
        "all_pass": all(checks.values()),
    }
    out = Path("data/eval_out/hitl_ext.json")
    write_json(out, report)
    log.info("T25 全部通过=%s checks=%s", report["all_pass"], json.dumps(checks, ensure_ascii=False))
    if not report["all_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
