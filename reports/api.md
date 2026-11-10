# API 工程化：流式 / 限流 / 缓存（T26 · g 任务）

> 2026-10-30 · 代码 `app/__008__api/` · 实测数据 `data/eval_out/api_bench.json`
> 复现：`python -m app.__008__api.__004__bench`

---

## 0. 一句话结论

**`/ask` 从「36 s 同步阻塞」变成「首 token 中位 1.73 s、最大 1.92 s」**，同问同参二次命中缓存后 `/ask` 2.5 ms、`/ask/stream` 2.7 ms；并发 10 全 200；限流 IP / Key 两维度都能稳定返回 429 + `retry-after`。

验收四项逐条对照：

| 验收项 | 要求 | 实测 | 结论 |
|---|---|---|---|
| 首 token | ≤ 2 s | 中位 1.73 s，最大 1.92 s（4 题热启动） | 达标 |
| 二次同问命中缓存 | 命中率 + TTFT 降幅 | `/ask` 4 953.6 → 2.5 ms（1 981×）；`/ask/stream` 1 999.0 → 2.7 ms | 达标 |
| 并发 10 | 不崩、限流回 429 带 `retry-after` | 10/10 = 200，墙钟 3.08 s；IP 维度 12 发 7 拒、Key 维度 6 发 4 拒，均带 `retry-after` | 达标 |
| 边界 | 不改 `flows.py`、不动 tier 默认值、不引外部服务 | `flows.py` 零改动；tier 默认仍 `C`；缓存/限流均为进程内实现 | 达标 |

---

## 1. 改了哪些文件

