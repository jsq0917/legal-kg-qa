# LexWeave 独立验收报告

- 验收对象：`LexWeave`（仓库根），HEAD `5853cf6`（master，较 origin/main 领先 18 个提交，未推送）
- 验收依据：`<EXTERNAL_TASK_BRIEF>`（只读，未修改）
- 验收时间：2026-11-11
- 验收方式：陌生仓库独立复核；自己跑测试、跑 parity、起服务、重算指标；不引用文档自述数字作为结论
- 只读声明：未修改仓库任何受版本控制的文件；临时脚本与原始输出全部放在 `2026-11-11\acceptance\work\`；验收前 `git status --porcelain` 为空，验收后仅多出 `?? 2026-11-11/`（本报告目录）
- 环境：项目自带 `.venv`（Python 3.13）、Neo4j 容器 `lexweave-neo4j`（`neo4j:5.26-community`，验收时已运行 7 小时，仅查询未写入）；API 服务在 8010 端口临时启动，验收结束已停止，端口已释放

## 总判定

**不通过。**

核心检索指标、配对区间、图版本 parity、B6 消融数字都能从原始 JSON 独立复算并与文档对上；但存在三处硬伤：图路“独有贡献”口径被独立复算推翻（4 题救回中只有 1 题真正来自图候选池）、HTTP 流式响应每次结束都抛 `ContextVar` 异常、`docs/tech_report.md` 仍把 5826/REQUIRES 422 标成“当前默认图”。

## 验收项汇总

| # | 验收项 | 判定 | 最主要的证据 |
|---|---|---|---|
| 1 | 同一图版本 + parity 可机读 | 通过 | `work/graph_parity_verify.json`：7 项检查全 true，Neo4j/NetworkX 同为 `g963749014664`、1799/5632 |
| 2 | 图哈希进入 health/trace/缓存/评测产物 | 部分通过 | `work/health.json`、`work/observability/trace.jsonl` 均带 gid；`citation_metrics_holdout.json` 无自身 gid |
| 3 | 同步与流式最终语义一致 | 部分通过 | 语义一致（final==sync），但每次 `/ask/stream` 结束都抛 `ValueError`，curl 退出码 18 |
| 4 | LLM 超时/重试/备用/降级可测可追踪 | 通过 | 120 个测试全过，其中 LLM 错误 12 个；trace 有降级字段；health 显示 max_retries=2 |
| 5 | 图路贡献有逐条证据、口径成立 | 不通过 | 532 条中 225 条其实在文本 top30 候选池；4 题救回中 3 题在文本候选池，`g157` 图路径为空 |
| 6 | 标注主体/偏置/用途披露完整 | 部分通过 | data_card 披露充分；但 `defines_human_precision.json` 仍写“本人 100/100 人工判定”，provenance 写 AI agent |
| 7 | CI 覆盖图版本/缓存/SSE/LLM 错误 | 部分通过 | CI 有 120 个测试与 parity；SSE 测试只测生成器、不测 HTTP，live 异常未被发现 |
| 8 | README/tech_report/data_card 口径一致 | 不通过 | tech_report L69/L72 仍写当前图 5826、REQUIRES 422；实际 manifest/pkl 为 5632、230 |
| 9 | live/fixture 分开、未伪造人工标注或提升 | 部分通过 | fixture 与 live 版本字段分开；但人工标注字段存在矛盾产物，图路提升被高估 |

---

## 逐条验收

### 1. 同一图版本与 parity（通过）

实际执行：

```powershell
.venv\Scripts\python.exe scripts\check_graph_parity.py --require-neo4j --out 2026-11-11\acceptance\work\graph_parity_verify.json
```

原始输出要点：

- `graph_build_id = g963749014664`
- NetworkX：nodes 1799 / edges 5632
- Neo4j：nodes 1799 / edges 5632；关系分布 `DEFINES 710 / REFERS_TO 4119 / APPLIES_TO 291 / REQUIRES 230 / SPECIAL_OF 185 / CITES 97`
- 检查项全部 true：`manifest_consistent`、`detector_detects_injected_diff`、`detector_detects_injected_edge`、`backends_stats_match`、`backends_consistent`、`neo4j_live`、`neo4j_required_met`

无 Neo4j 时的行为也实测了（把 `NEO4J_URI` 指到 7999）：

```powershell
$env:NEO4J_URI='bolt://127.0.0.1:7999'
.venv\Scripts\python.exe scripts\check_graph_parity.py --out ...\graph_parity_noneo4j.json
```

结果：`neo4j.status = unavailable`、`neo4j_live = false`，检测器自检仍为 true，没有把 skip 伪装成 live pass。状态字符串是 `unavailable` 而不是字面 `skipped`，但语义明确。

证据：`work/graph_parity_verify.json`、`work/graph_parity_noneo4j.json`、`work/health.json`。

### 2. 图哈希进入 health、trace、缓存和评测产物（部分通过）

实测：

- `GET /health`（8010）：`graph.graph_build_id = g963749014664`，`graph_backend = neo4j`，`degraded = false`，nodes 1799 / edges 5632。
- `/ask` 两次：第一次 miss、第二次 hit；两次 trace 都有 `graph_build_id = g963749014664`，`answer_version = 1`。
- 缓存 key：`app/__008__api/__002__cache.py` 的 `make_key` 包含 query/mode/tier/llm/model/prompt_version/policy_version/answer_version/graph_build_id，共 9 项；`tests/test_stream_contract.py` 的 `test_cache_key_separates_graph_and_policy_versions` 通过。
- 当前核心评测产物都带 gid：`retrieval_holdout_140.json`、`retrieval_holdout_140_no_rerank.json`、`graph_contribution.json`、`ablation_holdout_60.json` 全部为 `g963749014664`。

未完全满足的地方：

- `data/eval_out/citation_metrics_holdout.json` 自身没有 `graph_build_id`，只有一个 `source: data/eval_out/ablation_holdout_60.json` 的指针；而 README L69 写的是“所有评测 JSON 都带这个 ID”。严格按字面，这一条不成立。
- `data/eval_out/stream_contract.json` 的 `graph_build_id = gcontract0000`，是 fixture 假版本；README 已明确标注 fixture，这一点没有问题。

证据：`work/health.json`、`work/observability/trace.jsonl`、`work/stream_contract_fixture.json`。

### 3. 同步与流式最终语义一致（部分通过）

实测 1：离线 fixture 契约

```powershell
.venv\Scripts\python.exe scripts\check_stream_contract.py --out ...\stream_contract_fixture.json
```

结果：`mode=fixture`，`total=20`，`failed=0`，4 个档位 × 5 类用例；同步/流式/缓存命中/缓存未命中逐字段比较通过。

实测 2：live 契约

```powershell
.venv\Scripts\python.exe scripts\check_stream_contract.py --live --limit 10 --out ...\stream_contract_live.json
```

结果：`mode=live`，`total=40`，`failed=0`，`graph_build_id = g963749014664`。

实测 3：真实 HTTP 服务

启动 `uvicorn ... --port 8010`，`/ask/stream?query=试用期最长能约定多久？&mode=hybrid&tier=A_gates&llm=false`：

- 最终语义一致：`final.answer == sync.answer`、`citations` 相同、`refused` 相同、5 个 checks 相同、`answer_version` 都是 1、gid 都是 `g963749014664`。
- 但每次流式请求结束时，服务端都记录：

```
File ".../app/__008__api/__003__stream.py", line 147, in stream_answer
    with trace_request(...)
