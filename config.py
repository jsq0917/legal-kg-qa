"""LexWeave 全局配置。

设计取向：
1. 所有可变参数走环境变量 / ``.env``，代码里不硬编码路径与密钥；
2. ``LLM_API_KEY`` 缺失时不报错，而是把系统切到 ``deterministic`` 模式
   （规则抽取 + 图查询 + 无 LLM 生成），保证离线/CI 可复现；
3. 图后端 ``auto`` 能连 Neo4j 就用 Neo4j，连不上自动回落 NetworkX 离线镜像，
   两条路径共用同一套受限查询计划，评测口径一致。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent

# --------------------------------------------------------------------------------------
# 数据资产布局：固定目录，便于脚本与文档引用
# --------------------------------------------------------------------------------------
DATA_DIR = ROOT_DIR / "data"
CORPUS_PATH = DATA_DIR / "corpus" / "articles.jsonl"
LEXICON_DIR = DATA_DIR / "lexicon"
EXTRACTED_DIR = DATA_DIR / "extracted"
GOLD_DIR = DATA_DIR / "gold"
INDEX_DIR = DATA_DIR / "index"
EVAL_DIR = DATA_DIR / "eval_out"
GRAPH_DIR = DATA_DIR / "graph"

LAW_ORDER = ("中华人民共和国劳动合同法", "中华人民共和国劳动法", "中华人民共和国民法典")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- LLM ----------------
    llm_api_key: str = Field(default="", alias="LLM_API_KEY")
    llm_base_url: str = Field(default="https://api.deepseek.com/v1", alias="LLM_BASE_URL")
    llm_model: str = Field(default="deepseek-chat", alias="LLM_MODEL")
    llm_temperature: float = Field(default=0.0, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=2048, alias="LLM_MAX_TOKENS")
    llm_timeout: int = Field(default=60, alias="LLM_TIMEOUT")
    # 思考模式（思维链）。百炼的 deepseek-v4.1-flash 默认开启，思维链与正文共享 max_tokens，
    # 本项目 answer 节点只给 480 token，思考一开正文就被挤成空串（finish_reason=length），
    # 而 try_chat 对空串不抛错 -> 静默回落模板，看起来像「key 没生效」。
    # auto: 仅百炼端点下发 enable_thinking=False；on/off: 强制。
    llm_thinking: Literal["auto", "on", "off"] = Field(default="auto", alias="LLM_THINKING")

    # ---------------- Neo4j ----------------
    neo4j_uri: str = Field(default="bolt://localhost:7687", alias="NEO4J_URI")
    neo4j_user: str = Field(default="neo4j", alias="NEO4J_USER")
    neo4j_password: str = Field(default="lexweave", alias="NEO4J_PASSWORD")
    neo4j_database: str = Field(default="neo4j", alias="NEO4J_DATABASE")

    graph_backend: Literal["auto", "neo4j", "networkx"] = Field(default="auto", alias="GRAPH_BACKEND")

    # ---------------- Embedding / Rerank ----------------
    embedding_model_name: str = Field(default="BAAI/bge-m3", alias="EMBEDDING_MODEL_NAME")
    embedding_device: str = Field(default="auto", alias="EMBEDDING_DEVICE")
    embedding_batch_size: int = Field(default=32, alias="EMBEDDING_BATCH_SIZE")
    rerank_model_name: str = Field(default="BAAI/bge-reranker-v2-m3", alias="RERANK_MODEL_NAME")
    align_model_name: str = Field(default="BAAI/bge-large-zh-v1.5", alias="ALIGN_MODEL_NAME")
    align_threshold: float = Field(default=0.85, alias="ALIGN_THRESHOLD")

    # ---------------- 检索 ----------------
    text_top_k: int = Field(default=20, alias="TEXT_TOP_K")
    graph_top_k: int = Field(default=10, alias="GRAPH_TOP_K")
    hybrid_top_k: int = Field(default=8, alias="HYBRID_TOP_K")
    rrf_k: int = Field(default=60, alias="RRF_K")
    refuse_threshold: float = Field(default=0.62, alias="REFUSE_THRESHOLD")
    path_coverage_threshold: float = Field(default=0.60, alias="PATH_COVERAGE_THRESHOLD")
    # 域外拒答门：取 data/gold/gold_60.jsonl 60 条域内金标 top1 相似度的 5% 分位
    # （实测 0.6234，向下取两位有效数字 0.62）。低于此分位说明「问题与劳动法语料不对口」，
    # 直接拒答而不是硬答并引民法典条文。复算见 scripts/measure_off_domain.py。
    off_domain_threshold: float = Field(default=0.62, alias="OFF_DOMAIN_THRESHOLD")

    # ---------------- 金标集 ----------------
    # T07：金标从 60 扩到 200 后，用 GOLD_FILE 显式切换，默认仍指 gold_60（不切默认，
    # 免得 T03/T16 等下游指标准被动漂移）。取值 gold_60 | gold_200，路径为 data/gold/<值>.jsonl。
    gold_file: Literal["gold_60", "gold_200"] = Field(default="gold_60", alias="GOLD_FILE")

    # ---------------- 运行 ----------------
    session_db_path: str = Field(default="data/sessions/sessions.sqlite3", alias="SESSION_DB_PATH")
    checkpoint_dir: str = Field(default="data/sessions/checkpoints", alias="CHECKPOINT_DIR")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("llm_api_key", mode="before")
    @classmethod
    def _strip_key(cls, v: str | None) -> str:
        return (v or "").strip()

    # ---------------- 派生属性 ----------------
    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)

    @property
    def run_mode(self) -> str:
        return "llm" if self.llm_enabled else "deterministic"

    @property
    def data_dir(self) -> Path:
        return DATA_DIR

    @property
    def corpus_path(self) -> Path:
        return CORPUS_PATH

    @property
    def lexicon_dir(self) -> Path:
        return LEXICON_DIR

    @property
    def extracted_dir(self) -> Path:
        return EXTRACTED_DIR

    @property
    def gold_dir(self) -> Path:
        return GOLD_DIR

    @property
    def gold_path(self) -> Path:
        """GOLD_FILE 解析出的金标文件路径：gold_60 -> data/gold/gold_60.jsonl。"""
        return GOLD_DIR / f"{self.gold_file}.jsonl"

    @property
    def index_dir(self) -> Path:
        return INDEX_DIR

    @property
    def graph_dir(self) -> Path:
        return GRAPH_DIR

    @property
    def eval_dir(self) -> Path:
        return EVAL_DIR

    @property
    def session_db(self) -> Path:
        return ROOT_DIR / self.session_db_path

    @property
    def checkpoint_path(self) -> Path:
        return ROOT_DIR / self.checkpoint_dir


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

__all__ = ["Settings", "get_settings", "settings", "ROOT_DIR", "LAW_ORDER"]
