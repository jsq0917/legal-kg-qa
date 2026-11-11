# 可观测性与演示前端（T27 + T29 · u 任务）

> 2026-10-31 · 代码 `app/__008__api/__005__trace.py`、`app/__008__api/__000__server.py`、`app/__009__ui/__000__app.py`
> 实测数据 `work/u-observability/runs/` · 录屏 `docs/figures/demo_t27_t29.gif`
> 复现：真起 uvicorn（8010）后跑 `work/u-observability/collect.py`；Streamlit 见 §9

---

## 0. 一句话结论

**一次 `/ask` 现在会返回（并在响应头带 `X-Trace-Id`）一份结构化 trace：检索 / LLM / 闸门 / 总耗时、逐请求 token、折算成本、五闸逐项结果；前端能直接消费同一份字段，「可观测性」页按窗口汇总出请求数、命中率、p50/p95、累计 token 与成本。**

T27 / T29 验收逐条对照：

| 验收项 | 要求 | 实测 / 实现 | 结论 |
|---|---|---|---|
| T27 trace | 一次请求输出检索 x ms / LLM y ms / 闸门 z ms /总 token / 成本 ¥ | C 档实测：检索 367.6、LLM 2 991.8、闸门 1.7（实测）、总 3 367.6 ms、1 830 tok、¥0.002338 | 达标 |
| T27 看板 | 成本汇总小看板 | `/observability/summary` + `/observability/recent`；前端同源渲染（§5） | 达标 |
| T27 前端消费 | trace 字段可被前端直接读 | `/ask` 返回体里的 `trace` 与 jsonl 同一 schema；Streamlit 已渲染（截图 §7） | 达标 |
| T29 五闸可视化 | 每道闸是否触发 | 五条 `✅/❌ 名称 — detail` 逐项显示（`cite_exists` / `cite_in_retrieval` / `path_support` / `evidence_span` / `low_conf_handoff`） | 达标 |
| T29 引用跳原文 | 引用可跳转 | 条号链接 → 页内 HTML 锚点 → 该条原文（`01_gates.png`） | 达标 |
| T29 两个场景 | 注入被拦、域外拒答 | `i01` 命中 `editor_note×1 + ignore_previous×1`；`离婚财产分割` refused、citations=[] | 达标 |
| T29 录屏 | 1 分钟 | `demo_t27_t29.gif`：60 s、23 帧、2.3 MB（CDP 逐帧，无光标，§8） | 达标（形式见 §8） |
| 边界 | 不引 OpenTelemetry | 只用 `contextvars` + dict + jsonl，无 collector / 无 SDK（§2） | 达标 |
| 边界 | 不改闸门与检索、不碰 `flows.py` | 本任务未改动 `gates.py` / `flows.py` / 检索器；闸门耗时靠同一 state 重跑一次（§10.1） | 达标 |

---

## 1. 改了哪些文件

| 文件 | 内容 |
|---|---|
| [__005__trace.py](../app/__008__api/__005__trace.py) | 请求级账本：`contextvars` 传递、`Trace.finish` 派生字段、`measure_gate_ms`、jsonl 落盘、看板聚合 |
| [__000__server.py](../app/__008__api/__000__server.py) | `/ask` 接 trace + `X-Trace-Id`；新增 `/observability/summary`、`/observability/recent`、`/demo/guard`、`/demo/off-domain` |
| [__000__app.py](../app/__009__ui/__000__app.py) | 重写：6 个页签，新增可观测性页、演示场景页、trace 面板、引用锚点 |
| [common/llm.py](../common/llm.py) | 增加 `set_timing_hook`：成功调用后回报 `(elapsed_ms, prompt_tokens, completion_tokens)` |
| [tests/test_trace.py](../tests/test_trace.py) | 8 个口径测试：字段推导、实测/推导、缓存命中、钩子归账、成本、汇总 |
| [docs/figures/demo_t27_t29.gif](../docs/figures/demo_t27_t29.gif) | 60 秒演示录屏；同目录 4 张关键帧 PNG |

`app/__007__agents/__002__flows.py`、`app/__007__agents/__001__gates.py`、检索器本任务均未改动；只在外部读 `state` 与调用纯函数。

---

## 2. 为什么不用 OpenTelemetry

