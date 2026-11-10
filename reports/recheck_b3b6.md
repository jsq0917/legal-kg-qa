# T03 / v 任务：清洗后重评与回填（B3 / B6 口径复核）

日期：2026-10-31　｜　范围：`eval_retrieval`、`eval_router`、`eval_ablation` + README/报告数字回填
环境：CPU-only（`GRAPH_BACKEND=networkx`、`EMBEDDING_DEVICE=cpu`），GPU 被另一会话的量化基准独占
边界：不重建图、不改 `data/lexicon/`、不改评测口径与判据、不删旧 JSON、不做 git 写操作、不做 LLM 全量评测

---

## 1. 结论（先行）

| 评测 | 旧值（`*_pre_recheck`） | 重跑值（本次） | 判定 |
|---|---|---|---|
| 检索 text | R@5 0.7472 / all@5 0.6167 | **0.7472 / 0.6167** | 逐位一致 |
| 检索 graph（单跳） | 0.4417 / 0.2333 | **0.4389 / 0.2167** | R@5 −0.3pt、all@5 −1.7pt（各 ≈1 题），**噪声内** |
| 检索 hybrid | 0.7556 / 0.6167 | **0.7556 / 0.6167** | 逐位一致 |
| 路由 n=260 级联 | 0.7731 [0.7192,0.8231] | **0.7731 [0.7192,0.8231]** | 逐位一致 |
| 路由 gold-only | 0.705 | **0.705** | 一致；触发 18.85%（49/260）不变 |
| ablation | 四档 × 60，`llm_mode=llm` | **未重跑，保留原数** | 见 §2 原因 |

- **两件跑通、一件按边界不跑**。检索与路由重跑全部复现权威值，唯一实质差异是纯图 `all@5` 少 1 题。
- 该差异与「重建后 4 处概念→条文路径回退（g26/g45/g47/g48）」方向一致，属确定性回退而非随机抖动；
  但 1/60 = 1.7pt 远小于二项标准误 ≈0.064，**不能据此宣称图路变差**。
- README 本次回填 **31 处**（§2 1、§3 3、§4 0（已在前序完成）、§5 19、§8 2、§12 2、§13 2、§10/§11 2），
  另加 2 份报告（`case_bank.md`、`defines_guard.md`）与行动清单（附录 A + 面试句 + §1.3）。
- 剩余不一致：**0 处数字级**；仅存「历史口径备注」（`5697`/`819`/`0.4417`/`0.700` 只出现在明确标注「历史/已作废」的句子里）。

---

## 2. ablation 为什么没重跑

`data/eval_out/ablation.json` 的 `llm_mode="llm"`，是 60 条 × 4 档的真实 LLM 生成 + 配对 bootstrap 100 次，
产生于 16:40 图重建之前。三条路都不通：

1. **真 LLM 重跑**：等价于 LLM 全量评测（4×60 题），与本任务边界冲突；
2. **`--no-llm` 重跑**：脚本会把生成切成 deterministic，`grounded` / `cite_precision` / `cite_recall` 全部换口径，
   那是「换口径」不是「复评」，且会覆写 `ablation.json`；
3. **CPU 并发重跑**：同机此时有量化基准（`run_quant_eval.py`）与 60M 预训练（`train.py`）在跑，
   检索一步的中位延迟已被抬到 22–23 s（干净环境 0.4 s），四档 × 60 题不具备可比性。

因此保留原数，并在 README §8 末条与 §13 B6 行写明「真实 LLM 口径、重建前产物、本任务未重跑」。
可复算的部分（检索输入）已由 §5 重跑覆盖：hybrid R@5 与 ablation 的 0.7556 **完全相同**。

---

## 3. 核对清单：README 每个数字 → 产物与字段

