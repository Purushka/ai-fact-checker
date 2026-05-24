"""完整 end-to-end runner — 直接用 production FactCheckPipeline 跑：
search (博查) → fetch → extract (DeepSeek) → verify (DeepSeek) → score → answer

用法：
    python scripts/run_e2e_full.py --case-id MA01
    python scripts/run_e2e_full.py --claim "2025年中国GDP突破140万亿元" --mode numeric
    python scripts/run_e2e_full.py --cases-file ../.claude/skills/fact-check/tests/test_cases_v2.jsonl --limit 5
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
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

from factcheck.pipeline import FactCheckPipeline  # noqa: E402
from factcheck.schemas import CheckOptions, CheckRequest  # noqa: E402


# 选自 v2 + v3 测试集的 10 个代表性 case，覆盖 8 类难点
CASES = {
    "MA01": ("中华人民共和国民营经济促进法于2025年5月20日起施行", "policy", "supported"),
    "MA02": ("2025年中国新能源汽车销量超过1300万辆", "numeric", "supported"),
    "MA04": ("2025年中国GDP突破140万亿元", "numeric", "supported"),
    "MC01": ("国家统计局核定2023年中国GDP最终数为126.06万亿元", "numeric", "contradicted"),
    "MD02": ("中华人民共和国公司法当前生效版本是2018年修正版", "policy", "outdated"),
    "MG01": ("当前中国证监会主席是易会满", "entity_status", "contradicted"),
    "MZ01": ("中华人民共和国宪法最近一次修正是2018年3月11日", "policy", "supported"),
    "CD01": ("国家医保目录2024年版调整后中成药品种数量增加了40余种", "numeric", "contradicted"),
    "CD06": ("国家金融监督管理总局是2023年新组建的金融监管机构", "entity_status", "supported"),
    "CD10": ("民营经济促进法于2025年5月20日施行，是改革开放以来首部专门规范民营经济的法律", "policy", "supported"),
}


async def run(case_id: str, claim: str, mode: str, predicted: str | None) -> dict:
    t0 = time.time()
    pipeline = FactCheckPipeline()
    req = CheckRequest(
        claim=claim,
        mode=mode,
        options=CheckOptions(max_sources=5, require_official_source=(mode == "policy"),
                             use_cache=False, return_answer=False),
    )
    try:
        result = await pipeline.run(req)
        match = (result.verdict == predicted) if predicted else None
        evidence_top = []
        for e in (result.evidence or [])[:3]:
            evidence_top.append({
                "url": e.source_url,
                "source_type": e.source_type,
                "authority": e.authority_weight,
                "support": e.support_level,
                "agency": e.agency,
                "snippet": (e.snippet or "")[:120],
            })
        return {
            "case_id": case_id,
            "claim": claim,
            "mode": mode,
            "predicted": predicted,
            "verdict": result.verdict,
            "confidence": result.confidence,
            "confidence_level": result.confidence_level,
            "has_official_source": result.has_official_source,
            "policy_status": result.policy_status,
            "match": match,
            "gating_applied": result.gating_applied,
            "warnings": result.warnings[:3],
            "evidence_count": len(result.evidence),
            "evidence_top": evidence_top,
            "conflicts_count": len(result.conflicts),
            "reasoning": result.reasoning_summary[:200],
            "token_usage": result.token_usage.model_dump() if result.token_usage else {},
            "elapsed_sec": round(time.time() - t0, 2),
        }
    except Exception as e:
        import traceback
        return {
            "case_id": case_id,
            "claim": claim,
            "error": str(e),
            "traceback": traceback.format_exc()[:1500],
            "elapsed_sec": round(time.time() - t0, 2),
        }


async def amain(cases: list[tuple], out_path: str | None) -> int:
    results = []
    for case_id, claim, mode, predicted in cases:
        print(f"\n--- {case_id} ---", file=sys.stderr)
        print(f"claim: {claim}", file=sys.stderr)
        print(f"predicted: {predicted}", file=sys.stderr)
        r = await run(case_id, claim, mode, predicted)
        results.append(r)
        if "error" in r:
            print(f"ERROR: {r['error']}", file=sys.stderr)
        else:
            print(f"verdict: {r['verdict']} conf={r['confidence']} match={r['match']}", file=sys.stderr)
            print(f"evidence: {r['evidence_count']}  token: {r['token_usage'].get('total_tokens',0)}  elapsed: {r['elapsed_sec']}s", file=sys.stderr)

    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果写入: {out_path}", file=sys.stderr)

    ok = sum(1 for r in results if r.get("match") is True)
    n_with = sum(1 for r in results if r.get("match") is not None)
    total_t = sum(r.get("token_usage", {}).get("total_tokens", 0) for r in results)
    total_search = sum(r.get("token_usage", {}).get("search_calls", 0) for r in results)
    total_el = sum(r.get("elapsed_sec", 0) for r in results)
    print(f"\n=== 汇总 ===  匹配 {ok}/{n_with}  总 token {total_t}  总 search {total_search}  总耗时 {total_el:.0f}s", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--claim")
    ap.add_argument("--mode", default="general")
    ap.add_argument("--out", default="reports/e2e_full.json")
    args = ap.parse_args()

    if args.claim:
        cases = [("ADHOC", args.claim, args.mode, None)]
    elif args.all:
        cases = [(cid, claim, mode, pred) for cid, (claim, mode, pred) in CASES.items()]
    elif args.case_id:
        cases = [(cid, *CASES[cid]) for cid in args.case_id if cid in CASES]
    else:
        ap.print_help()
        return 2
    return asyncio.run(amain(cases, args.out))


if __name__ == "__main__":
    sys.exit(main())