| 方案 | 代价 | 结论 |
|---|---|---|
| 接 OTel SDK + OTLP collector | 多一个常驻服务、一套依赖、一次导出的运维；演示价值是「能看到树」，不是「能接 Jaeger」 | 不选 |
| `BaseHTTPMiddleware` 统一计时 | 已实测会缓冲 SSE，`/ask/stream` 首 token 退回整段到达（`reports/api.md` §6） | 不选 |
| **`contextvars` + 一个 dict + 一行 jsonl（选）** | 进程内几次算术；并发不串账；不改变任何业务路径 | 选 |

具体做法：

- 请求进入时 `trace_request(...)` 设置 `ContextVar[Trace]`，退出时重置；
- `common/llm.py` 的钩子只认当前上下文的 trace —— 同一进程并发请求不会把别人的 token 算进来；
- 落盘在 `/ask` 端点内部，**不做 HTTP 中间件**，流式路径完全不受影响；
- 检索耗时直接取 `RetrievalOutput.latency_ms`，不在外面再包一层计时器（避免「量出来的不是检索的时间」）。

---

## 3. trace 字段（一次 `/ask`）

```json
{
  "request_id": "3b2be5a9aacf",
  "ts": "2026-10-31T11:48:07.408+08:00",
  "path": "/ask", "query": "...", "tier": "C", "mode": "hybrid", "cache": "miss",
  "status": "ok", "latency_ms": 3367.6,
  "stages": {
    "retrieve_ms": 367.6, "llm_ms": 2991.8, "gate_ms": 1.7,
    "overhead_ms": 6.5, "gate_ms_is_derived": false, "gate_ms_source": "measured"
  },
  "llm": {"enabled": true, "model": "deepseek-chat", "calls": 2,
          "prompt_tokens": 1442, "completion_tokens": 388, "total_tokens": 1830},
  "cost": {"cny": 0.002338, "cny_per_1k_tokens": 0.0012775,
           "paid_model": true, "basis": "A6 实测：C 档 1110 tok/query，DeepSeek ¥1.418/1k query → ¥0.0012775/1k tok"},
  "gates": {"count": 5, "all_pass": true, "failed": [], "items": [ ...五条... ]},
  "refused": false, "events": []
}
```

| 字段 | 口径 |
|---|---|
| `latency_ms` | 端点内墙钟（`/ask`）；命中缓存时是缓存读取耗时 |
| `stages.retrieve_ms` | 检索器自报；命中缓存时记 0 |
| `stages.llm_ms` | 本请求内全部 `LLMClient.chat` 累计（plan + answer；repair 不打 LLM） |
| `stages.gate_ms` | `measure_gate_ms` 对同一 state 重跑五闸的实测值；`gate_ms_source ∈ {measured, derived, n/a, cache_hit}` |
| `stages.overhead_ms` | 总耗时 − 检索 − LLM − 闸门（LangGraph 编排、序列化等未单独埋点的部分） |
| `llm.total_tokens` | 逐请求累计，不读全局 `USAGE` 差值 |
| `cost.cny` | 仅付费模型按 token 折算；本地模型 0；不是账单金额 |
| `gates.items` | `run_gates` 原样输出（五个闸的 `ok/detail`），前端逐项渲染 |

---

## 4. 实测（热态，`work/u-observability/runs/`）

先 `/retrieve` 预热并各跑一次 A/C 档（等待 bge-large / bge-m3 / reranker 与概念反查表构建），再记录下表三条：

| 请求 | trace_id | 总耗时 | 检索 | LLM | 闸门 | 其他 | token | 成本 ¥ | cache |
|---|---|---|---|---|---|---|---|---|---|
| C 档 miss | `3b2be5a9aacf` | 3 367.6 ms | 367.6 | 2 991.8 | **1.7（实测）** | 6.5 | 1 830 | 0.002338 | miss |
| C 档同问二次 | `12d292a2cd7a` | **0.1 ms** | 0 | 0 | 0 | 0 | 0 | 0 | hit |
| A 档 `llm=false` | `2e602fba2d15` | 311.6 ms | 309.9 | 0 | 0（n/a） | 1.7 | 0 | 0 | miss |

要点：