| README 位置 | 数字 | 产物 | 字段 / 记录 |
|---|---|---|---|
| 徽章 tests | 75 passed / 1 red | `logs/pytest_v.log` | pytest 汇总（76 项、1 red：`test_graph_sample.py` g26/g45） |
| 徽章 / §2 图规模 | 1799 节点 / 5826 边 | `data/graph/stats.json` | `nodes` / `edges` |
| §2 边构成 | DEFINES 710、REFERS_TO 4119、CITES 97、REQUIRES 422、SPECIAL_OF 187、APPLIES_TO 291 | 同上 | `relationships.*` |
| §2 概念层机械边 / 推断边 | 706（291+230+185）、194（REQUIRES 192 + SPECIAL_OF 2） | `data/lexicon/stats.json` + `relationships` | `scenario_concept_edges`/`requires_edges`/`special_of_edges`；推断边由 `inferred=true` 计数 |
| §2 Article→Concept | 4829（DEFINES 705 规则 + 5 manual + REFERS_TO 4119） | `data/graph/stats.json` | `article_concept_edges.total` / `defines_by_provenance.rule=4824, manual=5` |
| §3 表 Article/Concept | 1465 / 281 | 同上 | `labels.Article` / `labels.Concept` |
| §3 机械复核 | 68/710 = 9.58%、63/705 = 8.94%、211 待语义、47 derived、384 normal | `data/eval_out/defines_edges_check_post_rebuild.json` | `all.absent/derived_only/pending_semantic/normal`、`absent_rate`、`supported_only` |
| §3 严口径 | 559/710、含人工 564 | `data/eval_out/defines_strict_formal.json` | `strict` / `with_manual` / `lost_all_defines=21` |
| §3 字面命中 | 98/99 = 98.99% | `data/graph/stats.json` | `audit.sampled/pass/precision`（`scope=literal_mention_proxy`） |
| §3 规则自洽 | 100/100、严读法 92/100 | `data/eval_out/defines_selfcheck.json` | `selfcheck_pass_rate` |
| §4 金标规模 / hop | 200（100/66/34） | `data/eval_out/retrieval_recheck.json` | `gold_report.total` / `by_hop` |
| §4 κ | 0.9473 / 0.9329 / 0.9856 | 同上 | `gold_report.kappa_pass_a_vs_b1/b2/b_stability` |
| §4 必要性 | 概念层 195/200（99+64+32）；条文层 173/200（94+60+19） | `work/parallel-run/T07/reports/gold200.md` §5 表 | 第 156/159/163 行 |
| §4 泄漏 / A1 交集 | 成句照抄 0/200；条文交集 0 | 同上 + `gold_report.leakage_fail` | `leakage_fail=0` |
| §4 路径回退 | 4 处（g26/g45 cc#733、g47 cc#1202、g48 cc#1194） | 只读复算（当前 pkl） | 与 `tests/test_graph_sample.py` 红灯一致 |
| §5 text / graph / hybrid | 0.7472 / 0.4389 / 0.7556（R@5）；0.6167 / 0.2167 / 0.6167（all@5）；path 0 / 0.5694 / 0.5694 | `data/eval_out/retrieval.json`（已并入 19:14 重跑） | `results.*.[recall@5,all@5,path_recall,citation_hit]` |
| §5 延迟 | 411 / 132 / 396 ms | 同上 | `latency_median_ms`（干净环境；重跑值另记 `latency_note`） |
| §5 图路 2-hop / PPR | 0.6111 / 0.5917（all@5 0.35） | `data/eval_out/retrieval.json` | `results.graph_2hop` / `results.graph_ppr` |
| §5 融合四格 | 0.7222 / 0.7056 / 0.7528 / 0.7583 | 同上 | `results.hybrid_rerank.cells.*.[recall@5]` |
| §5 融合差值 / 精排代价 | +1.11pp，CI [−0.0167, 0.0389]；中位 10.89 s vs 0.55 s | 同上 | `deltas.weighted_rerank_minus_text`；`cells.*.latency_median_ms` |
| §5 候选池 oracle | 0.9194 / 0.9250（52/60、53/60 全覆盖，1 题无金标） | 同上 | `oracles.pool_ceiling.rrf/weighted` |
| §5 路由 | 级联 0.7731 [0.7192,0.8231]、gold 0.705 [0.6449,0.77]、LR 0.7385、语义 0.7462、LLM 0.7231、规则 0.6077 | `data/eval_out/router.json` | `cascade_accuracy` / `gold_only.*` / `ci95.*` |
| §5 兜底触发 / 分档 | 18.85%（49/260），rule 48 / semantic 163 / llm 49；60/60、71/100、70/100 | 同上 | `cascade.llm_trigger_rate/llm_triggered/source_mix`、`confusions.cascade` |
| §8 四档表 | R@5 0.7556；cite_p 0.3683/0.3864/0.3709/0.3823；cite_r 0.7389/0.7389/0.7333/0.7333；grounded 0.7500/0.7333/0.7333/0.7833；闸门 0.2833/0.2333；calls 1/2/2/1；tokens 789/1523/1520/796；中位 2038/3453/3342/2155 ms | `data/eval_out/ablation.json` | `tiers.*`（**llm_mode=llm，重建前，未重跑**） |
| §8 bootstrap 16 行 | Δ 与 CI | 同上 | `bootstrap.pairs.*` |
| §12 局限 1/2 | 0.84pt、0.4389、0.6111/0.5917、0.7583；级联 0.7731、18.85% | 本表 §5 行 | 同上 |
| §12 局限 3/4/7/8/9 | 710、68/211/559、173/200、706/59、5697、4 处回退、103/134 | 见上各行 | 同上 |
| §10.1 命令注释 | 76 项、nodes 1799 / edges 5826 / DEFINES 710 | `logs/pytest_v.log` + `stats.json` | 同徽章 |
| §13 B1/B2/B3/B6 行 | 同 §3/§4/§5/§8 | 同上 | 同上 |

