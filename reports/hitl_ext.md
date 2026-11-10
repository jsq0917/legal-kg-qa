# T25 · HITL 扩展：超时 / 审计 / 多轮（t 任务）

> 日期：2026-10-31　|　owner：trae-2026-10-31　|　证据：`data/eval_out/hitl_ext.json`、`scripts/eval_hitl_ext.py`
> 结论：**4/4 验收项通过**（确定性路径，`use_llm=False`，时间注入模拟 24 h，不 sleep）。

## 1. 为什么做

原 B5 只证明了「挂起 → 人工确认 → 恢复」这一条主路径：`hitl.json` 里 5 条用例、3 条域内，
状态只有 `awaiting_review / completed`，没有超时、没有审计、多轮对话中途无法挂起。
生产上会直接卡在三处：

| 缺什么 | 线上后果 |
|---|---|
| 超时与升级 | 任务永远停在待复核，没人知道该谁处理 |
| 审计 | 谁在什么时候改了什么、改前改后是哪一版状态，事后查不到 |
| 多轮挂起 | 第 2 轮需要人工确认时，只能丢掉整段会话重来 |

## 2. 改了什么（`app/__006__hitl/`）

| 文件 | 改动 |
|---|---|
| `__001__checkpoint.py` | `Checkpoint` 新增 `deadline_at / escalated_at / assigned_to / session_id / turn_no / dialog_state`；`state_hash()`（规范化 sha256）；`CheckpointStore` 支持时钟注入。旧 JSON 靠默认值直接兼容 |
| `__003__pipeline.py` | 状态机加 `manual_queue`；`sweep_timeouts()` 超时自动转人工队列；`resume()` 记审计、回写多轮会话；新增 `start_turn()`（多轮中途挂起）、`queue()`（人工队列视图）；`pending()/stats()` 懒扫描超时 |
| `__004__audit.py`（新增） | 审计链：每次状态变更一行 JSONL，含 who / when / 变更前后状态哈希；`verify()` 重放链，断点可定位 |

状态机：

```
new --suspend--> awaiting_review --deadline_at(默认 24h)--> manual_queue
                        |                                        |
                        +--resume(人工修正)--> completed <-------+
```

超时**不删除任务**：转队列只是换处理人，之后照样能 `resume`。

### 2.1 三个实现口径（容易被质疑的地方）

1. **时间注入**：`HitlPipeline(clock=...)` + `CheckpointStore(clock=...)` + `AuditLog(clock=...)`
   同一口径；SLA 默认 24 h（`HITL_SLA_HOURS` 可覆盖，**没有改 `config.py`**）。
   评测里用 `Clock` 直接推进读数，24 h 超时在毫秒级复现，且不依赖机器时间。
2. **审计链**：每条记录的 `from_state_hash` 必须等于上一条的 `to_state_hash`，
   `verify()` 逐条重放；任何绕开审计的改写都会断链。哈希是 checkpoint 全量 JSON 的
   `sort_keys + 紧凑分隔符` sha256，避免 dict 顺序造成假差异。
3. **多轮挂起**：挂起时只写 user 轮，不写 assistant 轮；槽位快照进 `dialog_state`，
   恢复后由 `resume` 回写答案。所以会话历史里不会出现「挂起了一半的假答案」。

## 3. 评测结果（`data/eval_out/hitl_ext.json`，`all_pass=true`）

| 用例 | 场景 | 关键读数 | 判定 |
|---|---|---|---|
| case-within-sla | 挂起 23 h（< 24 h） | 未升级（0 条）；恢复 answer 614 字 | ✅ |
| case-expired-24h | 挂起 24.5 h（> 24 h） | 自动转 `manual_queue`（队列 1 条，overdue 1800 s）；队列中恢复 answer 802 字 | ✅ |
| case-multiturn-mid-suspend | 第 2 轮挂起，跨 24.5 h | 挂起前无假答案；恢复后回写答案 619 字；第 3 轮 `inherited=[monthly_wage,start_date,end_date]`，6 轮完整 | ✅ |

审计：3 个任务共 8 条记录，链全部连续，who / when / state-hash 齐全。

```
case-within:     system --suspend--> awaiting_review --reviewer-a@within(accept)--> completed
case-expired:    system --suspend--> awaiting_review --system:sla(超时)--> manual_queue
                 --reviewer-b@queue(accept)--> completed
case-multiturn:  user --suspend_turn--> awaiting_review --system:sla--> manual_queue
                 --reviewer-c@multiturn(accept)--> completed
```

存储：`checkpoints/` 下 3 个文件全为 `.json`，逐个可反序列化，**无 `.pkl`**。

## 4. 复现

```powershell
$env:EMBEDDING_DEVICE='cpu'   # 8 GB 卡被桌面占满时用 CPU；GPU 空闲时可去掉
.\.venv\Scripts\python.exe scripts\eval_hitl_ext.py
```

确定性路径：不调 LLM，两次运行 answer 长度一致（GPU / CPU 各跑一次，614 / 802 / 619 均一致）。

## 5. 边界与未做的事（如实记录）

- **未接线**：`flows.py`、`config.py`、`hitl.json` 均未改（按任务边界）；API `/hitl`
  仍只返回 pending + stats，`manual_queue` 明细通过 `HitlPipeline.queue()` 可取，
  没有新增端点（超出本卡「app/__006__hitl 改动」的产出范围）。
- **审计范围**：只记挂起任务的生命周期（suspend / timeout_escalate / resolve），
  不记检索与生成；非挂起任务的 completed/refused 仍只在 `CorrectionStore` 里留一行。
- **旧 checkpoint 兼容**：没有 `deadline_at` 的历史文件，按 `created_at + SLA` 补算，
  因此升级后第一次扫描会把「早就该升级」的旧任务一起转队列。
- **既有 B4 规则的一个坑（本卡未修）**：`extract_slots` 把「公司拖欠我工资，从2024年3月开始」
  里的 2024 抽成了 `monthly_wage`（新版 `_wage` 正则把「工资…<6 字>数字」当金额）。
  本卡的多轮用例在第 2 轮显式给出「月薪8000元」后槽位被正确覆盖，不影响验收；
  但这是 B4 槽位抽取的既有问题，应回流到 case bank（T09）。
- **未做**：真实时钟下的 24 h 等待（用注入时钟模拟，符合任务允许的口径）、
  多进程并发写审计的锁（单机单进程场景未触发）。

## 6. 验收对照

| 验收项 | 结果 |
|---|---|
| 挂起 24 h（时间注入模拟）后恢复成功且 answer 非空 | ✅ 24.5 h 转队列后恢复，answer 802 字 |
| 超时自动转人工队列 | ✅ `manual_queue` + `assigned_to=human_queue` |
| 审计日志可追每条状态变更 | ✅ 8 条记录，hash 链连续，who/when/hash 齐全 |
| 状态存 JSON 不存 pkl | ✅ 3/3 JSON，0 pkl |
