"""T26 的可复现基准：流式 TTFT / 缓存命中 / 并发 10 / 限流 429。

用法（在仓库根目录）::

    python -m app.__008__api.__004__bench                 # 全量，落 data/eval_out/api_bench.json
    python -m app.__008__api.__004__bench --no-llm        # 不测首 token（省 LLM 费用）

口径说明（写进报告时不要丢）：
* 首 token = 请求发出到第一个 ``event: token`` 的到达时间，**含检索**（检索是同步前置）；
* 冷启动（模型加载）单独计一段，**不并入**首 token 指标；
* 并发 10 用 ``llm=false`` 的确定性路径，避免 10 次真实生成把结论变成「API 额度测试」；
* 429 测试用两套配置各跑一遍：IP 维度与 Key 维度，证明「双维度」不是口号。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from app.__008__api.__000__server import app as fastapi_app  # noqa: E402
from app.__008__api.__001__ratelimit import LIMITER, LimitConfig  # noqa: E402
from app.__008__api.__002__cache import get_cache  # noqa: E402
from common.llm import llm_status  # noqa: E402
from config import settings  # noqa: E402

STREAM_QUERIES = [
    "公司拖欠我两个月工资，我可以直接解除劳动合同吗？",
    "试用期最长可以约定多久？",
    "用人单位单方解除劳动合同需要提前多少天通知？",
    "加班费的计算基数怎么确定？",
    "女职工产假期间的工资由谁支付？",
]

CONCURRENT_QUERIES = [
    "劳动合同到期不续签有经济补偿吗？",
    "未签书面劳动合同有什么后果？",
    "竞业限制最长可以约定几年？",
    "劳动者主动辞职需要提前多久通知？",
    "工伤认定后用人单位还要赔钱吗？",
    "经济补偿按什么标准计算？",
    "用人单位可以随意调岗降薪吗？",
    "非全日制用工可以约定试用期吗？",
    "请假不批算旷工吗？",
    "违法解除劳动合同的赔偿标准是什么？",
]

LIMIT_QUERIES = CONCURRENT_QUERIES + [
    "劳务派遣员工与正式员工待遇一样吗？",
    "公司不缴社保可以要求补缴吗？",
]


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return round(ordered[idx], 1)


async def stream_once(
    client: httpx.AsyncClient, query: str, tier: str = "A_gates", llm: bool = True
) -> dict[str, Any]:
    """读一次 SSE，记录首个事件 / 首个 token / 总时长的到达时刻。"""
    t0 = time.perf_counter()
    first_event_ms: float | None = None
    first_token_ms: float | None = None
    event: str | None = None
    text: list[str] = []
    checks: list[dict[str, Any]] | None = None
    done: dict[str, Any] | None = None
    status = 0
    async with client.stream(
        "GET",
        "/ask/stream",
        params={"query": query, "mode": "hybrid", "tier": tier, "llm": llm},
    ) as resp:
        status = resp.status_code
        async for line in resp.aiter_lines():
            if not line.strip():
                continue
            now_ms = (time.perf_counter() - t0) * 1000
            if line.startswith("event:"):
                event = line.split(":", 1)[1].strip()
                continue
            if not line.startswith("data:"):
                continue
            payload = json.loads(line.split(":", 1)[1].strip())
            if first_event_ms is None:
                first_event_ms = round(now_ms, 1)
            if event == "token":
                if first_token_ms is None:
                    first_token_ms = round(now_ms, 1)
                text.append(payload.get("text", ""))
            elif event == "check":
                checks = payload.get("checks")
            elif event == "done":
                done = payload
    total_ms = round((time.perf_counter() - t0) * 1000, 1)
    return {
        "query": query,
        "status": status,
        "first_event_ms": first_event_ms,
        "first_token_ms": first_token_ms,
        "total_ms": total_ms,
        "cache": (done or {}).get("cache"),
        "answer_chars": len("".join(text)),
        "checks": checks,
        "grounded": all(c["ok"] for c in checks) if checks else None,
    }


async def ask_once(
    client: httpx.AsyncClient, query: str, *, llm: bool, api_key: str | None = None
) -> dict[str, Any]:
    t0 = time.perf_counter()
    headers = {"X-API-Key": api_key} if api_key else None
    resp = await client.post(
        "/ask", json={"query": query, "mode": "hybrid", "tier": "C", "llm": llm}, headers=headers
    )
    latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    out: dict[str, Any] = {
        "status": resp.status_code,
        "latency_ms": latency_ms,
        "retry_after": resp.headers.get("retry-after"),
        "dimension": resp.headers.get("x-ratelimit-dimension"),
    }
    if resp.status_code == 200:
        body = resp.json()
        out["cache"] = body.get("cache")
        out["answer_chars"] = len(body.get("answer", ""))
    return out


def _step(msg: str) -> None:
    print(f"[bench {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True, cwd=Path(__file__).resolve().parents[2]
        ).strip()
    except Exception:
        return "unknown"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_server(port: int) -> tuple[uvicorn.Server, threading.Thread]:
    """真起一个 uvicorn：``httpx.ASGITransport`` 会把 SSE 缓冲成整段，TTFT 测不准。"""
    config = uvicorn.Config(fastapi_app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


async def run(no_llm: bool) -> dict[str, Any]:
    port = _free_port()
    server, server_thread = _start_server(port)
    timeout = httpx.Timeout(180.0, connect=10.0)
    result: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "git_sha": _git_sha(),
        "server": f"uvicorn 127.0.0.1:{port}（单进程，非 ASGITransport）",
        "llm": {k: v for k, v in llm_status().items() if k != "usage"},
        "initial_cache": get_cache().snapshot(),
        "notes": {
            "first_token": "请求发出 -> 第一个 event: token，含检索时间",
            "concurrency": "llm=false 的确定性路径，只测服务不崩与延迟",
            "cold_start": "首请求含 embedding/reranker 模型加载，单独计段",
        },
    }

    # 放开限流，先测流式与并发；429 放在最后用独立配置复现
    LIMITER.configure(LimitConfig(enabled=True, ip_rps=1000, ip_burst=1000, key_rps=1000, key_burst=1000))
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=timeout) as client:
        wall0 = time.perf_counter()
        health: httpx.Response | None = None
        for _ in range(120):
            try:
                health = await client.get("/health")
                break
            except Exception:
                await asyncio.sleep(0.5)
        if health is None:
            raise RuntimeError("uvicorn 未在 60s 内就绪")
        result["health"] = {"status": health.status_code, "graph_backend": health.json().get("graph_backend")}
        _step(f"health ok ({result['health']['graph_backend']})")

        if no_llm:
            result["streaming"] = {"skipped": True}
        else:
            cold = await asyncio.wait_for(stream_once(client, STREAM_QUERIES[0]), 120)
            result["cold_start_ms"] = round((time.perf_counter() - wall0) * 1000, 1)
            result["cold_first_token_ms"] = cold["first_token_ms"]
            _step(f"cold stream: first_token={cold['first_token_ms']}ms total={cold['total_ms']}ms")
            runs = [cold]
            for q in STREAM_QUERIES[1:]:
                runs.append(await asyncio.wait_for(stream_once(client, q), 120))
                _step(f"stream ttft={runs[-1]['first_token_ms']}ms")
            result["streaming"] = {
                "runs": runs,
                "first_token_ms": [r["first_token_ms"] for r in runs[1:]],
                "first_token_median_ms": _pct([r["first_token_ms"] for r in runs[1:] if r["first_token_ms"]], 0.5),
                "first_token_max_ms": max([r["first_token_ms"] for r in runs[1:] if r["first_token_ms"]], default=0.0),
                "all_within_2s": all((r["first_token_ms"] or 1e9) <= 2000 for r in runs[1:]),
            }

        # --- 缓存：同问两次（一次 miss 一次 hit），走 /ask 与 /ask/stream 各一遍 ---
        get_cache().clear()
        q = STREAM_QUERIES[0]
        miss = await asyncio.wait_for(ask_once(client, q, llm=not no_llm), 120)
        hit = await asyncio.wait_for(ask_once(client, q, llm=not no_llm), 120)
        _step(f"ask miss/hit: {miss['latency_ms']}ms / {hit['latency_ms']}ms ({hit.get('cache')})")
        get_cache().clear()
        s_miss = await asyncio.wait_for(stream_once(client, q, llm=not no_llm), 120)
        s_hit = await asyncio.wait_for(stream_once(client, q, llm=not no_llm), 120)
        _step(f"stream miss/hit ttft: {s_miss['first_token_ms']}ms / {s_hit['first_token_ms']}ms ({s_hit.get('cache')})")
        cstats = get_cache().snapshot()
        result["cache"] = {
            "ask_miss_ms": miss["latency_ms"],
            "ask_hit_ms": hit["latency_ms"],
            "ask_speedup": round(miss["latency_ms"] / max(hit["latency_ms"], 0.01), 1),
            "ask_miss_cache": miss.get("cache"),
            "ask_hit_cache": hit.get("cache"),
            "stream_miss_first_token_ms": s_miss["first_token_ms"],
            "stream_hit_first_token_ms": s_hit["first_token_ms"],
            "stream_hit_cache": s_hit.get("cache"),
            "stats": cstats,
        }

        # --- 并发 10 ---
        _step("concurrency 10 start")
        tasks = [ask_once(client, q, llm=False) for q in CONCURRENT_QUERIES]
        t0 = time.perf_counter()
        conc = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 180)
        _step(f"concurrency 10 done in {round(time.perf_counter() - t0, 1)}s")
        wall_ms = round((time.perf_counter() - t0) * 1000, 1)
        ok = [r for r in conc if isinstance(r, dict) and r["status"] == 200]
        errors = [repr(r) for r in conc if not isinstance(r, dict)]
        latencies = [r["latency_ms"] for r in ok]
        result["concurrency_10"] = {
            "sent": len(tasks),
            "ok": len(ok),
            "non_200": [r["status"] for r in conc if isinstance(r, dict) and r["status"] != 200],
            "errors": errors,
            "wall_ms": wall_ms,
            "p50_ms": _pct(latencies, 0.5),
            "p95_ms": _pct(latencies, 0.95),
            "max_ms": max(latencies, default=0.0),
            "cache_stats": get_cache().snapshot(),
        }

        # --- 限流：IP 维度 ---
        _step("rate limit ip start")
        LIMITER.configure(LimitConfig(enabled=True, ip_rps=1.0, ip_burst=5, key_rps=1000, key_burst=1000))
        tasks = [ask_once(client, q, llm=False) for q in LIMIT_QUERIES[:12]]
        ip_res = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 120)
        _step(f"rate limit ip done: {len([r for r in ip_res if isinstance(r, dict) and r['status'] == 429])} denied")
        ip_rows = [r for r in ip_res if isinstance(r, dict)]
        ip_429 = [r for r in ip_rows if r["status"] == 429]
        result["rate_limit_ip"] = {
            "config": LIMITER.config.as_dict(),
            "sent": len(tasks),
            "allowed": len([r for r in ip_rows if r["status"] == 200]),
            "denied": len(ip_429),
            "dimensions": sorted({r["dimension"] for r in ip_429}),
            "retry_after_values": sorted({r["retry_after"] for r in ip_429}),
            "all_with_retry_after": all(r["retry_after"] for r in ip_429) if ip_429 else False,
        }

        # --- 限流：Key 维度（IP 放开，单 key 只给 2 个令牌） ---
        _step("rate limit key start")
        LIMITER.configure(LimitConfig(enabled=True, ip_rps=1000, ip_burst=1000, key_rps=1.0, key_burst=2))
        tasks = [ask_once(client, q, llm=False, api_key="bench-key") for q in LIMIT_QUERIES[:6]]
        key_res = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 120)
        _step(f"rate limit key done: {len([r for r in key_res if isinstance(r, dict) and r['status'] == 429])} denied")
        key_rows = [r for r in key_res if isinstance(r, dict)]
        key_429 = [r for r in key_rows if r["status"] == 429]
        result["rate_limit_key"] = {
            "config": LIMITER.config.as_dict(),
            "sent": len(tasks),
            "allowed": len([r for r in key_rows if r["status"] == 200]),
            "denied": len(key_429),
            "dimensions": sorted({r["dimension"] for r in key_429}),
            "all_with_retry_after": all(r["retry_after"] for r in key_429) if key_429 else False,
        }
        result["rate_limit_stats"] = (await client.get("/rate-limit/stats")).json()

    server.should_exit = True
    server_thread.join(timeout=10)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="LexWeave API 工程化基准")
    parser.add_argument("--out", default="data/eval_out/api_bench.json")
    parser.add_argument("--no-llm", action="store_true", help="跳过真实生成，只测并发/缓存/限流")
    args = parser.parse_args()
    data = asyncio.run(run(args.no_llm))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[ok] 写入 {out}")
    print(
        json.dumps(
            {
                "model": settings.llm_model,
                "stream_median_ttft_ms": data.get("streaming", {}).get("first_token_median_ms"),
                "cache_hit": data["cache"]["stream_hit_cache"],
                "concurrency_10_ok": data["concurrency_10"]["ok"],
                "rate_limit_ip_denied": data["rate_limit_ip"]["denied"],
                "rate_limit_key_denied": data["rate_limit_key"]["denied"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
