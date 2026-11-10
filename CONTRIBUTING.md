# 贡献指南（CONTRIBUTING）

目标：**陌生人 40 分钟内能从干净 clone 跑到单测全绿，并知道怎么提一个能复现的 issue。**

本仓库的硬规矩只有三条：

1. **每个数字都要能复算**——结论必须落在 `data/eval_out/` 里的某个 JSON，或某个 `scripts/` 脚本的输出；
2. **不达标就照实写**——不为了好看改口径、凑数、放宽规则；
3. **不提交密钥**——`LLM_API_KEY` / `DEEPSEEK_API_KEY` / `NEO4J_PASSWORD` 只从环境变量读，见 `.env.example`。

---

## 1. 40 分钟上手清单（不需要 GPU、不需要 API key）

全程在仓库根目录执行。Windows / Linux / macOS 都用 `uv run`，不依赖 `.venv\Scripts\` 这种平台路径。

| # | 命令 | 期望 | 参考耗时 |
|---|---|---|---|
| 0 | `git clone <repo> && cd LexWeave` | 仓库可公开访问 | <1 min |
| 1 | `uv sync --extra dev --no-install-package torch` | 建环境（`uv.lock` 固定版本） | 2–6 min |
| 2 | `uv run python scripts/build_graph.py` | 日志出现 `nodes 1799 / edges 5697 / DEFINES 819` | ~15 s |
| 3 | `uv run python app/__001__ingest/__001__build_lexicon.py --check` | 无输出、退出码 0（术语库与种子一致） | <5 s |
| 4 | `uv run python -m pytest -m "not slow" -q` | 全绿（数量随仓库演进） | ~10 s |
| 5 | `uv run ruff check <你改的 py 文件>` | 0 error | <5 s |

> **第 1 步为什么省 torch**：`sentence-transformers` 会传递依赖 CUDA 版 torch（3.5 GB）。
> 第 2–4 步不 import 模型，省掉 torch 同样能过（README §10.1 已在独立 clone 上验过）。
> 要跑检索评测（§4）才需要完整安装：`uv sync --extra dev`（会下载三个 bge 模型，约 2 GB）。

第 2 步的数字与 README §2/§3 逐位一致；不一致说明你的 `data/extracted/` 与仓库不同，请勿改数字迁就。

---

## 2. 目录与「什么能改」

```
app/__001__ingest/   语料载入 + 术语库（jsonl 是种子编译产物）
app/__002__extract/  概念/边抽取（规则，provenance=rule）
app/__003__graph/    图存储（Neo4j 优先，回落 NetworkX）
app/__004__retrieval/ 文本路 / 图路 / 融合 / 路由
app/__005__dialog/   多轮槽位与指代
app/__006__hitl/     人在环 checkpoint
app/__007__agents/   LangGraph 三档编排 + 五道闸门
app/__008__api/      FastAPI
app/__009__ui/       Streamlit
scripts/             建库/建图/评测脚本（每个指标可独立复跑）
data/                corpus / extracted / lexicon / gold / eval_out（graph、index、sessions 被 .gitignore）
eval/                金标题库与通用指标（κ、bigram 重合）
tests/               不依赖模型与 API key 的单测
```

几条不可越过的边界：

- **不要手改 `data/lexicon/*.jsonl`**：它是 `app/__001__ingest/__000__lexicon_seed.py` 的编译产物，
  重建会丢改动。要加概念/边，改种子后重跑 `__001__build_lexicon.py`（`tests/test_lexicon.py` 会拦）。
- **不要提交 `data/graph/`、`data/index/`、`data/sessions/`、`data/neo4j/`**（已 gitignore，用户各自重建）。
- **不要覆盖别人已落盘的 `data/eval_out/*.json`**：重跑请另存 `*_pre_<标识>` 备份，新旧并列。
- **改评测口径（金标题、指标定义、`data/gold/`）= 换赛道**，必须在 PR 描述与 README 里同时说明，不能只改代码。
- **`flows.py`、`config.py`、`data/lexicon/` 是热点文件**：多人并行时先看 issue/PR 有没有人占用。

---

## 3. CI 会做什么（`.github/workflows/ci.yml`）

| job | 内容 | 说明 |
|---|---|---|
| `lint` | `ruff check`，**只查本 PR 改动的 `.py` 文件** | 全仓库尚有历史告警未清，全量扫描会把所有人的 PR 卡红；存量由各模块 owner 逐块清理 |
| `test` | `build_graph.py` + 术语库一致性 + `pytest -m "not slow"` | 不装 torch、不下载模型 |
| `offline-eval` | `scripts/run_all_evals.py --no-llm` | **不产生真实 LLM 调用**；六个 eval 里有四个依赖 bge 模型 + `data/index`，CI 不下载 2 GB 模型，检测不到索引时打 `notice` 跳过（自托管 runner / 本地已建索引时自动生效） |

CI **不读取任何密钥**、不跑真实 LLM、不做 GPU 任务。需要 LLM 或模型的评测在本地跑，见 §4。
换句话说，CI 保证的是「建图能复现、单测全绿、改动文件过 lint」，**不保证指标数字**——那是人的责任。

---

## 4. 本地评测（需要模型 / LLM key，不在 CI 里跑）

```bash
uv sync --extra dev                      # 完整环境（含 torch）
uv run python scripts/build_index.py     # 首次会下载 bge-m3 / bge-large / bge-reranker（约 2 GB）

uv run python scripts/eval_retrieval.py  # B3：text / graph / hybrid
uv run python scripts/eval_router.py     # 路由准确率
uv run python scripts/eval_ablation.py   # B6：三档 × 60 + bootstrap
uv run python scripts/eval_dialog.py     # B4：槽位（依赖 build_index 的索引产物）
uv run python scripts/eval_hitl.py       # B5：人在环
uv run python scripts/make_figures.py    # 从 eval_out 重画 README 的图
```

- LLM 相关脚本从环境变量读 key（见 `.env.example`）；**写进 `.env` 的 key 不要 commit**。
- 没有 key 时多数脚本会自动降级为 deterministic；`--no-llm` 是显式开关。
- 评测会写 `data/eval_out/*.json`。提交评测结果前先 `git diff` 看是不是只改了预期的那份。

---

## 5. 提 issue

用 `.github/ISSUE_TEMPLATE/` 里的模板，Bug 报告必须填满三栏：

- **复现命令**：从干净 clone 开始的最少命令序列；
- **期望**：README 的哪一句/哪个数字让你认为应该是什么；
- **实际**：关键报错或关键数字（贴全文没人看得懂）。

报错里如含密钥，先打码再贴。跑不出结果时，先对照 README §12「已知局限」——很多偏差是刻意披露的，不是 bug。

---

## 6. 提 PR

1. 从 `main` 切分支：`feat/<主题>`、`fix/<主题>`、`docs/<主题>`；
2. 改动尽量小，一个 PR 只做一件事（评测口径变更单独开 PR）；
3. 按 `.github/PULL_REQUEST_TEMPLATE.md` 填「验收证据」：贴命令 + 关键输出 + 产物路径；
4. 自查清单：
   - [ ] `pytest -m "not slow" -q` 全绿
   - [ ] 改动的 `.py` 文件 `ruff check` 通过
   - [ ] 没提交密钥 / 模型权重 / `data/graph` / `data/index`
   - [ ] 没手改 `data/lexicon/*.jsonl`（改的是种子）
   - [ ] 指标没达标时，PR 里写清「差多少、为什么」
5. 文档类变更（README、模板）不需要 eval 数据，但要说明「哪些数字会受影响」。

提交信息用 `<type>(<scope>): <一句话>`，type ∈ `feat / fix / docs / test / chore`，示例：

```
feat(retrieval): 图路加 2-hop 剪枝，R@5 0.442 → 0.51（60 条口径）
fix(gates): 引用抽取不再把「根据」并进法名
```

---

## 7. 代码约定

- Python ≥3.11（CI 用 3.12），`from __future__ import annotations`；
- `ruff`：`line-length = 110`、规则集 `E,F,I,UP,B`，`E501` 已忽略；
- 脚本一律 `sys.path.insert(0, <repo root>)` 后使用绝对导入，保证 `uv run python scripts/xxx.py` 可跑；
- 新逻辑优先加**确定性单测**（闸门、槽位、术语库这类不依赖模型的），模型相关实验放 `scripts/` 并落盘 JSON；
- 日志用 `common.logging_utils.get_logger`，不要 `print`；
- 路径用 `pathlib`，不要硬编码 Windows 盘符。

---

## 8. 发布

发布说明统一用 `.github/RELEASE_TEMPLATE.md`：变更 / 指标表 / 复现 / 升级注意 / 校验五段。
指标表里的每个数都要能指到 `data/eval_out/` 的文件；未达标项照实写。

---

## 9. 已知限制（写在这里省得每个 issue 都问一遍）

- 检索评测需要三个 bge 模型（约 2 GB）与 `data/index/`，CI 只跑确定性路径；
- Neo4j 是可选后端，连不上会自动回落 NetworkX（评测结果与 Neo4j 无关）；
- 仓库里部分历史文件尚未过 `ruff`（当前全量扫描有告警），CI 只卡改动文件；
- 数字的权威来源是 `data/eval_out/` 与 `scripts/`，README 若与之冲突，以产物为准并提 issue。
