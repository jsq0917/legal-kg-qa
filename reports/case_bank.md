# 失败案例库（case bank）

> T09 · 2026-10-30 · 数据：`data/cases/case_bank.jsonl`（116 条）
> 用途：面试里「最大的坑」「被证伪的结论」「为什么指标不涨」直接从库里取，不再靠回忆。
> 边界：只收已发生过、能在产物里指到的失败；不收 T05（域外硬答）与 T28（注入成功），那两类留给 T09b。

---

## 0. 一句话结论

**116 条 case，112 条仍 open，4 条已 fixed。**
真正值钱的不是「错了 116 次」，而是四条跨项目复现的机理：

1. **错误转移**：补数据把「关系整段不输出」从 55.0% 压到 35.0%（p=0.0005），但关系滥造从 41.7% 升到 63.3%，总 F1 的 95%CI 仍跨 0；
2. **定义边假阳性**：DEFINES 机械复核误配 68/710 = 9.58%（仅规则 63/705 = 8.94%），另有 211 条待语义判定，图路 R@5 0.4389（60 条 / in-sample）的天花板就在这层边；
3. **金标条文层必要性**：60 条里 8 条「概念对得上、条文对不上」，最差一条 g55 有 3 条锁定条文被判非必要；
4. **模型放错位置**：1.5B LoRA 训的是三元组抽取，却挂在回答节点；repair 触发 95%–100%，引用幻觉是默认行为不是偶发。

---

## 1. 库结构

每行一条 JSONL，字段与任务卡一致：

| 字段 | 含义 |
|---|---|
| `id` | `A3-*` / `B2-*` / `B7-*` / `A6-*` |
| `scene` | 场景（数据闭环 / 金标 / 图谱 / 本地模型接入） |
| `symptom` | 可观察现象，带数字与具体样本 |
| `root_cause` | 机理，不写「模型不行」这类空话 |
| `fix` | 已修法或下一步；`open` 的末尾一定带 `→ Txx` |
| `regression_cmd` | 一条可执行命令，断言该现象仍在（或修复未被回退） |
| `artifact` | 产物路径（JSONL 带 `#id=` / `#items[i]` 定位） |
| `status` | 只允许 `fixed` / `open` |

`open` 的 T 编号分布：

| 链到 | 条数 | 含义 |
|---|---:|---|
| T16 闭环复评 | 60 | A3 三条臂里每条都仍有关系错误，等 T16 统一复评 |
| T01 DEFINES 主语位闸门 | 37 | B7 旧盲判的 37 条「顺带提及」（现已归入机械复核 211 条待语义判定） |
| T07 金标 60→200 | 8 | B2 条文层必要性失败 |
| T15 A6b 抽取节点 | 2 | LoRA 位置错配 |
| T08 评测 harness | 2 | cite_precision 指标口径 |
| T17 / T18 / T20 | 各 1 | 容量差 / 偏好对齐 / 推理引擎延迟 |

> 说明：T16 是「复评」不是「修复」，所以 A3 的 60 条全部是 `open`。没有把「定向补数据后错误换了形态」写成 `fixed`。

---

## 2. 数量与来源

| 来源 | 库内条数 | 原始记录 | 聚合方式 |
|---|---:|---|---|
| A3 数据闭环 | 60 | 180 行（3 臂 × 60 条 test） | 同一篇条文在三臂各有一条，按条文聚合成 1 条；`symptom` 保留 v1 / v2_random / v2_targeted 三臂各自的 primary 与缺/多关系数 |
| B2 多跳金标 | 8 | 60 条里的 8 条条文层必要性失败 | 逐条 |
| B7 DEFINES 盲判 | 37 | 100 条盲判里的 37 条分歧 | 逐条 |
| A6 本地 1.5B 接入 | 11 | 60 题三臂 + 工程日志 | 4 条行级（g01/g02/g06/g20）+ 7 条工程（adapter 版本、rope_theta、chat_template、节点错配、repair、延迟、指标天花板） |
| **合计** | **116** | — | — |

A3 三臂的失效分布（原始 180 行，直接来自 `badcase_*_test.jsonl`）：

| 臂 | relation_absent | relation_spurious | dangling | 其他 |
|---|---:|---:|---:|---:|
| `v1_base` | 32（55.0%） | 22（41.7%） | 2 | 4 |
| `v2_random` | 24（40.0%） | 32（58.3%） | 2 | 2 |
| `v2_targeted` | 21（35.0%） | 34（63.3%） | 2 | 3 |

> 「其他」含 `parse_error` / `relation_direction` / `relation_type` / `entity_*` / `other`。全部逐条可查，见 artifact 里的三个文件。

