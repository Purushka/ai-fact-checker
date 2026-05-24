"""TimelineBuilder 单元测试 — 域名分类 + 模式识别 + 起源/扩散标记。"""

from __future__ import annotations

import pytest

from factcheck.extract.timeline_builder import TimelineBuilder, _domain_to_category


@pytest.fixture
def builder() -> TimelineBuilder:
    return TimelineBuilder()


def _ev(url: str, published: str | None = None, agency: str | None = None) -> dict:
    return {
        "source_url": url,
        "source_name": agency or url,
        "source_type": "official" if ".gov.cn" in url else "mainstream_media",
        "agency": agency,
        "published_at": published,
        "snippet": "x",
    }


# ============ 域名分类 ============

def test_classify_weibo_as_social_media():
    assert _domain_to_category("weibo.com") == "social_media"
    assert _domain_to_category("m.weibo.cn") == "social_media"


def test_classify_baijiahao_as_self_media():
    assert _domain_to_category("baijiahao.baidu.com") == "self_media"
    assert _domain_to_category("mp.weixin.qq.com") == "self_media"


def test_classify_xinhua_as_central_official_media():
    assert _domain_to_category("www.xinhuanet.com") == "central_official_media"
    assert _domain_to_category("news.cn") == "central_official_media"
    assert _domain_to_category("www.people.com.cn") == "central_official_media"
    assert _domain_to_category("cctv.com") == "central_official_media"


def test_classify_piyao_as_factcheck():
    assert _domain_to_category("www.piyao.org.cn") == "fact_check_platform"


def test_classify_gov_cn_root_as_official():
    assert _domain_to_category("www.gov.cn", "official") == "official"


def test_classify_baike_as_encyclopedia():
    assert _domain_to_category("baike.baidu.com") == "encyclopedia"


def test_classify_sina_as_aggregator():
    assert _domain_to_category("www.sina.com.cn") == "aggregator"
    assert _domain_to_category("news.qq.com") == "aggregator"


# ============ Timeline 构建 ============

def test_build_empty_returns_insufficient_data(builder: TimelineBuilder):
    t = builder.build([])
    assert t.n_stops == 0
    assert t.pattern == "insufficient_data"


def test_build_sorted_by_date(builder: TimelineBuilder):
    evs = [
        _ev("https://news.cn/a", "2024-08-25"),
        _ev("https://weibo.com/p1", "2024-08-15"),
        _ev("https://baijiahao.baidu.com/x", "2024-08-16"),
    ]
    t = builder.build(evs)
    assert t.n_stops == 3
    # 检查排序
    dates = [s.earliest_seen for s in t.stops]
    assert dates == ["2024-08-15", "2024-08-16", "2024-08-25"]


def test_grassroots_viral_pattern(builder: TimelineBuilder):
    """微博起源 → 央媒收尾。"""
    evs = [
        _ev("https://weibo.com/post/123", "2024-08-15"),
        _ev("https://baijiahao.baidu.com/x", "2024-08-16"),
        _ev("https://www.thepaper.cn/news", "2024-08-18"),
        _ev("https://news.cn/breaking", "2024-08-22"),
    ]
    t = builder.build(evs)
    assert t.pattern == "grassroots_viral"
    assert t.confidence >= 70


def test_fact_check_corrected_pattern(builder: TimelineBuilder):
    """含 piyao 节点 → fact_check_corrected。"""
    evs = [
        _ev("https://weibo.com/rumor", "2024-08-15"),
        _ev("https://www.piyao.org.cn/20240820/abc/c.html", "2024-08-22"),
    ]
    t = builder.build(evs)
    assert t.pattern == "fact_check_corrected"
    assert t.confidence >= 80


def test_official_dissemination_pattern(builder: TimelineBuilder):
    """国务院 → 央媒 → 主流，典型政府信息发布。"""
    evs = [
        _ev("https://www.gov.cn/policy", "2024-05-20", agency="国务院"),
        _ev("https://news.cn/coverage", "2024-05-21"),
        _ev("https://www.caixin.com/coverage", "2024-05-23"),
    ]
    t = builder.build(evs)
    assert t.pattern == "official_dissemination"


def test_coordinated_pattern(builder: TimelineBuilder):
    """多源 1-2 天内集中出现 → 协同传播。"""
    evs = [
        _ev("https://weibo.com/p1", "2024-08-15"),
        _ev("https://baijiahao.baidu.com/p2", "2024-08-15"),
        _ev("https://news.sina.com.cn/p3", "2024-08-16"),
        _ev("https://news.qq.com/p4", "2024-08-16"),
    ]
    t = builder.build(evs)
    assert t.pattern == "coordinated"


def test_is_likely_origin_marked_on_earliest_social(builder: TimelineBuilder):
    evs = [
        _ev("https://weibo.com/first", "2024-08-15"),
        _ev("https://news.cn/coverage", "2024-08-25"),
    ]
    t = builder.build(evs)
    origins = [s for s in t.stops if s.is_likely_origin]
    assert len(origins) == 1
    assert "weibo.com" in origins[0].source_url


def test_is_likely_amplifier_marked_on_latest_authoritative(builder: TimelineBuilder):
    evs = [
        _ev("https://weibo.com/first", "2024-08-15"),
        _ev("https://news.cn/coverage", "2024-08-25"),
    ]
    t = builder.build(evs)
    amplifiers = [s for s in t.stops if s.is_likely_amplifier]
    assert len(amplifiers) == 1
    assert "news.cn" in amplifiers[0].source_url


def test_dates_aggregated_into_earliest_latest(builder: TimelineBuilder):
    evs = [
        _ev("https://x.com/a", "2024-08-15"),
        _ev("https://x.com/b", "2024-08-25"),
        _ev("https://x.com/c", "2024-08-20"),
    ]
    t = builder.build(evs)
    assert t.earliest_date == "2024-08-15"
    assert t.latest_date == "2024-08-25"


def test_evidence_without_dates_no_pattern(builder: TimelineBuilder):
    evs = [_ev("https://weibo.com/x", None), _ev("https://news.cn/y", None)]
    t = builder.build(evs)
    assert t.n_stops == 2
    assert t.pattern == "insufficient_data"
