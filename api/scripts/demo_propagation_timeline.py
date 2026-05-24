"""演示：信息传播时间线追溯（PropagationTimeline）

跑 3 个真实形态的传播案例，展示 TimelineBuilder 的输出：

  Case 1: 草根病毒传播（微博起源 → 自媒体扩散 → 央媒收尾）
  Case 2: 辟谣链路（社媒谣言 → 自媒体放大 → piyao.org.cn 终止）
  Case 3: 政府信息发布（国务院 → 央媒 → 主流财经媒体）

每个 case 打印：
- 按时间排序的 stops（含 source_category + 起源/扩散标记）
- 自动识别的 pattern + confidence + note
"""

from __future__ import annotations

import sys

# Windows PowerShell GBK fallback
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

from factcheck.extract.timeline_builder import TimelineBuilder


def _print_timeline(case_name: str, claim: str, evidences: list[dict]) -> None:
    print("\n" + "=" * 78)
    print(f"  {case_name}")
    print("=" * 78)
    print(f"Claim: {claim}\n")

    timeline = TimelineBuilder().build(evidences)

    print(f"[pattern]    {timeline.pattern}   (confidence={timeline.confidence})")
    print(f"[note]       {timeline.note}")
    print(f"[date_range] {timeline.earliest_date}  →  {timeline.latest_date}   (n_stops={timeline.n_stops})")
    print()
    print(f"  {'date':<12} {'category':<24} {'source':<40} flags")
    print(f"  {'-' * 12} {'-' * 24} {'-' * 40} {'-' * 10}")
    for s in timeline.stops:
        flags = []
        if s.is_likely_origin:
            flags.append("ORIGIN")
        if s.is_likely_amplifier:
            flags.append("AMPLIFIER")
        flag_str = ",".join(flags) or "-"
        src = (s.source_name or "")[:38]
        print(f"  {s.earliest_seen or '-':<12} {s.source_category:<24} {src:<40} {flag_str}")


def case1_grassroots_viral() -> None:
    """案例 1：微博爆料 → 百家号搬运 → 澎湃报道 → 新华社总结
    典型病毒传播：底层社媒起源，主流媒体收尾。"""
    evs = [
        {
            "source_url": "https://weibo.com/2345/post/abcdef",
            "source_name": "@某网友爆料",
            "source_type": "social_media",
            "agency": None,
            "published_at": "2024-08-15",
            "snippet": "刚在小区门口看到 XX 事件，太离谱了……",
        },
        {
            "source_url": "https://baijiahao.baidu.com/s?id=1234567890",
            "source_name": "百家号·某情感号",
            "source_type": "content_farm",
            "agency": None,
            "published_at": "2024-08-16",
            "snippet": "网传 XX 地区发生 YY 事件，引发热议",
        },
        {
            "source_url": "https://www.thepaper.cn/newsDetail_forward_28392843",
            "source_name": "澎湃新闻",
            "source_type": "mainstream_media",
            "agency": "澎湃新闻",
            "published_at": "2024-08-18",
            "snippet": "记者实地走访 XX 地区核实 YY 事件经过",
        },
        {
            "source_url": "https://www.news.cn/2024-08/22/c_1130123456.htm",
            "source_name": "新华社",
            "source_type": "official",
            "agency": "新华社",
            "published_at": "2024-08-22",
            "snippet": "新华社：XX 事件多部门联合调查结果发布",
        },
    ]
    _print_timeline(
        "Case 1: 草根病毒传播（微博 → 自媒体 → 主流 → 央媒）",
        "某地发生XX事件",
        evs,
    )


def case2_fact_check_corrected() -> None:
    """案例 2：典型辟谣链路
    社媒谣言 → 自媒体放大 → 互联网联合辟谣平台收尾。"""
    evs = [
        {
            "source_url": "https://weibo.com/1234/postXYZ",
            "source_name": "@某营销号",
            "source_type": "social_media",
            "agency": None,
            "published_at": "2024-08-10",
            "snippet": "某地草坪被喷绿色油漆？竟然是为了应付检查？",
        },
        {
            "source_url": "https://baijiahao.baidu.com/s?id=9876543210",
            "source_name": "百家号·某资讯号",
            "source_type": "content_farm",
            "agency": None,
            "published_at": "2024-08-11",
            "snippet": "震惊！某地草皮造假事件背后真相",
        },
        {
            "source_url": "https://mp.weixin.qq.com/s?__biz=AAAAAAAA",
            "source_name": "微信公众号·吃瓜日报",
            "source_type": "content_farm",
            "agency": None,
            "published_at": "2024-08-12",
            "snippet": "全网热议的草皮染色事件，到底怎么回事？",
        },
        {
            "source_url": "https://www.piyao.org.cn/20240815/12345abc/c.html",
            "source_name": "中国互联网联合辟谣平台",
            "source_type": "official",
            "agency": "中国互联网联合辟谣平台",
            "published_at": "2024-08-15",
            "snippet": "经核实，所谓'草皮被染绿应付检查'实为正常的草坪护理喷洒着色剂，系养护惯例，并非造假。",
        },
    ]
    _print_timeline(
        "Case 2: 辟谣链路（社媒谣言 → 自媒体放大 → piyao 终止）",
        "某地草皮被染绿应付检查",
        evs,
    )