---

## 3. 五个最值得讲的坑

### 3.1 错误转移：数据能改「写不写」，改不了「写得对不对」

- 代表 case：`A3-CC-0003`（民法典第 3 条，金标 3 条关系，v1 输出 0 条；v2_targeted 输出 6 条但只有 2 条对）。
- 数字：目标失效 55.0% → 35.0%（相对 −36.4%，McNemar p=0.0005，零恶化）；但关系滥造 41.7% → 63.3%，关系 TP 13 → 17、FP 71 → 103，总 F1 Δ +1.51pt，95%CI [−2.76, +6.01] 跨 0。
- 面试说法：**「我不是没涨，是错误搬家了。继续补同分布数据只会把错误在不输出和滥造之间搬来搬去；瓶颈在训练目标和标注粒度，不在数据量。」**

### 3.2 定义边 9.58% 误配：图路弱不是检索算法的锅

- 代表 case：`B7-001`（`cc#791`「土地承包经营权」，旧盲判理由：条文规范建设工程合同，未涉及该概念）。
- 数字（2026-10-31 重建后口径）：DEFINES 710 条里机械可证误配 **68/710 = 9.58%**（只算规则边 63/705 = **8.94%**），
  待语义判定 **211 条**；`derived_only` 47、`normal` 384；严口径只读复算 **559/710 = 78.7%**（含人工 564）。
  图路 R@5 **0.4389**（60 条 / in-sample），概念层人工边密度 706 条 / 281 概念 ≈ 2.51 条/概念。
- 面试说法：**「我的图有 1,799 节点 / 5,826 边，DEFINES 机械复核误配 9.58%（68/710），另有 211 条待语义判定。这层边不是干净定义事实，也解释了图路召回为什么只有 0.44。」**
- 口径提醒：旧「盲判 63/100」是 LLM 盲判，两版提示词在 27%↔63% 间摆动，**不再当作精度使用**；这里只写机械口径。

### 3.3 金标条文层必要性 52/60：概念对得上，条文对不上

- 代表 case：`B2-g26`（押金/定金题，`cc#733` 被判非必要）、`B2-g55`（工伤与侵权双重获赔，`ll#73`、`cc#1165`、`cc#1166` 三条全失败）。
- 数字：8/60 条未通过；8 条分别是 g26 / g36 / g43 / g45 / g46 / g47 / g55 / g56。
- 面试说法：**「我没有把『检索到了』当成『金标对了』。金标自己也有 8 条条文层必要性问题，g26/g46/g55 到现在仍登记为 UNRESOLVED。」**

### 3.4 模型放错位置：LoRA 训抽取，却挂回答

- 代表 case：`A6-E04`、`A6-E05`。
- 数字：同一 60 题，DeepSeek repair 28/60（46.7%），LoRA 57/60（95.0%），1.5B 基座 60/60（100%）；五闸里真正在拦的只有 `cite_in_retrieval`。
- 面试说法：**「1.5B 的幻觉引用不是偶发，是默认行为。更关键的是我这个 adapter 训的是三元组抽取，却挂在回答节点上——位置就错了，现在挪回抽取节点做 A6b 试点。」**

### 3.5 三臂对照不是「微调一定更好」

- 代表 case：`A6-g02`（LoRA cite_p 0.25 / grounded 0.0，基座 0.5 / 1.0，adapter 负迁移）、`A6-g06`（LoRA 与基座都 grounded 0，DeepSeek 1.0）。
- 数字：总体 LoRA vs 基座 cite_precision +3.13pt（95%CI [+0.74, +5.60] 显著），但逐题有负迁移；LoRA vs DeepSeek −4.46pt（[−8.71, −0.57] 显著）。
- 面试说法：**「我只说总体显著的方向，不拿单题最好看的那条当结论。g02 就是反例：adapter 在这题上是负迁移。」**

---

## 4. 怎么检索

```powershell
# 按来源
Select-String -Path data/cases/case_bank.jsonl -Pattern '"id": "A3-'

# 按根因关键词
Select-String -Path data/cases/case_bank.jsonl -Pattern '错误转移|主语位|必要性|负迁移'

# 只看 open 且链到某个 T
Select-String -Path data/cases/case_bank.jsonl -Pattern 'T16|T01|T07'
```

字段是扁平的，`jq` / Python / Excel 都能直接读；没有嵌套结构，避免面试现场翻不出来。

---

## 5. 可复现性

**全量 116 条 `regression_cmd` 已逐条执行，116/116 返回 0。** 下表是验收要求的 10 条抽样：

