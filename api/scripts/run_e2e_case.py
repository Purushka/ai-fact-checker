"""端到端单 case 跑测脚本 — 用真实 DeepSeek + 真实 HTTP fetch 验证 pipeline。

绕过 Search 层（无 Tavily key），直接 inject 已知的真实 URL 列表（取自 cycle1-4 的搜索结果）。

用法：
    python scripts/run_e2e_case.py --case-id MA01
    python scripts/run_e2e_case.py --all       # 跑全部预置 case
    python scripts/run_e2e_case.py --case-id MA01 --json   # 仅输出 JSON

不会消耗搜索 quota；只消耗 DeepSeek tokens（每 case 约 3-6k）和若干次 HTTP GET。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from factcheck.extract import FactExtractor, QueryPlanner  # noqa: E402
from factcheck.fetch.http_fetcher import HttpFetcher  # noqa: E402
from factcheck.llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from factcheck.schemas import CheckOptions, CheckRequest  # noqa: E402
from factcheck.score.engine import ScoreEngine  # noqa: E402
from factcheck.score.source_classify import SourceClassifier  # noqa: E402
from factcheck.utils.token_counter import TokenAccumulator  # noqa: E402
from factcheck.verify import CrossValidator  # noqa: E402

CASES: dict[str, dict[str, Any]] = {
    "MA01": {
        "claim": "中华人民共和国民营经济促进法于2025年5月20日起施行",
        "mode": "policy",
        "predicted": "supported",
        "urls": [
            "https://www.ndrc.gov.cn/xxgk/zcfb/tz/202505/t20250520_1397832.html",
            "https://www.cac.gov.cn/2025-04/30/c_1747719110261160.htm",
        ],
    },
    "MA02": {
        "claim": "2025年中国新能源汽车销量超过1300万辆",
        "mode": "numeric",
        "predicted": "partially_supported",
        "note": "字面 1649>1300 成立，但 claim 用 1300 这个 misleading 数字 → partially_supported 更准",
        "urls": [
            "https://www.news.cn/fortune/20260114/cbbd861081c349d8ae238167ca418fa3/c.html",
            "https://www.stcn.com/article/detail/3593800.html",
        ],
    },
    "MC01": {
        "claim": "国家统计局核定2023年中国GDP最终数为126.06万亿元",
        "mode": "numeric",
        "predicted": "contradicted",
        "urls": [
            "https://www.stats.gov.cn/sj/zxfb/202412/t20241227_1957915.html",
            "https://www.stats.gov.cn/sj/sjjd/202412/t20241227_1957914.html",
        ],
    },
    "MD02": {
        "claim": "中华人民共和国公司法当前生效版本是2018年修正版",
        "mode": "policy",
        "predicted": "outdated",
        "urls": [
            "http://www.npc.gov.cn/npc/c2/c30834/202312/t20231229_433954.html",
        ],
    },
    "C2_05": {
        "claim": "中华人民共和国反垄断法自2008年8月1日起施行至今未做修改",
        "mode": "policy",
        "predicted": "outdated",
        "note": "已被 2022-08-01 修订版替代；outdated 比 contradicted 更精准（告诉调用方有新版）",
        "urls": [
            "http://www.npc.gov.cn/npc/c2/c30834/202206/t20220624_318276.html",
        ],
    },
    "C2_06": {
        "claim": "国家税务总局2025年第8号公告规定研发费用加计扣除比例统一为100%",
        "mode": "policy",
        "predicted": "unverifiable",
        "note": "fgk.chinatax.gov.cn 反爬，无 Playwright 无源时 unverifiable 是合理（不应 fake-contradicted）",
        "urls": [
            "https://fgk.chinatax.gov.cn/zcfgk/c102416/c5201978/content.html",
        ],
    },
    "MZ01": {
        "claim": "中华人民共和国宪法最近一次修正是2018年3月11日",
        "mode": "policy",
        "predicted": "supported",
        "urls": [
            "http://www.npc.gov.cn/zgrdw/npc/xinwen/2018-03/12/content_2049190.htm",
        ],
    },
    "CD01": {
        "claim": "国家医保目录2024年版调整后中成药品种数量增加了40余种",
        "mode": "numeric",
        "predicted": "contradicted",
        "urls": [
            "https://www.nhsa.gov.cn/art/2024/11/28/art_14_14884.html",
            "https://cn.chinadaily.com.cn/a/202411/28/WS6747e44fa310b59111da5f4a.html",
        ],
    },
}


def _build_llm() -> OpenAICompatibleProvider:
    import os
    key = os.environ.get("DEEPSEEK_API_KEY") or ""
    if not key:
        env_path = Path(__file__).resolve().parents[1] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DEEPSEEK_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置")
    return OpenAICompatibleProvider(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_key=key,
        default_model="deepseek-chat",
    )


async def run_case(case_id: str, case: dict[str, Any]) -> dict[str, Any]:
    claim = case["claim"]
    mode = case["mode"]
    urls = case["urls"]
    predicted = case.get("predicted")

    t0 = time.time()
    provider = _build_llm()
    token_acc = TokenAccumulator()
    classifier = SourceClassifier()
    fetcher = HttpFetcher()
    scorer = ScoreEngine()

    fetched = []
    for u in urls:
        page = await fetcher.fetch(u, timeout=12.0)
        fetched.append(page)
    ok_pages = [p for p in fetched if p.status == "ok"]
    classifications = [classifier.classify(p.url) for p in ok_pages]
    fetch_summary = [
        {"url": p.url, "status": p.status, "content_len": len(p.content or ""), "title": p.title[:60]}
        for p in fetched
    ]

    if not ok_pages:
        return {
            "case_id": case_id,
            "claim": claim,
            "predicted": predicted,
            "actual_verdict": "unverifiable",
            "actual_confidence": 0,
            "match": (predicted == "unverifiable") if predicted else None,
            "skipped_reason": "全部 URL 抓取失败",
            "fetch_summary": fetch_summary,
            "token_usage": token_acc.to_dict(),
            "elapsed_sec": round(time.time() - t0, 2),
        }

    planner = QueryPlanner(provider)
    plan = await planner.plan(claim, mode=mode, token_acc=token_acc)

    extractor = FactExtractor(provider)
    evidences = await extractor.extract_all(claim, mode, ok_pages, classifications, token_acc)

    validator = CrossValidator(provider)
    validation = await validator.validate(claim, mode, evidences, token_acc)

    evidence_dicts = [
        {
            "source_url": e.source_url,
            "source_type": e.source_type,
            "authority_weight": e.authority_weight,
            "agency": e.agency,
            "support_level": e.support_level,
            "published_at": e.published_at,
            "is_independent": e.is_independent,
            "content_fingerprint": e.content_fingerprint,
            "claims_about": e.claims_about,
            "snippet": e.snippet,
        }
        for e in evidences
    ]
    score = scorer.score(
        claim=claim, mode=mode, evidence=evidence_dicts,
        conflicts=validation.conflicts, policy_meta=validation.policy_meta,
        require_official_source=(mode == "policy"),
    )
    match = (score.verdict == predicted) if predicted else None
    return {
        "case_id": case_id,
        "claim": claim,
        "mode": mode,
        "predicted": predicted,
        "actual_verdict": score.verdict,
        "actual_confidence": score.confidence,
        "confidence_level": score.confidence_level,
        "policy_status": score.policy_status,
        "has_official_source": score.has_official_source,
        "match": match,
        "gating_applied": score.gating_applied,
        "evidence_summary": [
            {
                "url": e.source_url,
                "source_type": e.source_type,
                "authority": e.authority_weight,
                "support": e.support_level,
                "snippet": (e.snippet or "")[:120],
                "claims_about_keys": list((e.claims_about or {}).keys())[:10],
            }
            for e in evidences
        ],
        "conflicts": validation.conflicts,
        "missing_fields": score.missing_fields or validation.missing_fields,
        "queries_generated": plan.search_queries,
        "fetch_summary": fetch_summary,
        "token_usage": token_acc.to_dict(),
        "elapsed_sec": round(time.time() - t0, 2),
    }


async def amain(case_ids: list[str], output_json: bool) -> int:
    results = []
    for case_id in case_ids:
        case = CASES.get(case_id)
        if not case:
            print(f"未知 case: {case_id}", file=sys.stderr)
            continue
        print(f"\n--- {case_id} ---", file=sys.stderr)
        print(f"claim: {case['claim']}", file=sys.stderr)
        print(f"predicted: {case.get('predicted')}", file=sys.stderr)
        try:
            r = await run_case(case_id, case)
            results.append(r)
            print(f"actual:    {r['actual_verdict']} (conf={r.get('actual_confidence')})", file=sys.stderr)
            print(f"match:     {r.get('match')}", file=sys.stderr)
            print(f"tokens:    {r['token_usage']['total_tokens']}  elapsed: {r['elapsed_sec']}s", file=sys.stderr)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            results.append({"case_id": case_id, "error": str(e)})

    if output_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        ok = sum(1 for r in results if r.get("match") is True)
        wrong = sum(1 for r in results if r.get("match") is False)
        skipped = len(results) - ok - wrong
        print(f"\n=== 汇总 ===", file=sys.stderr)
        print(f"  匹配:   {ok}/{len(results)}", file=sys.stderr)
        print(f"  不匹配: {wrong}", file=sys.stderr)
        print(f"  跳过/错: {skipped}", file=sys.stderr)
        total_tokens = sum(r.get("token_usage", {}).get("total_tokens", 0) for r in results)
        total_elapsed = sum(r.get("elapsed_sec", 0) for r in results)
        print(f"  总 token: {total_tokens}  总耗时: {total_elapsed:.1f}s", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case-id", action="append")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="结果写入 JSON 文件")
    args = ap.parse_args()
    case_ids = list(CASES.keys()) if args.all else (args.case_id or [])
    if not case_ids:
        ap.print_help()
        return 2
    results_holder: list[dict] = []
    code = asyncio.run(amain_with_capture(case_ids, args.json, results_holder))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results_holder, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"结果写入 {args.out}", file=sys.stderr)
    return code


async def amain_with_capture(case_ids, output_json, results_holder):
    results = []
    for case_id in case_ids:
        case = CASES.get(case_id)
        if not case:
            continue
        print(f"\n--- {case_id} ---", file=sys.stderr)
        print(f"claim: {case['claim']}", file=sys.stderr)
        try:
            r = await run_case(case_id, case)
            results.append(r)
            print(f"predicted: {r['predicted']}  actual: {r['actual_verdict']}  match: {r.get('match')}", file=sys.stderr)
            print(f"tokens: {r['token_usage']['total_tokens']}  elapsed: {r['elapsed_sec']}s", file=sys.stderr)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            results.append({"case_id": case_id, "error": str(e)})
    results_holder.extend(results)
    ok = sum(1 for r in results if r.get("match") is True)
    wrong = sum(1 for r in results if r.get("match") is False)
    print(f"\n=== 汇总 ===  匹配 {ok}/{len(results)}  不匹配 {wrong}", file=sys.stderr)
    if output_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
