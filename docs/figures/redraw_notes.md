# 2026-11-02 配图重绘记录

目标：让 `docs/figures/` 里的图与 `docs/blog_kg_rag_public.md` / `docs/blog_kg_rag.md` 正文口径一致。
只改绘图脚本、图内标题/图注与图注文案；未改任何统计数字、JSON、评测产物，未重跑评测，未占用 GPU，未做 git 写操作。

## 备份

覆盖前的原图已备份到 `docs/figures/_backup_20261102/`：

| 文件 | 大小 | SHA256 前 16 位 | 原修改时间 |
|---|---|---|---|
| fig1_b3_retrieval.png | 116,602 B | `7A55DBCA3D2EE5FE` | 2026-10-31 18:21:07 |
| fig3_graph_composition.png | 106,889 B | `C4C161C85E165A62` | 2026-10-31 18:21:08 |

重绘后：

| 文件 | 大小 | SHA256 前 16 位 | 新修改时间 |
|---|---|---|---|
| fig1_b3_retrieval.png | 113,020 B | `FB9751679CF4CF00` | 2026-11-02 09:01:41 |
| fig3_graph_composition.png | 113,517 B | `1979B511C40D4F00` | 2026-11-02 09:01:41 |

## 逐图说明

### fig1_b3_retrieval.png — 已改

旧标题（左）：

> 三档检索主指标：hybrid 相对纯文本 +3.9pt（目标 15pt）

新标题（左）：

> 三档 R@5：文本 0.7472 / 单跳图 0.4389 / 混合 0.7556

旧标题（右）：

> 分跳数 R@5：跳数越多，图路掉得越快

新标题（右）：

> 分跳 R@5：二跳 / 三跳 / 跨法

旧底部图注：

> 数据来源：data/eval_out/retrieval.json（scripts/eval_retrieval.py 产出）。hybrid 未达 15pt 的原因见 README §5 / §12 第 1 条。

新底部图注：

> 数据来源：data/eval_out/retrieval.json#results.text/graph/hybrid（scripts/eval_retrieval.py 产出，60 条金标，in-sample，无留出集）。文本 vs 混合差 0.84pt。

依据字段（`data/eval_out/retrieval.json`）：

- `results.text.recall@5` = 0.7472
- `results.graph.recall@5` = 0.4389
- `results.hybrid.recall@5` = 0.7556
- `results.text.n` = 60；`(hybrid - text) * 100` = 0.84pt

说明：标题与差值的数字全部由上述字段格式化生成，不再写死；按要求已删除「目标线 / 达标」措辞。

### fig2_b6_bootstrap.png — 未改

标题与底部图注中未出现 5697 / 0.4417 / 819 / 660 / +3.9pt / 15pt 等过时数字，未发现需要修正的旧文案。图内数值由 `data/eval_out/ablation.json` 动态读取：

- `bootstrap["B-A:latency_ms"].delta` = 4914.0267
- `bootstrap["B-A:llm_calls"].delta` = 1
- `tiers.A.latency_median_ms` = 2037.8，`tiers.B.latency_median_ms` = 3453.4

该图保持原文件未重绘（修改时间仍为 2026-10-31 18:21:07）。

### fig3_graph_composition.png — 已改（标题/图注未变，修正分项构成）

标题与底部图注没有过时数字，保持不变；但图例只统计了 `rule / manual / regex`，漏掉 `inferred`，导致分项合计 5,632 与横轴「合计 5,826」矛盾。已补上「推断补边（194）」一类并重新绘制。

依据字段（`data/graph/graph.json`）：

- `len(edges)` = 5826
- `Counter(relation, provenance)`：`rule` 4824、`manual` 711、`regex` 97、`inferred` 194（REQUIRES 192 + SPECIAL_OF 2）
- 四类合计 = 5826，与横轴合计一致

### fig4_hybrid_rerank.png — 未改

标题与底部图注未出现过时数字；图内四格与目标线均来自 `data/eval_out/retrieval.json#results.hybrid_rerank`：

- `cells.rrf_norerank.recall@5` = 0.7222
- `cells.rrf_rerank.recall@5` = 0.7528
- `cells.weighted_norerank.recall@5` = 0.7056
- `cells.weighted_rerank.recall@5` = 0.7583
- `target.recall@5` = 0.78，`target.best_recall@5` = 0.7583

博客图注本身写明「虚线是 0.78 目标，四格都没到」，与图一致；「目标 0.78」措辞按博客口径保留，未做 fig1 同等删除。该图保持原文件未重绘（修改时间仍为 2026-10-31 18:21:08）。

## 博客图注核对

- `docs/blog_kg_rag_public.md` 与 `docs/blog_kg_rag.md` 的图 1 图注为「文本路 / 单跳图路 / 混合路的 R@5 与分跳结果（60 条金标…）」，未写死旧数字，与新图一致，未改。
- 两篇博客的图 2 图注为「四格 R@5…虚线是 0.78 目标，四格都没到…」，与 fig4 一致，未改。
- fig2 / fig3 未被这两篇博客引用，无对应图注需要同步。
- 正文统计数字（0.7472 / 0.4389 / 0.7556 / 0.7583 等）未做任何修改。

## 复现命令

在 `E:\project\LexWeave` 下执行。

全量重绘四张图：

```powershell
.venv\Scripts\python.exe scripts\make_figures.py --outdir docs/figures
```

仅重绘本任务改动的 fig1 与 fig3（避免触碰 fig2 / fig4）：

```powershell
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'scripts'); import make_figures as m; from pathlib import Path; out=Path('docs/figures'); m.fig_retrieval(out); m.fig_graph(out)"
```

字体沿用脚本原有配置：`SimHei → Microsoft YaHei → PingFang SC → Noto Sans CJK SC → DejaVu Sans`。
