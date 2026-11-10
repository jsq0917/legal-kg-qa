"""生成 211 条 pending_semantic DEFINES 边的人工盲标工具包。

只读取项目既有产物，不修改任何已有文件：
- data/eval_out/defines_edges_check_post_rebuild.json  （bucket == pending_semantic）
- data/extracted/articles.jsonl                         （条文原文）
- data/lexicon/concepts.jsonl                           （概念名）

输出（均在本目录）：
- defines_pending_211_20261110.xlsx   主标注表 + 填写说明
- defines_pending_211_20261110.csv    同内容 CSV（UTF-8 BOM，Excel 可直接打开）
- defines_pending_211_20261110_id_map.csv  盲标编号 ↔ 原始边的对应表

刻意不写入：provenance / cue / confidence / matched_alias / defines_score / window。
human_label 与 note 一律留空，禁止任何预填。
"""

from __future__ import annotations

import csv
import json
import math
import random
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent
DATE_TAG = "20261110"
SEED = 20261110

DERIV_SUFFIX = ("人", "权", "物", "方", "者")
SENT_BREAK = "。；\n"

HEADERS = [
    "盲标编号",
    "概念名",
    "条文编号",
    "条文标题",
    "条文原文",
    "概念出现位置（0起字符偏移）",
    "概念所在句",
    "人工判定",
    "备注",
]
LABELS = ["成立", "不成立", "无法判断"]


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def standalone_offsets(text: str, name: str) -> list[int]:
    """概念名原样出现、且后面不接派生后缀（人/权/物/方/者）的位置。"""
    out = []
    i = text.find(name)
    while i != -1:
        if text[i + len(name) : i + len(name) + 1] not in DERIV_SUFFIX:
            out.append(i)
        i = text.find(name, i + len(name))
    return out


def sentence_at(text: str, i: int) -> tuple[int, int]:
    start = i
    while start > 0 and text[start - 1] not in SENT_BREAK:
        start -= 1
    end = i
    while end < len(text) and text[end] not in SENT_BREAK:
        end += 1
    return start, min(end + 1, len(text))


def evidence_sentences(text: str, offsets: list[int]) -> str:
    seen: list[tuple[int, int]] = []
    for i in offsets:
        span = sentence_at(text, i)
        if span not in seen:
            seen.append(span)
    parts = [text[s:e].strip() for s, e in seen]
    if len(parts) == 1:
        return parts[0]
    marks = "①②③④⑤⑥⑦⑧⑨⑩"
    return " ".join(f"{marks[k]}{p}" for k, p in enumerate(parts[: len(marks)]))


def decluster(items: list[dict]) -> list[dict]:
    """避免相邻两条属于同一概念或同一条文，降低判定惯性。"""

    def conflicts(a: dict, b: dict) -> bool:
        return a["concept_name"] == b["concept_name"] or a["article_id"] == b["article_id"]

    for _ in range(8):
        changed = False
        for i in range(1, len(items)):
            if not conflicts(items[i - 1], items[i]):
                continue
            for j in range(i + 1, len(items)):
                if conflicts(items[i - 1], items[j]):
                    continue
                if j + 1 < len(items) and conflicts(items[j], items[i + 1]) and conflicts(items[i - 1], items[i + 1]):
                    continue
                items[i], items[j] = items[j], items[i]
                changed = True
                break
        if not changed:
            break
    return items


def display_width(s: str) -> int:
    return sum(2 if ord(c) > 127 else 1 for c in s)


def build_records() -> tuple[list[dict], list[dict]]:
    check = json.loads(
        (ROOT / "data" / "eval_out" / "defines_edges_check_post_rebuild.json").read_text(encoding="utf-8")
    )
    articles = {a["article_id"]: a for a in read_jsonl(ROOT / "data" / "extracted" / "articles.jsonl")}
    concepts = {c["id"]: c for c in read_jsonl(ROOT / "data" / "lexicon" / "concepts.jsonl")}

    pending = []
    for idx, row in enumerate(check["rows"]):
        if row["bucket"] != "pending_semantic":
            continue
        article = articles[row["article_id"]]
        name = concepts[row["concept_id"]]["name"]
        text = article["content"]
        offsets = standalone_offsets(text, name)
        # pending_semantic 的定义就是概念名原样出现，这里做一次断言防止源数据漂移
        assert offsets, (row["article_id"], row["concept_id"])
        pending.append(
            {
                "case_id": "",
                "concept_id": row["concept_id"],
                "concept_name": name,
                "article_id": row["article_id"],
                "citation": article.get("citation") or row.get("citation") or "",
                "article_text": text,
                "evidence_offsets": ", ".join(str(i) for i in offsets),
                "evidence_sentences": evidence_sentences(text, offsets),
                "human_label": "",
                "note": "",
                "source_row_index": idx,
            }
        )

    rng = random.Random(SEED)
    rng.shuffle(pending)
    pending = decluster(pending)
    for k, rec in enumerate(pending, start=1):
        rec["case_id"] = f"BL-{k:04d}"
    return pending, check["rows"]


def write_csv(records: list[dict]) -> Path:
    path = OUT_DIR / f"defines_pending_211_{DATE_TAG}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(HEADERS)
        for r in records:
            w.writerow(
                [
                    r["case_id"],
                    r["concept_name"],
                    r["article_id"],
                    r["citation"],
                    r["article_text"],
                    r["evidence_offsets"],
                    r["evidence_sentences"],
                    "",
                    "",
                ]
            )
    return path


