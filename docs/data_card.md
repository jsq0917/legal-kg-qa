# 数据卡：LexWeave 金标与评测集

本文件说明 `data/gold/` 下各数据集的**来源、标注主体、用途、调参历史与已知偏置**。
文中数量都可由 [`scripts/make_holdout.py`](file:///e:/project/LexWeave/scripts/make_holdout.py)
与下列命令复算；不可复算或没有人工结论的指标一律标注「不可用」。

## 1. 语料

| 项 | 内容 |
|---|---|
| 来源 | 三部现行法律公开文本：《中华人民共和国劳动合同法》98 条 / 《中华人民共和国劳动法》107 条 / 《中华人民共和国民法典》1260 条 |
| 规模 | 1465 条（`data/extracted/articles.jsonl`） |
| 预处理 | `app/__001__ingest` 切条；`app/__002__extract` 规则 + 词表抽概念/场景/引用，provenance=`rule`/`regex`/`manual` |
| 个人数据 | 无。法律文本属公开信息，不含自然人信息；问答脚本为构造题，不含真实个案 |
| 许可 | 代码 MIT；法条原文不受著作权保护；结构化产物随仓库以 MIT 提供，仅供研究，不构成法律意见 |

## 2. 样本划分与用途

`data/gold/splits.json` 是机器可读版本（数量、`hop_type` 分布、id 集合的 sha256 前缀），
由 `scripts/make_holdout.py` 从 `gold_200.jsonl` 与 `gold_60.jsonl` 复算写出。

| 数据集 | n | 二跳/三跳/跨法 | 角色 | 用途 |
|---|---|---|---|---|
| `gold_200.jsonl` | 200 | 100 / 66 / 34 | 题池全集 `full_pool` | 题池来源；**不单独作为评测分母** |
| `gold_60.jsonl` | 60 | 30 / 20 / 10 | 开发集 `dev_in_sample` | 调参与校准（coverage 系数、RRF k、链接阈值、topK、权重）；B3/B6 的 in-sample 报告 |
| `gold_140_holdout.jsonl` | 140 | 70 / 46 / 24 | 题级留出 `holdout` | 未参与调参；B3 留出集与图路贡献评估的分母 |
| `gold_60_holdout.jsonl` | 60 | 30 / 20 / 10 | 分层留出 `holdout_balanced` | 从 140 条按 `hop_type` 分层抽样（seed 20261110）；B6 四档留出集 |
| `router_260`（`data/eval_out/router.json` 的评测口径） | 260 | — | 路由评测 | `gold_200` + 60 条**词典模板合成**的 simple 题；simple 部分不是真实金标 |

规则：`gold_140` 与 `gold_60_holdout` 是**最终确认集**，不再用于调阈值；后续任何调参只能回到 `gold_60`。

## 3. 标注主体与流程

| 环节 | 主体 | 说明 |
|---|---|---|
| 场景 + 概念链（Pass A） | **人**（项目作者） | `eval/gold_combos.py` 逐条给定 `scenario_id` 与概念链，并写 `intent`；这是骨架，不是独立标注 |
| 条文锁定 | 规则候选 + **精排模型**排序 + 人工 override | 每个概念在本法内取正文命中概念名/别名的候选（≤3 条），用 bge-reranker-v2-m3 按题面相关性选一条；人工核对过的映射写入 `ARTICLE_OVERRIDES`（103 个题号 / 134 条映射，每条带理由） |
| 题面 | **LLM 生成** + 规则校验 | 按「场景 + 诉求 + 争点清单」写成 ≤60 字口语提问；用二元组重合率与最长连续公共子串卡抄写 |
| 必要性（Pass B） | **LLM**（DeepSeek），独立判读，跑两遍 | 只给题面 + 候选条文（含 2 条同法干扰项），判「哪些条文必需」；概念层与条文层各判一遍，三判读多数票 |

**κ 的含义**：`0.9473 / 0.9329 / 0.9856` 衡量的是「Pass A 的人定骨架」与「Pass B 的模型判读」之间的一致度，
以及两次 Pass B 之间的稳定性。**它不是双人独立盲标，也不是标注者间一致性**；且条文级 Pass B 与用于锁条文的精排模型相关，
κ 会被抬高，不能当作独立验证。

**Pass B 不是人工结论**：它用于构造金标，但不能反过来写成「人工标注 200 条」。

## 4. 必要性结果（可复算）

| 口径 | 结果 | 复算 |
|---|---|---|
| 概念层必要性 | 195/200（二跳 99/100、三跳 64/66、跨法 32/34） | `sum(r["validation"]["necessity_pass"] for r in gold_200)` |
| 条文层必要性（B7 验收 ≥170） | 173/200（二跳 94/100、三跳 60/66、跨法 19/34） | `sum(r["validation"]["necessity_article_pass"] ...)` |
| 成句照抄（最长公共子串 ≥10 字） | 0/200 | `validation.leakage_max` 的独立口径 |
| 5% 二元组重合率 | **184/200 超标，不可达** | 逐题 `annotation`/`validation` 原始值；改用上面的可解释口径 |
| 母本 60 条条文层必要性 | 52/60（8 条未过） | `gold_60.jsonl` 同口径 |

未过的 27 条与三类原因见 README §4；它们**保留在分母里**，没有删题或放宽判据。

## 5. 图的标注与「机械通过率」口径

`DEFINES` 边 710 条（705 规则 + 5 人工）。抽检不再使用「只给概念名 + 条文全文让模型盲判」的口径
（同一批边换措辞结论相差三十多个百分点）。现行四桶机械复核
（`scripts/check_defines_edges.py` → `data/eval_out/defines_edges_check_post_rebuild.json`）：

| 桶 | 条数 | 占比 |
|---|---|---|
| `absent`（误配） | 68 | 9.58% |
| `derived_only` | 47 | 6.62% |
| `pending_semantic`（字面命中、语义待判） | 211 | 29.72% |
| `normal` | 384 | 54.08% |

**`pending_semantic` 不计入机械通过率**，也不计入误配率：90.4%（1 − 9.58%）只能读作「字面支撑率」，
不是语义精度。`pending_semantic` 里做过一次 Kimi-K3.1-Pro 批量预判（成立 53/211 = 25.1%），
但那是 **LLM 预判、非人工**，4/211 条 `evidence` 不是原文，标签几乎完全由句法角色门控，
只能当预筛，不能外推到 710 条，也不能写成人工标注。

当前**没有**可靠的 `DEFINES` 语义精度单点估计；可给的只有区间：机械误配 9.58% ↔ 严口径 559/710 = 78.7%。

## 6. 调参历史与污染边界

| 组件 | 校准数据 | 说明 |
|---|---|---|
| coverage 加分系数、RRF `k=60`、融合权重 0.65:0.35 / 0.55:0.45 / 0.45:0.55 | `gold_60` | 在 60 条上试出，属于 in-sample |
| 实体链接阈值 0.85、`MAX_CONCEPTS_PER_ARTICLE=16` | `gold_60` + 词表覆盖实验 | 16 是「旧边零丢失」的最小值，不是按评测分数挑的 |
| `GOLD_PATH_REQUIRES` 59 条概念边 | `gold_200` 路径 | 为修通金标路径追加，**属于按验收结果修数据**，有循环风险；已披露 |
| 140 条与 60 条留出 | 未参与上述任何一步 | 只用于最终确认 |

另有一处**共享组件偏置**：金标条文锁定用了 bge-reranker-v2-m3，检索评测（默认档）也用它。
留出的是**题**，不是组件，所以留出集数字应读作「题级留出」，不能读作完全独立的第三方评测。
缓解办法：`scripts/eval_retrieval.py --no-rerank` 提供一条不经过精排的评价路径；
图路本身不使用精排，可作为不同评价路径的对照。任何「图路提升」结论必须同时给出这两条路径的数字。

**已跑的独立评价路径**（`data/eval_out/retrieval_holdout_140_no_rerank.json`，140 题、`g963749014664`）：
纯文本 R@5 0.6893 / all@5 0.4143，纯图 0.4250 / 0.1857，hybrid 0.4286 / 0.1714；
text − hybrid = +26.07pt [+19.29, +32.38]（显著）。**hybrid 的 +1.31pt 增益依赖精排**，
摘掉精排后加权 RRF 让 10 条图路候选压过文本候选，hybrid 退到图路水平。这条路径是**组件消融**，
不是「更公平的评测器」；它证明的是「增益对共享组件敏感」，不是「图路独立有效」。

## 7. 已知偏置汇总

1. 题面由 LLM 生成，可能复用抽取规则的高频表述，使检索/链接指标偏乐观；
2. 概念层 706 条边（APPLIES_TO 291 / REQUIRES 230 / SPECIAL_OF 185）100% 人工编写，
   其中 59 条 REQUIRES 是为修通金标路径而加；
3. 条文层 Pass B 与锁条文用的是同一个精排模型，κ 被抬高；
4. 跨法题只有 24–34 条，该桶的比例估计区间很宽，不作强结论；
5. 60 条合成 simple 题只用于路由评测，不能与真实金标混算。

## 8. 复算命令

```bash
# 重新生成 140/60 留出集与 splits.json（seed 固定，输出应逐字节一致）
.venv\Scripts\python.exe scripts/make_holdout.py

# 检索层（可加 --gold 切换数据集、--no-rerank 走独立评价路径）
.venv\Scripts\python.exe scripts/eval_retrieval.py --gold data/gold/gold_140_holdout.jsonl

# 图路贡献三层口径
.venv\Scripts\python.exe scripts/eval_graph_contribution.py

# 四档消融（需要 LLM key / 预算；已有缓存结果见 data/eval_out/ablation_holdout_60.json）
.venv\Scripts\python.exe scripts/eval_ablation.py --gold data/gold/gold_60_holdout.jsonl

# DEFINES 四桶机械复核
.venv\Scripts\python.exe scripts/check_defines_edges.py
```

所有新数字必须由上述命令在仓库内复算；没有落盘产物支撑的数字不写进 README / 简历。
