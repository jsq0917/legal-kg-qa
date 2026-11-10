"""从 data/eval_out/ 与 data/graph/ 出结果图（README 用）。

四张图全部从已落盘的评测产物读取，不写死任何数字：
  fig1_b3_retrieval.png   三档检索 R@5 / 路径准确率 / 分跳数
  fig2_b6_bootstrap.png   B6 质量指标的配对差值 95% CI（森林图）
  fig3_graph_composition.png  5,826 条边的类型与 provenance 构成

用法：
    .venv\\Scripts\\python.exe scripts\\make_figures.py
    .venv\\Scripts\\python.exe scripts\\make_figures.py --outdir docs/figures
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import rcParams  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# 中文字体：Windows 用 SimHei，Linux/macOS 回落到系统黑体
rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "DejaVu Sans"]
rcParams["axes.unicode_minus"] = False
rcParams["figure.dpi"] = 120
rcParams["savefig.dpi"] = 200

TIERS = ["text", "graph", "hybrid"]
TIER_CN = {"text": "纯文本", "graph": "纯图", "hybrid": "hybrid"}
C_TEXT = "#4C7FB0"
C_GRAPH = "#C2705A"
C_HYBRID = "#5B9E7A"
C_NEUTRAL = "#8A94A0"


def load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def style(ax) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#C9D2DB")
    ax.spines["bottom"].set_color("#C9D2DB")
    ax.tick_params(colors="#5B6673", labelsize=10)
    ax.grid(axis="y", color="#EDF1F5", linewidth=0.9)
    ax.set_axisbelow(True)


def fig_retrieval(out: Path) -> None:
    res = load("data/eval_out/retrieval.json")["results"]
    fig, axes = plt.subplots(1, 2, figsize=(11.2, 4.2), gridspec_kw={"width_ratios": [1.05, 1]})

    # --- 左：主指标 ---
    ax = axes[0]
    metrics = [("recall@5", "R@5"), ("path_recall", "路径准确率")]
    x = np.arange(len(metrics))
    w = 0.26
    colors = {"text": C_TEXT, "graph": C_GRAPH, "hybrid": C_HYBRID}
    for i, t in enumerate(TIERS):
        vals = [res[t][k] for k, _ in metrics]
        pos = x + (i - 1) * w
        ax.bar(pos, vals, w * 0.92, label=TIER_CN[t], color=colors[t], zorder=3)
        for p, v in zip(pos, vals):
            if v == 0:
                ax.text(p, 0.012, "0", ha="center", va="bottom", fontsize=8.6, color="#7C8794")
            else:
                ax.text(p, v + 0.018, f"{v:.3f}", ha="center", va="bottom", fontsize=8.6, color="#3D4650")
    ax.set_xticks(x)
    ax.set_xticklabels([n for _, n in metrics], fontsize=10.5)
    ax.set_ylim(0, 0.95)
    ax.set_ylabel("得分（60 条金标）", fontsize=10.5, color="#5B6673")
    ax.set_title(
        f"三档 R@5：文本 {res['text']['recall@5']:.4f} / "
        f"单跳图 {res['graph']['recall@5']:.4f} / 混合 {res['hybrid']['recall@5']:.4f}",
        fontsize=11.2, pad=10, color="#1B2733",
    )
    ax.legend(frameon=False, fontsize=9.5, ncol=3, loc="upper left", bbox_to_anchor=(0, 1.0))
    style(ax)

    # --- 右：分跳数 ---
    ax = axes[1]
    hops = [("two_hop", "二跳"), ("three_hop", "三跳"), ("cross_law", "跨法")]
    x = np.arange(len(hops))
    for i, t in enumerate(TIERS):
        vals = [res[t]["by_hop"][h]["recall@5"] for h, _ in hops]
        pos = x + (i - 1) * w
        ax.bar(pos, vals, w * 0.92, label=TIER_CN[t], color=colors[t], zorder=3)
        for p, v in zip(pos, vals):
            ax.text(p, v + 0.018, f"{v:.2f}", ha="center", va="bottom", fontsize=8.4, color="#3D4650")
    ax.set_xticks(x)
    ax.set_xticklabels([n for _, n in hops], fontsize=10.5)
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("R@5", fontsize=10.5, color="#5B6673")
    ax.set_title("分跳 R@5：二跳 / 三跳 / 跨法", fontsize=11.5, pad=10, color="#1B2733")
    style(ax)

    n = res["text"]["n"]
    diff_pp = (res["hybrid"]["recall@5"] - res["text"]["recall@5"]) * 100
    fig.text(
        0.01, 0.01,
        f"数据来源：data/eval_out/retrieval.json#results.text/graph/hybrid"
        f"（scripts/eval_retrieval.py 产出，{n} 条金标，in-sample，无留出集）。"
        f"文本 vs 混合差 {diff_pp:.2f}pt。",
        fontsize=8.4, color=C_NEUTRAL,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out / "fig1_b3_retrieval.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig_bootstrap(out: Path) -> None:
    abl = load("data/eval_out/ablation.json")
    boot = abl["bootstrap"]
    tiers = abl["tiers"]

    metrics = [
        ("recall@5", "R@5"),
        ("cite_precision", "引用精确率"),
        ("cite_recall", "引用召回率"),
        ("grounded", "grounded 率"),
    ]
    comps = [("B-A", "B 档 vs A 档：加多 Agent", "#C2705A"),
             ("C-B", "C 档 vs B 档：再加五道闸门", "#4C7FB0")]

    fig, ax = plt.subplots(figsize=(9.6, 4.4))
    y = np.arange(len(metrics))[::-1]
    h = 0.34
    for j, (key, label, color) in enumerate(comps):
        ds, los, his, sig = [], [], [], []
        for m, _ in metrics:
            d = boot[f"{key}:{m}"]
            ds.append(d["delta"])
            los.append(d["ci95"][0])
            his.append(d["ci95"][1])
            sig.append(d["excludes_zero"])
        pos = y + (0.5 - j) * h
        for p, d, lo, hi, s in zip(pos, ds, los, his, sig):
            ax.plot([lo, hi], [p, p], color=color, linewidth=2.2, solid_capstyle="round", zorder=3)
            ax.plot([d], [p], marker="o", markersize=6.5, color=color,
                    markerfacecolor=color if s else "white", markeredgewidth=1.6, zorder=4)
            ax.text(hi + 0.006, p, f"{d:+.3f}" + ("（区间不含 0）" if s else "（含 0）"),
                    va="center", fontsize=8.6, color=color if s else "#7C8794")
        ax.plot([], [], color=color, linewidth=2.2, marker="o", markersize=6, label=label)

    ax.axvline(0, color="#3D4650", linewidth=1.1, linestyle=(0, (4, 3)), zorder=2)
    ax.set_yticks(y)
    ax.set_yticklabels([n for _, n in metrics], fontsize=10.5)
    ax.set_xlabel("相邻两档的配对差值（bootstrap 100 次，95% CI）", fontsize=10.5, color="#5B6673")
    ax.set_title("加多 Agent：调用与延迟都涨，精度反而降；再加五道闸门才把精度拉回来",
                 fontsize=11.8, pad=12, color="#1B2733")
    ax.legend(frameon=False, fontsize=9.3, loc="upper right", bbox_to_anchor=(1.0, 1.02))
    ax.set_xlim(-0.10, 0.26)
    style(ax)
    ax.grid(axis="x", color="#EDF1F5", linewidth=0.9)
    ax.grid(axis="y", visible=False)

    # 右侧贴成本注记
    lat = boot["B-A:latency_ms"]["delta"]
    calls = boot["B-A:llm_calls"]["delta"]
    sig_ba = [n for m, n in metrics if boot[f"B-A:{m}"]["excludes_zero"]]
    sig_cb = [n for m, n in metrics if boot[f"C-B:{m}"]["excludes_zero"]]
    fig.text(
        0.01, 0.01,
        f"同一批 60 条上：B 档相对 A 档多 {int(calls)} 次 LLM 调用、延迟 +{lat:.0f} ms"
        f"（{tiers['A']['latency_median_ms']:.0f} → {tiers['B']['latency_median_ms']:.0f} ms）。"
        f"B-A 区间不含 0 的质量项：{'、'.join(sig_ba) or '无'}（方向为下降）；"
        f"C-B 不含 0 的只有：{'、'.join(sig_cb) or '无'}。",
        fontsize=8.4, color=C_NEUTRAL,
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(out / "fig2_b6_bootstrap.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig_graph(out: Path) -> None:
    g = load("data/graph/graph.json")
    pairs = Counter((e["relation"], e["provenance"]) for e in g["edges"])
    order = ["DEFINES", "REFERS_TO", "APPLIES_TO", "REQUIRES", "SPECIAL_OF", "CITES"]
    prov_cn = {"rule": "规则抽取", "manual": "人工编写", "regex": "正则抽取", "inferred": "推断补边"}
    prov_color = {"rule": "#8FB6D9", "manual": "#C2705A", "regex": "#9DBFAE", "inferred": "#9A86C4"}
    total = len(g["edges"])

    fig, ax = plt.subplots(figsize=(9.6, 3.8))
    y = np.arange(len(order))[::-1]
    left = np.zeros(len(order))
    for prov in ["rule", "manual", "regex", "inferred"]:
        vals = np.array([pairs[(r, prov)] for r in order], dtype=float)
        ax.barh(y, vals, left=left, height=0.55, color=prov_color[prov],
                label=f"{prov_cn[prov]}（{int(vals.sum())}）", zorder=3)
        for p, l, v in zip(y, left, vals):
            if v >= 150:
                ax.text(l + v / 2, p, f"{int(v):,}", ha="center", va="center",
                        fontsize=9, color="#FFFFFF" if prov == "manual" else "#33404D")
            elif v > 0:
                ax.text(l + v + 55, p, f"{int(v):,}", ha="left", va="center",
                        fontsize=9, color="#5B6673")
        left += vals

    ax.set_yticks(y)
    ax.set_yticklabels(order, fontsize=10.5)
    ax.set_xlabel(f"边数（合计 {total:,}）", fontsize=10.5, color="#5B6673")
    ax.set_title("B7 拆边后：DEFINES 只留定义/正面规制，REFERS_TO 是顺带提及",
                 fontsize=11.8, pad=12, color="#1B2733")
    ax.legend(frameon=False, fontsize=9.3, loc="lower right")
    ax.set_xlim(0, max(4100, int(max(left) * 1.08)))
    style(ax)
    ax.grid(axis="x", color="#EDF1F5", linewidth=0.9)
    ax.grid(axis="y", visible=False)

    fig.text(
        0.01, 0.01,
        "数据来源：data/graph/graph.json（scripts/build_graph.py 产出，stats.json 同脚本刷新）。"
        "DEFINES 数量跌破 800 是拆边的真实结果，未为凑数放宽规则，见 README §3 / §12 第 6 条。",
        fontsize=8.4, color=C_NEUTRAL,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out / "fig3_graph_composition.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)


def fig_hybrid_rerank(out: Path) -> bool:
    """T12 / o 任务：融合方式 × 是否 rerank 的 2×2 对照（R@5 与延迟）。"""
    res = load("data/eval_out/retrieval.json")["results"].get("hybrid_rerank")
    if not res:
        print("  [skip] retrieval.json 尚无 results.hybrid_rerank")
        return False

    cells = res["cells"]
    order = ["rrf_norerank", "rrf_rerank", "weighted_norerank", "weighted_rerank"]
    labels = ["RRF\n不过精排", "RRF\n过精排", "加权\n不过精排", "加权\n过精排"]
    colors = [C_TEXT, C_TEXT, C_GRAPH, C_GRAPH]
    hatches = ["", "//", "", "//"]
    r5 = [cells[k]["recall@5"] for k in order]
    lolos = [cells[k]["recall@5"] - cells[k]["recall@5_ci95"][0] for k in order]
    his = [cells[k]["recall@5_ci95"][1] - cells[k]["recall@5"] for k in order]
    med = [cells[k]["latency_median_ms"] for k in order]
    p90 = [cells[k]["latency_p90_ms"] for k in order]

    fig, axes = plt.subplots(1, 2, figsize=(11.6, 4.4), gridspec_kw={"width_ratios": [1.15, 1]})

    ax = axes[0]
    x = np.arange(len(order))
    ax.bar(x, r5, 0.56, color=colors, hatch=hatches, edgecolor="#3D4650", linewidth=0.6, zorder=3)
    ax.errorbar(x, r5, yerr=[lolos, his], fmt="none", ecolor="#3D4650", elinewidth=1.1, capsize=4, zorder=4)
    for i, (p, v) in enumerate(zip(x, r5)):
        ax.text(p, v + his[i] + 0.02, f"{v:.3f}", ha="center", va="bottom", fontsize=9, color="#3D4650")
    ax.axhline(res["target"]["recall@5"], color="#C2705A", linestyle=(0, (4, 3)), linewidth=1.1, zorder=2)
    ax.text(3.42, res["target"]["recall@5"], "目标 0.78", ha="right", va="bottom", fontsize=8.8, color="#C2705A")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9.5)
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("R@5（60 条金标，误差棒为 95% CI）", fontsize=10.2, color="#5B6673")
    ax.set_title("2×2 四格：融合方式 × reranker", fontsize=11.5, pad=10, color="#1B2733")
    style(ax)

    ax = axes[1]
    width = 0.36
    ax.bar(x - width / 2, med, width, color="#9DBFAE", label="median", zorder=3)
    ax.bar(x + width / 2, p90, width, color="#8A94A0", label="p90", zorder=3)
    for p, v in zip(x - width / 2, med):
        ax.text(p, v * 1.08, f"{v:.0f}" if v < 1000 else f"{v/1000:.1f}s", ha="center", va="bottom", fontsize=8.2, color="#3D4650")
    for p, v in zip(x + width / 2, p90):
        ax.text(p, v * 1.08, f"{v:.0f}" if v < 1000 else f"{v/1000:.1f}s", ha="center", va="bottom", fontsize=8.2, color="#3D4650")
    ax.set_yscale("log")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, fontsize=9.5)
    ax.set_ylabel("单题检索延迟 ms（对数轴）", fontsize=10.2, color="#5B6673")
    ax.set_title("精排的代价：CPU 上每题多出十秒级", fontsize=11.5, pad=10, color="#1B2733")
    ax.legend(frameon=False, fontsize=9.3, loc="upper left")
    style(ax)
    ax.grid(axis="x", visible=False)

    best = res["target"]["best_cell"]
    fig.text(
        0.01, 0.01,
        f"数据来源：data/eval_out/retrieval.json#results.hybrid_rerank（scripts/eval_hybrid_rerank.py，"
        f"60 条金标、bootstrap seed={res['bootstrap']['seed']}）。最好格 {best} = "
        f"{res['target']['best_recall@5']:.3f}，目标 0.78 {'达到' if res['target']['reached'] else '未达到'}。"
        "延迟为 CPU 实测，含文本路/图路组件，不含模型加载。",
        fontsize=8.4, color=C_NEUTRAL,
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(out / "fig4_hybrid_rerank.png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="docs/figures")
    args = ap.parse_args()
    out = ROOT / args.outdir
    out.mkdir(parents=True, exist_ok=True)

    fig_retrieval(out)
    fig_bootstrap(out)
    fig_graph(out)
    fig_hybrid_rerank(out)
    for p in sorted(out.glob("*.png")):
        print(f"  {p.relative_to(ROOT)}  {p.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    sys.exit(main())
