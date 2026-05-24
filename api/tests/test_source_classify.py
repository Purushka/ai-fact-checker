"""source_classify 单元测试。"""
from __future__ import annotations

from factcheck.score.source_classify import SourceClassifier


def test_classify_gov_cn_root():
    c = SourceClassifier()
    r = c.classify("http://www.gov.cn/zhengce/zhengceku/2024/content.htm")
    assert r.source_type == "official"
    assert r.authority_weight == 1.0


def test_classify_chinatax():
    c = SourceClassifier()
    r = c.classify("https://www.chinatax.gov.cn/n810219/n810744/content.html")
    assert r.source_type == "regulator"
    assert r.agency == "国家税务总局"


def test_classify_qd_city():
    c = SourceClassifier()
    r = c.classify("http://qd.gov.cn/n172/index.html")
    assert r.tier == "tier3_city"
    assert r.authority_weight == 0.88


def test_classify_unknown_gov_subdomain_pattern():
    c = SourceClassifier()
    r = c.classify("https://hrss.somecity.gov.cn/page.html")
    assert r.source_type == "official"
    assert r.matched_by.startswith("pattern:")


def test_classify_zhihu_low_weight():
    c = SourceClassifier()
    r = c.classify("https://zhuanlan.zhihu.com/p/12345")
    assert r.source_type == "social_media"
    assert r.authority_weight <= 0.30


def test_classify_default_fallback():
    c = SourceClassifier()
    r = c.classify("https://random-site.example.com/page")
    assert r.matched_by == "default_fallback"
    assert r.authority_weight == 0.30


def test_classify_invalid_url():
    c = SourceClassifier()
    r = c.classify("not a url")
    assert r.source_type == "unknown"
    assert r.is_blacklisted is False


def test_classify_caam():
    c = SourceClassifier()
    r = c.classify("http://www.caam.org.cn/chn/4/cate_31/con_5238088.html")
    assert r.source_type == "industry_association"
    assert r.authority_weight == 0.85
