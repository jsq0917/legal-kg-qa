"""Streamlit 演示前端：把 B3/B4/B5/B6 四个验收项各开一个页签，并补 T27/T29。

刻意做成「一屏能看懂」：
* 问答页直接暴露三档编排（A/B/C）与三档检索（text/graph/hybrid），
  逐项显示五道闸的通过情况，并在同一次请求后给出 trace（检索 / LLM / 闸门 / token / 成本）；
* 条文表里的引用可以点回原文（页内锚点），不是只给一个条号；
* 可观测性页读 ``data/observability/trace.jsonl``，汇总请求数 / token / 成本 / 延迟；
* 演示场景页把两个「看得见的失败」摆出来：「注入被拦」与「域外拒答」；
* 多轮页显示槽位、缺失项、本轮继承项与指代标记 —— 这是 B4 的可观测性；
* 复核页列出挂起任务并支持三种人工介入方式 —— 这是 B5 的闭环；
* 评测页直接读 ``data/eval_out/*.json``，页面上看到的就是报告里的数字。

启动：``streamlit run app/__009__ui/__000__app.py``
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd
import streamlit as st

from app.__008__api.__005__trace import (
    TRACE_PATH,
    install_llm_hook,
    measure_gate_ms,
    recent_traces,
    summarize_traces,
    trace_request,
    write_trace,
)
from common import prompt_guard
from common.llm import llm_status
from config import settings

EVAL_FILES = {
    "B2 金标准": "data/eval_out/gold.json",
    "B3 检索三档": "data/eval_out/retrieval.json",
    "B3 路由": "data/eval_out/router.json",
    "B4 多轮": "data/eval_out/dialog.json",
    "B5 人在环": "data/eval_out/hitl.json",
    "B6 消融": "data/eval_out/ablation.json",
}

INJECTION_FILE = Path("data/security/injection_20.jsonl")
OFF_DOMAIN_CASES = ("离婚财产分割怎么办？", "房屋买卖合同违约了怎么索赔？")


@st.cache_resource(show_spinner="加载检索器与图（首次约 20s）…")
def load_runtime():
    from app.__004__retrieval.__005__retriever import get_retriever
    from app.__006__hitl.__003__pipeline import HitlPipeline
    from app.__007__agents.__001__gates import build_article_concepts
    from app.__007__agents.__002__flows import make_flow

    install_llm_hook()
    ret = get_retriever()
    art_concepts = build_article_concepts(ret.graph.store)
    flows = {t: make_flow(ret, ret.text.articles, t, art_concepts=art_concepts) for t in ("A_gates", "A", "B", "C")}
    return ret, flows, art_concepts, HitlPipeline(retriever=ret)


def gate_badge(check: dict) -> str:
    return ("✅ " if check["ok"] else "❌ ") + f"**{check['gate']}** — {check['detail']}"


def anchor_id(article_id: str) -> str:
    """条号里有 ``#``，不能直接当 HTML 锚点；统一成 ``art-lcl-85`` 这种形态。"""
    return "art-" + re.sub(r"[^A-Za-z0-9]+", "-", str(article_id)).strip("-")


def render_trace(trace: dict | None) -> None:
    if not trace:
        return
    stages = trace.get("stages") or {}
    llm = trace.get("llm") or {}
    cost = trace.get("cost") or {}
    st.subheader("本次请求 trace")
    cols = st.columns(5)
    cols[0].metric("总耗时(ms)", trace.get("latency_ms", 0))
    cols[1].metric("检索(ms)", stages.get("retrieve_ms", 0))
    cols[2].metric("LLM(ms)", stages.get("llm_ms", 0))
    cols[3].metric("闸门(ms)", stages.get("gate_ms", 0))
    cols[4].metric("Token", llm.get("total_tokens", 0))
    rows = [
        ("检索", stages.get("retrieve_ms", 0)),
        ("LLM", stages.get("llm_ms", 0)),
        ("闸门（含编排）", stages.get("gate_ms", 0)),
        ("其他开销", stages.get("overhead_ms", 0)),
    ]
    total = max(float(trace.get("latency_ms") or 0), 1e-9)
    st.dataframe(
        pd.DataFrame(
            [{"阶段": name, "耗时(ms)": ms, "占比": f"{(ms / total) * 100:.1f}%"} for name, ms in rows]
        ),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        f"成本 ¥{cost.get('cny', 0):.6f}（{cost.get('basis')}）；"
        f"cache={trace.get('cache')}；trace_id=`{trace.get('request_id')}`"
    )
    if stages.get("gate_ms_is_derived"):
        st.caption("注：闸门耗时 = 总耗时 − 检索 − LLM，含 LangGraph 编排开销，不是单独埋点。")


