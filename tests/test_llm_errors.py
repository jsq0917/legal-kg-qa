"""LLM 错误治理单测：分类、重试边界、换 Key、备用模型、降级记录（不发真实请求）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from common import llm as llm_mod
from common.llm import (
    LLMClient,
    LLMDisabled,
    LLMError,
    classify_error,
    reset_degradation_hook,
    reset_model_hook,
    set_degradation_hook,
    set_model_hook,
)


class FakeHTTPError(Exception):
    def __init__(self, status_code: int, text: str = "") -> None:
        super().__init__(text or f"HTTP {status_code}")
        self.status_code = status_code


class FakeCompletions:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.outcomes:
            raise AssertionError("没有更多预设结果")
        item = self.outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=4),
        )


class FakeClient:
    def __init__(self, outcomes: list) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(outcomes))


class FakeStream:
    """最小可迭代上游流；记录是否被 close，用来验证客户端断开会关连接。"""

    def __init__(self, chunks: list[str]) -> None:
        self.chunks = chunks
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content=chunk))],
                usage=None,
            )

    def close(self) -> None:
        self.closed = True


class FakeStreamClient:
    def __init__(self, stream: FakeStream) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: stream))


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(llm_mod.settings, "llm_max_retries", 1, raising=False)
    monkeypatch.setattr(llm_mod.settings, "llm_retry_backoff_s", 0.0, raising=False)
    monkeypatch.setattr(llm_mod.settings, "llm_fallback_model", "", raising=False)
    c = LLMClient(api_key="key-primary", base_url="http://fake", model="model-a")
    return c


def _install(monkeypatch, mapping: dict[tuple[str, str], FakeClient]):
    monkeypatch.setattr(LLMClient, "_client", lambda self, key, model: mapping[(key, model)])


def test_classify_error_taxonomy():
    assert classify_error(FakeHTTPError(401)) == "auth"
    assert classify_error(FakeHTTPError(402, "insufficient balance")) == "quota"
    assert classify_error(FakeHTTPError(429)) == "rate_limit"
    assert classify_error(FakeHTTPError(503)) == "server_error"
    assert classify_error(TimeoutError("timed out")) == "timeout"
    assert classify_error(ConnectionError("connection reset")) == "network"
    assert classify_error(ValueError("bad request")) == "unknown"
    assert classify_error(LLMError("schema", "x")) == "schema"


def test_auth_error_switches_key(monkeypatch, client):
    client.api_key = "bad-key"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "good-key")
    _install(
        monkeypatch,
        {
            ("bad-key", "model-a"): FakeClient([FakeHTTPError(401)]),
            ("good-key", "model-a"): FakeClient(["答案"]),
        },
    )
    meta = client.chat_with_meta([{"role": "user", "content": "q"}])
    assert meta["text"] == "答案"
    assert meta["errors"][0]["kind"] == "auth"
    assert client.api_key == "good-key"


def test_rate_limit_retries_then_fails(monkeypatch, client):
    fake = FakeClient([FakeHTTPError(429), FakeHTTPError(429)])
    _install(monkeypatch, {("key-primary", "model-a"): fake})
    with pytest.raises(LLMError) as exc:
        client.chat_with_meta([{"role": "user", "content": "q"}])
    assert exc.value.kind == "rate_limit"
    assert len(fake.chat.completions.calls) == 2  # max_retries=1 -> 最多 2 次
    assert client.last_meta["degraded_reason"] == "upstream_failed"


def test_non_retryable_fails_immediately(monkeypatch, client):
    fake = FakeClient([FakeHTTPError(400, "bad request")])
    _install(monkeypatch, {("key-primary", "model-a"): fake})
    with pytest.raises(LLMError) as exc:
        client.chat_with_meta([{"role": "user", "content": "q"}])
    assert exc.value.kind == "unknown"
    assert len(fake.chat.completions.calls) == 1  # 参数错误不重试


def test_empty_output_is_retryable_and_reported(monkeypatch, client):
    fake = FakeClient(["   ", ""])
    _install(monkeypatch, {("key-primary", "model-a"): fake})
    with pytest.raises(LLMError) as exc:
        client.chat_with_meta([{"role": "user", "content": "q"}], max_tokens=16)
    assert exc.value.kind == "empty_output"
    assert len(fake.chat.completions.calls) == 2


def test_fallback_model_after_primary_exhausted(monkeypatch, client):
    client.fallback_model = "model-b"
    _install(
        monkeypatch,
        {
            ("key-primary", "model-a"): FakeClient([FakeHTTPError(503), FakeHTTPError(503)]),
            ("key-primary", "model-b"): FakeClient(["备用答案"]),
        },
    )
    seen: list[tuple] = []
    token = set_model_hook(lambda model, reason: seen.append((model, reason)))
    try:
        meta = client.chat_with_meta([{"role": "user", "content": "q"}])
    finally:
        reset_model_hook(token)
    assert meta["text"] == "备用答案"
    assert meta["model"] == "model-b"
    assert meta["degraded_reason"] == "fallback_model"
    # trace 依赖这个钩子把「配置的主模型」改成「实际用了备用模型」，并记下降级原因。
    assert seen == [("model-b", "fallback_model")]


def test_primary_model_success_reports_no_degradation(monkeypatch, client):
    _install(monkeypatch, {("key-primary", "model-a"): FakeClient(["答案"])})
    seen: list[tuple] = []
    token = set_model_hook(lambda model, reason: seen.append((model, reason)))
    try:
        client.chat_with_meta([{"role": "user", "content": "q"}])
    finally:
        reset_model_hook(token)
    assert seen == [("model-a", None)]


def test_closing_stream_consumer_closes_upstream(monkeypatch, client):
    stream = FakeStream(["a", "b"])
    monkeypatch.setattr(LLMClient, "_client", lambda self, key, model: FakeStreamClient(stream))
    gen = client.chat_stream([{"role": "user", "content": "q"}])
    assert next(gen) == "a"
    gen.close()  # 等价于客户端断开
    assert stream.closed is True


def test_no_key_reports_degradation(client):
    c = LLMClient(api_key="", model="model-a")
    seen: list[tuple] = []
    token = set_degradation_hook(lambda reason, detail, model: seen.append((reason, detail, model)))
    try:
        assert c.try_chat([{"role": "user", "content": "q"}], default="fallback") == "fallback"
    finally:
        reset_degradation_hook(token)
    assert seen and seen[0][0] == "no_key"


def test_json_schema_violation_is_classified(client, monkeypatch):
    _install(monkeypatch, {("key-primary", "model-a"): FakeClient(['{"level": 3}', '{"level": 3}'])})
    with pytest.raises(LLMError) as exc:
        client.json_chat([{"role": "user", "content": "q"}], schema={"level": str})
    assert exc.value.kind == "schema"

    seen: list[tuple] = []
    token = set_degradation_hook(lambda reason, detail, model: seen.append((reason, detail, model)))
    try:
        assert client.try_json([{"role": "user", "content": "q"}], default=None, schema={"level": str}) is None
    finally:
        reset_degradation_hook(token)
    assert seen and seen[0][0] == "schema"


def test_json_parse_error_is_classified(client, monkeypatch):
    _install(monkeypatch, {("key-primary", "model-a"): FakeClient(["not json"])})
    with pytest.raises(LLMError) as exc:
        client.json_chat([{"role": "user", "content": "q"}])
    assert exc.value.kind == "json_parse"


def test_disabled_stream_meta(client):
    c = LLMClient(api_key="", model="model-a")
    with pytest.raises(LLMDisabled):
        list(c.chat_stream([{"role": "user", "content": "q"}]))
    assert c.last_stream["degraded_reason"] == "no_key"
    assert c.last_stream["emitted"] is False
