# 图路多跳改造：PPR 与 2-hop 剪枝（T11 / n 任务）

> 日期：2026-10-31；主工程 `E:\project\LexWeave`；全程 CPU（开跑前 `nvidia-smi` 显示 RTX 4060 已占 7753/8188 MiB、36% util，另有 python 进程在用，故所有推理显式 `EMBEDDING_DEVICE=cpu`、`GRAPH_BACKEND=networkx`）。
> 画像基线：1799 节点 / 5826 边；图路 R@5 **0.4389**、median **301.5 ms**（`data/eval_out/retrieval_post_m_rebuild.json`，60 条）。

## 1. 结论

| 档 | R@5 | 95% CI（bootstrap 1000 次） | 是否 ≥0.58 | median | p90 |
|---|---|---|---|---|---|
| 基线 graph（networkx，官方值） | 0.4389 | — | — | 301.5 ms | — |
| 基线 graph（本机同进程复算） | 0.4389 | [0.3389, 0.5361] | — | 244.8 ms | — |
| **graph_ppr** | **0.5917** | [0.4972, 0.6833] | **是（+1.2pp）** | **239.2 ms** | **318.8 ms** |
| **graph_2hop** | **0.6111** | [0.5222, 0.7000] | **是（+3.1pp）** | **224.7 ms** | **264.7 ms** |

**排除 194 条 `inferred` 边的对照（单列）：**

| 档 | R@5 | 95% CI | median | 与含推断边的差 |
|---|---|---|---|---|
| graph_ppr（排除 inferred） | 0.5972 | [0.5083, 0.6889] | 245.2 ms | **+0.55pp**（反而更高） |
| graph_2hop（排除 inferred） | 0.6083 | [0.5139, 0.6972] | 224.5 ms | **−0.28pp** |

**推断边敏感性结论：两档的提升都不来自推断边。** 排除 194 条 medium 置信度的推断边后，2-hop 只掉 0.28pp（不到 1 条问题），PPR 反而升 0.55pp。若把 0.44→0.59/0.61 归因于 m 任务补的推断边，不成立。

**是否达到 0.58：2-hop 达到（0.6111），PPR 勉强达到（0.5917），但两者都是在同一套 60 条金标上选的参数、没有留出集，属于 in-sample 数字。** 60 条里 1 条问题 ≈ 1.67pp（两概念题 ≈0.83pp），PPR 的 1.2pp 优势不足 1 条问题，CI 下界只有 0.50；2-hop 的 3.1pp 优势约 2 条问题，CI 下界 0.52。**不能把"≥0.58"当成稳定通过，只能算在这 60 条上过了线。**

**上限在哪（如实报）：**

1. **实体链接是主瓶颈。** 150 个金标概念实例中只有 85 个被 linker 直接链上（`path_recall` 两档均为 0.5694），漏掉的 65 个里有 49 个距某个种子概念只有 1 跳——所以靠多跳扩散还能救回一部分。用金标概念直接作种子（理想上限）时，同一套排序器 2-hop 可到 **0.73**、PPR 约 **0.65–0.69**；说明剩余差距主要在"种子没收全"，而不是两跳本身。
2. **60 条口径太小，尤其 cross_law 只有 10 条。** 本报告用的 gold_60 分桶是 two_hop 30 / three_hop 20 / **cross_law 10**（不是 20 条）；cross_law 上 1 条问题 = 10pp，方向性可看、定论不可下。
3. **2-hop 的硬上限还受"是否允许 REFERS_TO 作末梢投影"约束。** 本实现的"只走 REQUIRES/SPECIAL_OF"指**遍历**只走这两类边；把概念映射到条文时仍用 DEFINES+REFERS_TO 索引（REFERS_TO 占 4119/5826=70.7%，不走它就没有条文可返回）。若把 REFERS_TO **连投影也禁用**，用金标概念作种子的理想候选池上限只有 **0.3056**——那种严格读法下 0.58 结构上不可达，不是调参能解决的。
4. **PPR 接近自己的天花板。** 它在 60 条上的最好网格结果也只有 0.575–0.597（本报告选的配置 0.5917）；继续加 damping/反向边/加大 REFERS_TO 权重只在小数点后第二位上抖动，属于噪声。

## 2. 逐桶 R@5（n 为本报告实测条数）

