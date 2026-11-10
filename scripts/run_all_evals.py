"""T08a 评测 harness：一条命令顺序跑六个 eval，落一份可追溯的 manifest。

定位是「取证」而不是「刷新数字」：
六个 eval 脚本都把结果直接写进 ``data/eval_out/<name>.json``（README §10.2 就是这么写的）。
如果 harness 顺手覆盖这些文件，正在写 README / 写报告的人会拿到与既有结论不一致的中间值。
所以这里对每个产物做 **先备份 -> 跑 -> 读指标与指纹 -> 按原字节还原**；
manifest 记录的是本次运行的指标指纹，磁盘上留下的仍是跑之前那一份。

用法：
    python scripts/run_all_evals.py --no-llm        # 确定性路径，CI 用
    python scripts/run_all_evals.py                 # 真实 LLM（ablation/hitl 走生成）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.path_utils import write_json  # noqa: E402

MANIFEST = ROOT / "data" / "eval_out" / "manifest.json"


@dataclass(frozen=True)
class Job:
    """一个 eval 脚本 + 它唯一会写的产物。"""

    name: str
    script: str
    artifact: str
    no_llm_args: tuple[str, ...] = ()
    llm_args: tuple[str, ...] = ()
    #: 支持 ``--limit`` 的脚本才传；不支持的脚本传了会直接 argparse 报错
    supports_limit: bool = False


JOBS: tuple[Job, ...] = (
    Job("retrieval", "scripts/eval_retrieval.py", "data/eval_out/retrieval.json", supports_limit=True),
    Job("router", "scripts/eval_router.py", "data/eval_out/router.json"),
    Job(
        "ablation",
        "scripts/eval_ablation.py",
        "data/eval_out/ablation.json",
        no_llm_args=("--no-llm",),
        supports_limit=True,
    ),
    Job("dialog", "scripts/eval_dialog.py", "data/eval_out/dialog.json"),
    Job("hitl", "scripts/eval_hitl.py", "data/eval_out/hitl.json", llm_args=("--llm",)),
    Job("gold", "scripts/check_gold.py", "data/eval_out/gold.json"),
)


# --------------------------------------------------------------------------------------
# 指纹与读写
# --------------------------------------------------------------------------------------
def sha256_8(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()[:8]


def fingerprint(obj: Any) -> str:
    """指标指纹：规范化 JSON 的 sha256 前 8 位，用于回答「这次跑的和上次是不是同一份数字」。"""
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_8(raw)


def git_info() -> dict[str, Any]:
    def run(*args: str) -> str:
        try:
            return subprocess.run(
                ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=15, check=False
            ).stdout.strip()
        except Exception:
            return ""

    status = run("status", "--porcelain")
    files = [line for line in status.splitlines() if line.strip()]
    return {
        "sha": run("rev-parse", "HEAD") or "unknown",
        "dirty": bool(files),
        "changed_files": len(files),
        # 只留前 20 行，manifest 不用来替代 git status
        "status_short": files[:20],
    }


def read_artifact(path: Path) -> tuple[bytes, Any]:
    raw = path.read_bytes()
    return raw, json.loads(raw)


# --------------------------------------------------------------------------------------
# 每个 eval 的「关键指标」白名单：只落可读、可对比的字段，不把 rows 整份塞进 manifest
# --------------------------------------------------------------------------------------
def metrics_retrieval(data: dict) -> dict:
    out = {}
    for mode, res in (data.get("results") or {}).items():
        out[mode] = {
            key: res.get(key)
            for key in ("n", "recall@5", "all@5", "path_recall", "citation_hit", "latency_median_ms")
        }
    return out


def metrics_router(data: dict) -> dict:
    return {key: data.get(key) for key in ("n", "by_label", "rule_accuracy", "lr_loo_accuracy")}


def metrics_ablation(data: dict) -> dict:
    return {
        "llm_mode": data.get("llm_mode"),
        "retrieval_mode": data.get("retrieval_mode"),
        "n": data.get("n"),
        "tiers": {
            tier: {
                key: summary.get(key)
                for key in (
                    "n",
                    "recall@5",
                    "path_accuracy",
                    "cite_precision",
                    "cite_recall",
                    "grounded",
                    "llm_calls_mean",
                    "tokens_mean",
                    "latency_median_ms",
                )
            }
            for tier, summary in (data.get("tiers") or {}).items()
        },
    }


def metrics_dialog(data: dict) -> dict:
    return {
        key: data.get(key)
        for key in (
            "scripts",
            "turns",
            "status_accuracy",
            "slot_accuracy",
            "inheritance_accuracy",
            "coref_pass",
            "restart_ok",
            "restart_continues",
        )
    }


def metrics_hitl(data: dict) -> dict:
    return {
        "all_pass": data.get("all_pass"),
        "generation": data.get("generation"),
        "checkpoint_files": data.get("checkpoint_files"),
        "off_domain_threshold": data.get("off_domain_threshold"),
        "stats": data.get("stats"),
    }


def metrics_gold(data: dict) -> dict:
    return {
        "total": data.get("total"),
        "by_hop": data.get("by_hop"),
        "leakage": {
            key: (data.get("leakage") or {}).get(key)
            for key in ("verbatim_fail", "verbatim_fail_rate", "strict_fail", "build_fail", "max_leak")
        },
        "necessity_pass_rate": (data.get("necessity") or {}).get("pass_rate"),
        "path_pass_rate": (data.get("path_integrity") or {}).get("pass_rate"),
        "kappa": data.get("kappa"),
    }


METRICS = {
    "retrieval": metrics_retrieval,
    "router": metrics_router,
    "ablation": metrics_ablation,
    "dialog": metrics_dialog,
    "hitl": metrics_hitl,
    "gold": metrics_gold,
}


def extract_metrics(name: str, data: Any) -> Any:
    if not isinstance(data, dict):
        return {"_error": "产物不是 JSON 对象"}
    try:
        return METRICS[name](data)
    except Exception as exc:  # 产物字段变动不应让整条 harness 崩掉
        return {"_error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------------------
# 单任务执行：备份 -> 跑 -> 读 -> 还原
# --------------------------------------------------------------------------------------
def run_job(job: Job, args: argparse.Namespace, env: dict[str, str]) -> dict[str, Any]:
    artifact = ROOT / job.artifact
    existed = artifact.exists()
    original = artifact.read_bytes() if existed else None
    stat = artifact.stat() if existed else None

    cmd = [sys.executable, job.script]
    if args.no_llm:
        cmd += job.no_llm_args
    else:
        cmd += job.llm_args
    if args.limit and job.supports_limit:
        cmd += ["--limit", str(args.limit)]

    print(f"\n[{job.name}] {' '.join(cmd)}", flush=True)
    t0 = time.perf_counter()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=args.timeout,
            check=False,
        )
        exit_code = proc.returncode
        stdout, stderr = proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        exit_code = -9
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
        stderr += f"\n[harness] timeout after {args.timeout}s"
    except Exception as exc:  # 脚本不存在等
        timed_out = False
        exit_code = -1
        stdout, stderr = "", f"[harness] {type(exc).__name__}: {exc}"
    duration = time.perf_counter() - t0

    metrics: Any = {"_error": "产物未生成"}
    produced_sha: str | None = None
    produced_mtime: float | None = None
    changed = False
    parsed_ok = False
    try:
        if artifact.exists():
            raw, data = read_artifact(artifact)
            produced_sha = sha256_8(raw)
            produced_mtime = artifact.stat().st_mtime
            changed = produced_sha != sha256_8(original) if original is not None else True
            metrics = extract_metrics(job.name, data)
            parsed_ok = not (isinstance(metrics, dict) and metrics.get("_error"))
    except Exception as exc:  # 产物损坏时仍要还原，否则会把别人的 eval json 留在坏状态
        metrics = {"_error": f"{type(exc).__name__}: {exc}"}
        stderr += f"\n[harness] 产物读取失败: {exc}"
    finally:
        # ---- 还原：既有文件按原字节写回，原本不存在的删掉 ----
        restored = False
        if existed and original is not None:
            artifact.write_bytes(original)
            if stat is not None:
                os.utime(artifact, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            restored = artifact.read_bytes() == original
        elif not existed and artifact.exists():
            artifact.unlink()
            restored = True

    status = "ok" if exit_code == 0 and parsed_ok else "failed"
    print(
        f"[{job.name}] {status} exit={exit_code} {duration:.1f}s "
        f"metrics={fingerprint(metrics)} restored={restored}",
        flush=True,
    )

    return {
        "name": job.name,
        "script": job.script,
        "cmd": cmd,
        "status": status,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_s": round(duration, 2),
        "artifact": job.artifact,
        "artifact_existed": existed,
        "artifact_changed_during_run": changed,
        "artifact_mtime_before": stat.st_mtime if stat else None,
        "artifact_mtime_during_run": produced_mtime,
        "artifact_sha256_8_before": sha256_8(original) if original is not None else None,
        "artifact_sha256_8_during_run": produced_sha,
        "artifact_restored": restored,
        "metrics": metrics,
        "metrics_sha256_8": fingerprint(metrics),
        "log_tail": {
            "stdout": stdout[-400:].strip(),
            "stderr": stderr[-400:].strip(),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="顺序跑六个 eval，产出 data/eval_out/manifest.json")
    ap.add_argument("--no-llm", action="store_true", help="确定性路径：ablation/hitl 不走 LLM，CI 用")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 条（只对支持 --limit 的脚本生效）")
    ap.add_argument("--timeout", type=int, default=1800, help="单个脚本超时秒数（默认 1800）")
    args = ap.parse_args()

    env = dict(os.environ)
    if args.no_llm:
        # 双保险：即使某个脚本内部还有「可选增强」的 LLM 调用，也让它走确定性分支
        env["LLM_API_KEY"] = ""
        env["DEEPSEEK_API_KEY"] = ""

    started = time.perf_counter()
    jobs = [run_job(job, args, env) for job in JOBS]
    total = time.perf_counter() - started

    failed = [j["name"] for j in jobs if j["status"] != "ok"]
    manifest = {
        "schema": "t08a-manifest/v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git": git_info(),
        "run": {
            "mode": "deterministic" if args.no_llm else "llm",
            "args": vars(args),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cwd": str(ROOT),
        },
        "jobs": jobs,
        "summary": {
            "n_jobs": len(jobs),
            "ok": len(jobs) - len(failed),
            "failed": failed,
            "all_ok": not failed,
            "total_duration_s": round(total, 2),
        },
    }
    write_json(MANIFEST, manifest)
    print(
        f"\nmanifest -> {MANIFEST.relative_to(ROOT)} | "
        f"{manifest['summary']['ok']}/{manifest['summary']['n_jobs']} ok | {total:.1f}s"
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
