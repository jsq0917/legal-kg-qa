# Release 说明模板

> 用法：在 GitHub 网页端发布 Release 时，把下面内容复制进说明框，替换 `<...>` 占位。
> 版本号规则：`vMAJOR.MINOR`。**换评测口径 / 换金标 / 改图 schema** 才算 MINOR 及以上，
> 只改代码与文档用 `vX.Y+1`；不允许在没有证据的情况下写「效果提升」。

---

## v<X.Y> · <一句话主题>

<用两三句话说明这个版本解决了什么。>

### 变更

- 功能：<新增的检索/路由/闸门/API 能力，写文件级落点>
- 修复：<修了什么，根因是什么>
- 数据与图：<节点/边数的变化、是否重建，重跑命令是什么>
- 工程化：<CI、脚本、文档>

### 指标（全部可在 `data/eval_out/` 复算）

| 指标 | 上一版 | 本版 | 口径 |
|---|---|---|---|
| R@5（text / graph / hybrid） | <..> | <..> | 60 条金标，`eval_retrieval.py` |
| 路由准确率 | <..> | <..> | `eval_router.py`，200 条金标（未扩前为 60 条） |
| 引用精确率 / 召回率（C 档） | <..> | <..> | `eval_ablation.py`，bootstrap 100 |
| 图规模（nodes / edges） | <..> | <..> | `build_graph.py` 刷新的 `stats.json` |

> 未达标的项照实写：例如「hybrid R@5 0.756，目标 ≥0.78，未达标，原因见 README §12 局限 1」。

### 复现

```bash
git clone <repo> && cd LexWeave
uv sync --extra dev
uv run python scripts/build_graph.py   # 期望 graph_build_id=g963749014664 / nodes 1799 / edges 5632
uv run python -m pytest -m "not slow" -q
```

### 升级注意

- 破坏性变更：<schema / 配置键 / 数据路径变化>
- 需要重跑的产物：<data/index、data/graph、data/gold 等>
- 已知问题：<指向 issue 编号或 README §12 对应局限>

### 校验

- 发布 commit：`<sha>`
- 测试：`<n> passed`（`pytest -m "not slow"`，CI 运行记录链接：<URL>）
- 数据说明：法条原文为公开法律文本，产物仅供学习研究，不构成法律意见（见 NOTICE.md）