File ".../app/__008__api/__005__trace.py", line 238, in trace_request
    reset_model_hook(model_token)
File ".../common/llm.py", line 116, in reset_model_hook
    _MODEL_HOOK.reset(token)
ValueError: <Token ...> was created in a different Context
```

- `curl.exe -sS -N` 实测退出码 18（`transfer closed with outstanding read data remaining`）；PowerShell `Invoke-WebRequest` 报 `response ended prematurely`。Python `urllib` 有时能读完 21 个事件，但服务端异常仍然出现。
- 更糟的是：`/stream` 的 trace 仍写 `"status": "ok"`，异常没有进入可追踪状态。

结论：同步/流式的答案、引用、拒答、修复和 checks 语义一致（这部分通过）；但流式清理契约失败，正常结束也会抛异常，属于验收项 3 的实质缺陷。证据：`work/stream_contract_fixture.json`、`work/stream_contract_live.json`、`work/uvicorn3.log`、`work/stream_body.txt`、`work/live_stream_compare.py`。

### 4. LLM 超时、重试、备用模型、降级可测可追踪（通过）

实测：

```powershell
.venv\Scripts\python.exe -m pytest -q
# 120 个测试全部通过，退出码 0
.venv\Scripts\python.exe -m pytest tests\test_llm_errors.py tests\test_stream_contract.py tests\test_trace.py tests\test_graph_version.py -q
# 12 + 10 + 8 + 14 全过
```

- `common/llm.py` 有错误分类、重试、可选 fallback model、`record_degradation` hook。
- `/health` 显示 `max_retries=2`；`fallback_model=null`，说明线上没有配置备用模型，但机制存在且有测试覆盖。
- trace 文件包含 `llm_calls`、`degraded`、`degraded_reason` 等字段；测试覆盖超时、429/5xx、schema 重试等 12 个场景。

限制：备用模型只在测试/配置层可验证，线上未配置，因此“线上发生过 fallback”没有实测证据；这不影响“可测试、可追踪”的判定。证据：`work/pytest_full.log`、`tests/test_llm_errors.py`、`work/health.json`。

### 5. 图路独特贡献有逐条证据（不通过）

先从原始 JSON 复算，数字与文档一致：

- `graph_only_candidates` 532 条
- verdict：`necessary 22 / relevant_not_necessary 197 / noise 313`
- 文本 top5 失败 67 题；`text_failed_graph_recovered = 4`
- 失败原因：`entity_linking_failure 9 / graph_path_incomplete 50 / ranking_miss 7`
- 4 个救回案例：`g95 / g97 / g150 / g157`

但独立重跑文本候选池后，口径不成立：

| 检查 | 结果 |
|---|---|
| 532 条“图路独有候选”中，真正不在文本 top30 候选池的 | 307 条（304 条来自 graph top5，3 条来自 graph top10） |
| 532 条中其实在文本 top30 候选池 | **225 条（42.3%）** |
| 22 条 necessary 中在文本 top30 候选池 | **16 条** |
| 22 条 necessary 中真正在图候选池外 | **6 条** |
| 22 条 necessary 中进入 hybrid top5 | 10 条；另外 12 条只出现在 graph top5，没有进入最终 hybrid |
| 4 个救回案例中金标条文在文本 top30 候选池 | **3 个（g95、g97、g157）** |
| 真正的图路救回（金标不在文本 top30，且在图 top5） | **1 个（g150）** |

关键反例 `g157`：

- 缺失金标 `ll#40`，`graph_top5` 里根本没有它（`in_graph_top5 = false`），只在 `hybrid_top5` 里；
- `graph_paths = {"ll#40": []}`，图路径为空；
- 它来自文本 top30 候选池，是精排后进入 top5 的，不是图路救回。

