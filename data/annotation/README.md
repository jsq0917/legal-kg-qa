# DEFINES `pending_semantic` 人工盲标工具包（211 条）

> 生成日期：2026-11-10
> 用途：供项目所有者**本人逐条手工判定** 211 条语义待判的 `DEFINES` 边是否成立。
> 本目录全部为新增文件，未修改 README、评测脚本或任何已有数据文件；未执行 git commit。
> **2026-11-10 追加**：本目录另有一批 Kimi 预判结果（`kimi_batch_211_*`），它是 **LLM 判定，不是人工标注**，
> 不能替代本工具包，也不能把它的 25.1% 当语义精度。详见第 10 节。

## 1. 这批边是什么（口径先对齐）

`pending_semantic` 的机械定义：**概念名在条文正文中原样出现，但机械判据给不出「定义式」或「分句主语位 + 模态词」证据**，因此不判对错，留给人判。

需要说明一个口径差异：任务描述里的 **819 条**是 P3 扩词典后、T01 清洗前的旧口径（`defines_edges_check_pre_rebuild.json`，当时 `pending_semantic` 为 **225** 条）；当前仓库 `data/extracted/mentions_typed.jsonl` 与 `data/graph/stats.json` 的 `DEFINES` 是 **710** 条，其中 `pending_semantic` 为 **211** 条（`data/eval_out/defines_edges_check_post_rebuild.json`）。**本工具包导出的是当前 710 条口径下的 211 条**，两个口径不要混着报。

## 2. 文件清单

| 文件 | 用途 |
|---|---|
| `defines_pending_211_20261110.xlsx` | 主标注表。`标注表` sheet 共 211 行数据 + `填写说明` sheet；`人工判定` 列带下拉框 |
| `defines_pending_211_20261110.csv` | 与 xlsx 完全同内容、同行序的 CSV（UTF-8 带 BOM，Excel 可直接双击） |
| `defines_pending_211_20261110_id_map.csv` | 盲标编号 `case_id` ↔ `concept_id` / `article_id` / 源文件行号，回填时用 |
| `build_pending_semantic_kit.py` | 生成脚本。只读既有产物，随机种子 `20261110`，可复算 |

表内只有判定所需事实：**概念名、条文编号、条文标题、条文原文、概念名在原文中的字符偏移、概念所在句**。刻意不含 `provenance`、`cue`、`matched_alias`、`confidence`、`defines_score`、`window`，也没有任何预填答案或倾向提示。

## 3. 怎么填

1. 打开 xlsx，进入 `标注表`。**只填 H 列「人工判定」和 I 列「备注」**。
2. H 列从下拉框选：`成立` / `不成立` / `无法判断`。留空按「未标」处理，不要跳行。
3. I 列：判 `不成立` 或 `无法判断` 时**必填**一句话依据；判 `成立` 可留空。
4. 不要改 A–G 列、不要改行序、不要插入或删除行、不要加列——回填脚本按固定列名和 `case_id` 读取。
5. 表已按概念/条文打散（相邻两行的概念名和条文编号都不相同）。请按行号独立判定，不要回看已判行。

## 4. 判定标准（与项目 B7 口径一致，别自己加严）

判 **`成立`** 只有两种情形：

1. **定义**：条文给出该概念的内涵、外延或构成，如「X 是指……」「X 是……的合同/行为/权利」。
2. **正面规制**：概念（或其别名）位于条文某个分句的**主语位置**，条文直接规定它「应当 / 不得 / 可以 / 有权 / 必须」做什么，或直接规定它的范围、效力。

判 **`不成立`** 的典型：概念只作动词宾语（订立 / 解除 / 变更 X）、只在介词短语里（对 X、关于 X）、只在并列列举里、只用于举例或背景说明；条文真正规范的是别的主体或别的事项，概念只是被顺带提到。

判 **`无法判断`**：条文残缺、概念本身歧义、需要外部法理才能定，且你无法用一句话给出稳定理由时使用。`无法判断` 的数量本身有信息量，不要为了填满而硬判。

示例（引自项目既有标注说明）：

- 「承揽人交付的工作成果不符合质量要求的，定作人可以合理选择请求承揽人承担修理……」对「定作人」→ `成立`（分句主语位正面规制）。
- 「订立借款合同，借款人应当按照贷款人的要求提供与借款有关的业务活动和财务状况的真实情况」对「借款合同」→ `不成立`（「借款合同」是「订立」的宾语）。

## 5. 防止被判据带偏

- **不要**去 `data/extracted/mentions_typed.jsonl` 或 `defines_edges_check_post_rebuild.json` 反查这条边的 `cue` / `matched_alias` / `confidence`。看到 `cue=normative:应当` 就等于被提示了答案。
- **禁止用 LLM 或规则预先标注、生成答案、给出倾向提示**。2026-10-31 已发生过一次 AI 回填被误当成人工标注（见 `data/eval_out/defines_human_labels.README.md`），该归属不成立，不能重演。
- 本项目做**单人盲标**，不要假装双人双盲。若日后找同学对其中 50 条独立标一遍，必须写成「50 条子集、单次、非完整双盲」，一致性单独报告。

## 6. 预计耗时

- 单条 1.5–3 分钟（读 17–213 字的条文 + 对照概念名 + 写一句依据）。
- 211 条约 **5–10.5 小时**纯标注；按每天 3–4 小时计，约 **2–3 天**。
- 建议分 3–5 次完成，单次不超过 90 分钟；先标 10 条校准速度，再决定批量节奏。