def show_answer(state: dict) -> None:
    st.markdown(state.get("answer", ""))
    trace = state.get("trace") or {}
    latency = state.get("latency_ms") or trace.get("latency_ms", 0)
    cols = st.columns(4)
    cols[0].metric("耗时(ms)", latency)
    cols[1].metric("LLM 调用", state.get("llm_calls", 0))
    cols[2].metric("覆盖度", state.get("retrieval", {}).get("coverage", 0))
    cols[3].metric("top1 相似", state.get("retrieval", {}).get("top1_similarity", 0))
    if state.get("refused"):
        st.warning("系统判定证据不足，给出的是「无法确定」类回答，应转人工复核。")
    if state.get("path_note"):
        with st.expander("推理链路（规划节点产出）"):
            st.write(state["path_note"])
    if state.get("checks"):
        st.subheader("五道闸")
        for c in state["checks"]:
            st.markdown(gate_badge(c))
        if state.get("repaired"):
            st.info(f"触发确定性修复，已重试 {state.get('attempts', 0)} 轮。")
    items = state.get("retrieval", {}).get("items", [])
    if items:
        st.subheader("检索到的条文")
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "条文": i["citation"],
                        "分数": round(i.get("score", 0.0), 4),
                        "来源": i.get("source"),
                        "覆盖": i.get("coverage"),
                        "图路径": " -> ".join(i.get("path") or []) or "-",
                    }
                    for i in items
                ]
            ),
            use_container_width=True,
        )
        st.markdown(
            "跳原文："
            + " · ".join(
                f"[{i['citation']}](#{anchor_id(i['article_id'])})" for i in items
            )
        )
        st.caption("（点击条号会滚到下方对应的条文原文；锚点只在本页生效。）")
        for i in items:
            aid = anchor_id(i["article_id"])
            st.markdown(f'<a id="{aid}"></a>', unsafe_allow_html=True)
            st.markdown(f"**{i['citation']}**（{i.get('law_name')}）\n\n{i.get('content', '')}")
    linked = state.get("retrieval", {}).get("linked", {})
    if linked.get("concepts"):
        st.caption("命中概念：" + "、".join(f"{c['name']}({c['score']:.2f})" for c in linked["concepts"]))
    if linked.get("scenarios"):
        st.caption("命中场景：" + "、".join(f"{s['name']}({s['score']:.2f})" for s in linked["scenarios"]))
    render_trace(state.get("trace"))


def render_qa(ret, flows, art_concepts) -> None:
    c1, c2, c3 = st.columns([3, 1, 1])
    query = c1.text_input("问题", value="公司拖了我两个月工资，我能要回来吗？", key="qa_query")
    tier_label = c2.selectbox(
        "编排", ["A_gates 单链路+五闸", "A 单链路", "B 多 Agent", "C 多 Agent+五闸"], index=0, key="qa_tier"
    )
    tier = tier_label.split(" ")[0]
    mode = c3.selectbox("检索", ["hybrid", "text", "graph"], index=0, key="qa_mode")
    if st.button("提问", type="primary", key="qa_btn") and query.strip():
        with st.spinner("检索 + 生成 + 校验…"):
            llm_on = bool(llm_status()["enabled"])
            with trace_request(
                "/ui-ask",
                query=query,
                tier=tier,
                mode=mode,
                llm_enabled=llm_on,
                model=settings.llm_model if llm_on else None,
            ) as tr:
                state = flows[tier](query, mode=mode)
                gate_ms = measure_gate_ms(state, ret.text.articles, art_concepts) if tier in ("C", "A_gates") else None
                trace = tr.finish(state, cache="miss", gate_ms=gate_ms)
            write_trace(trace)
            state["trace"] = trace
        st.session_state["qa_state"] = state
    if "qa_state" in st.session_state:
        show_answer(st.session_state["qa_state"])