根因在代码：

- `scripts/eval_graph_contribution.py` 文件头 L12 写“出现在图路但不在文本路候选里的条文”；
- 实现 L147 的 `in_text_candidates` 只比较 `text_top`（top5），而候选集定义本身已经排除了 top5，所以 532 条里该字段恒为 false（实测 `in_text_candidates_true = 0`）。这不是一个有效的“图候选池外”检查。
- 因此 `graph_contribution.md` 和 README 里的“图路独有候选 532”“必要 22”“图路补回 4 题”按字面被高估；按同一候选池定义，真实数字是 307 / 6 / 1。

不能判通过：任务书明确要求“每个图路救回的案例都能给出文本路失败原因和完整图路径”，`g157` 的路径为空且不在 graph top5。证据：`work/graph_only_origin.json`、`work/check_graph_only_origin.py`、`data/eval_out/graph_contribution.json`、`data/eval_out/graph_contribution.md`。

### 6. 标注主体、共享组件偏置、样本用途披露（部分通过）

做得好的部分：

- `docs/data_card.md` 明确写了 Pass A（人）起草场景/概念链、Pass B（模型）三票判必要性、Kimi 外部抽检、共享 reranker 偏置、gold_60/gold_140/gold_60_holdout 的用途与交集。
- README L227 明确写“κ 达标但非双人盲标”；L232-L240 区分 Pass A/Pass B；L242-L245 主动解释 5% 泄漏率不可达。
- 留出集数字都带 n 与不确定性。