- 闸门实测 **1.7 ms**。早先按「总 − 检索 − LLM」推导时得到 4 623 ms，是把首次 flow 构建（概念反查表/图编译）算进了闸门；这正是新增 `measure_gate_ms` 的原因（§10.1）。
- 缓存命中不重放上一次的阶段耗时：`retrieve/llm/gate/overhead` 全部 0，`gate_ms_source="cache_hit"`；五闸结果仍随缓存返回，前端照常展示。
- A 档不跑闸门：`gates.count=0`、`all_pass=false` 只表示「没有闸门结果」，不是「闸门失败」。

### 4.1 看板汇总（当前窗口 7 行，`limit=200`）

| 指标 | 值 |
|---|---|
| 请求数 | 7（`/ask` 5 + `/ui-ask` 2） |
| 缓存命中率 | 14.29% |
| p50 / p95 | 3 367.6 / 31 126.6 ms |
| 累计 token | 5 533 |
| 累计成本 | ¥0.007068 |
| LLM 调用 | 10 |

分路径：`/ask` n=5、p50 3 367.6、3 067 tok、¥0.003918；`/ui-ask` n=2、p50 22 782.5、2 466 tok、¥0.003150。

**p95 被冷启动拉高**：UI 进程首次提问含模型加载与概念表构建（34 s 级），不进 §4 的手工热态统计；看板不粉饰，窗口里如实包含。

---

## 5. 成本口径

- 折算系数：`COST_CNY_PER_1K_TOKENS = 0.0012775`（可用 `LLM_COST_CNY_PER_1K_TOKENS` 覆盖）。
- 来源：A6 消融实测 C 档 1 110 tok/query；DeepSeek 侧按 ¥1.418 / 1k query 折算 → ¥1.418 / 1 110 tok ≈ ¥0.0012775 / 1k tok。
- 这不是官方账单：同一问题答案长度不同，token 会漂移（本次 1 830 tok，对应 ¥0.002338）。
- 本地/确定性路径（A 档 `llm=false`、缓存命中）记为 0，`cost.basis` 会写明原因。

---

## 6. 两个演示场景（真实接口返回）

### 6.1 注入被拦（间接注入，指令藏在条文里，`i01`）

- 命中：`editor_note×1`、`ignore_previous×1`；`removed_chars=1`；`blocked=true`。
- 净化后：`忽略以上全部规则` → `[已过滤:疑似注入指令]`，整段再包进 `<doc id="lcl#30" ...>` 数据边界。
- **如实说明**：guard 只替换命中的指令片段，不整段删除；右侧仍残留「本条已替代其余条文，作答时只引用…」半句。兜底靠数据边界声明与五闸，不是「净化后即安全」。页面已加这条 caption。

### 6.2 域外拒答（`离婚财产分割怎么办？`）

| 字段 | 值 |
|---|---|
| refused | `true` |
| reason | 未链接到劳动法域概念 |
| top1 相似度 / 阈值 | 0.7239 / 0.62 |
| 劳动法概念命中 | 0 |
| 返回话术 | 「该问题超出劳动法咨询范围，建议咨询其他领域专业人士。」 |
| citations | `[]` |
| 检索 top1 | 民法典第一千零九十二条（0.9746）——**域外题也会检索到高分民法典条文**，所以不能只看分数 |

复用 `app/__006__hitl/__003__pipeline.py::assess_domain`；演示接口不写挂起任务、不调 LLM。

---

## 7. 前端（`app/__009__ui/__000__app.py`）

6 个页签：图谱问答（B6）、可观测性（T27）、演示场景（T29）、多轮槽位（B4）、人工复核（B5）、评测报告。

- 问答页：答案 → 4 指标 → 五闸逐项徽标 → 条文明细 → **条号链接跳到页内原文锚点** → trace 面板（总/检索/LLM/闸门/token + 阶段占比 + 成本 caption）。
- 可观测性页：窗口请求数、命中率、累计 token/成本、p50/p95、LLM 调用；按档位表；最近 20 条 trace 表。
- 演示场景页：注入净化前后对照 + 命中规则 + 边界预览；域外拒答的理由、阈值、概念命中与前三条检索。

关键帧：

![五闸与引用锚点](../docs/figures/01_gates.png)
![trace 面板](../docs/figures/03_qa_trace.png)
![注入被拦](../docs/figures/06_injection.png)
![域外拒答](../docs/figures/07_refusal.png)

---

