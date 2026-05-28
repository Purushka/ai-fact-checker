"""自动 case 生成器 — 从权威源原文反向生成 claim 变体。

避免污染的关键：claim 不是人工凭记忆写的，而是 LLM 从搜到的真实原文反向生成。
GT 锚定到原文 URL，可重复 verify。

生成流程：
  1. 用 search provider 查 hard topic（含具体数字/日期/文号的）
  2. fetch 抓返回的权威 URL（gov.cn / npc.gov.cn 等）
  3. LLM 从原文提取一个具体可核查事实
  4. LLM 生成 4 种 claim 变体（supported / contradicted / outdated / partial）
  5. 输出 JSONL，每条含 ground_truth_url + ground_truth_evidence
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
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

from factcheck.fetch.http_fetcher import HttpFetcher  # noqa: E402
from factcheck.llm.base import LLMMessage  # noqa: E402
from factcheck.llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from factcheck.search.orchestrator import SearchOrchestrator  # noqa: E402
from factcheck.score.source_classify import SourceClassifier  # noqa: E402
from factcheck.utils.json_parse import parse_json_lenient  # noqa: E402

# 30 个 hard topic，覆盖：法规、地方、人事、数据、文号、行业
HARD_TOPICS = [
    # 法规修订时间线（claim 会问"X 法当前是 Y 年版本"，让 system + bare 比试）
    "中华人民共和国公司法 2023年修订 第七次会议",
    "中华人民共和国行政复议法 2023年修订 主席令第九号",
    "中华人民共和国民事诉讼法 2023年修正",
    "中华人民共和国反垄断法 2022年8月1日施行 修订",
    "中华人民共和国国家安全法 2015年7月1日施行",
    "中华人民共和国数据安全法 2021年9月1日施行",
    "中华人民共和国个人信息保护法 2021年11月1日施行",
    # 2024-2026 具体政策
    "国务院 国发 2025 11号 人工智能+ 行动 意见",
    "国务院 民营经济促进法 2025年5月20日",
    "央行 LPR 2025年12月报价",
    "国家发改委 2025 民营经济 通知 486号",
    "国家医保局 2024年版 医保目录 91种",
    "国家税务总局 2023年12号 小微企业 所得税",
    "财政部税务总局 2023年第7号 研发费用 加计扣除",
    # 数据
    "国家统计局 2024年 国民经济 总值 修订",
    "国家统计局 2023年 GDP 最终核实 129万亿",
    "国家统计局 2024年 出生人口 数据",
    "海关总署 2024年 进出口 43.85万亿",
    "中汽协 2024年 汽车 出口 585万辆",
    "中汽协 2025年 新能源汽车 销量 1649万辆",
    # 地方具体
    "深圳市 人社局 创业补贴 一次性 1万元",
    "苏州工业园区 集成电路 IP购买 300万 流片",
    "杭州 未来科技城 海外人才 安家费",
    "北京 海淀区 中关村 人工智能 10亿元 算力",
    "上海 浦东新区 张江 生物医药 扶持",
    # 人事
    "证监会 主席 吴清 接任 易会满 2024年2月",
    "国家金融监督管理总局 局长 李云泽 2023年",
    "国家统计局 局长 康义 任免",
    # 司法/行业
    "最高人民法院 民法典 合同编 司法解释 2023",
    "国务院 渐进式延迟退休 2024年9月13日 全国人大常委会",
]

EXTRACTOR_PROMPT = """\
你是一个测试用例生成器。给定一段权威源原文，提取一个**具体可核查的事实**（含数字/日期/文号/人名+职位/具体金额），
然后生成 4 个 claim 变体用于事实核查测试。

要求：
1. 提取的事实**必须**在原文中明确出现（不能编造）
2. 每个 claim 变体严格按以下规则构造：
   - **supported**：保持原文核心事实不变，可以改写语序
   - **contradicted**：改 1 个关键数字/日期/文号为明显错误的值（且能在原文中找到反证）
   - **outdated**：如果原文是"X 法/政策 Y 年新修订/施行"，用一个早于 Y 的旧版本年份作为 claim（让 outdated 成立）；如果是单一发布事件没有"新旧版本"概念，跳过 outdated（用 null）
   - **partially_supported**：保留事实方向但故意把具体数字模糊化或与不准确细节合并（例如把"5000元"说成"3000-5000元"，或把"5月20日施行"说成"2025年上半年施行"）

3. 测试 case 的具体度要够：避免"宪法 / 增值税 13% / OpenAI Sam Altman"这种基础常识；要含具体文号/数字/日期细节

