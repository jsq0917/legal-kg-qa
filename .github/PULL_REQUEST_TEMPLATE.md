# PR 说明

## 这是什么变更

- [ ] 新功能（检索 / 路由 / 编排 / 闸门 / API）
- [ ] 修 bug
- [ ] 评测与数据（脚本、`data/eval_out/`、金标）
- [ ] 文档 / 工程化（README、CI、模板）
- [ ] 重构（不改行为；请说明用什么证明「行为没变」）

关联 issue / 任务编号（T01–T36 或 #编号）：

## 做了什么、为什么

<!-- 一到三段。重点写「为什么」，不要逐文件罗列 diff。 -->

## 验收证据（必填）

> 每个数字都要能指到一个产物路径或 commit。没跑过的内容不要写成「已通过」。

```bash
# 复制即可执行的验证命令
uv run python -m pytest -m "not slow" -q
uv run ruff check <改动的 py 文件>
```

- 关键输出 / 数字（带口径）：
- 产物落盘路径（如 `data/eval_out/retrieval.json`）：

## 口径与数据影响

- [ ] 没有改评测口径（金标题、指标定义、`data/gold/`）
- [ ] 没有手改 `data/lexicon/*.jsonl`（改的是种子 `app/__001__ingest/__000__lexicon_seed.py`，并已重建）
- [ ] 没有覆盖别人已有的 `data/eval_out/*.json`（如需重跑，已另存 `*_pre_<本次标识>` 备份）
- [ ] README 里受影响的数字已同步（或已在 PR 里注明「README 待整合步骤统一更新」，本 PR 不直接改 README）
- [ ] 如果某个指标**没达标**，PR 描述里如实写了「差多少 / 为什么」

## 检查清单

- [ ] `uv run python -m pytest -m "not slow" -q` 全绿
- [ ] 改动的 `.py` 文件 `ruff check` 通过（CI 会按改动文件卡）
- [ ] 没有提交密钥、`.env`、模型权重、`data/graph/`、`data/index/`
- [ ] 新增/修改的脚本都能在 README §10 的口径下独立复跑
- [ ] 如改了闸门/槽位等确定性逻辑，`tests/` 里有对应断言