| 档 | two_hop (n=30) | three_hop (n=20) | cross_law (n=10) |
|---|---|---|---|
| 基线 graph（同进程复算） | 0.4667 | 0.4417 | **0.3500** |
| graph_ppr | 0.6833 | 0.5083 | **0.4833** |
| graph_2hop | 0.6833 | 0.5667 | **0.4833** |
| graph_ppr（排除 inferred） | 0.6833 | 0.5083 | **0.5167** |
| graph_2hop（排除 inferred） | 0.6667 | 0.5833 | **0.4833** |

`cross_law` 从 0.35 提到 0.4833（+13.3pp，两档一致），排除推断边后 PPR 甚至到 0.5167。方向是对的，但 10 条样本下这只是方向性信号：1 条问题就能让该桶动 10pp。three_hop 也有改善，two_hop 提升最大（0.4667 → 0.6833）。

## 3. 参数

### graph_ppr（`app/__004__retrieval/graph_ppr.py`）

| 参数 | 值 | 说明 |
|---|---|---|
| damping（alpha） | **0.85** | 标准 PPR 衰减 |
| max_iter / tol | **100 / 1e-6** | 1799 节点，通常 <30 轮收敛；不收敛时放宽到 500 轮重试 |
| 遍历边 | `REQUIRES`、`SPECIAL_OF`（正向） | 不反向、不走 REFERS_TO、不走 CITES |
| 投影边权重 | `DEFINES`=1.0×score，`REFERS_TO`=0.7×score | 再乘概念 `idf`；条文节点是汇点，质量不再回流 |
| 种子 | linker 概念分 + 场景 `APPLIES_TO`（0.8×weight×score） | 场景节点同时进入 personalization |
| 规范度微调 | 排序后 `1 + 0.3×canonicality`（仅直接种子概念） | 概念名在章节名/首句的条文加分 |

### graph_2hop（`app/__004__retrieval/graph_2hop.py`）

| 参数 | 值 | 说明 |
|---|---|---|
| 最大跳数 | **2** | 种子为第 0 跳，最多再扩 2 跳 |
| 遍历边 | `REQUIRES` / `SPECIAL_OF`，正向+反向 | 反向权重乘 0.45；REFERS_TO 不参与遍历 |
| 每跳权重 | REQUIRES 0.62、SPECIAL_OF 0.48，再乘边 confidence | 与主检索器的衰减一致 |
| 剪枝规则 | 每跳前沿按权重截断 **60** 个概念；投影前按权重只留 **60** 个概念；每个概念只取「规范度→边分」排序前 **20** 条条文 | 硬性 2 跳 + 三层截断 |
| 融合 | RRF，k=5；概念权重再乘 `idf` | 避免单条高置信边决定全局排序 |
| 覆盖率加成 | `1 + 0.35×直接命中覆盖率` | 与主检索器同口径 |

两档都复用同一个 `EntityLinker`（阈值 0.85），未改 linker、未改 `__005__retriever.py` 主流程。

## 4. 每档实际用了多少边

图总量：1799 节点 / 5826 边；`DEFINES` 710、`REFERS_TO` 4119、`REQUIRES` 422（机械 230 + 推断 192）、`SPECIAL_OF` 187（机械 185 + 推断 2）、`APPLIES_TO` 291、`CITES` 97。

### graph_ppr（每题参与的静态图规模，图在 `__init__` 建好后缓存）

| 边类型 | 参与边数 | 角色 |
|---|---|---|
| REQUIRES | 422 | 概念间扩散（含 192 推断） |
| SPECIAL_OF | 187 | 概念间扩散（含 2 推断） |
| APPLIES_TO | 291 | 场景 → 概念种子 |
| DEFINES | 710 | 概念 → 条文投影 |
| REFERS_TO | 4119 | 概念 → 条文投影（只作投影，不遍历） |
| 场景 APPLIES_TO 实际读取 | 3.8 条/题 | linker 命中场景时才走 |

### graph_2hop（60 题累计的实际遍历/投影次数）

| 指标 | 每题均值 | 60 题合计 |
|---|---|---|
| 概念边遍历（正+反，含重复） | 261.3 | 15,677 |
| ├ REQUIRES | 216.4 | 12,987 |
| ├ SPECIAL_OF | 44.8 | 2,690 |
| ├ 反向边 | 145.9 | 8,755 |
| └ 其中 inferred 边 | 115.3 | 6,921 |
| 场景种子边 | 3.8 | 225 |
| 投影边考察 | 673.1 | 40,389 |
| ├ DEFINES | 192.0 | 11,521 |
| └ REFERS_TO | 481.1 | 22,868 |
| 候选条文数 | 419.5 | 25,170 |
| 最终返回 | 5 | 300 |