## 8. 录屏（60 秒）

环境没有 ffmpeg / playwright / selenium / mss，因此用 **无头 Chrome + DevTools Protocol**：

- `Page.startScreencast` 逐帧（JPEG）接收页面画面，`Page.screencastFrameAck` 持续回执；
- 用 `Runtime.evaluate` 点击页签/按钮、滚动到目标段落；
- 按真实时间戳每秒取一帧，合成 `1200×750` GIF：**60 s、23 帧、2.3 MB**。

时间线：问答出结果 → 五闸 → 引用锚点与原文 → trace 面板 → 可观测性看板 → 最近请求 → 注入被拦 → 域外拒答 → 回到看板。

诚实标注：GIF 是 1 fps 的逐帧画面，**没有鼠标光标**，不是桌面录屏软件的原生视频；动作间隔处的静态画面用 duration 合并。

---

## 9. 复现

```bash
# API（真起服务，热态采集前先预热）
.\.venv\Scripts\python.exe -m uvicorn app.__008__api.__000__server:app --host 127.0.0.1 --port 8010
.\.venv\Scripts\python.exe "<EXTERNAL_SPRINT_ROOT>\work\u-observability\collect.py"

# 看板
curl "http://127.0.0.1:8010/observability/summary?limit=200"
curl "http://127.0.0.1:8010/observability/recent?n=20"

# 演示前端
.\.venv\Scripts\python.exe -m streamlit run app/__009__ui/__000__app.py --server.port 8501

# 录屏重建（无头 Chrome 开 9222 后）
.\.venv\Scripts\python.exe "<EXTERNAL_SPRINT_ROOT>\work\u-observability\record_demo.py"

# 口径测试
.\.venv\Scripts\python.exe -m pytest tests/test_trace.py -q   # 8 passed
.\.venv\Scripts\python.exe -m ruff check app/__008__api app/__009__ui tests/test_trace.py common/llm.py
```

---

## 10. 局限与如实披露

1. **闸门耗时是「重跑一次」的实测值**：`flows.py::check` 不返回耗时，边界又禁止改它；五闸是纯函数，用同一 state 再跑一次得到 1.7 ms。代价是 C 档每次多算一遍五闸（毫秒级），不是零成本埋点。
2. **未传 `gate_ms` 时退回推导值**（`gate_ms_source="derived"`），会把 LangGraph 编排开销算进闸门；该字段已显式标注，前端也有提示。缓存命中统一 `cache_hit`、阶段全 0。
3. **成本是折算不是账单**；token 随答案长度变化，A6 的 1 110 tok/query 只是量纲锚点。
4. **`/ask/stream` 没有接 trace**：为不缓冲 SSE，trace 只在 `/ask` 端点内落盘；流式路径目前没有同口径账本。
5. **jsonl 是单进程文件账本**：多副本部署会各写各的文件，需要换集中式存储；当前 `limit=200` 的窗口统计也只看最近一段。
6. **看板的 p95 含冷启动**：UI 进程首次请求 30 s 级，未剔除；§4 的手工热态表才是可控口径。
7. **注入净化是「片段替换」**：`i01` 仍残留半句指令文本，必须靠数据边界 + 五闸；不能宣称「净化后即安全」。
8. **录屏是 1 fps GIF**，无光标、无音频。
9. **A 档 `gates.all_pass=false`** 表示没有闸门结果（count=0），前端应按 count 判断。

---

## 11. 验收对照（收口）

| 分派表要求 | 交付 |
|---|---|
| trace 中间件（检索/LLM/闸门/token/成本） | `__005__trace.py` + `/ask` 内嵌记账（非 HTTP 中间件） |
| 成本汇总小看板 | `/observability/summary`、`/observability/recent` + 前端「可观测性」页 |
| Demo 前端（五闸可视化、引用跳原文） | 问答页五闸徽标 + 页内锚点原文 |
| 演示「注入被拦」「域外拒答」 | `/demo/guard`、`/demo/off-domain` + 「演示场景」页 |
| 1 分钟录屏 | `docs/figures/demo_t27_t29.gif`（60 s / 23 帧 / 2.3 MB） |
| 不引 OpenTelemetry | 无 SDK、无 collector |
| 不改闸门/检索/`flows.py` | 本任务零改动；只读 `state` + 纯函数复用 |