| 文件 | 内容 |
|---|---|
| [__001__ratelimit.py](file:///E:/project/LexWeave/app/__008__api/__001__ratelimit.py) | 令牌桶 + 纯 ASGI 中间件，IP 与 `X-API-Key` 双维度 |
| [__002__cache.py](file:///E:/project/LexWeave/app/__008__api/__002__cache.py) | 进程内 LRU + TTL 响应缓存，key 六字段 sha256 |
| [__003__stream.py](file:///E:/project/LexWeave/app/__008__api/__003__stream.py) | SSE 逐 token 生成器 + 五闸末尾校验 + 缓存回放 |
| [__004__bench.py](file:///E:/project/LexWeave/app/__008__api/__004__bench.py) | 可复现基准：真起 uvicorn，测 TTFT / 缓存 / 并发 / 429 |
| [__000__server.py](file:///E:/project/LexWeave/app/__008__api/__000__server.py) | `/ask` 接缓存、`/ask/stream` 改逐 token、新增 `/cache/stats` 与 `/rate-limit/stats` |

`app/__007__agents/__002__flows.py`、`config.py`、tier 默认值、图配置均未改动；只读调用 `run_gates`、`ANSWER_SYSTEM`、`_deterministic_answer`、`_fmt_items`。

---

## 2. 流式与五闸的顺序冲突：二选一，选「流式输出 + 末尾附校验结果」

冲突本身：五闸（`run_gates`）是确定性规则，但**只能在完整答案之后判定**；而流式的价值在「不等整段生成完就吐字」。两者不可能同时满足「先校验再输出」。

选型对比：

| 方案 | 行为 | 为什么不选 / 选 |
|---|---|---|
| 先流后校正 | 流完再整段替换为通过闸门的版本 | 前端会闪一下；替换逻辑与 `flows.py::repair` 重复，等于埋一份会漂移的副本；边界又禁止改 `flows.py` |
| **流式输出 + 末尾校验（选）** | token 照常流；结束时推 `check` 事件（五闸逐项 + `grounded`），`done` 再给总账；前端用「校验徽标」兜底 | 不静默改写、不复制 repair 逻辑、不动 `flows.py` |

代价必须写明：**已经流出的错误文字收不回来**。实测 5 题里有 2 题 `grounded=false`（`cite_in_retrieval` 越界引用、1 题 `cite_exists` 编造条号），这些在 `check` 事件里如实标红，但不回写正文。

配套取舍：**流式路径把 `plan` 节点降级为确定性摘要**（不调 LLM）。C 档的 plan 是一次完整生成，实测会把首个 token 推到 3–5 s，直接违反 ≤2 s；流式只保留 `retrieve → answer` 两段，校验照做。非流式 `/ask` 仍是完整三节点，所以两套路径**不是同一口径**，报告里不能混用。

---

## 3. 口径（数字怎么来的，不能丢）

- **首 token** = 请求发出 → 第一个 `event: token` 到达，**含检索**（检索是同步前置，`first_event_ms` 单独记录：0.26–0.43 s）。
- **冷启动单列**：首次请求含 bge-large / bge-m3 / reranker 加载（30.3 s），**不并入**首 token 指标，否则「首 token ≤ 2 s」永远不可能成立。
- **并发 10 走 `llm=false`**：避免把结论变成「API 额度测试」，只测服务不崩与延迟；10 题互不相同，因此并发段增量全是 miss。
- **429 用两套配置各跑一遍**：IP 维度（`ip_rps=1, ip_burst=5`，12 发）与 Key 维度（`ip` 放开、`key_rps=1, key_burst=2`，6 发），证明「双维度」不是口号。
- 基准必须**真起 uvicorn**：`httpx.ASGITransport` 会把 SSE 缓冲成整段，所有事件同一时刻到达，TTFT 完全测不准（踩过，见 §6）。

---

## 4. 实测数字（`data/eval_out/api_bench.json`，git `754f0a5`）

### 4.1 流式首 token

| 请求 | first_event（检索完） | first_token | total |
|---|---|---|---|
| 冷启动（含模型加载） | 23 737 ms | 26 747 ms | 27 081 ms |
| 试用期最长可以约定多久 | 395.6 ms | **1 734.0 ms** | 2 438 ms |
| 单方解除需提前几天通知 | 430.8 ms | **1 921.3 ms** | 3 419 ms |
| 加班费基数怎么确定 | 282.1 ms | **912.5 ms** | 1 736 ms |
| 女职工产假工资由谁支付 | 261.3 ms | **967.6 ms** | 1 757 ms |

热启动：中位 **1 734 ms**、最大 **1 921 ms**、`all_within_2s = true`。

### 4.2 缓存

| 路径 | 首次（miss） | 二次（hit） | 降幅 |
|---|---|---|---|
| `/ask`（同步、完整三节点） | 4 953.6 ms | **2.5 ms** | 1 981× |
| `/ask/stream`（首 token） | 1 999.0 ms | **2.7 ms** | 740× |
| `/ask/stream`（整段回放） | — | 与答案长度成正比（分片 24 字/片） | — |

缓存 key = `sha256(query 归一化 · mode · tier · llm 开关 · 模型版本 · 提示词版本 · prompt_guard 开关)`。同一问题换 tier 或换模型都会换 key，不会把 A/B/C 三档的答案互相污染。基准人为构造 2 hit / 9 total，`hit_rate=0.222` **不是**生产环境命中率。

### 4.3 并发 10（`llm=false` 确定性路径）

`sent=10, ok=10, non_200=[], errors=[]`，墙钟 3 079 ms，p50 3 051 ms、p95 3 078 ms、max 3 078 ms。延迟大头是 10 路同时检索争 CPU（单请求确定性路径本身 ~3 s），**不是服务阻塞或缓存锁**。

### 4.4 限流

| 维度 | 配置 | 发送 | 放行 | 拒绝 | `X-RateLimit-Dimension` | `retry-after` |
|---|---|---|---|---|---|---|
| IP | `ip_rps=1, ip_burst=5` | 12 | 5 | 7 | `ip` | `1`（全部带） |
| Key | `key_rps=1, key_burst=2` | 6 | 2 | 4 | `key` | 全部带 |

默认参数（环境变量可覆盖）：`RATE_LIMIT_IP_RPS=5` / `IP_BURST=10` / `KEY_RPS=10` / `KEY_BURST=20`；`/health`、`/docs`、`/openapi.json`、`/rate-limit/stats` 不占配额。

---

## 5. 复现

```bash
# 仓库根目录
python -m app.__008__api.__004__bench              # 全量，写 data/eval_out/api_bench.json
python -m app.__008__api.__004__bench --no-llm     # 只测缓存/并发/限流，不花 LLM 费用
python -m app.__008__api.__004__bench --out data/eval_out/api_bench_custom.json

# 手工验收
curl -N "http://127.0.0.1:8000/ask/stream?query=试用期最长可以约定多久&tier=C"
curl -s http://127.0.0.1:8000/cache/stats
curl -s -H "X-API-Key: k1" http://127.0.0.1:8000/rate-limit/stats
```

环境变量：`CACHE_MAX_ENTRIES`（默认 256）、`CACHE_TTL_S`（默认 3600）、`CACHE_ENABLED`、`RATE_LIMIT_*`（见上）。**key 只从环境变量读，不落盘**。

---

## 6. 踩坑与修复（面试可直接讲）

1. **`httpx.ASGITransport` 把 SSE 缓冲成整段**。首版基准用 ASGI transport，显示 `meta/token/check/done` 全部在 31 938.8 ms 同一时刻到达，误判「流式没生效」。改为线程内真起 uvicorn + 网络 client 后，才测出真实的 0.26 s 首事件 / 1.7 s 首 token。
2. **失效 key 的 401 往返吃掉了首 token 预算**。本机 `LLM_API_KEY`（尾号 `1heg`）失效、`DEEPSEEK_API_KEY` 有效；`LLMClient.chat` 成功后会记住可用 key，但流式路径自建 client 不复用，于是**每个请求都先打一次 401**（0.3–1.0 s）。首轮实测首 token 中位 2 964 ms、最大 4 945 ms，正是这笔开销。修复：流式路径进程内记录鉴权失败的 key（`_BAD_KEYS`），后续请求直接跳过；重跑后中位 1 734 ms、最大 1 921 ms。
3. **`BaseHTTPMiddleware` 不适合包 SSE**。限流中间件写成纯 ASGI（直接发 `http.response.start`），不改变分块时序。
4. **并发段首跑偶发挂死**（CPU 打满、>180 s 不返回，无法稳定复现；独立探针 10 并发 `/retrieve` 27.1 s、10 并发 `/ask` 12.1 s 全部 200）。处置：基准加 `_step` 逐段打印 + 单段 `asyncio.wait_for`，挂死时能定位到具体段而不是整个进程；重跑两次均 3.0–3.1 s 完成。**未归因的部分不写成「已修复」**。

---

## 7. 局限（如实写，不粉饰）

1. **冷启动 30 s 不可忽略**：模型加载（bge-large / bge-m3 / reranker）在首请求里同步完成。演示前必须预热一次 `/health` + 一次 `/ask`，否则第一眼就是 30 s。
2. **缓存与限流都是单进程内存态**：多副本部署时各副本独立计数、独立缓存，命中率与限流都不成立；替换点已收敛在 `get_cache()` / `Limiter`，但不引入 Redis 是本次边界。
3. **流式路径比 `/ask` 少一个 plan 节点**：答案的「推理链路」是确定性摘要而非 LLM 规划，回答质量与 C 档完整路径不完全等价；这是为 ≤2 s 首 token 付的价。
4. **已流出的错误文字不回写**：五闸失败只体现在 `check` / `done` 事件，前端必须消费这两个事件，否则用户看不到校验结果。
5. **首 token 依赖外部 API 波动**：DeepSeek 首字 0.9–1.9 s，4 题样本量小，不构成 p95 结论；真实 p95 需要更多样本（基准脚本可直接扩 `STREAM_QUERIES`）。
6. **429 的 `retry-after` 是向上取整的秒数**：`ip_rps=1` 时全部为 `1`；高 rps 配置下会更细，但仍以秒为单位。
7. **`/cache/stats` 的 `hit_rate` 是进程生命周期累计值**，重启归零；不代表生产命中率。
8. **`/ask` 与 `/ask/stream` 共享同一缓存命名空间**（key 都是「问题 + 参数 + 模型版本」，不含路径）：跨路径命中时，`/ask` 返回的是流式写入的条目，缺少 `plan` / `attempts` / `repaired` 等编排字段（答案、引用、五闸结果都在）。若前端依赖这些字段做展示，需要按路径拆命名空间——本次为省演示预热成本没有拆。

---

## 8. README patch 建议（不直接改 README）

> 在 README 的 API 段落后追加：

```markdown
### 流式与工程化（T26）

- `GET /ask/stream?query=...&tier=C`：SSE，事件序 `meta -> token* -> check -> done`；
  首 token 含检索，热启动中位 1.73 s（冷启动含模型加载 30 s，需预热）。
- 流式路径不调 LLM 做 plan（否则首 token 3–5 s），五闸在末尾以 `check` 事件给出，
  **不回写正文**；需要「校验后改写」请用 `POST /ask`。
- `POST /ask` 带响应级缓存（同问同参第二次 ~2.5 ms）；`GET /cache/stats` 看命中率。
- 限流为 IP + `X-API-Key` 双维度令牌桶，429 带 `Retry-After` 与 `X-RateLimit-Dimension`；
  `RATE_LIMIT_*` 可调，`GET /rate-limit/stats` 可核对。
- 复现基准：`python -m app.__008__api.__004__bench`（`--no-llm` 省额度）。
```