注：这里的计数是「遍历次数」，同一条边被不同概念或不同问题重复走过会重复计；不是唯一边数。

### 基线（同进程复算）

`GraphRetriever(hops=2, use_cites=True)`：沿 `REQUIRES`/`SPECIAL_OF` **正向**扩 2 跳，经 `concept_articles`（DEFINES+REFERS_TO）投影，再补一跳 `CITES`。该基线没有逐边计数插桩，只能列可用边类型，不能报实测遍历量——这一点如实标注为**不可复核**。

## 5. 延迟

| 档 | median | p90 | 基线 2× 限（603 ms） |
|---|---|---|---|
| 基线（官方值） | 301.5 ms | — | — |
| graph_ppr | 239.2 ms | 318.8 ms | 通过（0.79× / 0.53×） |
| graph_2hop | 224.7 ms | 264.7 ms | 通过（0.75× / 0.44×） |
| graph_ppr（排除 inferred） | 245.2 ms | — | 通过 |
| graph_2hop（排除 inferred） | 224.5 ms | — | 通过 |

两次独立运行的 median 在 224.7–268.7 ms 之间波动（CPU 上与图形界面进程共享核心），但都远低于 2× 上限。延迟含实体链接（bge-large-zh-v1.5，CPU），不含一次性模型加载与图构建（已预热/建缓存）。

## 6. 推断边敏感性（单列，归因判断）

- 图里 194 条 `inferred=true`、confidence=medium 的边：REQUIRES 192 / SPECIAL_OF 2。核对文件 `data/eval_out/gap_edges_mark_check.json`（`inferred_edges_in_graph=194`、机械层被误标 0）。
- **2-hop**：含推断边 0.6111 → 排除后 0.6083，`cross_law` 0.4833→0.4833，`three_hop` 0.5667→0.5833。差 −0.28pp。
- **PPR**：含推断边 0.5917 → 排除后 0.5972，`cross_law` 0.4833→0.5167。差 +0.55pp。
- 两档在含推断边时每题平均实际走过 115.3 条 inferred 概念边，但没有带来正收益。**结论：提升来自「换排序/换遍历方式」，不来自推断边；推断边在这套用法下是无收益甚至轻微负收益。**

## 7. 复现

```powershell
cd E:\project\LexWeave
$env:EMBEDDING_DEVICE='cpu'; $env:GRAPH_BACKEND='networkx'
# 只算，不写 retrieval.json
E:\project\LexWeave\.venv\Scripts\python.exe scripts\eval_multihop.py --with-baseline --raw C:\tmp\multihop_raw.json
# 追加两档（自动备份 retrieval.json 到 data/eval_out/_backup_n_20261031/）
E:\project\LexWeave\.venv\Scripts\python.exe scripts\eval_multihop.py --append
```

金标口径：`data/gold/gold_60.jsonl`（未换）。指标口径与 `scripts/eval_retrieval.py` 一致：`recall@5 = |金标必需条文 ∩ top5| / |金标必需条文|`；`path_recall = |linker 概念 ∩ 金标概念链| / |链|`。

## 8. 改了哪些文件

| 类型 | 文件 | 说明 |
|---|---|---|
| 新增 | `app/__004__retrieval/graph_ppr.py` | PPR 检索器 |
| 新增 | `app/__004__retrieval/graph_2hop.py` | 2-hop 剪枝检索器 |
| 新增 | `scripts/eval_multihop.py` | 评测 + 追加写入口（含 bootstrap CI / 边计数） |
| 新增 | `reports/graph_multihop.md` | 本报告 |
| 修改 | `data/eval_out/retrieval.json` | **追加** `results.graph_ppr`、`results.graph_2hop` 两个 key；`text/graph/hybrid` 原值未动 |
| 备份 | `data/eval_out/_backup_n_20261031/retrieval.json` | 原文件备份，SHA256 `4FC46C0A25AE3E0FD4543E0AA73100645D7E8870ECBACAA8F02B087D3982BE1A` |
| 备份 | `data/eval_out/_backup_n_20261031/retrieval.20261031_172458.json` | 追加脚本自动备份（同原文件字节） |

未动：`data/graph/`（未重建，图仍是 1799/5826）、`data/lexicon/`、`app/__002__extract/__003__edge_typing.py`、`app/__004__retrieval/__005__retriever.py`、`data/gold/gold_60.jsonl`；未做 git 写操作；未跑任何 LLM / 端到端生成评测。
