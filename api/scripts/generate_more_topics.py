"""补充 topic 跑 generator，凑足 200 case。"""
from __future__ import annotations

import asyncio
import sys
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

from generate_cases_from_sources import HARD_TOPICS, generate_one_topic
from factcheck.fetch.http_fetcher import HttpFetcher
from factcheck.llm.openai_compatible import OpenAICompatibleProvider
from factcheck.search.orchestrator import SearchOrchestrator
from factcheck.score.source_classify import SourceClassifier
import json

# 补充 30 个新 hard topic（避免与原 30 个 topic 重复）
EXTRA_TOPICS = [
    # 行业协会数据
    "中国汽车工业协会 2024年12月 销量 信息发布会",
    "中国互联网信息中心 CNNIC 第54次报告 2024年8月",
    "中国信通院 2024年 5G发展 报告 数据",
    "中国电子工业协会 2024年 集成电路 增速",
    # 监管处罚 / 罚单
    "国家市场监督管理总局 2024年 反垄断 处罚 案例",
    "证监会 2024年 处罚 资本市场 案例",
    "网信办 2024年 算法备案 名单 公布",
    # 司法
    "最高人民法院 2024年 知识产权 司法解释 发布",
    "最高人民检察院 2024年 检察工作报告 数据",
    # 央行数据 / 货币政策
    "央行 2024年 M2 同比增长 数据",
    "央行 2025年 11月 LPR 报价",
    "国家外汇管理局 2024年 外汇储备 数据",
    # 部委具体文号 2024-2025
    "工信部 2024年 第X号 公告 新能源汽车",
    "教育部 2024年 高校设置 通知",
    "人社部 2024年 灵活就业 政策 通知",
    "民政部 2024年 婚姻登记 数据 通报",
    # 地方深度政策
    "成都市 高新区 软件企业 2024年 奖励 政策",
    "西安市 2024年 高校毕业生 创业 补贴",
    "广州市 黄埔区 生物医药 2024年 扶持",
    "南京市 江宁区 高新技术企业 2024年 认定",
    # 2025-2026 新事件
    "2025年 第十五届全国人大常委会 第十五次会议",
    "2025年 中央经济工作会议 部署 全文",
    "2026年 政府工作报告 关键数据",
    # 行业重大事件
    "比亚迪 2024年 销量 突破 万辆",
    "宁德时代 2024年 装机量 全球",
    "华为 2024年 智能汽车 业务 数据",
    # 教育 / 医疗
    "国务院 2024年 国家自然科学基金 资助 总额",
    "卫健委 2024年 居民健康素养 数据",
    "教育部 2024年 高考 改革 省份 实施",
    "国家中医药管理局 2024年 中医药 服务 数据",
]


async def main():
    import os
    llm = OpenAICompatibleProvider(
        name="deepseek", base_url="https://api.deepseek.com/v1",
        api_key=os.environ["DEEPSEEK_API_KEY"], default_model="deepseek-chat",
    )
    search = SearchOrchestrator()
    fetcher = HttpFetcher()
    classifier = SourceClassifier()

    out = ROOT / ".." / ".claude" / "skills" / "fact-check" / "tests" / "test_cases_v5_extra.jsonl"
    out = out.resolve()
    all_cases = []
    base_idx = 1000  # 避免与 v5_generated 的 ID 冲突
    for i, topic in enumerate(EXTRA_TOPICS, 1):
        cases = await generate_one_topic(topic, llm, search, fetcher, classifier, base_idx + i)
        all_cases.extend(cases)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as f:
            for c in all_cases:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"\n生成 extra: {len(all_cases)} cases → {out}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
