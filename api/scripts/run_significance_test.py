"""50 case bare vs system 配对统计显著性实验。

每个 case 跑两次：
  1. 裸 DeepSeek（仅训练知识）
  2. 完整系统 V5（搜索 + 评分）

输出：
  - 配对结果表
  - McNemar's test χ² 与 p-value
  - 两个准确率的 95% bootstrap 置信区间
  - 系统增量价值的 95% CI
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
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

BARE_SYSTEM_PROMPT = """\
你是事实核查助手。给定一个事实声明，仅靠你的训练知识判断其可信度。**不能搜索网络**。
返回 JSON：
{
  "verdict": "supported | contradicted | outdated | unverifiable | out_of_scope | partially_supported",
  "confidence": 0-100,
  "reasoning": "一两句话依据"
}
verdict 含义：
- supported：声明完全成立
- partially_supported：方向对但细节有偏差
- contradicted：声明与事实相反
- outdated：声明描述的状态/版本已过期/被替代
- unverifiable：你没有足够把握判断
- out_of_scope：主观评价或模糊声明
只返回 JSON。"""


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    return cases


def _build_bare_provider() -> OpenAICompatibleProvider:
    import os
    return OpenAICompatibleProvider(
        name="deepseek-bare",
        base_url="https://api.deepseek.com/v1",
        api_key=os.environ["DEEPSEEK_API_KEY"],
        default_model="deepseek-chat",
    )


async def run_bare(claim: str) -> dict:
    t0 = time.time()
    provider = _build_bare_provider()
    try:
        r = await provider.chat(
            [LLMMessage(role="system", content=BARE_SYSTEM_PROMPT),
             LLMMessage(role="user", content=f"声明：{claim}")],
            temperature=0.0, max_tokens=400, timeout=30,
        )
        parsed = parse_json_lenient(r.text) or {}
        return {
            "verdict": parsed.get("verdict", "unknown"),
            "confidence": int(parsed.get("confidence", 0) or 0),
            "reasoning": (parsed.get("reasoning") or "")[:200],
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
            "tokens": result.token_usage.total_tokens if result.token_usage else 0,
            "elapsed": round(time.time() - t0, 2),
        }
    except Exception as e:
        return {"verdict": "ERROR", "error": str(e)[:200], "elapsed": round(time.time() - t0, 2)}


def is_match(verdict: str, gt: str) -> bool:
    if not verdict or verdict == "ERROR":
        return False
    # 宽松：partial_supported 算与 supported / outdated 部分等价的话太松；保持严格相等
    return verdict == gt


def mcnemar_test(b: int, c: int) -> tuple[float, float]:
    """McNemar's test with exact binomial p-value (准确，适合 small n)。
    b = bare 对 system 错（regression）
    c = bare 错 system 对（improvement）
    Returns (χ², exact_two_sided_p)
    """
    if b + c == 0:
        return 0.0, 1.0
    chi2 = (abs(b - c) - 1) ** 2 / (b + c)
    # Exact two-sided binomial under H0: P(success)=0.5
    n = b + c
    k = min(b, c)
    p_one_tail = sum(math.comb(n, i) * (0.5 ** n) for i in range(k + 1))
    p = min(1.0, 2 * p_one_tail)
    return chi2, p


def bootstrap_ci(matches: list[bool], n_boot: int = 5000, ci: float = 0.95) -> tuple[float, float]:
    if not matches:
        return 0.0, 0.0
    n = len(matches)
    rng = random.Random(42)
    means = []
    for _ in range(n_boot):
        s = sum(matches[rng.randint(0, n - 1)] for _ in range(n))
        means.append(s / n)
    means.sort()
    lo = means[int(n_boot * (1 - ci) / 2)]
    hi = means[int(n_boot * (1 + ci) / 2) - 1]
    return lo, hi


async def amain(cases_path: Path, out_path: Path, limit: int | None = None) -> int:
    cases = load_cases(cases_path)
    if limit:
        cases = cases[:limit]

    print(f"加载 {len(cases)} cases", file=sys.stderr)

    results = []
    for i, c in enumerate(cases, 1):
        cid = c["id"]
        claim = c["claim"]
        mode = c.get("mode", "general")
        gt = c["ground_truth"]

        print(f"\n[{i}/{len(cases)}] {cid} ({mode}) GT={gt}", file=sys.stderr)
        print(f"  claim: {claim[:80]}", file=sys.stderr)

        bare = await run_bare(claim)
        bm = is_match(bare.get("verdict"), gt)
        print(f"  裸:   {bare.get('verdict')} ({bare.get('confidence')}) {'✓' if bm else '✗'}  t={bare['elapsed']}s", file=sys.stderr)

        sys_r = await run_system(claim, mode)
        sm = is_match(sys_r.get("verdict"), gt)
        print(f"  系统: {sys_r.get('verdict')} ({sys_r.get('confidence')}) {'✓' if sm else '✗'}  t={sys_r['elapsed']}s", file=sys.stderr)

        results.append({
            "case_id": cid, "claim": claim, "mode": mode, "category": c.get("category"),
            "ground_truth": gt,
            "bare": bare, "bare_match": bm,
            "system": sys_r, "system_match": sm,
        })

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    n = len(results)
    n11 = sum(1 for r in results if r["bare_match"] and r["system_match"])
    n10 = sum(1 for r in results if r["bare_match"] and not r["system_match"])
    n01 = sum(1 for r in results if not r["bare_match"] and r["system_match"])
    n00 = sum(1 for r in results if not r["bare_match"] and not r["system_match"])

    bare_ok = n11 + n10
    sys_ok = n11 + n01

    chi2, p = mcnemar_test(b=n10, c=n01)
    bare_lo, bare_hi = bootstrap_ci([r["bare_match"] for r in results])
    sys_lo, sys_hi = bootstrap_ci([r["system_match"] for r in results])
    diff_samples = [r["system_match"] - r["bare_match"] for r in results]
    diff_lo, diff_hi = bootstrap_ci([d > 0 for d in diff_samples]) if any(d > 0 for d in diff_samples) else (0.0, 0.0)

    print(f"\n{'='*70}", file=sys.stderr)
    print(f"=== n={n} 配对实验结果 ===", file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)
    print(f"  裸准确率:    {bare_ok}/{n} = {bare_ok/n*100:.1f}%  [95% CI: {bare_lo*100:.1f}% — {bare_hi*100:.1f}%]", file=sys.stderr)
    print(f"  系统准确率:  {sys_ok}/{n} = {sys_ok/n*100:.1f}%  [95% CI: {sys_lo*100:.1f}% — {sys_hi*100:.1f}%]", file=sys.stderr)
    print(f"  配对差值:    +{(sys_ok-bare_ok)/n*100:.1f}pp", file=sys.stderr)
    print(f"\n  四象限混淆:", file=sys.stderr)
    print(f"    n11 两都对:     {n11}", file=sys.stderr)
    print(f"    n10 裸对系统错: {n10}", file=sys.stderr)
    print(f"    n01 裸错系统对: {n01}", file=sys.stderr)
    print(f"    n00 两都错:     {n00}", file=sys.stderr)
    print(f"\n  McNemar's test (配对二项检验):", file=sys.stderr)
    print(f"    χ² = {chi2:.4f}", file=sys.stderr)
    print(f"    p = {p:.6f}  {'(显著 ✓)' if p < 0.05 else '(不显著 ✗)'}", file=sys.stderr)
    if p < 0.001:
        print(f"    p < 0.001 强显著", file=sys.stderr)
    elif p < 0.01:
        print(f"    p < 0.01 高显著", file=sys.stderr)

    bare_hallucin = sum(1 for r in results if r["bare"].get("confidence", 0) >= 90 and not r["bare_match"]
                        and r["bare"].get("verdict") in ("supported", "contradicted", "outdated"))
    sys_hallucin = sum(1 for r in results if r["system"].get("confidence", 0) >= 90 and not r["system_match"]
                       and r["system"].get("verdict") in ("supported", "contradicted", "outdated"))
    print(f"\n  自信错判（conf >= 90 且错）:", file=sys.stderr)
    print(f"    裸:   {bare_hallucin}/{n} = {bare_hallucin/n*100:.1f}%", file=sys.stderr)
    print(f"    系统: {sys_hallucin}/{n} = {sys_hallucin/n*100:.1f}%", file=sys.stderr)

    print(f"\n  按 category 拆分:", file=sys.stderr)
    cats = {}
    for r in results:
        cat = r.get("category", "?")
        cats.setdefault(cat, {"n": 0, "bare": 0, "sys": 0})
        cats[cat]["n"] += 1
        if r["bare_match"]:
            cats[cat]["bare"] += 1
        if r["system_match"]:
            cats[cat]["sys"] += 1
    for cat in sorted(cats):
        v = cats[cat]
        print(f"    {cat:25} n={v['n']:>2}  裸 {v['bare']}/{v['n']:>2} ({v['bare']/v['n']*100:>3.0f}%)  系统 {v['sys']}/{v['n']:>2} ({v['sys']/v['n']*100:>3.0f}%)", file=sys.stderr)

    summary = {
        "n": n,
        "bare_accuracy": bare_ok / n,
        "system_accuracy": sys_ok / n,
        "bare_ci_95": [bare_lo, bare_hi],
        "system_ci_95": [sys_lo, sys_hi],
        "diff_pp": (sys_ok - bare_ok) / n * 100,
        "mcnemar_chi2": chi2,
        "mcnemar_p": p,
        "significant_at_005": p < 0.05,
        "n11_both_correct": n11,
        "n10_bare_only": n10,
        "n01_system_only": n01,
        "n00_both_wrong": n00,
        "bare_overconfident_wrong": bare_hallucin,
        "system_overconfident_wrong": sys_hallucin,
        "by_category": {k: {"n": v["n"], "bare": v["bare"], "sys": v["sys"],
                            "bare_acc": v["bare"] / v["n"], "sys_acc": v["sys"] / v["n"]}
                        for k, v in cats.items()},
    }
    out_path.with_suffix(".summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="../.claude/skills/fact-check/tests/test_cases_v4_balanced.jsonl")
    ap.add_argument("--out", default="reports/significance_n50.json")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = ROOT / cases_path
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    return asyncio.run(amain(cases_path, out_path, args.limit))


if __name__ == "__main__":
    sys.exit(main())