只返回 JSON：
{
  "fact_summary": "一句话概述提取出的事实",
  "ground_truth_evidence": "原文中支撑此事实的具体片段（≤200 字）",
  "mode": "policy/numeric/entity_status/event",
  "variants": [
    {"verdict": "supported", "claim": "..."},
    {"verdict": "contradicted", "claim": "..."},
    {"verdict": "outdated", "claim": "..." 或 null},
    {"verdict": "partially_supported", "claim": "..."}
  ]
}

如果原文不适合生成 4 种 verdict（如不存在历史版本无法 outdated），对应 claim 设为 null。
如果原文质量太差无法提取具体事实，返回 {"skip": true, "reason": "..."}。"""


async def generate_one_topic(topic: str, llm, search, fetcher, classifier, idx: int) -> list[dict]:
    print(f"\n[{idx}] topic: {topic}", file=sys.stderr)
    search_res = await search.search([topic], per_query_limit=4, total_cap=4)
    if not search_res.results:
        print(f"  ✗ no search results", file=sys.stderr)
        return []

    # 取权威分最高的 1 个源
    sorted_results = sorted(search_res.results, key=lambda r: -classifier.classify(r.url).authority_weight)
    top = sorted_results[0]
    auth = classifier.classify(top.url).authority_weight
    print(f"  → top source: {top.url[:75]} (auth={auth})", file=sys.stderr)

    if auth < 0.6:
        print(f"  ✗ authority too low, skip", file=sys.stderr)
        return []

    page = await fetcher.fetch(top.url, timeout=10.0)
    if page.status != "ok" or not page.content:
        print(f"  ✗ fetch failed: {page.status}", file=sys.stderr)
        return []

    user_msg = f"权威源 URL: {top.url}\n\n原文（≤4000 字）：\n{page.content[:4000]}"
    try:
        resp = await llm.chat(
            [LLMMessage(role="system", content=EXTRACTOR_PROMPT),
             LLMMessage(role="user", content=user_msg)],
            temperature=0.0, max_tokens=1500, timeout=45.0,
        )
    except Exception as e:
        print(f"  ✗ LLM failed: {e}", file=sys.stderr)
        return []

    parsed = parse_json_lenient(resp.text) or {}
    if not isinstance(parsed, dict) or parsed.get("skip"):
        print(f"  ✗ skipped: {parsed.get('reason', 'unparseable')}", file=sys.stderr)
        return []

    variants = parsed.get("variants", [])
    fact = parsed.get("fact_summary", "")
    evidence = parsed.get("ground_truth_evidence", "")[:300]
    mode = parsed.get("mode", "general")

    cases = []
    for i, v in enumerate(variants):
        c = v.get("claim")
        verdict = v.get("verdict")
        if not c or not verdict:
            continue
        case_id = f"GEN_{idx:03d}_{verdict[:3].upper()}"
        cases.append({
            "id": case_id,
            "claim": c,
            "mode": mode,
            "ground_truth": verdict,
            "ground_truth_url": top.url,
            "ground_truth_evidence": evidence,
            "fact_summary": fact,
            "category": f"GEN_{verdict}",
            "topic": topic,
            "source_authority": auth,
        })
    print(f"  ✓ generated {len(cases)} cases", file=sys.stderr)
    return cases


async def amain(topics: list[str], out_path: Path) -> int:
    import os
    llm = OpenAICompatibleProvider(
        name="deepseek", base_url="https://api.deepseek.com/v1",
        api_key=os.environ["DEEPSEEK_API_KEY"], default_model="deepseek-chat",
    )
    search = SearchOrchestrator()
    fetcher = HttpFetcher()
    classifier = SourceClassifier()

    all_cases = []
    for i, topic in enumerate(topics, 1):
        cases = await generate_one_topic(topic, llm, search, fetcher, classifier, i)
        all_cases.extend(cases)
        # 增量保存
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for c in all_cases:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"\n生成完成: {len(all_cases)} cases → {out_path}", file=sys.stderr)

    by_verdict = {}
    for c in all_cases:
        by_verdict[c["ground_truth"]] = by_verdict.get(c["ground_truth"], 0) + 1
    print(f"分布: {by_verdict}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../docs/skill-spec/tests/test_cases_v5_generated.jsonl")
    ap.add_argument("--limit", type=int, help="只跑前 N 个 topic")
    args = ap.parse_args()
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    topics = HARD_TOPICS[:args.limit] if args.limit else HARD_TOPICS
    print(f"将处理 {len(topics)} 个 topic", file=sys.stderr)
    return asyncio.run(amain(topics, out_path))


if __name__ == "__main__":
    sys.exit(main())
