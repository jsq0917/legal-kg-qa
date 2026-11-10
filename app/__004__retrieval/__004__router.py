"""查询路由：规则快路 → 语义层 → LLM 兜底（三级级联），输出 simple/medium/complex。

为什么是三级而不是单模型：
* 规则层零成本、完全可解释，只接「一眼能判」的短问与并列诉求，判不准就弃权（不硬判）；
* 语义层用 bge-large-zh 句向量 + 5 维结构特征做三分类 LR（200 条金标只够 5 折 CV）；
* LLM 只在语义层置信度低时触发，控制调用率（评测口径 <20%），用延迟换准确率。

阈值写在本模块而不是 ``config.py``：config 由 T05 独占，路由升级不改全局配置。
所有落盘模型都在 ``data/index/``，不覆盖原 ``router_lr.json``（保留旧口径可比）。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from common.logging_utils import get_logger  # noqa: E402

log = get_logger("router")

MULTI_PART_MARKERS = ("同时", "并且", "又", "还要", "另外", "以及", "分别", "一方面", "不但", "而且")

FEATURE_KEYS = ("n_concepts", "n_scenarios", "length", "markers", "clauses")

LEVELS = {
    "simple": {"top_k": 3, "text_weight": 0.65, "graph_weight": 0.35, "hops": 1},
    "medium": {"top_k": 5, "text_weight": 0.55, "graph_weight": 0.45, "hops": 2},
    "complex": {"top_k": 8, "text_weight": 0.45, "graph_weight": 0.55, "hops": 2},
}

# 语义层「敢判」的门槛：top1 概率减去 top2 概率。低于此值说明两档几乎打平，
# 交给 LLM。0.20 是按 260 条评测集上「LLM 触发率 ≈ 19%」先验定的，不是事后调参。
SEMANTIC_MARGIN_THRESHOLD = 0.20
SEMANTIC_MODEL_PATH = Path("data/index/router_semantic.json")

ROUTE_LLM_SYSTEM_PROMPT = """你是法律问答系统的查询路由器。判断用户问题需要检索几条不同法条才能答全，输出 JSON。

判定方法：先把问题切成"独立的问题点"（每个问题点对应一条实体法条 / 一个法律后果），再数个数：
- 1 个问题点 -> simple（检索 3 条足够）
- 2 个问题点 -> medium（检索 5 条）
- 3 个及以上问题点，或必须同时引用《劳动合同法》《劳动法》《民法典》两部以上 -> complex（检索 8 条）

示例：
问：试用期最长可以多久？ -> 只问试用期上限，1 个问题点 -> {"level":"simple","n_issues":1}
问：公司拖欠两个月工资，这算不算违法，我还能不能要回来？ -> 违法认定 + 追讨途径，2 个问题点 -> {"level":"medium","n_issues":2}
问：公司违法辞退我，我能不能既要赔偿金，又要精神损害赔偿？ -> 违法解除赔偿 + 侵权精神损害（跨劳动合同法与民法典），2 个问题点但跨法 -> {"level":"complex","n_issues":2}
问：我签了竞业协议，公司不给补偿，还让我赔违约金，这协议对我还有约束力吗？ -> 补偿 + 违约金 + 协议效力，3 个问题点 -> {"level":"complex","n_issues":3}