发现的具体矛盾：

1. `data/eval_out/defines_human_precision.json` 的 `annotator` 写的是“本人（100/100 逐条人工判定）”；同一套标签的 `data/eval_out/defines_human_labels.provenance.json` 写的是 `annotator = AI agent (non-human)`、`requested_by = user`。`reports/defines_guard.md` 也明确把旧的“人工盲标 49/100”判为无效。也就是说，至少有一个标注产物仍在冒充人工。
2. `gold_140_holdout.jsonl` / `gold_60_holdout.jsonl` 的记录里没有 `reviewer`、`generated_at`、`model` 字段，只有 `annotation.pass_a/pass_b_run1/pass_b_run2`；标注主体只能靠文档解释，不能从产物本身机读追溯。
3. 调参历史只能部分验证：`gold_140_holdout.jsonl` 在 `c9f2c3a`（2026-11-10 20:54:02）入库；9 秒后的 `c0504f6`（20:54:11）又改了检索/路由/词典/闸门。当前留出集产物（11-11 01:06 重新生成）的 graph R@5 从 `0.4131` 变成 `0.4250`、hybrid 从 `0.7691` 变成 `0.7714`。这不能证明用留出集调过参，但也无法独立证明“留出集未参与调参”；只能标为“仅文档自述，未验证”。

结论：披露文本完整度较高，但产物层仍有“人工/模型”矛盾，且留出集使用历史无法完全自证，因此部分通过。证据：`data/eval_out/defines_human_precision.json`、`data/eval_out/defines_human_labels.provenance.json`、`reports/defines_guard.md`、`data/gold/gold_140_holdout.jsonl`、`git log c9f2c3a c0504f6`。

### 7. CI 覆盖图版本、缓存、SSE 契约、LLM 错误（部分通过）

CI 配置确实覆盖了这些主题：

- `pytest -q`：120 个测试，含 `test_graph_version.py` 14 个、`test_stream_contract.py` 10 个、`test_llm_errors.py` 12 个、`test_trace.py` 8 个；
- `scripts/check_graph_parity.py` 在 CI 中跑；无 Neo4j 时由 parity 自检覆盖注入差异；
- `test_cache_key_separates_graph_and_policy_versions` 覆盖缓存版本隔离。

但覆盖质量有缺口：

- `tests/test_stream_contract.py` 直接调用 `stream_answer()` 生成器，没有走 FastAPI 的 `StreamingResponse`/threadpool；因此所有测试通过，而真实 `/ask/stream` 每次结束都抛 `ContextVar` 异常。SSE 契约检查没有覆盖传输层。
- CI 的 parity 任务不带 `--require-neo4j`，真正 live Neo4j 的 parity 只在单独 job/手工命令里跑；本次我们手工跑通了。

结论：主题覆盖有，但 SSE 的实际 HTTP 路径没有回归保护，因此部分通过。证据：`.github/workflows/ci.yml`、`tests/test_stream_contract.py`、`work/pytest_full.log`。

### 8. README、tech_report、data_card 口径一致（不通过）

独立对照当前 manifest/pkl：