def render_observability() -> None:
    st.caption(f"数据源：`{TRACE_PATH}`（每次 `/ask` 或本页提问追加一行；单进程文件，不引 collector）")
    if st.button("刷新", key="obs_refresh"):
        st.rerun()
    summary = summarize_traces()
    overall = summary["overall"]
    cols = st.columns(4)
    cols[0].metric("窗口内请求", overall["n"])
    cols[1].metric("缓存命中率", f"{summary['cache_hit_rate'] * 100:.1f}%")
    cols[2].metric("累计 Token", overall["total_tokens"])
    cols[3].metric("累计成本 ¥", f"{overall['cost_cny']:.6f}")
    cols2 = st.columns(3)
    cols2[0].metric("p50 延迟(ms)", overall["p50_ms"])
    cols2[1].metric("p95 延迟(ms)", overall["p95_ms"])
    cols2[2].metric("LLM 调用", overall["llm_calls"])
    st.caption(f"成本口径：{summary['cost_basis']}（¥{summary['cost_cny_per_1k_tokens']}/1k tok）")
    if summary["by_tier"]:
        st.subheader("按档位")
        st.dataframe(
            pd.DataFrame(summary["by_tier"]).T,
            use_container_width=True,
        )
    rows = recent_traces(n=20)
    if not rows:
        st.info("还没有 trace。先在「图谱问答」提一个问题，或调用一次 `POST /ask`。")
        return
    st.subheader("最近请求")
    flat = [
        {
            "time": r.get("ts"),
            "path": r.get("path"),
            "tier": r.get("tier"),
            "cache": r.get("cache"),
            "total_ms": r.get("latency_ms"),
            "retrieve_ms": (r.get("stages") or {}).get("retrieve_ms"),
            "llm_ms": (r.get("stages") or {}).get("llm_ms"),
            "gate_ms": (r.get("stages") or {}).get("gate_ms"),
            "tokens": (r.get("llm") or {}).get("total_tokens"),
            "cost_cny": (r.get("cost") or {}).get("cny"),
            "failed_gates": ",".join((r.get("gates") or {}).get("failed") or []) or "-",
        }
        for r in rows
    ]
    st.dataframe(pd.DataFrame(flat), use_container_width=True, hide_index=True)


def load_injection_cases() -> list[dict]:
    if not INJECTION_FILE.exists():
        return []
    rows = [json.loads(line) for line in INJECTION_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("injected_doc")]