**未纳入本轮核对（边界或未重评）**：§14 的 A6 三臂数字（LLM 全量评测，边界禁止；产物在实验仓库 `work/a6-local-llm/reports/`）、
§11 环境版本号、`docs/figures/` 三张图的绘制数据（图由 `scripts/` 读同一批 JSON 生成）。

---

## 4. 发现的不一致（旧值 → 新值 / 依据）

| # | 位置 | 旧值 | 新值 | 依据 |
|---|---|---|---|---|
| 1 | README §5 纯图 all@5 | 0.233 | **0.2167** | 19:14 重跑 `retrieval_recheck.json#results.graph.all@5`；差 1/60 |
| 2 | README §5 路由 | 0.700（规则）/ 0.738（LR） | **0.6077 / 0.7385；级联 0.7731** | `router.json` n=260（旧值来自 n=60 旧口径，分母不同不可比） |
| 3 | README §5 混淆 | simple 16/20、medium 11/30、complex 29/30 | **60/60、71/100、70/100** | `router.json#confusions.cascade` |
| 4 | README §5 hybrid 分层 | 跨法 0.583→0.600 | **0.5833→0.5500（−3.3pt）** | `retrieval_recheck.json#results.hybrid.by_hop.cross_law` |
| 5 | README §12 局限 2 | 0.700/0.738、11/30、16/20 | 级联 0.7731、71/100、60/60 | 同 #2/#3 |
| 6 | README §13 B6 行 | C−B grounded +0.083 [0.017,0.183]、B−A cite_p +0.002、C−A +0.067 | 与 §8 对齐：grounded 全含 0，仅成本项显著 | `ablation.json#bootstrap`（旧行是更早一轮运行的数） |
| 7 | README §13 B3 行 | +0.8pt、0.700/0.738、0.442 | +0.84pt、0.7731、0.4389 | 同 #1/#2 |
| 8 | README §3 字面命中 | 99/100 | **98/99（实抽 99）** | `data/graph/stats.json#audit` |
| 9 | `reports/case_bank.md` §0/§3.2 | 5,697 边、盲判 63%、0.4417、2.76 边/概念 | 5,826、9.58%（68/710）+211 待语义、0.4389、2.51 | 用户权威表 + `stats.json`/`defines_edges_check_post_rebuild.json` |
| 10 | `reports/defines_guard.md` 顶部 | 819 条、49/100 人工盲标作当前精度 | 加口径提醒：819 是清洗前历史，当前 710 / 9.58% / 211 / 559 | 同上 |
| 11 | 行动清单 §1.3 | 577 边、209 概念、2.76 | 706（291+230+185）、281、2.51 | `data/lexicon/stats.json` |
| 12 | 行动清单 §8 面试句 1/3 | 5,697、63%、2.76 | 5,826、9.58%+211、2.51；0.4389/0.7472 | 同上 |
| 13 | 行动清单 附录 A | 未标注历史口径 | 表头改为「历史基线（重建前，不作当前口径）」+ 每行「历史/现 x」 | `README §3/§5` 对照 |
| 14 | 行动清单 T01 / T11 / T13 卡 | 819/225、0.4417、0.700/0.7375 | 加「重建后现行口径」710/68/211/559；0.4389；级联 0.7731 | 同上 |

