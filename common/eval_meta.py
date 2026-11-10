"""评测产物的公共元信息：图版本与金标分片角色。

评测 JSON 必须能回答两个问题：**这份数字属于哪张图**、**这批题参与过调参吗**。
两个值都从仓库内文件推导，不手填。
"""

from __future__ import annotations

from pathlib import Path


def graph_build_id(graph_dir: Path | str | None = None) -> str:
    from app.__003__graph.__005__version import read_manifest

    manifest = read_manifest(graph_dir)
    return (manifest or {}).get("graph_build_id") or "unknown"


def graph_content_hash(graph_dir: Path | str | None = None) -> str:
    from app.__003__graph.__005__version import read_manifest

    manifest = read_manifest(graph_dir)
    return (manifest or {}).get("content_hash") or "unknown"


def split_role(gold_path: str | Path) -> str:
    """按文件名给出分片角色；未知文件一律标 ``custom``，避免被当成留出集引用。"""
    name = Path(gold_path).name
    if name == "gold_60.jsonl":
        return "dev_in_sample"
    if name == "gold_140_holdout.jsonl":
        return "holdout"
    if name == "gold_60_holdout.jsonl":
        return "holdout_balanced"
    if name == "gold_200.jsonl":
        return "full_pool"
    return "custom"