- 当前图：`g963749014664`，nodes 1799 / edges 5632；`REQUIRES 230`、`SPECIAL_OF 185`、`inferred_true = 0`。
- README L64-L67、L70、L903 与 data_card 基本一致，且 README L70 明确说“5826 / REQUIRES 422 是重建前口径，已作废”。

但 `docs/tech_report.md` 仍自相矛盾：

- L9：`5632` 条边；
- L69：`1,799 / 5,826`，口径写“当前默认图”；
- L72：`REQUIRES 422 / SPECIAL_OF 187`，注释“REQUIRES 含 192 推断、SPECIAL_OF 含 2 推断”；
- L73：`inferred=true 194`。

实际 pkl 里 `inferred_true = 0`，manifest 也是 230/185，说明 L69/L72/L73 是重建前旧口径，却没有像 README 那样标为历史快照。

另外两处数字/说明不一致：

- tech_report L53 写“检索 1000 次、路由 2000 次、配对 1000 次，seed=20261031”；实际 `scripts/eval_retrieval.py` 的 seed 是 `20261029`，`scripts/eval_router.py` 的 seed 是 `42`。我用 `20261029` 复算出的 CI 与产物完全一致，说明文档里的 seed 写错了。
- README L69 写“所有评测 JSON 都带这个 ID”，但 `citation_metrics_holdout.json` 没有自身 `graph_build_id`（只有 source 指针）。

因此第 8 条不能判通过。证据：`docs/tech_report.md` L9/L53/L69/L72/L73、`README.md` L64-L70/L69、`data/graph/graph_manifest.json`、`data/graph/lexweave_graph.pkl`（`inferred_true=0`）、`work/verify_metrics_result.json`。

### 9. live 与 fixture 分开、未伪造人工标注或提升数字（部分通过）

通过的部分：

- fixture 产物 `stream_contract.json`：`model=contract-fixture-model`、`graph_build_id=gcontract0000`；
- live 产物 `stream_contract_live.json`：`model=deepseek-v4.1-flash@...`、`graph_build_id=g963749014664`；
- README 明确标注了 fixture 假版本，没有把 fixture 当 live。
- git 初始工作区干净；`.env` 未被跟踪；`git grep` 未发现 `sk-` 形式的密钥；没有把 `.venv`、缓存、日志提交进仓库。
- 18 个提交未推送，符合“完成后再推送”的约定。

未通过的部分：

- `defines_human_precision.json` 仍把 AI 生成的标签写成“本人 100/100 逐条人工判定”，与 provenance 直接矛盾；这属于“伪造人工标注”的产物级问题，即使文档已声明其无效，产物本身仍不诚实。
- 第 5 条已证明“图路补回 4 题”按候选池口径只有 1 题成立；这属于提升数字被高估，而不是完全伪造。
- `data/gold/llm_cache.json`（约 3.6 MB）作为缓存文件被跟踪；不是阻断问题，但更像运行缓存而非应入库交付物。

结论：live/fixture 分离通过；人工标注与图路提升的诚实性未完全通过。

---

## 独立复算结果（与文档对照）

### 留出集 140（`retrieval_holdout_140.json`）

从 `predicted × gold` 逐题重算，行级不一致 0 条；题号集合与 gold 完全一致（140 题，two/three/cross = 70/46/24）。

| 模式 | R@5（复算） | all@5（复算） | 中位延迟 | 文档 |
|---|---:|---:|---:|---|
| text | 0.7583 | 0.5214 | 211.6 ms | 一致 |
| graph | 0.4250 | 0.1857 | 96.3 ms | 一致 |
| hybrid | 0.7714 | 0.5286 | 244.6 ms | 一致 |

配对 bootstrap（按题重采样，seed 20261029，n=1000，复算与产物一致）：

- hybrid − text：`+1.31pt`，95% CI `[−0.60, +3.21]`，含 0；
- graph − text：`−33.33pt`，95% CI `[−40.12, −27.14]`；
- 用 20 个不同 seed 做稳健性检查：hybrid−text 的 CI 下界在 `−0.71 ~ −0.36pt`、上界在 `+3.10 ~ +3.45pt` 之间，始终含 0。结论“不显著”是稳的。

