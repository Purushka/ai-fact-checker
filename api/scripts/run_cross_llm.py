"""跨 LLM 一致性测试 — 同一 case 在多个国内模型上跑，对比 verdict 是否一致。

用 OpenRouter 统一接入：DeepSeek / Qwen / Kimi / GLM 等。

用法：
    python scripts/run_cross_llm.py --case-id MZ01
    python scripts/run_cross_llm.py --all
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

env_path = ROOT / ".env"
if env_path.exists():
    import os
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

import factcheck.pipeline as pmod  # noqa: E402
from factcheck.llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from factcheck.pipeline import FactCheckPipeline  # noqa: E402
from factcheck.schemas import CheckOptions, CheckRequest  # noqa: E402

CASES = {
    "MA01": ("中华人民共和国民营经济促进法于2025年5月20日起施行", "policy", "supported"),
    "MA04": ("2025年中国GDP突破140万亿元", "numeric", "supported"),
    "MD02": ("中华人民共和国公司法当前生效版本是2018年修正版", "policy", "outdated"),
    "MZ01": ("中华人民共和国宪法最近一次修正是2018年3月11日", "policy", "supported"),
    "CD01": ("国家医保目录2024年版调整后中成药品种数量增加了40余种", "numeric", "contradicted"),
    "CD10": ("民营经济促进法于2025年5月20日施行，是改革开放以来首部专门规范民营经济的法律", "policy", "supported"),
}

MODELS = {
    "deepseek-chat": "deepseek/deepseek-chat",
    "qwen-2.5-72b": "qwen/qwen-2.5-72b-instruct",
    "kimi-k2":      "moonshotai/kimi-k2",
    "qwen3-235b":   "qwen/qwen3-235b-a22b",
}


def _build_provider(model_id: str) -> OpenAICompatibleProvider:
    import os
    key = os.environ.get("OPENROUTER_API_KEY") or ""
    base = os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
    return OpenAICompatibleProvider(name=f"openrouter:{model_id}", base_url=base, api_key=key, default_model=model_id)


async def run_one(case_id: str, claim: str, mode: str, predicted: str, model_key: str, model_id: str) -> dict:
    t0 = time.time()
    provider = _build_provider(model_id)
    original_get = pmod.get_provider
    pmod.get_provider = lambda name=None: provider
    try:
        pipeline = FactCheckPipeline()
        req = CheckRequest(
            claim=claim, mode=mode,
            options=CheckOptions(max_sources=4, require_official_source=(mode == "policy"),
                                  use_cache=False, return_answer=False),
        )
        result = await pipeline.run(req)
        return {
            "case_id": case_id, "model": model_key, "model_id": model_id,
            "predicted": predicted,
            "verdict": result.verdict, "confidence": result.confidence,
            "has_official_source": result.has_official_source,
            "policy_status": result.policy_status,
            "match": result.verdict == predicted,
            "evidence_count": len(result.evidence),
            "tokens": result.token_usage.total_tokens if result.token_usage else 0,
            "elapsed": round(time.time() - t0, 2),
            "reasoning": (result.reasoning_summary or "")[:150],
        }
    except Exception as e:
        return {"case_id": case_id, "model": model_key, "error": str(e)[:300],
                "elapsed": round(time.time() - t0, 2)}
    finally:
        pmod.get_provider = original_get


async def amain(case_ids: list[str], out_path: str) -> int:
    all_results = []
    for case_id in case_ids:
        case = CASES.get(case_id)
        if not case:
            continue
        claim, mode, predicted = case
        print(f"\n=== {case_id} ({mode}) ===  predicted={predicted}", file=sys.stderr)
        print(f"  claim: {claim}", file=sys.stderr)
        for model_key, model_id in MODELS.items():
            print(f"  → {model_key} ...", file=sys.stderr, flush=True)
            r = await run_one(case_id, claim, mode, predicted, model_key, model_id)
            all_results.append(r)
            if "error" in r:
                print(f"    ✗ ERROR: {r['error'][:100]}", file=sys.stderr)
            else:
                mark = "✓" if r["match"] else "✗"
                print(f"    {mark} verdict={r['verdict']} conf={r['confidence']} tokens={r['tokens']} elapsed={r['elapsed']}s", file=sys.stderr)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写入: {out_path}", file=sys.stderr)

    print("\n=== 跨 LLM 矩阵 ===", file=sys.stderr)
    print(f"{'Case':8} {'Predicted':18}", end="", file=sys.stderr)
    for mk in MODELS:
        print(f" {mk:14}", end="", file=sys.stderr)
    print(" | 一致?", file=sys.stderr)
    print("-" * 110, file=sys.stderr)
    for case_id in case_ids:
        if case_id not in CASES:
            continue
        row = [r for r in all_results if r.get("case_id") == case_id]
        verdicts = {r["model"]: r.get("verdict", "ERR") for r in row}
        all_same = len({v for v in verdicts.values() if v != "ERR"}) == 1
        all_match_pred = all(r.get("match") for r in row)
        print(f"{case_id:8} {CASES[case_id][2]:18}", end="", file=sys.stderr)
        for mk in MODELS:
            v = verdicts.get(mk, "ERR")[:14]
            print(f" {v:14}", end="", file=sys.stderr)
        mark = "✓全一致" if all_same and all_match_pred else ("⚠分歧" if not all_same else "全错")
        print(f" | {mark}", file=sys.stderr)

    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="reports/cross_llm.json")
    args = ap.parse_args()
    cases = list(CASES.keys()) if args.all else (args.case_id or [])
    if not cases:
        ap.print_help()
        return 2
    return asyncio.run(amain(cases, args.out))


if __name__ == "__main__":
    sys.exit(main())