def render_demo(ret) -> None:
    from app.__006__hitl.__003__pipeline import REFUSAL_ANSWER, assess_domain

    st.subheader("场景一：注入被拦（间接注入，指令藏在条文里）")
    st.caption("复用 T28 的 `prompt_guard`：命中指令片段 → 替换成占位符 → 包进 `<doc>` 数据边界。不调 LLM。")
    cases = load_injection_cases()
    if not cases:
        st.warning(f"缺少 {INJECTION_FILE}，先跑 T28 的注入评测。")
    else:
        labels = {f"{c['id']} · {c['category']} · {c['query'][:24]}": c for c in cases}
        picked = st.selectbox("注入样本", list(labels), key="demo_inj")
        case = labels[picked]
        doc = case["injected_doc"]
        original = doc.get("content", "")
        sanitized, hits = prompt_guard.sanitize_with_stats(original)
        left, right = st.columns(2)
        with left:
            st.markdown("**净化前（检索到的条文原文）**")
            st.text(original)
        with right:
            st.markdown("**净化后（进 prompt 的文本）**")
            st.text(sanitized)
        cols = st.columns(3)
        cols[0].metric("命中规则", len(hits))
        cols[1].metric("替换字符数", len(original) - len(sanitized))
        cols[2].metric("占位符", prompt_guard.INSTRUCTION_PLACEHOLDER)
        if hits:
            st.success("注入指令已拦截：" + "、".join(f"{k}×{v}" for k, v in hits.items()))
            st.caption(
                "如实说明：guard 只替换命中的指令片段，不整段删除。"
                "右侧仍可能残留半句指令文本，靠 `<doc>` 数据边界声明与五道闸兜底，不是「净化后即安全」。"
            )
        else:
            st.warning("这条样本没有命中任何规则——如实显示，不粉饰。")
        with st.expander("数据边界预览（真实进 prompt 的形态）"):
            st.code(prompt_guard.guard_block([doc]), language="text")

    st.divider()
    st.subheader("场景二：域外拒答（问题不在劳动法域内）")
    st.caption("复用 T05 的 `assess_domain`：劳动法专属概念硬闸 + top1 相似度软闸；拒答不调 LLM、引用为空。")
    col1, col2 = st.columns([3, 1])
    q = col1.text_input("问题", value=OFF_DOMAIN_CASES[0], key="demo_off_q")
    preset = col2.selectbox("预设", ["自定义", *OFF_DOMAIN_CASES], key="demo_off_preset")
    if preset != "自定义":
        q = preset
    if st.button("判定", key="demo_off_btn"):
        retrieval = ret.retrieve(q).as_dict()
        gate = assess_domain(retrieval)
        st.session_state["off_domain"] = {"query": q, "gate": gate, "retrieval": retrieval}
    info = st.session_state.get("off_domain")
    if info:
        gate = info["gate"]
        if gate["refused"]:
            st.error(f"已拒答：{gate['reason']}")
            st.markdown(f"**返回话术**：{REFUSAL_ANSWER}")
            st.caption("citations = []，used_llm = false —— 拒答不是「硬答完再道歉」。")
        else:
            st.success("未触发拒答（判定为域内问题）")
        cols = st.columns(3)
        cols[0].metric("top1", gate["retrieval_top_score"])
        cols[1].metric("阈值", gate["off_domain_threshold"])
        cols[2].metric("劳动法概念命中", len(gate["domain_concepts"]))
        st.caption("检索到的前三条（域外题也会检索到民法典条文，所以不能只看分数）：")
        st.dataframe(
            pd.DataFrame(info["retrieval"].get("items", [])[:3])[
                ["citation", "law_name", "score"]
            ],
            use_container_width=True,
            hide_index=True,
        )


def render_dialog(ret) -> None:
    from app.__005__dialog.__002__dialog_manager import DialogManager

    if "dm" not in st.session_state:
        st.session_state.dm = DialogManager(retriever=ret)
        st.session_state.sid = st.session_state.dm.new_session()
        st.session_state.turns = []
    dm = st.session_state.dm
    st.caption(f"会话 ID：`{st.session_state.sid}`（刷新后仍可按此恢复，槽位状态存在 SQLite）")
    text = st.text_input("输入一轮，例如「2024年5月离职了」或「那如果没签合同呢」", key="dlg_input")
    if st.button("发送", key="dlg_btn") and text.strip():
        turn = dm.handle(st.session_state.sid, text)
        st.session_state.turns.append(turn.as_dict())
    for t in st.session_state.turns:
        st.markdown(f"**第 {t['turn_no']} 轮 · {t['status']}** · 意图 `{t['intent']}`")
        st.markdown(f"- 槽位：`{json.dumps(t['slots'], ensure_ascii=False)}`")
        st.markdown(f"- 缺失：`{t['missing']}`　本轮继承：`{t['inherited']}`　指代：`{t['coreference']}`")
        if t["question"]:
            st.info(f"追问：{t['question']}")
        if t["retrieval"]:
            items = t["retrieval"]["items"][:3]
            st.success("命中：" + "；".join(i["citation"] for i in items))


