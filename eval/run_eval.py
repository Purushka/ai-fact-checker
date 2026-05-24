"""自动化评估管道 — bench × system，输出 precision/recall/F1 + 延迟 + token + 成本。

用法：
    # mock 模式（用之前 e2e 跑过的 cache，不消耗 API）
    python eval/run_eval.py --mock

    # live 模式（真跑 pipeline，消耗 token + search quota）
    python eval/run_eval.py --live --limit 50

    # 自定义 bench
    python eval/run_eval.py --bench eval/data/benchmark_cn.jsonl --live
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api" / "src"))

env_path = ROOT / "api" / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def load_bench(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out


def normalize_verdict(v: str) -> str:
    """将 verdict 归一化用于 GT 比较。"""
    if v in ("contradicted", "refuted"):
        return "contradicted"
    return v


def compute_metrics(results: list[dict]) -> dict:
    """计算 per-class + overall precision/recall/F1。

    Verdict 是多分类（supported/contradicted/outdated/unverifiable/partially_supported/conflicting/out_of_scope）。
    Macro-averaged F1 报告 + per-class confusion。
    """
    classes = [
        "supported",
        "contradicted",
        "outdated",
        "unverifiable",
        "partially_supported",
    ]
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    n_total = 0
    n_correct = 0
    latencies = []
    tokens = []

    for r in results:
        gt = normalize_verdict(r["ground_truth"])
        pred = normalize_verdict(r.get("predicted_verdict", "unknown"))
        n_total += 1
        if gt == pred:
            n_correct += 1
            tp[gt] += 1
        else:
            fp[pred] += 1
            fn[gt] += 1
        if r.get("elapsed") is not None:
            latencies.append(r["elapsed"])
        if r.get("tokens") is not None:
            tokens.append(r["tokens"])

    per_class = {}
    for cls in classes:
        p = tp[cls] / max(1, tp[cls] + fp[cls])
        rec = tp[cls] / max(1, tp[cls] + fn[cls])
        f1 = 2 * p * rec / max(1e-9, p + rec)
        per_class[cls] = {
            "support": tp[cls] + fn[cls],
            "precision": round(p, 3),
            "recall": round(rec, 3),
            "f1": round(f1, 3),
        }
    macro_f1 = round(sum(c["f1"] for c in per_class.values()) / max(1, len(classes)), 3)

    # 成本估算（DeepSeek deepseek-chat: ~$0.27/M input + $1.10/M output ≈ $0.7/M 平均 ≈ ¥5/M）
    # 加上搜索 ~¥0.04/case 平均
    avg_tokens = sum(tokens) / max(1, len(tokens))
    avg_latency = sum(latencies) / max(1, len(latencies))
    cost_token_yuan = avg_tokens * 5 / 1_000_000
    cost_search_yuan = 0.04
    avg_cost_yuan = round(cost_token_yuan + cost_search_yuan, 4)

    return {
        "n_total": n_total,
        "n_correct": n_correct,
        "accuracy": round(n_correct / max(1, n_total), 3),
        "macro_f1": macro_f1,
        "per_class": per_class,
        "avg_latency_sec": round(avg_latency, 2),
        "avg_tokens": int(avg_tokens),
        "avg_cost_yuan": avg_cost_yuan,
    }


def per_category_breakdown(results: list[dict]) -> dict:
    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        cat = r.get("category", "other")
        by_cat[cat].append(r)
    out = {}
    for cat, rs in by_cat.items():
        n = len(rs)
        n_correct = sum(
            1
            for r in rs
            if normalize_verdict(r["ground_truth"])
            == normalize_verdict(r.get("predicted_verdict", ""))
        )
        out[cat] = {
            "n": n,
            "correct": n_correct,
            "accuracy": round(n_correct / max(1, n), 3),
        }
    return out


async def run_live(bench: list[dict], limit: int | None) -> list[dict]:
    from factcheck.pipeline import FactCheckPipeline
    from factcheck.schemas import CheckOptions, CheckRequest

    pipeline = FactCheckPipeline()
    out: list[dict] = []
    cases = bench[:limit] if limit else bench
    for i, c in enumerate(cases, 1):
        print(f"  [{i}/{len(cases)}] {c['id']} GT={c['ground_truth']}", file=sys.stderr)
        t0 = time.time()
        req = CheckRequest(
            claim=c["claim"],
            mode="policy" if c.get("category") == "policy" else "general",
            options=CheckOptions(max_sources=4, use_cache=False, return_answer=False),
        )
        try:
            result = await pipeline.run(req)
            out.append(
                {
                    **c,
                    "predicted_verdict": result.verdict,
                    "predicted_confidence": result.confidence,
                    "predicted_ci": (
                        [result.confidence_interval.lo, result.confidence_interval.hi]
                        if result.confidence_interval
                        else None
                    ),
                    "elapsed": round(time.time() - t0, 2),
                    "tokens": (
                        result.token_usage.total_tokens if result.token_usage else 0
                    ),
                    "search_calls": (
                        result.token_usage.search_calls if result.token_usage else 0
                    ),
                }
            )
        except Exception as e:
            out.append({**c, "predicted_verdict": "ERROR", "error": str(e)[:200]})
        print(
            f"    → {out[-1].get('predicted_verdict')} (conf={out[-1].get('predicted_confidence')})",
            file=sys.stderr,
        )
    return out


def run_mock(bench: list[dict], cached_results_path: Path | None) -> list[dict]:
    """从已有 e2e 跑测结果 JSON 复用，bypass API 调用。"""
    if not cached_results_path or not cached_results_path.exists():
        print(
            "  mock 缺 cache，fallback 用 bench 的 GT 当 predicted（仅 schema 测试）",
            file=sys.stderr,
        )
        return [
            {**c, "predicted_verdict": c["ground_truth"], "elapsed": 0.0, "tokens": 0}
            for c in bench
        ]
    cached = json.loads(cached_results_path.read_text(encoding="utf-8"))
    by_claim = {r.get("claim"): r for r in cached if isinstance(r, dict)}
    out = []
    for c in bench:
        cr = by_claim.get(c["claim"])
        if cr:
            sys_r = cr.get("system", {})
            out.append(
                {
                    **c,
                    "predicted_verdict": sys_r.get("verdict", "unknown"),
                    "predicted_confidence": sys_r.get("confidence", 0),
                    "elapsed": sys_r.get("elapsed", 0),
                    "tokens": sys_r.get("tokens", 0),
                }
            )
        else:
            out.append(
                {**c, "predicted_verdict": "NOT_IN_CACHE", "elapsed": 0, "tokens": 0}
            )
    return out


def write_md_report(report: dict, out_path: Path) -> None:
    m = report["metrics"]
    lines = [
        f"# Eval Report — {report['run_id']}",
        "",
        f"- **Bench**: {report['bench_path']}  (n={m['n_total']})",
        f"- **Mode**: {report['mode']}",
        f"- **Generated**: {report['generated_at']}",
        "",
        "## Overall",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Accuracy | {m['accuracy']*100:.1f}% ({m['n_correct']}/{m['n_total']}) |",
        f"| Macro F1 | {m['macro_f1']} |",
        f"| Avg latency | {m['avg_latency_sec']}s |",
        f"| Avg tokens | {m['avg_tokens']} |",
        f"| Avg cost (LLM+search) | ¥{m['avg_cost_yuan']} |",
        "",
        "## Per-class metrics",
        "",
        "| Class | Support | Precision | Recall | F1 |",
        "|---|---:|---:|---:|---:|",
    ]
    for cls, v in m["per_class"].items():
        lines.append(
            f"| {cls} | {v['support']} | {v['precision']} | {v['recall']} | {v['f1']} |"
        )

    lines.extend(
        [
            "",
            "## Per-category accuracy",
            "",
            "| Category | n | Correct | Accuracy |",
            "|---|---:|---:|---:|",
        ]
    )
    for cat, v in report["by_category"].items():
        lines.append(
            f"| {cat} | {v['n']} | {v['correct']} | {v['accuracy']*100:.1f}% |"
        )
    out_path.write_text("\n".join(lines), encoding="utf-8")


async def amain(args: argparse.Namespace) -> int:
    bench_path = Path(args.bench)
    if not bench_path.is_absolute():
        bench_path = ROOT / bench_path
    bench = load_bench(bench_path)
    print(f"加载 {len(bench)} bench cases from {bench_path.name}", file=sys.stderr)

    if args.live:
        print("LIVE 模式：调用真 pipeline", file=sys.stderr)
        results = await run_live(bench, args.limit)
    else:
        print("MOCK 模式：复用已有 cache 结果", file=sys.stderr)
        cache_path = (
            Path(args.cache)
            if args.cache
            else ROOT / "api" / "reports" / "significance_n200.json"
        )
        if not cache_path.is_absolute():
            cache_path = ROOT / cache_path
        results = run_mock(bench, cache_path)

    metrics = compute_metrics(results)
    by_cat = per_category_breakdown(results)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = {
        "run_id": run_id,
        "mode": "live" if args.live else "mock",
        "bench_path": str(
            bench_path.relative_to(ROOT) if bench_path.is_absolute() else bench_path
        ),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "metrics": metrics,
        "by_category": by_cat,
        "results": results if args.dump_results else None,
    }
    reports_dir = ROOT / "eval" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_out = reports_dir / f"eval_{run_id}.json"
    md_out = reports_dir / f"eval_{run_id}.md"
    json_out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_md_report(report, md_out)

    print("\n=== Eval ===", file=sys.stderr)
    print(
        f"  Accuracy: {metrics['accuracy']*100:.1f}% ({metrics['n_correct']}/{metrics['n_total']})",
        file=sys.stderr,
    )
    print(f"  Macro F1: {metrics['macro_f1']}", file=sys.stderr)
    print(
        f"  Avg latency: {metrics['avg_latency_sec']}s  tokens: {metrics['avg_tokens']}  cost: ¥{metrics['avg_cost_yuan']}",
        file=sys.stderr,
    )
    print(f"\n报告写入: {json_out}\n            {md_out}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bench", default="eval/data/benchmark_cn.jsonl")
    ap.add_argument("--live", action="store_true", help="真跑 pipeline，消耗 API")
    ap.add_argument("--mock", action="store_true", help="用缓存（默认）")
    ap.add_argument("--cache", help="cache 文件路径（mock 模式用）")
    ap.add_argument("--limit", type=int)
    ap.add_argument(
        "--dump-results", action="store_true", help="把 results 详情写入 JSON"
    )
    args = ap.parse_args()
    if not args.live:
        args.mock = True
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