只输出 {"level":"simple|medium|complex","n_issues":整数}。"""


@dataclass
class RouteDecision:
    level: str
    top_k: int
    text_weight: float
    graph_weight: float
    hops: int
    reason: str
    features: dict[str, float]
    source: str = "rule"  # rule | semantic | llm | legacy_lr | default
    confidence: float = 1.0  # 语义层记 top1-top2 概率差；规则/LLM 记 1.0


def features_of(query: str, n_concepts: int, n_scenarios: int) -> dict[str, float]:
    markers = sum(1 for m in MULTI_PART_MARKERS if m in query)
    question_parts = query.count("？") + query.count("?")
    clauses = sum(query.count(p) for p in ("，", "、", "；", "。"))
    return {
        "n_concepts": float(n_concepts),
        "n_scenarios": float(n_scenarios),
        "length": float(len(query)),
        "markers": float(markers),
        "questions": float(question_parts),
        "clauses": float(clauses),
    }


def rule_route(f: dict[str, float]) -> tuple[str, str]:
    """旧的全量规则（保留给 B3 基线复现用；新版走 :func:`rule_fast`）。"""
    if f["markers"] >= 1 or f["n_concepts"] >= 3 or (f["n_concepts"] >= 2 and f["length"] >= 28):
        return "complex", "多诉求/多概念"
    if f["n_concepts"] <= 1 and f["length"] < 28 and f["markers"] == 0:
        return "simple", "单概念短问"
    if f["n_concepts"] >= 2 or f["clauses"] >= 2:
        return "medium", "双概念"
    return "medium", "默认中等"


def rule_fast(f: dict[str, float]) -> tuple[str | None, str]:
    """规则快路：只接高置信信号，其余返回 ``None`` 表示弃权给语义层。

    实测这两条守卫在 260 条评测集上精确率 1.0（覆盖 18.5%），所以敢于短路；
    旧规则的问题正是「判不准也硬判」，把大量两跳口语题推成 complex。
    """
    if f["n_concepts"] <= 1 and f["length"] < 28 and f["markers"] == 0:
        return "simple", "单概念短问（高置信）"
    if f["markers"] >= 2 and f["n_concepts"] >= 3:
        return "complex", "多诉求多概念（高置信）"
    return None, "规则弃权"


def _decision(level: str, reason: str, features: dict[str, float], source: str, confidence: float) -> RouteDecision:
    cfg = LEVELS[level]
    return RouteDecision(level=level, reason=reason, features=features, source=source, confidence=confidence, **cfg)


class QueryRouter:
    """旧版路由（规则 + 5 维特征 LR），保留给基线对比与语义模型缺失时的回落。"""

    def __init__(self, model_path: Path | None = None) -> None:
        self.model_path = model_path or Path("data/index/router_lr.json")
        self.model = None
        self.meta: dict = {}
        if self.model_path.exists():
            try:
                self._load()
            except Exception:
                log.warning("路由模型加载失败，回落规则路由")

    def _load(self) -> None:
        from sklearn.linear_model import LogisticRegression

        blob = json.loads(self.model_path.read_text(encoding="utf-8"))
        clf = LogisticRegression(max_iter=500)
        import numpy as np

        clf.classes_ = np.array(blob["classes"])
        clf.coef_ = np.array(blob["coef"])
        clf.intercept_ = np.array(blob["intercept"])
        self.model = clf
        self.meta = blob.get("meta", {})

    def fit(self, X: list[list[float]], y: list[str]) -> dict:
        from sklearn.linear_model import LogisticRegression

        clf = LogisticRegression(max_iter=1000)
        clf.fit(X, y)
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        import numpy as np

        self.model_path.write_text(
            json.dumps(
                {
                    "classes": list(clf.classes_),
                    "coef": np.asarray(clf.coef_).tolist(),
                    "intercept": np.asarray(clf.intercept_).tolist(),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.model = clf
        return {"classes": list(clf.classes_)}

    def route(self, query: str, n_concepts: int, n_scenarios: int, use_model: bool = True) -> RouteDecision:
        f = features_of(query, n_concepts, n_scenarios)
        level, reason = rule_route(f)
        if use_model and self.model is not None:
            import numpy as np

            x = np.array([[f[k] for k in FEATURE_KEYS]])
            pred = str(self.model.predict(x)[0])
            reason = f"LR 模型（规则={level}）"
            level = pred
        return _decision(level, reason, f, "legacy_lr" if self.model is not None and use_model else "rule", 1.0)


def feature_vector(query: str, n_concepts: int, n_scenarios: int) -> list[float]:
    f = features_of(query, n_concepts, n_scenarios)
    return [f[k] for k in FEATURE_KEYS]


class SemanticRouter:
    """语义层：``[5 维结构特征 | bge-large-zh 句向量]`` 上的三分类 LR。

    只拿句向量做 kNN 实测 0.64，明显不如「句向量 + 结构特征」的 0.75（见 reports/router.md），
    所以部署的是后者；kNN 版本在评测里保留为对照，不参与决策。
    """

    def __init__(self, model_path: Path | None = None) -> None:
        self.model_path = model_path or SEMANTIC_MODEL_PATH
        self.classes: list[str] = []
        self.coef: Any = None
        self.intercept: Any = None
        self.embedding_model: str = ""
        self.ready = False
        if self.model_path.exists():
            try:
                self._load()
            except Exception:
                log.warning("语义路由模型加载失败，回落旧 LR")

    def _load(self) -> None:
        import numpy as np

        blob = json.loads(self.model_path.read_text(encoding="utf-8"))
        self.classes = list(blob["classes"])
        self.coef = np.array(blob["coef"], dtype=float)
        self.intercept = np.array(blob["intercept"], dtype=float)
        self.embedding_model = blob.get("embedding_model", "")
        self.ready = True

    def fit(self, queries: list[str], features: list[dict[str, float]], y: list[str], embeddings: Any = None) -> dict:
        """训练并落盘。``embeddings`` 传入则复用（评测里已经编码过）。"""
        import numpy as np
        from sklearn.linear_model import LogisticRegression

        mat = self.build_matrix(queries, features, embeddings)
        clf = LogisticRegression(max_iter=3000)
        clf.fit(mat, y)
        blob = {
            "classes": [str(c) for c in clf.classes_],
            "coef": np.asarray(clf.coef_).tolist(),
            "intercept": np.asarray(clf.intercept_).tolist(),
            "embedding_model": self._embedding_name(),
            "feature_spec": list(FEATURE_KEYS) + ["embedding"],
        }
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        self.model_path.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
        self.classes, self.coef, self.intercept = blob["classes"], np.asarray(clf.coef_), np.asarray(clf.intercept_)
        self.ready = True
        return {"classes": blob["classes"], "dim": int(mat.shape[1]), "n": len(y)}

    def build_matrix(self, queries: list[str], features: list[dict[str, float]], embeddings: Any = None) -> Any:
        import numpy as np

        if embeddings is None:
            embeddings = self.encode(queries)
        emb = np.asarray(embeddings, dtype=float)
        feats = np.asarray([[f[k] for k in FEATURE_KEYS] for f in features], dtype=float)
        return np.hstack([feats, emb])

    def encode(self, queries: list[str]) -> Any:
        from common.embedding_model import get_align_encoder

        return get_align_encoder().encode(queries)

    def _embedding_name(self) -> str:
        from config import settings

        return settings.align_model_name

    def predict(self, query: str, features: dict[str, float], embedding: Any = None) -> tuple[str, float]:
        """返回 ``(level, margin)``；margin = top1 概率 − top2 概率。"""
        import numpy as np

        mat = self.build_matrix([query], [features], None if embedding is None else np.asarray([embedding]))
        logits = self.coef @ mat[0] + self.intercept
        probs = np.exp(logits - logits.max())
        probs = probs / probs.sum()
        order = probs.argsort()[::-1]
        margin = float(probs[order[0]] - probs[order[1]]) if len(order) > 1 else 1.0
        return str(self.classes[int(order[0])]), margin


def route_llm(query: str) -> tuple[str | None, float]:
    """LLM 兜底：返回 ``(level, 延迟 ms)``；未配置 key 或调用失败返回 ``(None, 0)``。"""
    import time

    from common.llm import get_llm

    llm = get_llm()
    if not llm.enabled:
        return None, 0.0
    t0 = time.perf_counter()
    out = llm.try_json(
        [{"role": "system", "content": ROUTE_LLM_SYSTEM_PROMPT}, {"role": "user", "content": query}],
        default=None,
        schema={"level": str},
        temperature=0,
        max_tokens=64,
    )
    level = out.get("level") if isinstance(out, dict) else None
    return (level if level in LEVELS else None), (time.perf_counter() - t0) * 1000


class CascadeRouter:
    """三级级联：规则快路 → 语义层（margin 足够）→ LLM 兜底。

    与旧 :class:`QueryRouter` 的接口一致（``route(query, n_concepts, n_scenarios)``），
    所以 ``Retriever`` 可以无感替换；语义模型缺失时自动退回旧 LR。
    """

    def __init__(
        self,
        semantic_model_path: Path | None = None,
        enable_llm: bool | None = None,
        margin_threshold: float = SEMANTIC_MARGIN_THRESHOLD,
    ) -> None:
        from config import settings

        self.semantic = SemanticRouter(semantic_model_path)
        self.legacy = QueryRouter()
        self.enable_llm = settings.llm_enabled if enable_llm is None else enable_llm
        self.margin_threshold = margin_threshold
        self.stats: dict[str, Any] = {"rule": 0, "semantic": 0, "llm": 0, "legacy_lr": 0, "default": 0, "llm_ms": []}

    def route(self, query: str, n_concepts: int, n_scenarios: int) -> RouteDecision:
        f = features_of(query, n_concepts, n_scenarios)

        level, reason = rule_fast(f)
        if level is not None:
            self.stats["rule"] += 1
            return _decision(level, reason, f, "rule", 1.0)

        if self.semantic.ready:
            level, margin = self.semantic.predict(query, f)
            if margin >= self.margin_threshold:
                self.stats["semantic"] += 1
                return _decision(level, f"语义层（margin={margin:.2f} ≥ {self.margin_threshold:.2f}）", f, "semantic", margin)
        else:
            legacy = self.legacy
            level, reason = rule_route(f)
            if legacy.model is not None:
                import numpy as np

                pred = str(legacy.model.predict(np.array([[f[k] for k in FEATURE_KEYS]]))[0])
                self.stats["legacy_lr"] += 1
                return _decision(pred, "语义模型缺失，回落旧 LR", f, "legacy_lr", 1.0)
            self.stats["default"] += 1
            return _decision(level, reason, f, "default", 1.0)

        if self.enable_llm:
            llm_level, llm_ms = route_llm(query)
            if llm_level is not None:
                self.stats["llm"] += 1
                if llm_ms:
                    self.stats["llm_ms"].append(round(llm_ms, 1))
                return _decision(llm_level, f"LLM 兜底（{llm_ms:.0f} ms）", f, "llm", 1.0)

        # LLM 不可用 / 失败：用语义层的 argmax，并如实把低置信写进 reason
        self.stats["semantic"] += 1
        return _decision(level, f"语义层低置信（margin={margin:.2f}），无 LLM 兜底", f, "semantic", margin)
