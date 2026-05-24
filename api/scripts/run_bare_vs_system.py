"""裸 LLM vs 系统对比 — 同模型同 claim，区别只在 search + score + prompt 工程

裸 LLM：直接问 DeepSeek "这个 claim 你判什么？"，仅训练知识，无搜索无评分
系统  ：完整 pipeline (Bocha search → fetch → DeepSeek extract → cross_validate → score → verdict)

用法：
    python scripts/run_bare_vs_system.py --all
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

from factcheck.llm.base import LLMMessage  # noqa: E402
from factcheck.llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from factcheck.pipeline import FactCheckPipeline  # noqa: E402
from factcheck.schemas import CheckOptions, CheckRequest  # noqa: E402
from factcheck.utils.json_parse import parse_json_lenient  # noqa: E402

CASES = {
    "MA01": ("中华人民共和国民营经济促进法于2025年5月20日起施行", "policy", "supported"),
    "MA04": ("2025年中国GDP突破140万亿元", "numeric", "supported"),
    "MC01": ("国家统计局核定2023年中国GDP最终数为126.06万亿元", "numeric", "contradicted"),
    "MD02": ("中华人民共和国公司法当前生效版本是2018年修正版", "policy", "outdated"),
    "MG01": ("当前中国证监会主席是易会满", "entity_status", "contradicted"),
    "MZ01": ("中华人民共和国宪法最近一次修正是2018年3月11日", "policy", "supported"),
    "CD01": ("国家医保目录2024年版调整后中成药品种数量增加了40余种", "numeric", "contradicted"),
    "CD06": ("国家金融监督管理总局是2023年新组建的金融监管机构", "entity_status", "supported"),
    "CD10": ("民营经济促进法于2025年5月20日施行，是改革开放以来首部专门规范民营经济的法律", "policy", "supported"),
}

BARE_SYSTEM_PROMPT = """\
你是事实核查助手。给定一个事实声明，判断它的可信度。
你只能使用你的训练知识，**不能搜索网络**。
返回 JSON：
{
  "verdict": "supported | contradicted | outdated | unverifiable | out_of_scope",
  "confidence": 0-100,
  "reasoning": "一两句话说明判断依据"
}
- supported：声明完全成立
- contradicted：声明与事实相反
- outdated：声明描述的状态/版本已过期/被替代
- unverifiable：你没有足够把握判断
- out_of_scope：主观评价或模糊声明