无精排对照（`retrieval_holdout_140_no_rerank.json`）：

| 模式 | R@5（复算） | all@5（复算） |
|---|---:|---:|
| text | 0.6893 | 0.4143 |
| graph | 0.4250 | 0.1857 |
| hybrid | 0.4286 | 0.1714 |

text − hybrid = `+26.07pt`，95% CI `[+19.41, +32.74]`（文本显著更好），与文档一致。

### B6 留出集 60（`ablation_holdout_60.json`）

从 60 题 × 4 档的逐题 rows 重算：

| 档位 | grounded 修复前 | grounded 修复后 | LLM 调用/题 | tokens/题 | 中位延迟 |
|---|---:|---:|---:|---:|---:|
| A | 0.9333 | 0.9667 | 1.3167 | 888.9 | 2296 ms |
| B | 0.6667 | 0.7333 | 2.3167 | 1618.6 | 3558 ms |
| C | 0.6833 | 0.7833 | 2.3167 | 1605.8 | 3564 ms |
| A_gates | 0.8833 | 1.0000 | 1.3167 | 890.8 | 2302 ms |

- 总调用：`436`；总 tokens：`300326`；与 README §8.2 一致。
- 配对区间复算与产物一致：A_gates−A grounded `+3.33pt [0.00, 8.33]`（含 0 贴边界）；B−A grounded `−23.33pt [−33.33, −11.67]`；A_gates−C grounded `+21.67pt [+11.67, +31.67]`；B−A 引用精确率 `−5.12pt [−8.98, +0.27]`。
- 小差异：README 表里 B 档 tokens 写 1618，实际均值 1618.6（截断而非四舍五入），不影响结论。

证据：`work/verify_metrics_result.json`、`work/verify_metrics.py`。

### 图版本与 parity

- `graph_manifest.json` / pkl / health / Neo4j 均为 `g963749014664`、1799 节点、5632 边。
- pkl 关系分布与 manifest 完全一致；`inferred_true = 0`。
- parity 的 7 项检查全部 true，包含“注入一条差异边能检出并失败”的自检。

---

## 问题清单（按严重度）

### 阻断