前序已完成、不在本次 diff 内：DEFINES 819 → 710、严口径 660 → 559/564、「人工精度 63%」删除、
`GraphRAG` → 「图谱 RAG / KG-RAG」、5697 仅在「Neo4j 重建前导入」语境保留、
4204 → 4119、577 → 706、§4 由 gold_60 改为 gold_200 口径、§8 四档与 16 行 bootstrap 重写。

---

## 5. 被改掉的禁用表述

| 禁用表述 | 处理 |
|---|---|
| 「63%」（作定义级精度） | 全部删除。改为「机械复核误配 9.58%（68/710；规则 63/705=8.94%）+ 211 条待语义」 |
| 「人工精度 / 人工盲标 49%」作精度 | 删除；`defines_guard.md` 顶部标注「LLM 盲判、已作废」；`defines_human_labels.csv` 仅作预填参照 |
| 「严口径 660」 | 改为 **559/710 = 78.7%（含人工 564）**，并注明只读复算 |
| 「DEFINES 819」（作当前值） | 改为 **710（705 规则 + 5 人工）**；819 仅保留为「旧口径/清洗前」历史 |
| 「GraphRAG」 | 改为「图谱 RAG / KG-RAG（图增强检索）」；图路是自实现的单跳/2-hop/PPR，不是 GraphRAG |
| 「5,697 边」（作当前值） | 改为 **5,826**；5697 只出现在 Neo4j 旧导入与 ablation 重建前说明 |
| 「REFERS_TO 4,204」「概念层人工边 577」「2.76 边/概念」 | 改为 4,119、706、2.51 |
| 「路由 0.700 / 0.7375」「0.85/85% 达标口径」 | 改为 n=260 级联 0.7731 / gold-only 0.705，目标统一写 0.88 未达标 |
| 「0.442 / 0.233」（作当前） | 改为 0.4389 / 0.2167 |

---

## 6. 改动文件与备份

**新增**
- `data/eval_out/retrieval_recheck.json`（19:14，干净重跑，仅 text/graph/hybrid 三档 + gold_report）
- `reports/recheck_b3b6.md`（本文）

**更新（数据）**
- `data/eval_out/retrieval.json`：text/graph/hybrid 三档并入 19:14 重跑值（保留 `graph_ppr`/`graph_2hop`/`hybrid_rerank` 与 `gold_report`）；延迟保留干净环境值，重跑延迟写进 `latency_note`
- `data/eval_out/router.json`（19:13 重跑覆盖，值与旧版逐位一致）

**更新（文档）**
- `README.md`（§2 §3 §5 §8 §12 §13）
- `reports/case_bank.md`、`reports/defines_guard.md`
- `F:\大模型应用开发\个人简历\算法岗求职行动清单.md`（§1.3、§8、附录 A、T01/T11/T13 卡）

**备份（未删除任何旧 JSON）**
- `data/eval_out/retrieval_pre_recheck.json`（= 18:20 版；另有 `_backup_o_20261031/retrieval.20261031_182054.json`）
- `data/eval_out/router_pre_recheck.json`（= 11:59 版）
- `data/eval_out/ablation_pre_recheck.json`（= 11:44 `llm_mode=llm` 版，`ablation.json` 未改动）
- 日志：`logs/v_recheck_retrieval.log`（首行 `图后端: networkx`）、`logs/v_recheck_router.log`、`logs/pytest_v.log`

**未动**：`data/graph/`、`data/lexicon/`、`scripts/eval_*.py` 口径与判据、`build_resume*.py`、git（无写操作）。