def render_hitl(pipe) -> None:
    stats = pipe.stats()
    st.metric("待复核", stats.get("pending", 0))
    st.caption(f"介入率 {stats.get('intervention_rate', 0):.2f}　修正类型分布 {stats.get('correction_types', {})}")
    q = st.text_input("发起一条需要复核的问题", value="公司股东之间转让股权需要缴什么税？", key="hitl_q")
    force = st.checkbox("强制复核（跳过自动判定）", value=True, key="hitl_force")
    if st.button("提交", key="hitl_start") and q.strip():
        with st.spinner("检索中…"):
            out = pipe.start(q, force_review=force)
        st.json(out.get("retrieval", {}))
        st.session_state["hitl_task"] = out.get("task_id")
    pending = pipe.pending()
    if pending:
        st.subheader("挂起任务")
        for p in pending:
            with st.expander(f"{p['task_id']} · {p['query'][:28]} · {p['reason'][:40]}"):
                st.write(f"top1={p['top1_similarity']:.3f}　coverage={p['coverage']:.3f}")
                action = st.radio("修正方式", ["accept", "edit_articles", "edit_answer", "provide_answer"], key=f"act_{p['task_id']}")
                payload: dict = {"type": action}
                if action == "edit_articles":
                    ids = st.text_input("条文 ID（如 lcl#85, ll#91）", key=f"ids_{p['task_id']}")
                    payload["article_ids"] = [x.strip() for x in ids.split(",") if x.strip()]
                if action in ("edit_answer", "provide_answer"):
                    payload["answer"] = st.text_area("人工答案", key=f"ans_{p['task_id']}")
                if st.button("提交修正", key=f"sub_{p['task_id']}"):
                    try:
                        res = pipe.resume(p["task_id"], payload)
                        st.success("已恢复")
                        st.markdown(res.get("answer", ""))
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))


def render_eval() -> None:
    names = st.multiselect("报告", list(EVAL_FILES), default=["B3 检索三档", "B6 消融"])
    for name in names:
        path = Path(EVAL_FILES[name])
        if not path.exists():
            st.warning(f"缺少 {path}，请先运行对应评测脚本。")
            continue
        blob = json.loads(path.read_text(encoding="utf-8"))
        st.subheader(name)
        if name == "B3 检索三档":
            rows = [{"档位": k, **{m: v for m, v in val.items() if not isinstance(v, dict)}} for k, val in blob.items()]
            st.dataframe(pd.DataFrame(rows), use_container_width=True)
        elif name == "B6 消融":
            st.dataframe(pd.DataFrame(blob.get("tiers", {})).T, use_container_width=True)
            if blob.get("cost_table"):
                st.caption("成本表")
                st.dataframe(pd.DataFrame(blob["cost_table"]).T, use_container_width=True)
            boot = blob.get("bootstrap", {})
            if boot:
                st.caption("配对 bootstrap（100 次，零是否落在区间内）")
                st.dataframe(pd.DataFrame(boot).T, use_container_width=True)
        else:
            st.json(blob)


def main() -> None:
    st.set_page_config(page_title="LexWeave · 劳动法图谱问答", layout="wide")
    st.title("LexWeave · 劳动法知识图谱双路检索问答")
    st.caption(f"运行模式：{settings.run_mode}　LLM：{llm_status().get('model') or '未配置'}")

    ret, flows, art_concepts, pipe = load_runtime()
    backend = ret.graph.store.backend
    st.caption(f"图后端：{backend}　语料：{len(ret.text.articles)} 条")

    tabs = st.tabs(
        ["图谱问答（B6）", "可观测性（T27）", "演示场景（T29）", "多轮槽位（B4）", "人工复核（B5）", "评测报告"]
    )
    with tabs[0]:
        render_qa(ret, flows, art_concepts)
    with tabs[1]:
        render_observability()
    with tabs[2]:
        render_demo(ret)
    with tabs[3]:
        render_dialog(ret)
    with tabs[4]:
        render_hitl(pipe)
    with tabs[5]:
        render_eval()


if __name__ == "__main__":
    main()