1. **`/ask/stream` 结束时必抛 `ValueError`**
   - 位置：[stream.py](../../../app/__008__api/__003__stream.py#L147)、[trace.py](../../../app/__008__api/__005__trace.py#L238)、[llm.py](../../../common/llm.py#L116)
   - `stream_answer()` 同步生成器在 threadpool 中运行，`trace_request` 在生成器线程里 set 的 ContextVar token，在 `close` 时从另一个 Context reset，触发 `ValueError: ... was created in a different Context`。
   - 影响：`curl` 退出码 18，部分客户端收到“response ended prematurely”；trace 仍写 `status: ok`。
2. **图路贡献口径被高估**
   - 位置：[eval_graph_contribution.py](../../../scripts/eval_graph_contribution.py#L12)、[eval_graph_contribution.py](../../../scripts/eval_graph_contribution.py#L147)
   - 532 条“图路独有候选”中 225 条在文本 top30 候选池；4 题“图路救回”中 3 题在文本候选池，`g157` 图路径为空且不在 graph top5。
3. **tech_report 仍把旧图口径标成当前**
   - 位置：[tech_report.md](../../../docs/tech_report.md#L69)、[tech_report.md](../../../docs/tech_report.md#L72)
   - 当前实际 5632 / REQUIRES 230 / SPECIAL_OF 185 / inferred 0，文档写 5826 / 422 / 187 / 194 并标注“当前默认图”。

### 重要

4. **标注产物仍有人工/模型矛盾**
   - `defines_human_precision.json`：`annotator = 本人（100/100 逐条人工判定）`
   - `defines_human_labels.provenance.json`：`annotator = AI agent (non-human)`
5. **SSE 回归测试不覆盖 HTTP 传输层**
   - `tests/test_stream_contract.py` 直接调用生成器，不经过 `StreamingResponse`，因此 120 个测试全过但线上仍异常。
6. **留出集使用历史无法完全自证**
   - 留出集入库 9 秒后即有检索/路由/词典/闸门改动；当前留出集产物是之后重新生成的，graph 0.4131→0.4250、hybrid 0.7691→0.7714。不能证明调过参，但也不能独立证明未调参。

### 次要

7. `docs/tech_report.md#L53` 的 seed `20261031` 与代码实际（检索 `20261029`、路由 `42`）不符。
8. README L69 声称“所有评测 JSON 都带 gid”，但 `citation_metrics_holdout.json` 没有。
9. `data/gold/llm_cache.json`（3.6 MB）作为运行缓存被跟踪，仓库卫生问题。

---

## 仅文档自述、未验证的项

以下内容我只能读到文档说法，无法从现有产物独立证明：

- Pass A（场景/概念链）确实由人逐条编写；概念层 706 条边“100% 人工编写”。
- 103 个题号 / 134 条 `ARTICLE_OVERRIDES` 确实经过人工核对。
- 留出集 140/60 从未参与任何阈值、权重、RRF k、图路径补边的调参。
- 线上曾发生备用模型 fallback（当前 `fallback_model=null`，只有机制和测试）。
- `ablation.json` 确为“重建前、未重跑”的 2026-11-04 产物（只能核对时间戳与文档标签，无法核对“未重跑”的意图）。

---

## 复现命令清单

```powershell
# 1. 全量测试（120 个，退出码 0）
.venv\Scripts\python.exe -m pytest -q

# 2. 图版本 parity（真实 Neo4j）
.venv\Scripts\python.exe scripts\check_graph_parity.py --require-neo4j --out 2026-11-11\acceptance\work\graph_parity_verify.json

# 3. 无 Neo4j 的 skip 行为
$env:NEO4J_URI='bolt://127.0.0.1:7999'
.venv\Scripts\python.exe scripts\check_graph_parity.py --out 2026-11-11\acceptance\work\graph_parity_noneo4j.json

# 4. 流式契约（fixture / live）
.venv\Scripts\python.exe scripts\check_stream_contract.py --out 2026-11-11\acceptance\work\stream_contract_fixture.json
.venv\Scripts\python.exe scripts\check_stream_contract.py --live --limit 10 --out 2026-11-11\acceptance\work\stream_contract_live.json

# 5. 独立复算
.venv\Scripts\python.exe 2026-11-11\acceptance\work\verify_metrics.py
.venv\Scripts\python.exe 2026-11-11\acceptance\work\check_graph_only_origin.py

# 6. 起服务实测 health/trace/cache/SSE
$env:TRACE_DIR='2026-11-11\acceptance\work\observability'
.venv\Scripts\python.exe -m uvicorn app.__008__api.__000__server:app --host 127.0.0.1 --port 8010
curl.exe -sS -N "http://127.0.0.1:8010/ask/stream?query=...&mode=hybrid&tier=A_gates&llm=false"
.venv\Scripts\python.exe 2026-11-11\acceptance\work\live_stream_compare.py
```

## 交付状态

- 验收开始时 `git status --porcelain` 为空；当前只有本报告目录 `?? 2026-11-11/`，没有修改任何原有文件。
- 18 个提交领先 origin/main，未推送（符合约定）。
- 未跟踪 `.env`，未在 git 历史中发现 `sk-` 形式密钥。
- 临时服务已停止，8010 端口无监听；Neo4j 容器保持验收前状态，仅执行只读查询。

## 结论

本项目“能跑、多数核心数字能复算”，但还不能作为一次干净的验收通过：图路贡献的核心叙事被独立候选池复算推翻，线上流式接口存在必现异常，技术报告仍保留旧图口径。建议至少修复上述 3 个阻断项后重新验收。
