"""信息传播时间线构造器 — 从 evidence 列表反推 claim/事件的传播路径。

用法：
    builder = TimelineBuilder()
    timeline = builder.build(evidences)

输出：
    PropagationTimeline 含按时间排序的 stops + 自动模式识别（grassroots_viral / official_dissemination / 等）
"""

from __future__ import annotations

from datetime import date
from urllib.parse import urlparse

from ..schemas import PropagationStop, PropagationTimeline


def _domain_to_category(domain: str, source_type: str | None = None) -> str:
    """URL 域名 → propagation source_category。"""
    d = domain.lower()
    # 辟谣
    if "piyao.org.cn" in d or "/piyao/" in d or "辟谣" in d:
        return "fact_check_platform"
    # 央媒
    if any(
        x in d
        for x in [
            "xinhuanet.com",
            "news.cn",
            "people.cn",
            "people.com.cn",
            "cctv.com",
            "cctv.cn",
            "cnr.cn",
            "qstheory.cn",
        ]
    ):
        return "central_official_media"
    # 地方融媒体（含 .gov.cn 但不是顶级部委、地方党媒）
    if d.endswith(".gov.cn") and source_type in ("official", "regulator"):
        return "official"
    if any(x in d for x in [".gov.cn", "rongmei", "融媒", "dzwww", "scol", "gmw.cn", "ce.cn"]):
        return "local_official_media"
    # 主流媒体
    if any(
        x in d
        for x in [
            "caixin.com",
            "yicai.com",
            "21jingji.com",
            "thepaper.cn",
            "nbd.com.cn",
            "stcn.com",
            "cs.com.cn",
        ]
    ):
        return "mainstream_media"
    # 百科
    if "baike" in d or "wikipedia" in d:
        return "encyclopedia"
    # 自媒体平台
    if any(
        x in d
        for x in [
            "mp.weixin",
            "weixin.qq",
            "baijiahao",
            "toutiao.com",
            "sohu.com/a",
            "sohu.com/dy",
            "163.com/dy",
            "qq.com/rain",
            "163.com/news/article",
        ]
    ):
        return "self_media"
    # 社交媒体
    if any(
        x in d
        for x in [
            "weibo.com",
            "weibo.cn",
            "zhihu.com",
            "douyin.com",
            "xiaohongshu.com",
            "bilibili.com",
            "tieba.baidu",
        ]
    ):
        return "social_media"
    # 聚合门户
    if any(x in d for x in ["sina.com.cn", "sina.cn", "163.com", "qq.com", "sohu.com", "ifeng.com"]):
        return "aggregator"
    # 官方（gov.cn 兜底）
    if ".gov.cn" in d:
        return "official"
    return "other"


CATEGORY_PRIORITY = {
    # 越早传播阶段越靠"上游"
    "social_media": 1,
    "self_media": 2,
    "aggregator": 3,
    "encyclopedia": 4,
    "local_official_media": 5,
    "mainstream_media": 6,
    "central_official_media": 7,
    "official": 8,
    "fact_check_platform": 9,  # 辟谣是事件的"终结"节点
    "other": 5,
}


class TimelineBuilder:
    def build(self, evidences: list[dict]) -> PropagationTimeline:
        stops: list[PropagationStop] = []
        for e in evidences:
            url = e.get("source_url", "")
            if not url:
                continue
            domain = urlparse(url).hostname or ""
            cat = _domain_to_category(domain, e.get("source_type"))
            pub = e.get("published_at")
            stops.append(
                PropagationStop(
                    source_url=url,
                    source_name=e.get("agency") or e.get("source_name") or domain,
                    source_category=cat,  # type: ignore[arg-type]
                    earliest_seen=pub,
                    snippet=(e.get("snippet") or "")[:200] or None,
                )
            )

        # 按 earliest_seen 排序（None 排到末尾）
        def _sort_key(s: PropagationStop) -> tuple[int, str]:
            return (0, s.earliest_seen) if s.earliest_seen else (1, "")

        stops.sort(key=_sort_key)

        # 标记 likely origin（最早 + 类型偏上游）和 amplifier（最晚 + 类型偏权威）
        if stops:
            # 找最早有日期的 + 类型 ∈ {social_media, self_media, aggregator}
            for s in stops:
                if s.earliest_seen and s.source_category in ("social_media", "self_media", "aggregator"):
                    s.is_likely_origin = True
                    break
            # 找最晚有日期的 + 类型 ∈ {central_official_media, official, fact_check_platform}
            for s in reversed(stops):
                if s.earliest_seen and s.source_category in (
                    "central_official_media",
                    "official",
                    "fact_check_platform",
                ):
                    s.is_likely_amplifier = True
                    break

        # 时间范围
        dates = [s.earliest_seen for s in stops if s.earliest_seen]
        earliest = min(dates) if dates else None
        latest = max(dates) if dates else None

        # 模式识别
        pattern, conf, note = self._detect_pattern(stops, dates)

        return PropagationTimeline(
            earliest_date=earliest,
            latest_date=latest,
            n_stops=len(stops),
            stops=stops,
            pattern=pattern,  # type: ignore[arg-type]
            confidence=conf,
            note=note,
        )

    def _detect_pattern(self, stops: list[PropagationStop], dates: list[str]) -> tuple[str, int, str]:
        if len(stops) < 2 or len(dates) < 2:
            return "insufficient_data", 20, "时间数据不足 2 个，无法判断模式"

        cats = [s.source_category for s in stops if s.earliest_seen]
        if not cats:
            return "insufficient_data", 20, "无 published_at 数据"

        has_factcheck = "fact_check_platform" in cats
        first_cat = cats[0]
        last_cat = cats[-1]
        first_prio = CATEGORY_PRIORITY.get(first_cat, 5)
        last_prio = CATEGORY_PRIORITY.get(last_cat, 5)

        if has_factcheck:
            return (
                "fact_check_corrected",
                85,
                f"时间线含辟谣节点（fact_check_platform），起点 {first_cat}, 终点 {last_cat}",
            )

        # 时间窗口内分布
        try:
            date_objs = sorted([date.fromisoformat(d[:10]) for d in dates])
            span_days = (date_objs[-1] - date_objs[0]).days
        except (ValueError, IndexError):
            span_days = -1

        # official_dissemination 优先于 coordinated：权威源短期连续发布是正常新闻级联，
        # 不属于"协同传播"（后者特指低层级源短时间内一致推送同一叙事）
        if first_prio >= 6:
            return (
                "official_dissemination",
                70,
                f"起源主流/官方 {first_cat}，常规信息发布",
            )

        # coordinated 仅在起点非权威（first_prio <= 4，即 social_media/self_media/aggregator/encyclopedia）
        # 且短时间内多源齐发时才成立
        if span_days >= 0 and span_days <= 3 and len(stops) >= 3 and first_prio <= 4:
            return (
                "coordinated",
                70,
                f"{len(stops)} 个非权威源在 {span_days} 天内集中出现，疑似协同传播",
            )

        if first_prio <= 3 and last_prio >= 6:
            return (
                "grassroots_viral",
                75,
                f"起源 {first_cat} → 终至 {last_cat}，典型病毒传播路径",
            )

        if first_prio >= 7 and last_prio <= 3:
            return (
                "narrative_distortion",
                65,
                f"起源权威 {first_cat} → 终至 {last_cat}，疑似主流→自媒体转述变形",
            )

        return (
            "insufficient_data",
            40,
            f"起 {first_cat} → 终 {last_cat}，模式不明显",
        )
