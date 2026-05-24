"""200 case 大规模显著性 + Calibration 实验。

每个 case 跑 bare + system，记录：
- verdict / confidence / confidence_interval / has_official_source / policy_status
- evidence URLs（用于溯源验证）

之后用 hybrid_decision 推导 hybrid verdict。
增量保存，容许中断重启。
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
sys.path.insert(0, str(Path(__file__).resolve().parent))

env_path = ROOT / ".env"
if env_path.exists():
    import os
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from run_significance_test import BARE_SYSTEM_PROMPT, load_cases  # noqa: E402
from run_hybrid_significance import hybrid_decision  # noqa: E402

from factcheck.llm.base import LLMMessage  # noqa: E402
from factcheck.llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from factcheck.pipeline import FactCheckPipeline  # noqa: E402
from factcheck.schemas import CheckOptions, CheckRequest  # noqa: E402
from factcheck.utils.json_parse import parse_json_lenient  # noqa: E402


def _build_bare() -> OpenAICompatibleProvider:
    import os
    return OpenAICompatibleProvider(
        name="deepseek-bare",
        base_url="https://api.deepseek.com/v1",
        api_key=os.environ["DEEPSEEK_API_KEY"],
        default_model="deepseek-chat",
    )


async def run_bare(claim: str) -> dict:
    t0 = time.time()
    p = _build_bare()
    try:
        r = await p.chat(
            [LLMMessage(role="system", content=BARE_SYSTEM_PROMPT),
             LLMMessage(role="user", content=f"声明：{claim}")],
            temperature=0.0, max_tokens=400, timeout=30,
        )
        parsed = parse_json_lenient(r.text) or {}
        return {
            "verdict": parsed.get("verdict", "unknown"),
            "confidence": int(parsed.get("confidence", 0) or 0),
            "reasoning": (parsed.get("reasoning") or "")[:120],
            "tokens": r.total_tokens,
            "elapsed": round(time.time() - t0, 2),
        }
    except Exception as e:
        return {"verdict": "ERROR", "error": str(e)[:200]}


async def run_system_full(claim: str, mode: str) -> dict:
    t0 = time.time()
    pipeline = FactCheckPipeline()
    req = CheckRequest(
        claim=claim, mode=mode,
        options=CheckOptions(max_sources=4, require_official_source=(mode == "policy"),
                              use_cache=False, return_answer=False),
    )
    try:
        result = await pipeline.run(req)
        ci = result.confidence_interval
        return {
            "verdict": result.verdict,
            "confidence": result.confidence,
            "ci_lo": ci.lo if ci else None,
            "ci_hi": ci.hi if ci else None,
            "has_official": result.has_official_source,
            "policy_status": result.policy_status,
            "evidence_count": len(result.evidence),
            "evidence_urls": [e.source_url for e in result.evidence][:5],
            "current_version": result.policy.current_version.model_dump() if result.policy and result.policy.current_version else None,
            "previous_versions": [v.model_dump() for v in result.policy.previous_versions] if result.policy and result.policy.previous_versions else [],
            "tokens": result.token_usage.total_tokens if result.token_usage else 0,
            "elapsed": round(time.time() - t0, 2),
        }
    except Exception as e:
        return {"verdict": "ERROR", "error": str(e)[:200], "elapsed": round(time.time() - t0, 2)}


def is_match(v: str | None, gt: str) -> bool:
    if not v or v == "ERROR":
        return False
    return v == gt


async def amain(cases_path: Path, out_path: Path, resume: bool = True) -> int:
    cases = load_cases(cases_path)
    print(f"加载 {len(cases)} cases", file=sys.stderr)

    # 增量加载已跑结果
    existing: dict[str, dict] = {}
    if resume and out_path.exists():
        try:
            for r in json.loads(out_path.read_text(encoding="utf-8")):
                existing[r["case_id"]] = r
            print(f"  恢复已跑 {len(existing)} cases", file=sys.stderr)
        except Exception:
            pass

    results = []
    for i, c in enumerate(cases, 1):
        cid = c["id"]
        if cid in existing:
            results.append(existing[cid])
            continue
        claim = c["claim"]
        mode = c.get("mode", "general")
        gt = c["ground_truth"]

        print(f"\n[{i}/{len(cases)}] {cid} GT={gt}", file=sys.stderr)
        print(f"  claim: {claim[:80]}", file=sys.stderr)

        bare = await run_bare(claim)
        sys_r = await run_system_full(claim, mode)
        # 用 run_hybrid 的 hybrid_decision
        hybrid = hybrid_decision(bare, sys_r)

        bm = is_match(bare.get("verdict"), gt)
        sm = is_match(sys_r.get("verdict"), gt)
        hm = is_match(hybrid.get("verdict"), gt)

        print(f"  bare:   {bare.get('verdict')}({bare.get('confidence')}) {'✓' if bm else '✗'}", file=sys.stderr)
        print(f"  system: {sys_r.get('verdict')}({sys_r.get('confidence')}) CI=[{sys_r.get('ci_lo')},{sys_r.get('ci_hi')}] {'✓' if sm else '✗'}", file=sys.stderr)
        print(f"  hybrid: {hybrid['verdict']}({hybrid['confidence']}) [{hybrid['source']}] {'✓' if hm else '✗'}", file=sys.stderr)

        results.append({
            "case_id": cid, "claim": claim, "mode": mode,
            "category": c.get("category"), "topic": c.get("topic"),
            "ground_truth": gt,
            "ground_truth_url": c.get("ground_truth_url", ""),
            "bare": bare, "bare_match": bm,
            "system": sys_r, "system_match": sm,
            "hybrid": hybrid, "hybrid_match": hm,
        })

        # 增量保存
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    n = len(results)
    print(f"\n{'='*70}", file=sys.stderr)
    print(f"=== n={n} 完整对比 ===", file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)
    for label, key in [("裸 DeepSeek", "bare_match"), ("系统", "system_match"), ("Hybrid", "hybrid_match")]:
        ok = sum(1 for r in results if r.get(key))
        print(f"  {label:14}  {ok}/{n} = {ok/n*100:.1f}%", file=sys.stderr)

    # McNemar exact
    import math

    def mcnemar(b, c):
        n_ = b + c
        if n_ == 0:
            return 0.0, 1.0
        chi2 = (abs(b - c) - 1) ** 2 / n_
        k = min(b, c)
        p_one = sum(math.comb(n_, i) * (0.5 ** n_) for i in range(k + 1))
        return chi2, min(1.0, 2 * p_one)

    for label, key in [("Hybrid", "hybrid_match"), ("系统", "system_match")]:
        n10 = sum(1 for r in results if r["bare_match"] and not r[key])
        n01 = sum(1 for r in results if not r["bare_match"] and r[key])
        chi2_v, p_v = mcnemar(n10, n01)
        print(f"\n  {label} vs 裸 McNemar: n10={n10} n01={n01} χ²={chi2_v:.3f} exact_p={p_v:.6f} {'显著 ✓' if p_v < 0.05 else '不显著 ✗'}", file=sys.stderr)

    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="../.claude/skills/fact-check/tests/test_cases_v6_combined.jsonl")
    ap.add_argument("--out", default="reports/significance_n200.json")
    args = ap.parse_args()
    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = ROOT / cases_path
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    return asyncio.run(amain(cases_path, out_path))


if __name__ == "__main__":
    sys.exit(main())
