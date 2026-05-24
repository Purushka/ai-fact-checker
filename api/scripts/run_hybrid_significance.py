"""Hybrid 模式显著性实验 — 三方对比：裸 / 系统 / 系统+裸兜底

Hybrid 策略：
  1. 跑系统
  2. 若 system 是 unverifiable 或 partially_supported(conf<60)，且 bare conf≥80 → 用 bare verdict
  3. 否则用 system verdict

这个 hybrid 是 production-grade fallback：
  - system 强决断 → 信任 evidence-based
  - system 弱判断 → 借用 bare 的训练知识兜底
  - 但 bare 高信心错判（如 MC01/CD01）仍走 system，因为 system 在那些 case 有 evidence-based reasoning
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

# 复用 run_significance_test 的函数
sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_significance_test import (
    BARE_SYSTEM_PROMPT, load_cases, run_bare, run_system, is_match, mcnemar_test, bootstrap_ci
)


def hybrid_decision(bare: dict, system: dict) -> dict:
    """裸-系统级联决策。

    规则（按优先级）：
      1. 系统 out_of_scope → 信系统（subjective 短路）
      2. 系统高信心决断（≥70）→ 信系统（包括 supported/contradicted/outdated）
      3. 系统弱 + 裸高信心（≥85）且 verdict 明确 → 用裸（bare_fallback）
      4. 系统低信心 contradicted + 裸高信心 outdated/contradicted → 用裸（同向加固）
      5. 否则用系统（保守）
    """
    sv = system.get("verdict", "")
    sc = system.get("confidence", 0)
    bv = bare.get("verdict", "")
    bc = bare.get("confidence", 0)

    if sv == "out_of_scope":
        return {"verdict": sv, "confidence": sc, "source": "system_subjective"}

    if sv in ("supported", "contradicted", "outdated") and sc >= 70:
        return {"verdict": sv, "confidence": sc, "source": "system_strong"}

    # 系统弱判断 + 裸高信心 → 用裸
    if sv in ("unverifiable", "partially_supported", "conflicting") and bc >= 85 and bv not in ("unverifiable", "out_of_scope"):
        return {"verdict": bv, "confidence": min(bc, 70), "source": "bare_fallback"}

    # 系统 contradicted 但信心低 + 裸高信心同向（都说 claim 错）→ 用裸的更具体 verdict
    if sv == "contradicted" and sc < 70 and bc >= 85 and bv in ("contradicted", "outdated"):
        return {"verdict": bv, "confidence": min(bc, 70), "source": "bare_strong_negative"}

    # 系统 outdated 信心低 + 裸高信心 supported → 用裸（系统过度误判 outdated）
    if sv == "outdated" and sc < 50 and bc >= 85 and bv == "supported":
        return {"verdict": bv, "confidence": min(bc, 70), "source": "bare_override_outdated"}

    return {"verdict": sv, "confidence": sc, "source": "system_conservative"}


async def amain(cases_path: Path, out_path: Path, limit: int | None = None) -> int:
    cases = load_cases(cases_path)
    if limit:
        cases = cases[:limit]

    print(f"加载 {len(cases)} cases", file=sys.stderr)
    results = []

    # 加载已有的 bare/system 结果作为缓存避免重跑
    cache_file = ROOT / "reports" / "significance_n50.json"
    cached = {}
    if cache_file.exists():
        for r in json.loads(cache_file.read_text(encoding="utf-8")):
            cached[r["case_id"]] = r
        print(f"从 cache 复用 {len(cached)} cases 数据，仅重跑系统改动影响的 case", file=sys.stderr)

    for i, c in enumerate(cases, 1):
        cid = c["id"]
        claim = c["claim"]
        mode = c.get("mode", "general")
        gt = c["ground_truth"]

        # bare 不变，复用 cache
        cached_r = cached.get(cid)
        if cached_r:
            bare = cached_r["bare"]
        else:
            print(f"  → bare {cid} ...", file=sys.stderr)
            bare = await run_bare(claim)

        # system 用最新代码（含 subjective short-circuit + 阈值调整）
        print(f"  → system {cid} ...", file=sys.stderr)
        sys_r = await run_system(claim, mode)

        hybrid = hybrid_decision(bare, sys_r)

        bm = is_match(bare.get("verdict"), gt)
        sm = is_match(sys_r.get("verdict"), gt)
        hm = is_match(hybrid.get("verdict"), gt)

        print(f"  [{i}/{len(cases)}] {cid} GT={gt}", file=sys.stderr)
        print(f"    裸: {bare.get('verdict')}({bare.get('confidence')}) {'✓' if bm else '✗'}", file=sys.stderr)
        print(f"    系统: {sys_r.get('verdict')}({sys_r.get('confidence')}) {'✓' if sm else '✗'}", file=sys.stderr)
        print(f"    Hybrid: {hybrid['verdict']}({hybrid['confidence']}) [{hybrid['source']}] {'✓' if hm else '✗'}", file=sys.stderr)

        results.append({
            "case_id": cid, "claim": claim, "mode": mode, "category": c.get("category"),
            "ground_truth": gt,
            "bare": bare, "bare_match": bm,
            "system": sys_r, "system_match": sm,
            "hybrid": hybrid, "hybrid_match": hm,
        })

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    n = len(results)
    print(f"\n{'='*70}", file=sys.stderr)
    print(f"=== n={n} 三方对比 ===", file=sys.stderr)
    print(f"{'='*70}", file=sys.stderr)

    for label, matches in [
        ("裸 DeepSeek", [r["bare_match"] for r in results]),
        ("系统 V5'", [r["system_match"] for r in results]),
        ("Hybrid", [r["hybrid_match"] for r in results]),
    ]:
        ok = sum(matches)
        lo, hi = bootstrap_ci(matches)
        print(f"  {label:14}  {ok}/{n} = {ok/n*100:.1f}%  [95% CI: {lo*100:.1f}% — {hi*100:.1f}%]", file=sys.stderr)

    # McNemar: hybrid vs bare
    n01 = sum(1 for r in results if r["hybrid_match"] and not r["bare_match"])
    n10 = sum(1 for r in results if r["bare_match"] and not r["hybrid_match"])
    chi2, p = mcnemar_test(b=n10, c=n01)
    print(f"\n  Hybrid vs 裸 McNemar:", file=sys.stderr)
    print(f"    n10 = {n10}  n01 = {n01}", file=sys.stderr)
    print(f"    χ² = {chi2:.4f}  p = {p:.6f}  {'显著 ✓' if p < 0.05 else '不显著 ✗'}", file=sys.stderr)

    # McNemar: system vs bare
    sn01 = sum(1 for r in results if r["system_match"] and not r["bare_match"])
    sn10 = sum(1 for r in results if r["bare_match"] and not r["system_match"])
    schi2, sp = mcnemar_test(b=sn10, c=sn01)
    print(f"\n  系统 V5' vs 裸 McNemar:", file=sys.stderr)
    print(f"    n10 = {sn10}  n01 = {sn01}", file=sys.stderr)
    print(f"    χ² = {schi2:.4f}  p = {sp:.6f}  {'显著 ✓' if sp < 0.05 else '不显著 ✗'}", file=sys.stderr)

    # Category breakdown
    print(f"\n  按 category:", file=sys.stderr)
    cats = {}
    for r in results:
        cat = r.get("category", "?")
        cats.setdefault(cat, {"n": 0, "bare": 0, "sys": 0, "hyb": 0})
        cats[cat]["n"] += 1
        if r["bare_match"]: cats[cat]["bare"] += 1
        if r["system_match"]: cats[cat]["sys"] += 1
        if r["hybrid_match"]: cats[cat]["hyb"] += 1
    for cat in sorted(cats):
        v = cats[cat]
        print(f"    {cat:25} n={v['n']:>2}  裸 {v['bare']}/{v['n']} ({v['bare']/v['n']*100:>3.0f}%)  系统 {v['sys']}/{v['n']} ({v['sys']/v['n']*100:>3.0f}%)  Hybrid {v['hyb']}/{v['n']} ({v['hyb']/v['n']*100:>3.0f}%)", file=sys.stderr)

    # 自信错判
    print(f"\n  自信错判（conf≥90 且错）:", file=sys.stderr)
    for label, key, cf_key in [("裸", "bare", "bare_match"), ("系统 V5'", "system", "system_match"), ("Hybrid", "hybrid", "hybrid_match")]:
        cnt = sum(1 for r in results if r[key].get("confidence", 0) >= 90 and not r[cf_key]
                  and r[key].get("verdict") in ("supported", "contradicted", "outdated"))
        print(f"    {label:14}  {cnt}/{n}", file=sys.stderr)

    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="../docs/skill-spec/tests/test_cases_v4_balanced.jsonl")
    ap.add_argument("--out", default="reports/hybrid_n50.json")
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
