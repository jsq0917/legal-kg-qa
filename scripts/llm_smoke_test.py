"""LLM 连通性冒烟测试：一条最小 chat，验证 key / endpoint / model 三者是否配套。

为什么要单独一个脚本：这三个值是分开配的，错配的报错又都长得像「鉴权失败」。
最常见的一种是「百炼的 key + DeepSeek 的 base_url」，服务端只回 401 invalid_api_key，
看不出是端点错了。这里把三个值一起打出来，再按状态码给出可执行的修复建议。

用法：
    uv run python scripts/llm_smoke_test.py
    uv run python scripts/llm_smoke_test.py --model qwen3.8-flash
    uv run python scripts/llm_smoke_test.py --base-url https://dashscope.aliyuncs.com/compatible-mode/v1

退出码：0 通；1 调用失败；2 配置缺失（key 为空，系统会跑在 deterministic 模式）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402

HINTS = {
    401: "鉴权失败。三件事按顺序查：① key 是否是当前端点的（百炼 key 不能打 api.deepseek.com）；"
    "② .env 里的 key 有没有引号/换行/尾随空格；③ 控制台里 key 是否被禁用或超额。",
    403: "无权限。多为模型未在该业务空间开通，或 key 属于另一个业务空间。",
    404: "端点或模型不存在。base_url 必须以 /compatible-mode/v1 结尾（不是 /v1）；"
    "模型名要写全，如 deepseek-v4.1-flash，不要写成 DeepSeek-V4.1-Flash。",
    400: "参数不被接受。常见：max_tokens 超出上限、temperature 不在 [0,2)、"
    "消息里混入了模型不支持的多模态 part。",
    429: "触发限流（百炼按 TPM 动态限流）。降低并发或加重试退避；本项目的 LLMClient 是 max_retries=0，不会自动重试。",
}


def mask(secret: str) -> str:
    if len(secret) <= 10:
        return "***" if secret else "(空)"
    return f"{secret[:6]}...{secret[-4:]} (len={len(secret)})"


def main() -> int:
    ap = argparse.ArgumentParser(description="LLM 连通性冒烟测试")
    ap.add_argument("--base-url", default=None, help="覆盖 LLM_BASE_URL")
    ap.add_argument("--model", default=None, help="覆盖 LLM_MODEL")
    ap.add_argument("--prompt", default="只回复两个字：收到", help="测试用问题")
    ap.add_argument("--max-tokens", type=int, default=64)
    args = ap.parse_args()

    base_url = args.base_url or settings.llm_base_url
    model = args.model or settings.llm_model
    key = settings.llm_api_key

    print("endpoint :", base_url)
    print("model    :", model)
    print("api_key  :", mask(key))
    print("run_mode :", settings.run_mode, "| llm_enabled:", settings.llm_enabled)

    if not key:
        print("\n[FAIL] LLM_API_KEY 为空。config.llm_enabled=False 时会静默降级为 deterministic，"
              "不会抛错——这也是「配了 key 但答案仍是模板」的头号原因。")
        return 2

    from openai import OpenAI

    from common.llm import thinking_extra_body

    client = OpenAI(api_key=key, base_url=base_url, timeout=settings.llm_timeout, max_retries=0)
    extra_body = thinking_extra_body(base_url)
    if extra_body:
        print("thinking :", "关闭（enable_thinking=False）" if not extra_body.get("enable_thinking") else "开启")
    else:
        print("thinking : 不干预（该端点不识别 enable_thinking）")
    t0 = time.perf_counter()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": args.prompt}],
            temperature=0.0,
            max_tokens=args.max_tokens,
            **({"extra_body": extra_body} if extra_body else {}),
        )
    except Exception as exc:  # SDK 异常统一在这里翻译成人话
        elapsed = (time.perf_counter() - t0) * 1000
        status = getattr(exc, "status_code", None)
        print(f"\n[FAIL] {type(exc).__name__} status={status} {elapsed:.0f}ms")
        print(str(exc)[:500])
        if status in HINTS:
            print("\n提示：", HINTS[status])
        elif "Connection" in type(exc).__name__ or "timeout" in str(exc).lower():
            print("\n提示：网络不通或超时。检查代理/防火墙；百炼的北京地域域名在境外网络下常需走代理。")
        return 1

    elapsed = (time.perf_counter() - t0) * 1000
    msg = resp.choices[0].message
    print(f"\n[OK] {elapsed:.0f}ms  finish_reason={resp.choices[0].finish_reason}")
    print("返回模型 :", resp.model)
    print("content  :", repr(msg.content)[:300])
    reasoning = getattr(msg, "reasoning_content", None)
    if reasoning:
        print("reasoning:", repr(reasoning)[:160], "（该模型思考模式已开启，会额外计费）")
    if resp.usage:
        u = resp.usage
        print(f"usage    : prompt={u.prompt_tokens} completion={u.completion_tokens} total={u.total_tokens}")
    if not (msg.content or "").strip():
        print("\n[WARN] content 为空。思考模式下 max_tokens 会被思维链吃掉，正文没配额了——"
              "把 LLM_MAX_TOKENS 调大或关闭思考模式。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