def case3_official_dissemination() -> None:
    """案例 3：政府信息正常发布
    国务院 → 央媒 → 主流财经媒体。这是健康的信息级联。"""
    evs = [
        {
            "source_url": "https://www.gov.cn/zhengce/content/202405/20/content_678901.htm",
            "source_name": "中国政府网",
            "source_type": "official",
            "agency": "国务院",
            "published_at": "2024-05-20",
            "snippet": "国务院印发《关于 XX 行业高质量发展若干意见》",
        },
        {
            "source_url": "https://www.news.cn/politics/2024-05/21/c_1212345678.htm",
            "source_name": "新华社",
            "source_type": "official",
            "agency": "新华社",
            "published_at": "2024-05-21",
            "snippet": "新华社权威解读：XX 行业新政六大要点",
        },
        {
            "source_url": "https://www.people.com.cn/n1/2024/0522/c1006-40234567.html",
            "source_name": "人民日报",
            "source_type": "official",
            "agency": "人民日报",
            "published_at": "2024-05-22",
            "snippet": "人民日报评论员文章：推动 XX 行业向新而行",
        },
        {
            "source_url": "https://www.caixin.com/2024-05-23/102078901.html",
            "source_name": "财新网",
            "source_type": "mainstream_media",
            "agency": "财新",
            "published_at": "2024-05-23",
            "snippet": "财新解读：XX 行业新政的市场含义",
        },
    ]
    _print_timeline(
        "Case 3: 政府信息发布（国务院 → 央媒 → 主流财经）",
        "国务院印发XX行业新政",
        evs,
    )


def case4_coordinated() -> None:
    """案例 4：可疑协同传播
    多个低层级源（微博 + 百家号 + 聚合门户）在 1-2 天内集中发同一叙事。"""
    evs = [
        {
            "source_url": "https://weibo.com/1234/p1",
            "source_name": "@账号A",
            "source_type": "social_media",
            "agency": None,
            "published_at": "2024-08-15",
            "snippet": "某产品 XX 真的是个智商税……",
        },
        {
            "source_url": "https://weibo.com/5678/p2",
            "source_name": "@账号B",
            "source_type": "social_media",
            "agency": None,
            "published_at": "2024-08-15",
            "snippet": "刚买了 XX 后悔死了，全是噱头",
        },
        {
            "source_url": "https://baijiahao.baidu.com/s?id=p3",
            "source_name": "百家号·某测评号",
            "source_type": "content_farm",
            "agency": None,
            "published_at": "2024-08-15",
            "snippet": "深度测评：XX 产品的五大坑你别踩",
        },
        {
            "source_url": "https://news.sina.com.cn/c/p4",
            "source_name": "新浪新闻",
            "source_type": "mainstream_media",
            "agency": None,
            "published_at": "2024-08-16",
            "snippet": "XX 产品引发用户质疑：是创新还是噱头？",
        },
        {
            "source_url": "https://news.qq.com/a/p5",
            "source_name": "腾讯新闻",
            "source_type": "mainstream_media",
            "agency": None,
            "published_at": "2024-08-16",
            "snippet": "网友热议：XX 产品到底值不值？",
        },
    ]
    _print_timeline(
        "Case 4: 协同传播（低层级源 1-2 天内集中推送）",
        "XX产品是智商税",
        evs,
    )


if __name__ == "__main__":
    print("\n" + "█" * 78)
    print("  PropagationTimeline 演示 — 4 种真实传播模式")
    print("█" * 78)

    case1_grassroots_viral()
    case2_fact_check_corrected()
    case3_official_dissemination()
    case4_coordinated()

    print("\n" + "=" * 78)
    print("  说明：")
    print("=" * 78)
    print("""
  TimelineBuilder 从 evidence 列表（含 source_url + published_at）反推传播路径：

  1. URL 域名分类为 11 类 source_category（social_media / self_media / aggregator /
     mainstream_media / local_official_media / central_official_media / encyclopedia /
     official / fact_check_platform 等）
  2. 按时间戳排序
  3. 标记 is_likely_origin（最早有日期 + 非权威类）和 is_likely_amplifier（最晚 + 权威类）
  4. 模式识别（pattern + confidence + note）：
       - fact_check_corrected: 含 piyao.org.cn 节点（confidence 85）
       - official_dissemination: 起源 first_prio ≥ 6（官方/央媒/主流，confidence 70）
       - coordinated: 3+ 非权威源在 3 天内集中（confidence 70）
       - grassroots_viral: 起源 ≤ 3（社媒/自媒/聚合）+ 终至 ≥ 6（confidence 75）
       - narrative_distortion: 起源权威 → 终至低层（confidence 65）
       - insufficient_data: 数据不足或模式不明显

  集成位置：pipeline.py 在 evidence 收集后自动调用 TimelineBuilder，
  最终通过 CheckResponse.propagation_timeline 字段返回给调用方。
""")