def write_id_map(records: list[dict]) -> Path:
    path = OUT_DIR / f"defines_pending_211_{DATE_TAG}_id_map.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["case_id", "concept_id", "concept_name", "article_id", "citation", "source_bucket", "source_row_index"])
        for r in records:
            w.writerow(
                [
                    r["case_id"],
                    r["concept_id"],
                    r["concept_name"],
                    r["article_id"],
                    r["citation"],
                    "pending_semantic",
                    r["source_row_index"],
                ]
            )
    return path


def write_xlsx(records: list[dict]) -> Path:
    path = OUT_DIR / f"defines_pending_211_{DATE_TAG}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "标注表"

    header_fill = PatternFill("solid", fgColor="1F4E79")
    header_font = Font(bold=True, color="FFFFFF", size=11)
    thin = Side(style="thin", color="BFBFBF")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    wrap = Alignment(wrap_text=True, vertical="top")
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws.append(HEADERS)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center
        cell.border = border

    widths = [10, 16, 10, 32, 62, 20, 55, 12, 42]
    for col, width in zip("ABCDEFGHI", widths):
        ws.column_dimensions[col].width = width

    for r in records:
        ws.append(
            [
                r["case_id"],
                r["concept_name"],
                r["article_id"],
                r["citation"],
                r["article_text"],
                r["evidence_offsets"],
                r["evidence_sentences"],
                "",
                "",
            ]
        )

    for row_idx in range(2, len(records) + 2):
        lines = max(
            math.ceil(display_width(str(ws.cell(row=row_idx, column=5).value or "")) / (widths[4] - 2)),
            math.ceil(display_width(str(ws.cell(row=row_idx, column=7).value or "")) / (widths[6] - 2)),
            1,
        )
        ws.row_dimensions[row_idx].height = min(15 * lines + 4, 120)
        for col_idx in range(1, len(HEADERS) + 1):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.border = border
            cell.alignment = center if col_idx in (1, 3, 8) else wrap

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:I{len(records) + 1}"

    dv = DataValidation(
        type="list",
        formula1='"' + ",".join(LABELS) + '"',
        allow_blank=True,
        showDropDown=False,
        error="请从下拉列表选择：成立 / 不成立 / 无法判断",
        errorTitle="判定值不合法",
        prompt="成立 / 不成立 / 无法判断",
        promptTitle="人工判定",
    )
    ws.add_data_validation(dv)
    dv.add(f"H2:H{len(records) + 1}")

    note = wb.create_sheet("填写说明")
    note.column_dimensions["A"].width = 110
    lines_text = [
        ("LexWeave DEFINES 待判边人工盲标（211 条）", True, 14),
        ("", False, 11),
        ("1. 本表只包含判定所需事实：概念名、条文编号/标题、条文原文、概念名在原文中的位置与所在句。", False, 11),
        ("   不含规则来源、别名、cue、置信度或任何预判——请不要去 data/extracted/mentions_typed.jsonl 反查。", False, 11),
        ("2. 每行只填两列：", False, 11),
        ("   H 列「人工判定」：从下拉框选 成立 / 不成立 / 无法判断（留空按未标处理）。", False, 11),
        ("   I 列「备注」：判 不成立 或 无法判断 时必须写一句话依据；判 成立 可留空。", False, 11),
        ("3. 判「成立」只有两种情形（与项目 B7 口径一致）：", False, 11),
        ("   (a) 定义：条文给出该概念的内涵、外延或构成，如「X 是指……」「X 是……的合同」。", False, 11),
        ("   (b) 正面规制：概念（或其别名）位于某分句主语位置，条文直接规定它应当/不得/可以/有权/必须做什么，", False, 11),
        ("       或直接规定它的范围、效力。", False, 11),
        ("4. 判「不成立」的典型：只作动词宾语（订立/解除/变更 X）、只在介词短语里（对 X、关于 X）、", False, 11),
        ("   只在并列列举里、只用于举例或背景说明，条文真正规范的是别的主体或事项。", False, 11),
        ("5. 拿不准就选「无法判断」并写理由；不要为了填满而硬判。", False, 11),
        ("6. 顺序已按概念/条文打散，且不改动原文；请按行号顺序独立判定，不要回看已判行。", False, 11),
        ("7. 统计口径：精度 = 成立 /（成立 + 不成立）；「无法判断」不计入分母，单独报告条数。", False, 11),
        ("8. 填完保存本文件，按同行 README 的合并步骤回填，不要覆盖任何已有数据文件。", False, 11),
        ("", False, 11),
        ("预计耗时：每条 1.5–3 分钟，全部约 5–10.5 小时；建议分 3–5 次完成，单次不超过 90 分钟。", False, 11),
        ("判据与文件来源详见同目录 README.md。", False, 11),
    ]
    for i, (text, bold, size) in enumerate(lines_text, start=1):
        cell = note.cell(row=i, column=1, value=text)
        cell.font = Font(bold=bold, size=size)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    wb.save(path)
    return path


def main() -> None:
    records, _ = build_records()
    assert len(records) == 211, len(records)
    assert len({r["case_id"] for r in records}) == 211
    assert all(r["human_label"] == "" and r["note"] == "" for r in records)

    xlsx = write_xlsx(records)
    csv_path = write_csv(records)
    id_map = write_id_map(records)
    print(f"rows: {len(records)}")
    print(f"xlsx: {xlsx}")
    print(f"csv:  {csv_path}")
    print(f"map:  {id_map}")


if __name__ == "__main__":
    main()
