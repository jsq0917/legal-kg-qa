"""OpenAI 兼容 LLM 客户端。

与 TCM 项目 ``PromptTemplate | my_llm | JsonOutputParser`` 的链式写法保持一致，
但把「链」收敛成一个显式客户端，好处是：
* 单点重试 / 超时 / 退避，避免每个节点各写一遍；
* 全局累计 token 与调用次数，B6 的成本表直接读这里；
* 无 key 时抛 :class:`LLMDisabled`，调用方走确定性分支而不是静默失败。
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from common.logging_utils import get_logger
from config import settings

log = get_logger("llm")


class LLMDisabled(RuntimeError):
    """未配置 API Key 时抛出，调用方应回落到确定性逻辑。"""


#: 错误分类（写进 trace / 降级记录，回答「为什么这条答案是降级产物」）。
ERROR_KINDS = {
    "no_key",  # 未配置 Key，确定性模式
    "auth",  # 401/403，换 Key
    "quota",  # 余额/配额不足
    "rate_limit",  # 429
    "timeout",  # 请求或流式超时
    "server_error",  # 5xx
    "network",  # 连不上
    "empty_output",  # 空正文（常见于思维链吃满 max_tokens）
    "json_parse",  # 输出不是合法 JSON
    "schema",  # JSON 结构不符合约定
    "unknown",
}

#: 只有这些错误值得重试；鉴权/参数错误重试没有意义。
RETRYABLE_KINDS = {"rate_limit", "timeout", "server_error", "network", "empty_output"}


class LLMError(RuntimeError):
    def __init__(self, kind: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.kind = kind if kind in ERROR_KINDS else "unknown"
        self.status = status


def classify_error(exc: BaseException) -> str:
    """把 openai / httpx / 内置异常归一成可统计的字符串，不做字符串硬匹配之外的猜测。"""
    if isinstance(exc, LLMError):
        return exc.kind
    if isinstance(exc, LLMDisabled):
        return "no_key"
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    text = str(exc).lower()
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    if status in (401, 403) or "invalid_api_key" in text or "authentication" in text:
        return "auth"
    if status == 402 or "insufficient" in text or "quota" in text or "余额" in text:
        return "quota"
    if status == 429 or "rate limit" in text or "too many requests" in text:
        return "rate_limit"
    if isinstance(exc, TimeoutError) or "timeout" in text or "timed out" in text:
        return "timeout"
    if status is not None and 500 <= status < 600:
        return "server_error"
    if isinstance(exc, (ConnectionError, OSError)) or "connection" in text or "connect" in text:
        return "network"
    return "unknown"


#: 降级钩子挂在线程/协程上下文里，避免并发请求互相污染。
_DEGRADATION_HOOK: ContextVar[Callable[..., None] | None] = ContextVar("llm_degradation_hook", default=None)


def set_degradation_hook(hook: Callable[..., None] | None):
    return _DEGRADATION_HOOK.set(hook)


def reset_degradation_hook(token) -> None:
    _DEGRADATION_HOOK.reset(token)


def report_degradation(reason: str, detail: str = "", model: str | None = None) -> None:
    hook = _DEGRADATION_HOOK.get()
    if hook is not None:
        try:
            hook(reason, detail, model)
        except Exception:  # 观测失败不能影响主流程
            pass


@dataclass
class UsageCounter:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failures: int = 0
    _lock: Lock = field(default_factory=Lock, repr=False)

    def add(self, prompt: int, completion: int) -> None:
        with self._lock:
            self.calls += 1
            self.prompt_tokens += prompt
            self.completion_tokens += completion

    def reset(self) -> None:
        with self._lock:
            self.calls = 0
            self.prompt_tokens = 0
            self.completion_tokens = 0
            self.failures = 0

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "calls": self.calls,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.prompt_tokens + self.completion_tokens,
                "failures": self.failures,
            }


USAGE = UsageCounter()

#: 可观测性钩子：每次成功调用回报 ``(耗时 ms, prompt_tokens, completion_tokens)``。
#: 只做回调，不改变调用语义；没注册钩子时（默认）零开销。
_TIMING_HOOK: Callable[[float, int, int], None] | None = None


def set_timing_hook(hook: Callable[[float, int, int], None] | None) -> None:
    global _TIMING_HOOK
    _TIMING_HOOK = hook


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _extract_json(text: str) -> Any:
    """从模型输出里取出 JSON，容忍 ```json 围栏与前后废话。"""
    text = text.strip()
    m = _JSON_BLOCK.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"LLM 输出无法解析为 JSON: {text[:200]}")


def is_dashscope(base_url: str) -> bool:
    return "dashscope.aliyuncs.com" in base_url or "maas.aliyuncs.com" in base_url


def thinking_extra_body(base_url: str) -> dict[str, Any] | None:
    """思考模式开关：只在百炼端点上发，避免 OpenAI / 本地端点因未知字段 400。

    ``enable_thinking=False`` 能让 deepseek-v4.1-flash / qwen3 系列跳过思维链，
    否则思维链会先把 max_tokens 吃满、content 返回空串。非百炼端点一律不发该字段——
    DeepSeek 官方与本地 Ollama 都不需要它，发了反而可能被判 400。
    冒烟脚本也调这个函数，保证「测的」和「跑的」是同一条参数路径。
    """
    if not is_dashscope(base_url):
        return None
    return {"enable_thinking": settings.llm_thinking == "on"}


class LLMClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.llm_api_key
        self.base_url = base_url or settings.llm_base_url
        self.model = model or settings.llm_model
        self.temperature = settings.llm_temperature if temperature is None else temperature
        self.fallback_model = (settings.llm_fallback_model or "").strip()
        self.max_retries = max(0, int(settings.llm_max_retries))
        self.backoff_s = max(0.0, float(settings.llm_retry_backoff_s))
        self._clients: dict[tuple[str, str], Any] = {}
        self._bad_keys: set[str] = set()
        #: 最近一次调用的元信息（模型、尝试次数、错误序列、是否换模型）。
        self.last_meta: dict[str, Any] = {}
        #: 最近一次流式调用的元信息（用于区分「0 token 未输出」与「中途断开」）。
        self.last_stream: dict[str, Any] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.candidate_keys())

    def candidate_keys(self) -> list[str]:
        """主 key 之外允许一个备用 key（DEEPSEEK_API_KEY）。

        本机环境里 LLM_API_KEY 可能是失效的占位值，而 DEEPSEEK_API_KEY 有效；
        把「换 key 重试」收敛在客户端，避免上层每个节点都写一遍容错。
        """
        keys = [self.api_key] if self.api_key else []
        fb = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        if fb and fb not in keys:
            keys.append(fb)
        return keys

    def good_keys(self) -> list[str]:
        """跳过本进程内已判定鉴权失败的 key，避免每个请求先吃一次 401。"""
        keys = self.candidate_keys()
        good = [k for k in keys if k not in self._bad_keys]
        return good or keys

    def _client(self, key: str, model: str) -> Any:
        from openai import OpenAI

        ck = (model, key)
        if ck not in self._clients:
            self._clients[ck] = OpenAI(
                api_key=key, base_url=self.base_url, timeout=settings.llm_timeout, max_retries=0
            )
        return self._clients[ck]

    def _models(self) -> list[tuple[str, str | None]]:
        """候选 (模型, 降级原因)：主模型 → 备用模型（若配置且不同）。"""
        out: list[tuple[str, str | None]] = [(self.model, None)]
        if self.fallback_model and self.fallback_model != self.model:
            out.append((self.fallback_model, "fallback_model"))
        return out

    def _kwargs(self, messages: list[dict[str, str]], *, temperature, max_tokens, json_mode: bool) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
        }
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        extra_body = thinking_extra_body(self.base_url)
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    def chat_with_meta(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> dict[str, Any]:
        """带错误分类与有限重试的调用；返回 ``{text, model, attempts, errors, degraded_reason}``。

        重试策略：只在 :data:`RETRYABLE_KINDS` 上重试，指数退避；鉴权错误直接换 key；
        参数错误（400/422）立即失败，不浪费额度。主模型耗尽后才会换 :data:`fallback_model`。
        """
        if not self.enabled:
            raise LLMDisabled("LLM_API_KEY 为空，系统运行在 deterministic 模式")
        kwargs = self._kwargs(messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode)
        errors: list[dict[str, Any]] = []
        attempts = 0
        last_exc: BaseException | None = None
        for model, degraded_reason in self._models():
            keys = self.good_keys()
            for attempt in range(self.max_retries + 1):
                retryable_seen = False
                for key in keys:
                    attempts += 1
                    t0 = time.perf_counter()
                    try:
                        resp = self._client(key, model).chat.completions.create(model=model, **kwargs)
                    except Exception as exc:
                        kind = classify_error(exc)
                        errors.append({"kind": kind, "model": model, "key_tail": key[-4:], "detail": str(exc)[:160]})
                        last_exc = exc
                        if kind == "auth":
                            self._bad_keys.add(key)
                            log.warning("API Key 鉴权失败（尾号 %s），尝试下一个 key", key[-4:])
                            continue
                        if kind in RETRYABLE_KINDS:
                            retryable_seen = True
                            log.warning("LLM 调用失败（%s），第 %d 次重试", kind, attempt + 1)
                            continue
                        USAGE.failures += 1
                        raise LLMError(kind, str(exc)[:300]) from exc

                    elapsed_ms = (time.perf_counter() - t0) * 1000
                    usage = getattr(resp, "usage", None)
                    prompt_tokens = (getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
                    completion_tokens = (getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0
                    USAGE.add(prompt_tokens, completion_tokens)
                    if _TIMING_HOOK is not None:
                        _TIMING_HOOK(elapsed_ms, prompt_tokens, completion_tokens)
                    content = (resp.choices[0].message.content or "") if resp.choices else ""
                    finish = resp.choices[0].finish_reason if resp.choices else None
                    if not content.strip():
                        # 空正文：思维链吃满 / 上游异常都可能。按可重试处理，重试后仍空则报错，
                        # 让上层明确落入「降级」而不是把空串当成功。
                        kind = "empty_output"
                        errors.append(
                            {"kind": kind, "model": model, "key_tail": key[-4:], "detail": f"finish_reason={finish}"}
                        )
                        last_exc = LLMError(kind, f"空正文（finish_reason={finish}）")
                        retryable_seen = True
                        log.warning("LLM 返回空正文（finish_reason=%s）", finish)
                        continue
                    if key != self.api_key:
                        self.api_key = key
                    self.last_meta = {
                        "text": content,
                        "model": model,
                        "attempts": attempts,
                        "errors": errors,
                        "degraded_reason": degraded_reason,
                    }
                    return self.last_meta
                if not retryable_seen:
                    break
                if attempt < self.max_retries:
                    time.sleep(self.backoff_s * (2**attempt))
        USAGE.failures += 1
        kinds = sorted({e["kind"] for e in errors}) or ["unknown"]
        self.last_meta = {"text": "", "model": self.model, "attempts": attempts, "errors": errors, "degraded_reason": "upstream_failed"}
        raise LLMError(kinds[0], f"LLM 调用失败：{kinds}；最后错误 {last_exc}")

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        return self.chat_with_meta(
            messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode
        )["text"]

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 480,
        temperature: float = 0.0,
    ) -> Iterator[str]:
        """逐 token 产出。鉴权失败换 key；**一旦已经吐出内容就绝不重试**（会重复半段）。"""
        if not self.enabled:
            self.last_stream = {"model": self.model, "emitted": False, "degraded_reason": "no_key"}
            raise LLMDisabled("LLM_API_KEY 为空，系统运行在 deterministic 模式")
        kwargs = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        errors: list[dict[str, Any]] = []
        for model, degraded_reason in self._models():
            for key in self.good_keys():
                client = self._client(key, model)
                # 先试 include_usage，部分兼容端点不认这个字段，去掉再试一次
                try:
                    stream = client.chat.completions.create(model=model, **kwargs, stream_options={"include_usage": True})
                except Exception as exc:
                    kind = classify_error(exc)
                    if kind == "auth":
                        self._bad_keys.add(key)
                        errors.append({"kind": kind, "model": model, "key_tail": key[-4:]})
                        continue
                    try:
                        stream = client.chat.completions.create(model=model, **kwargs)
                    except Exception as exc2:
                        kind2 = classify_error(exc2)
                        errors.append({"kind": kind2, "model": model, "key_tail": key[-4:]})
                        if kind2 in ("timeout", "server_error", "network", "rate_limit"):
                            self.last_stream = {"model": model, "emitted": False, "errors": errors, "degraded_reason": "upstream_failed"}
                            raise LLMError(kind2, str(exc2)[:300]) from exc2
                        continue
                usage = None
                emitted = False
                t_stream = time.perf_counter()
                try:
                    for chunk in stream:
                        if getattr(chunk, "usage", None) is not None:
                            usage = chunk.usage
                        choices = getattr(chunk, "choices", None) or []
                        if not choices:
                            continue
                        delta = getattr(choices[0].delta, "content", None)
                        if delta:
                            emitted = True
                            yield delta
                except Exception as exc:
                    USAGE.failures += 1
                    kind = classify_error(exc)
                    self.last_stream = {
                        "model": model,
                        "emitted": emitted,
                        "errors": errors + [{"kind": kind, "model": model, "key_tail": key[-4:]}],
                        "degraded_reason": "stream_interrupted" if emitted else "upstream_failed",
                    }
                    raise LLMError(kind, f"流式中断（已输出={emitted}）: {str(exc)[:200]}") from exc
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0 if usage is not None else 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0 if usage is not None else 0
                USAGE.add(prompt_tokens, completion_tokens)
                if _TIMING_HOOK is not None:
                    _TIMING_HOOK((time.perf_counter() - t_stream) * 1000, prompt_tokens, completion_tokens)
                self.last_stream = {
                    "model": model,
                    "emitted": emitted,
                    "errors": errors,
                    "degraded_reason": degraded_reason or ("empty_output" if not emitted else None),
                }
                return
        USAGE.failures += 1
        self.last_stream = {"model": self.model, "emitted": False, "errors": errors, "degraded_reason": "upstream_failed"}
        raise LLMError("auth", f"所有候选 Key 均不可用: {[e['kind'] for e in errors]}")

    def json_chat(self, messages: list[dict[str, str]], *, schema: Any = None, **kwargs: Any) -> Any:
        text = self.chat(messages, json_mode=True, **kwargs)
        try:
            payload = _extract_json(text)
        except ValueError as exc:
            raise LLMError("json_parse", str(exc)[:300]) from exc
        if schema is not None:
            _validate_schema(payload, schema)
        return payload

    def try_chat(self, messages: list[dict[str, str]], default: str = "", **kwargs: Any) -> str:
        """失败返回 default，供「LLM 可选增强」的节点使用；失败原因写进降级记录。"""
        try:
            return self.chat(messages, **kwargs)
        except LLMDisabled as exc:
            report_degradation("no_key", str(exc), self.model)
            return default
        except LLMError as exc:
            report_degradation(exc.kind, str(exc), self.model)
            return default
        except Exception as exc:
            report_degradation(classify_error(exc), str(exc)[:200], self.model)
            return default

    def try_json(self, messages: list[dict[str, str]], default: Any = None, *, schema: Any = None, **kwargs: Any) -> Any:
        try:
            return self.json_chat(messages, schema=schema, **kwargs)
        except LLMDisabled as exc:
            report_degradation("no_key", str(exc), self.model)
            return default
        except LLMError as exc:
            report_degradation(exc.kind, str(exc), self.model)
            return default
        except Exception as exc:
            report_degradation(classify_error(exc), str(exc)[:200], self.model)
            return default


def _validate_schema(obj: Any, schema: Any, path: str = "$") -> None:
    """极简结构校验：``dict`` 表示必含字段，``list`` 表示元素 schema，``type`` 表示类型。"""
    if isinstance(schema, dict):
        if not isinstance(obj, dict):
            raise LLMError("schema", f"{path} 期望 object，实际 {type(obj).__name__}")
        for key, sub in schema.items():
            if key not in obj:
                raise LLMError("schema", f"{path}.{key} 缺失")
            _validate_schema(obj[key], sub, f"{path}.{key}")
        return
    if isinstance(schema, list):
        if not isinstance(obj, list):
            raise LLMError("schema", f"{path} 期望 list，实际 {type(obj).__name__}")
        if schema:
            for i, item in enumerate(obj):
                _validate_schema(item, schema[0], f"{path}[{i}]")
        return
    if schema is int:
        if not isinstance(obj, int) or isinstance(obj, bool):
            raise LLMError("schema", f"{path} 期望 int，实际 {type(obj).__name__}")
        return
    if not isinstance(obj, schema):
        raise LLMError("schema", f"{path} 期望 {getattr(schema, '__name__', schema)}，实际 {type(obj).__name__}")


_default_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _default_client
    if _default_client is None:
        _default_client = LLMClient()
    return _default_client


def llm_probe() -> bool:
    """一次极小的真实调用，确认 key 可用；无效 key 时整条流水线应降级而不是反复重试。"""
    try:
        get_llm().chat([{"role": "user", "content": "ping"}], max_tokens=8, temperature=0)
        return True
    except Exception:
        return False


def llm_status() -> dict[str, Any]:
    llm = get_llm()
    return {
        "enabled": llm.enabled,
        "model": llm.model if llm.enabled else None,
        "fallback_model": llm.fallback_model or None,
        "max_retries": llm.max_retries,
        "base_url": llm.base_url if llm.enabled else None,
        "run_mode": settings.run_mode,
        "usage": USAGE.snapshot(),
    }