## 7. 统计口径

- **精度 = `成立` 条数 ÷（`成立` + `不成立`）**。
- `无法判断` **不计入分母**，单独报告条数；不要悄悄把它算成 0 或 1。
- 若 `无法判断 > 0`，分母为 `211 − 无法判断数`，并在报告里写明。
- 95% 置信区间用 Wilson 区间（不要用正态近似，小样本/接近 1 时不可靠）：

```python
from math import sqrt

def wilson_ci(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return center - half, center + half
```

## 8. 回填后如何合并回项目数据

填完保存 xlsx（或 csv），按下面步骤生成**新文件**，不要覆盖任何已有数据：

```python
import pandas as pd

base = "data/annotation/"
lab = pd.read_excel(base + "defines_pending_211_20261110.xlsx", sheet_name="标注表", dtype=str)
idmap = pd.read_csv(base + "defines_pending_211_20261110_id_map.csv", dtype=str)[["case_id", "concept_id"]]

lab["人工判定"] = lab["人工判定"].fillna("").str.strip()
allowed = {"成立", "不成立", "无法判断"}
unfilled = int(lab["人工判定"].eq("").sum())
assert unfilled == 0, f"还有 {unfilled} 条未填"
assert set(lab["人工判定"]) <= allowed, set(lab["人工判定"]) - allowed
assert lab["盲标编号"].is_unique and len(lab) == 211

lab = lab.rename(columns={"概念名": "concept_name", "条文编号": "article_id"})
lab = lab.merge(idmap, left_on="盲标编号", right_on="case_id", how="left", validate="one_to_one")
assert lab["concept_id"].notna().all()

lab["human_label"] = lab["人工判定"].map({"成立": 1, "不成立": 0, "无法判断": "?"})
lab["note"] = lab["备注"].fillna("")
lab["annotator"] = "本人（逐条人工判定，无 LLM 参与）"
lab["annotated_at"] = "2026-11-XX"  # 填实际完成日期

out = lab[["盲标编号", "concept_id", "concept_name", "article_id",
           "human_label", "note", "annotator", "annotated_at"]]
out.to_csv("data/eval_out/defines_pending_semantic_labels_20261110.csv",
           index=False, encoding="utf-8")
```

合并时用 `(concept_id, article_id)` 回到源 JSON：211 条中有一对同名同条文的记录（概念名「违约金」、条文 `lcl#22`，两个不同 `concept_id`），**只按概念名 + 条文编号会撞车**，必须带 `concept_id` 或用 `case_id`。

统计：

```python
counts = lab["human_label"].value_counts(dropna=False)
n_yes, n_no = int(counts.get(1, 0)), int(counts.get(0, 0))
n_unknown = int(counts.get("?", 0))
precision = n_yes / (n_yes + n_no)
ci = wilson_ci(n_yes, n_yes + n_no)
print(n_yes, n_no, n_unknown, precision, ci)
```

## 9. 已知边界（写报告时必须带上）

- 这 211 条只是 710 条 `DEFINES` 中的「待语义判定」桶，不能把它算出的精度直接说成「710 条定义边的精度」。完整口径仍需同时交代：`absent` 68、`derived_only` 47、`normal` 384 的处理方式。
- `normal`、`derived_only`、`absent` 的机械归类不等于语义判定；本工具包不替它们背书。
- 字符偏移从 0 开始计；「概念所在句」按 `。；\n` 切句，仅用于定位，判定仍以条文原文为准。
- 本轮为单人单次盲标；如需 κ，只能是 50 条子集、单次、非完整双盲，且要单独披露。

## 10. 2026-11-10 Kimi 预判批次（非人工，不能替代本表）

文件：`kimi_batch_211_parsed.csv`（判定）、`kimi_batch_211_review_queue.csv`（待复核 59 条）、
`kimi_batch_211_raw.txt`（原始输出）、`batch_input_211.txt` 与两份 `prompt_defines_kimi_k31pro_*`。

**判定结果**：211/211 全覆盖、无重复；成立 **53** 条（25.1%，Wilson 95% CI 19.8–31.4%）、
不成立 158 条、无法判断 **0** 条；`basis` 全是 S/N，**D 为 0**。

**质量结论（详见 [`docs/kimi_annotation_audit_20261110.md`](file:///E:/project/LexWeave/docs/kimi_annotation_audit_20261110.md)）**：

- 4/211 条 `evidence` 不是原文连续子串（1 条串用了邻条 `cc#613` 的措辞），说明证据字段不可直接采信；
- 53 条成立全部来自 `role=主语/主语中心语`，标签几乎完全由句法角色门控；
- 0 条「无法判断」、0 条定义式，边界样本（如 `BL-0174`/`BL-0201`）被强行二选一。

**口径**：这批结果只能写「LLM 判定（Kimi）」，**不是人工标注、不是真实语义精度**，不能填进本工具包的
`人工判定` 列，也不能把 25.1% 外推到 710 条 DEFINES。第 5 节“禁止用 LLM 预先标注”的规则仍然有效：
它约束的是**人工盲标**这条线；Kimi 批次是并行的辅助实验，不是替代。

**校准**：已按分层抽 40 条做真人抽检表 `audit_kimi_40_20261110.csv`（20 成立 + 20 不成立，
含全部 6 条质检异常，行序随机，不含任何模型判定提示；生成脚本 `scripts/make_kimi_audit_sample.py`，seed 20261110）。
填完后用一致率 + Cohen's κ + Wilson 区间校准；判读规则见核验文档第 6 节，先写死再算。
