"""1.3 — 用完整 pipeline 跑 Case A (P019)，逐 step 记录 tokens/cost/latency。

测试 claim："新标准要求电动自行车安装金属鞍座"（ground_truth: contradicted）

Pipeline 7 步：
  1. SubjectiveDetector（短路检查）
  2. QueryPlanner（LLM 生成搜索 query）
  3. SearchOrchestrator（多 provider 并发搜索）
  4. FetchOrchestrator（并发抓页面）
  5. FactExtractor（LLM 抽证据）
  6. CrossValidator（LLM 交叉验证）
  7. ScoreEngine（评分 + 模式判别）

记录每步：tokens、latency、API cost（估算）
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

# DeepSeek 定价：input $0.07/1M tokens, output $1.10/1M tokens（chat 模型 2025 价）
# 用 CNY: input ¥0.5/1M, output ¥8.0/1M
PRICE_INPUT_PER_1M = 0.5  # CNY
PRICE_OUTPUT_PER_1M = 8.0  # CNY

# 搜索定价（估算）
PRICE_BOCHA_PER_CALL = 0.05  # CNY
PRICE_METASO_PER_CALL = 0.05  # CNY
PRICE_ANYSEARCH_PER_CALL = 0.02  # CNY


def _llm_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens / 1_000_000) * PRICE_INPUT_PER_1M + (
        completion_tokens / 1_000_000
    ) * PRICE_OUTPUT_PER_1M


async def run_one_with_timing() -> dict:
    from factcheck.config import get_settings
    from factcheck.pipeline import FactCheckPipeline
    from factcheck.schemas import CheckOptions, CheckRequest

    s = get_settings()
    print(f"\n[config] LLM provider: {s.llm_default_provider} / model: {s.llm_default_model}")
    print(f"[config] search providers: primary={s.search_primary}, secondary={s.search_secondary}, fallback={s.search_fallback}")

    req = CheckRequest(
        claim="新标准要求电动自行车安装金属鞍座",
        mode="entity_status",
        options=CheckOptions(use_cache=False, max_sources=8, return_answer=True),
    )

    pipeline = await FactCheckPipeline.create()

    print("\n[run] starting pipeline...")
    t0 = time.time()
    resp = await pipeline.run(req)
    total_elapsed = time.time() - t0

    return {
        "claim": req.claim,
        "total_latency_s": round(total_elapsed, 2),
        "verdict": resp.verdict,
        "confidence": resp.confidence,
        "evidence_count": len(resp.evidence),
        "token_usage": resp.token_usage.model_dump() if resp.token_usage else None,
        "response": resp.model_dump(),
    }


def _summarize(result: dict) -> None:
    print("\n" + "=" * 70)
    print("  cost baseline — P019")
    print("=" * 70)
    print(f"claim: {result['claim']}")
    print(f"verdict: {result['verdict']}  (confidence={result['confidence']})")
    print(f"total latency: {result['total_latency_s']}s")
    print(f"evidence: {result['evidence_count']} pieces")

    tu = result.get("token_usage")
    if tu:
        print("\nToken usage:")
        print(f"  provider:         {tu.get('provider')}")
        print(f"  model:            {tu.get('model')}")
        print(f"  prompt_tokens:    {tu.get('prompt_tokens')}")
        print(f"  completion_tokens:{tu.get('completion_tokens')}")
        print(f"  total_tokens:     {tu.get('total_tokens')}")
        print(f"  search_calls:     {tu.get('search_calls')}")
        print(f"  fetch_calls:      {tu.get('fetch_calls')}")

        llm_cost = _llm_cost(tu.get("prompt_tokens", 0), tu.get("completion_tokens", 0))
        # 假设 search call 分摊到 3 个 provider
        search_calls = tu.get("search_calls", 0)
        search_cost = search_calls * (PRICE_BOCHA_PER_CALL + PRICE_METASO_PER_CALL + PRICE_ANYSEARCH_PER_CALL) / 3
        total_cost = llm_cost + search_cost
        print("\nCost (CNY estimate):")
        print(f"  LLM cost:          ¥{llm_cost:.6f}  (input ¥{PRICE_INPUT_PER_1M}/1M + output ¥{PRICE_OUTPUT_PER_1M}/1M)")
        print(f"  Search cost:       ¥{search_cost:.6f}  ({search_calls} calls × avg ¥0.04)")
        print(f"  TOTAL per claim:   ¥{total_cost:.6f}")
        print(f"  TOTAL per 1k:      ¥{total_cost * 1000:.2f}")


async def main() -> None:
    result = await run_one_with_timing()
    _summarize(result)

    out_path = Path(__file__).parent / "cost_baseline.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nfull response → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
