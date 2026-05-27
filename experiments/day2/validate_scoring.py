"""2.3 — 评分引擎对比人工判断（3 个真实 piyao case）。

跑完整 pipeline 对 P019/P015/P021 三个 case：
  人工 = piyao 给出的 ground_truth (3 个全部 contradicted)
  系统 = pipeline 给出的 verdict + confidence + breakdown

记录差异 + 失败模式。
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / "api" / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "src"))


CASES = [
    {
        "id": "P019",
        "claim": "新标准要求电动自行车安装金属鞍座",
        "ground_truth": "contradicted",
        "category": "policy_standard",
        "human_note": "实际新标准只要求座椅防火阻燃，未要求金属",
    },
    {
        "id": "P015",
        "claim": "喝纯净水容易缺乏微量元素",
        "ground_truth": "contradicted",
        "category": "health",
        "human_note": "人体微量元素主要来自饮食，纯净水不影响吸收",
    },
    {
        "id": "P021",
        "claim": "广州街头草皮被人工染绿",
        "ground_truth": "contradicted",
        "category": "social",
        "human_note": "实际是常规园林养护药剂，非染色",
    },
]


def _verdict_match(human: str, system: str) -> str:
    if human == system:
        return "MATCH"
    # 部分接近：contradicted 系统给 partially_supported / unverifiable
    if human == "contradicted":
        if system in ("unverifiable", "partially_supported"):
            return "PARTIAL"
        if system == "supported":
            return "MISS_OPPOSITE"  # 严重错误
    return "DIFFER"


async def run_one(case: dict) -> dict:
    from factcheck.pipeline import FactCheckPipeline
    from factcheck.schemas import CheckOptions, CheckRequest

    print(f"\n--- Running {case['id']}: {case['claim'][:40]} ---")
    req = CheckRequest(
        claim=case["claim"],
        mode="entity_status",
        options=CheckOptions(use_cache=False, max_sources=8, return_answer=False),
    )
    pipeline = await FactCheckPipeline.create()
    t0 = time.time()
    resp = await pipeline.run(req)
    elapsed = time.time() - t0

    match = _verdict_match(case["ground_truth"], resp.verdict)
    print(f"  human verdict:  {case['ground_truth']}")
    print(f"  system verdict: {resp.verdict}  (conf={resp.confidence}, level={resp.confidence_level})")
    print(f"  match status:   {match}")
    print(f"  evidence count: {len(resp.evidence)}")
    print(f"  latency:        {elapsed:.1f}s")

    pt = resp.propagation_timeline
    if pt:
        print(f"  propagation:    pattern={pt.pattern}  conf={pt.confidence}  n_stops={pt.n_stops}")
    return {
        "case_id": case["id"],
        "claim": case["claim"],
        "human_verdict": case["ground_truth"],
        "human_note": case["human_note"],
        "system_verdict": resp.verdict,
        "system_confidence": resp.confidence,
        "system_confidence_level": resp.confidence_level,
        "match": match,
        "evidence_count": len(resp.evidence),
        "has_official_source": resp.has_official_source,
        "reasoning_summary": resp.reasoning_summary,
        "warnings": list(resp.warnings),
        "latency_s": round(elapsed, 2),
        "propagation_pattern": pt.pattern if pt else None,
        "propagation_confidence": pt.confidence if pt else None,
        "token_usage": resp.token_usage.model_dump() if resp.token_usage else None,
    }


async def main() -> None:
    print("=" * 70)
    print("  2.3 评分引擎 vs 人工判断（3 piyao case）")
    print("=" * 70)

    results = []
    for c in CASES:
        r = await run_one(c)
        results.append(r)

    print("\n\n" + "=" * 70)
    print("  汇总")
    print("=" * 70)
    print(f"{'case':<6} {'human':<15} {'system':<22} {'conf':<6} {'match':<14} {'latency':<8}")
    for r in results:
        print(
            f"{r['case_id']:<6} {r['human_verdict']:<15} {r['system_verdict']:<22} "
            f"{r['system_confidence']:<6} {r['match']:<14} {r['latency_s']}s"
        )

    n_match = sum(1 for r in results if r["match"] == "MATCH")
    n_partial = sum(1 for r in results if r["match"] == "PARTIAL")
    n_miss = sum(1 for r in results if r["match"] == "MISS_OPPOSITE")
    n_differ = sum(1 for r in results if r["match"] == "DIFFER")
    print(f"\n  MATCH: {n_match}/3, PARTIAL: {n_partial}/3, MISS_OPPOSITE: {n_miss}/3, DIFFER: {n_differ}/3")

    out_path = Path(__file__).parent / "scoring_validation.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