| case | 复现结果 |
|---|---|
| `A3-CC-0003` | `REPRODUCED CC-0003 relation_absent 3 0` |
| `A3-CC-0016` | `REPRODUCED CC-0016 relation_absent 2 0` |
| `A3-CC-0034` | `REPRODUCED CC-0034 dangling_rel 3 4` |
| `B2-g26` | `REPRODUCED g26 cc#733` |
| `B2-g55` | `REPRODUCED g55 ll#73` |
| `B7-001` | `REPRODUCED cc#791 土地承包经营权 条文规范建设工程合同，未涉及土地承包经营权` |
| `B7-033` | `REPRODUCED cc#620 标的物 标的物在介词短语中，规范对象是买受人检验义务` |
| `A6-g01` | `REPRODUCED g01 0.3333 0.5 0.5 0.0 1.0 1.0` |
| `A6-E02` | `OK rope_theta=1e6` |
| `A6-E06` | `5136.7 3687.6 0.098 1.418` |

口径要说清：**`regression_cmd` 复现的是「已落盘的失败证据」，不是重跑模型。** 它断言原始 badcase 行 / 盲判项 / 三臂指标仍然是这个值，用来防止「报告说修好了但证据被改了」。真正重跑模型的命令在 §6。

---

## 6. 重跑命令（可选，成本更高）

```powershell
# A3 badcase 逐条归因（约 1 分钟，CPU；tag 换成 regress 避免覆盖原产物）
cd F:/大模型应用开发/算法岗冲刺/work/a3-data-loop
../a1-lora-sft/.venv/Scripts/python.exe scripts/01_badcase.py `
  --preds runs/v1_base_test/preds.jsonl --gold ../a1-lora-sft/data/test_gold.jsonl `
  --articles ../a1-lora-sft/data/articles_all.jsonl --tag regress_v1

# B2 条文层必要性（会连本机 Neo4j，约 5 秒）
cd <仓库根>  # 切回主仓库（上文在 F: 的冲刺目录里执行）
./.venv/Scripts/python.exe scripts/check_gold.py `
  --gold-file data/gold/gold_60.jsonl --out data/eval_out/gold60_check.json

# B7 盲判（约 5 分钟，调 LLM；会覆盖 data/eval_out/defines_blind_review.json）
# 注意：T01 正在动 __003__edge_typing.py，跑前先确认没有别的会话在写这份产物
./.venv/Scripts/python.exe scripts/audit_defines.py --mode blind --sample 100 --seed 20261030

# A6 三臂对照（复用已落盘的 60 条检索缓存，约 15 分钟）
cd F:/大模型应用开发/算法岗冲刺/work/a6-local-llm
../a1-lora-sft/.venv/Scripts/python.exe scripts/03_compare.py
```

> A6 的 `03_compare.py` 只重算 `compare.json`；要重跑三臂生成需 `02_eval_c_tier.py --retrieval-cache reports/retrieval_60.json`，并保证 Ollama 端点可用。

---

## 7. 边界与局限

1. **A3 的 180 行聚合成 60 条**：同一篇条文在三臂里各出现一次，重复三次没有检索价值；聚合后 `symptom` 保留三臂差异，artifact 仍指向三个原始文件。原始 180 行一条没删。
2. **B7 的 37 条是 LLM 盲判，不是人工盲标**：`reviewer` 字段写明三投；这 37 条只能证明「与建边规则不同源的判断认为它只是顺带提及」，不能替代 H-01 人工 100 条。
3. **不收 T05 / T28**：域外硬答与注入成功属于 W3 的 T09b，本轮库内 0 条；避免把未发生的 case 提前写进去。
4. **`fixed` 只有 4 条**：A6 的 adapter 版本、rope_theta、chat_template、以及 g01 的基座失败。其余 112 条是真实未解，不做「已修复」美化。
5. **没有改任何源码**：本轮只新增 `data/cases/case_bank.jsonl` 与本报告；评测脚本、图谱、金标、flows 均未动。

---

## 8. 产物

| 产物 | 路径 |
|---|---|
| 案例库 | `data/cases/case_bank.jsonl`（116 行） |
| 本报告 | `reports/case_bank.md` |
| A3 原始 badcase | `F:/大模型应用开发/算法岗冲刺/work/a3-data-loop/data/badcase_{v1_base,v2_random,v2_targeted}_test.jsonl` |
| B2 金标 | `data/gold/gold_60.jsonl`、`data/eval_out/gold_article_review.json` |
| B7 盲判 | `data/eval_out/defines_blind_review.json` |
| A6 三臂 | `F:/大模型应用开发/算法岗冲刺/work/a6-local-llm/reports/ctier_*.json`、`compare.json` |
