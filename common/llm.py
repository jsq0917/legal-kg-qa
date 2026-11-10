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
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from common.logging_utils import get_logger
from config import settings

log = get_logger("llm")


class LLMDisabled(RuntimeError):
    """未配置 API Key 时抛出，调用方应回落到确定性逻辑。"""


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
        self._clients: list[tuple[str, Any]] = []

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

    def _clients_or_init(self) -> list[tuple[str, Any]]:
        if not self._clients:
            from openai import OpenAI

            for key in self.candidate_keys():
                self._clients.append(
                    (
                        key,
                        OpenAI(api_key=key, base_url=self.base_url, timeout=settings.llm_timeout, max_retries=0),
                    )
                )
        return self._clients

    @staticmethod
    def _is_auth_error(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        text = str(exc)
        return status == 401 or "401" in text or "Authentication" in text or "invalid_api_key" in text

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        if not self.enabled:
            raise LLMDisabled("LLM_API_KEY 为空，系统运行在 deterministic 模式")
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": max_tokens or settings.llm_max_tokens,
        }
        extra_body = thinking_extra_body(self.base_url)
        if extra_body:
            kwargs["extra_body"] = extra_body
        auth_errors: list[str] = []
        for key, client in self._clients_or_init():
            t0 = time.perf_counter()
            try:
                resp = client.chat.completions.create(**kwargs)
            except Exception as exc:  # 网络/额度/鉴权
                if self._is_auth_error(exc):
                    auth_errors.append(f"{key[-4:]}: {str(exc)[:80]}")
                    log.warning("API Key 鉴权失败（尾号 %s），尝试下一个 key", key[-4:])
                    continue
                USAGE.failures += 1
                log.warning("LLM 调用失败: %s", exc)
                raise
            elapsed_ms = (time.perf_counter() - t0) * 1000
            usage = getattr(resp, "usage", None)
            prompt_tokens = (getattr(usage, "prompt_tokens", 0) or 0) if usage is not None else 0
            completion_tokens = (getattr(usage, "completion_tokens", 0) or 0) if usage is not None else 0
            USAGE.add(prompt_tokens, completion_tokens)
            if _TIMING_HOOK is not None:
                _TIMING_HOOK(elapsed_ms, prompt_tokens, completion_tokens)
            if key != self.api_key:
                self.api_key = key  # 记住可用 key，后续调用不再走失败分支
                self._clients = [(key, client)]
            content = resp.choices[0].message.content or ""
            if not content.strip() and resp.choices[0].finish_reason == "length":
                # 空正文 + length：几乎都是思维链占满了 max_tokens。这里必须出声，
                # 否则上层 try_chat 会把它当普通空串吞掉，静默回落到模板答案。
                log.warning(
                    "LLM 返回空正文（finish_reason=length，max_tokens=%s）。"
                    "多为思考模式吃满配额，检查 LLM_THINKING=off 或调大 LLM_MAX_TOKENS",
                    kwargs.get("max_tokens"),
                )
            return content
        USAGE.failures += 1
        raise RuntimeError(f"所有候选 API Key 均鉴权失败: {auth_errors}")

    def json_chat(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        text = self.chat(messages, json_mode=True, **kwargs)
        return _extract_json(text)

    def try_chat(self, messages: list[dict[str, str]], default: str = "", **kwargs: Any) -> str:
        """失败返回 default，供「LLM 可选增强」的节点使用。"""
        try:
            return self.chat(messages, **kwargs)
        except LLMDisabled:
            return default
        except Exception:
            return default

    def try_json(self, messages: list[dict[str, str]], default: Any = None, **kwargs: Any) -> Any:
        try:
            return self.json_chat(messages, **kwargs)
        except LLMDisabled:
            return default
        except Exception:
            return default


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
        "base_url": llm.base_url if llm.enabled else None,
        "run_mode": settings.run_mode,
        "usage": USAGE.snapshot(),
    }