只返回 JSON。"""


def _build_deepseek() -> OpenAICompatibleProvider:
    import os
    return OpenAICompatibleProvider(
        name="deepseek-bare",
        base_url="https://api.deepseek.com/v1",
        api_key=os.environ["DEEPSEEK_API_KEY"],
        default_model="deepseek-chat",
    )


async def run_bare(claim: str) -> dict:
    t0 = time.time()
    provider = _build_deepseek()
    try:
        r = await provider.chat(
            [LLMMessage(role="system", content=BARE_SYSTEM_PROMPT),
             LLMMessage(role="user", content=f"声明：{claim}")],
            temperature=0.0, max_tokens=500, timeout=30,
        )
        parsed = parse_json_lenient(r.text) or {}
        return {
            "verdict": parsed.get("verdict", "unknown"),
            "confidence": int(parsed.get("confidence", 0)),
            "reasoning": parsed.get("reasoning", "")[:200],
            "tokens": r.total_tokens,
            "elapsed": round(time.time() - t0, 2),
        }
    except Exception as e:
        return {"verdict": "ERROR", "error": str(e)[:200], "elapsed": round(time.time() - t0, 2)}


async def run_system(claim: str, mode: str) -> dict:
    t0 = time.time()
    pipeline = FactCheckPipeline()
    req = CheckRequest(
        claim=claim, mode=mode,
        options=CheckOptions(max_sources=4, require_official_source=(mode == "policy"),
                              use_cache=False, return_answer=False),
    )
    try:
        result = await pipeline.run(req)
        return {
            "verdict": result.verdict,
            "confidence": result.confidence,
            "has_official": result.has_official_source,
            "evidence_count": len(result.evidence),
            "policy_status": result.policy_status,
            "tokens": result.token_usage.total_tokens if result.token_usage else 0,
            "search_calls": result.token_usage.search_calls if result.token_usage else 0,
            "elapsed": round(time.time() - t0, 2),
            "reasoning": (result.reasoning_summary or "")[:200],
            "warnings": result.warnings[:2],
        }
    except Exception as e:
        return {"verdict": "ERROR", "error": str(e)[:200], "elapsed": round(time.time() - t0, 2)}


async def amain(case_ids: list[str], out_path: str) -> int:
    rows = []
    for case_id in case_ids:
        case = CASES.get(case_id)
        if not case:
            continue
        claim, mode, gt = case
        print(f"\n=== {case_id} ({mode})  GT={gt} ===", file=sys.stderr)
        print(f"  claim: {claim}", file=sys.stderr)

        print(f"  → 裸 DeepSeek ...", file=sys.stderr, flush=True)
        bare = await run_bare(claim)
        bare_match = bare.get("verdict") == gt
        print(f"    verdict={bare.get('verdict')} conf={bare.get('confidence')} match={bare_match} t={bare['elapsed']}s", file=sys.stderr)

        print(f"  → 系统 (Bocha + DeepSeek) ...", file=sys.stderr, flush=True)
        sys_r = await run_system(claim, mode)
        sys_match = sys_r.get("verdict") == gt
        print(f"    verdict={sys_r.get('verdict')} conf={sys_r.get('confidence')} match={sys_match} t={sys_r['elapsed']}s", file=sys.stderr)

        rows.append({
            "case_id": case_id, "claim": claim, "mode": mode, "ground_truth": gt,
            "bare": bare, "bare_match": bare_match,
            "system": sys_r, "system_match": sys_match,
            "skill_added_value": (sys_match and not bare_match),
            "skill_regression": (bare_match and not sys_match),
        })

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果: {out_path}", file=sys.stderr)

    n = len(rows)
    bare_ok = sum(1 for r in rows if r["bare_match"])
    sys_ok = sum(1 for r in rows if r["system_match"])
    a_right_b_wrong = sum(1 for r in rows if r["skill_added_value"])
    a_wrong_b_right = sum(1 for r in rows if r["skill_regression"])
    bare_tokens = sum(r["bare"].get("tokens", 0) for r in rows)
    sys_tokens = sum(r["system"].get("tokens", 0) for r in rows)
    bare_time = sum(r["bare"].get("elapsed", 0) for r in rows)
    sys_time = sum(r["system"].get("elapsed", 0) for r in rows)
    print(f"\n=== 汇总 ({n} cases) ===", file=sys.stderr)
    print(f"  裸 DeepSeek 准确率:  {bare_ok}/{n} = {bare_ok/n*100:.0f}%   token={bare_tokens}  time={bare_time:.0f}s", file=sys.stderr)
    print(f"  系统准确率:          {sys_ok}/{n} = {sys_ok/n*100:.0f}%   token={sys_tokens}  time={sys_time:.0f}s", file=sys.stderr)
    print(f"  差距:                +{(sys_ok-bare_ok)/n*100:.0f}pp", file=sys.stderr)
    print(f"  系统增量价值 case:   {a_right_b_wrong}", file=sys.stderr)
    print(f"  系统回归 case:       {a_wrong_b_right}", file=sys.stderr)
    print(f"  系统 token / bare:   {sys_tokens/max(1,bare_tokens):.1f}x", file=sys.stderr)
    print(f"  系统耗时 / bare:     {sys_time/max(1,bare_time):.1f}x", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="reports/bare_vs_system.json")
    args = ap.parse_args()
    cases = list(CASES.keys()) if args.all else (args.case_id or [])
    if not cases:
        ap.print_help()
        return 2
    return asyncio.run(amain(cases, args.out))


if __name__ == "__main__":
    sys.exit(main())
