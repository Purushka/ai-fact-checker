"""score engine 单元测试。所有用例都是确定性纯计算，不需要外部依赖。"""
from __future__ import annotations

from datetime import date

from factcheck.score.engine import DEFAULT_WEIGHTS, ScoreEngine


def _make_engine() -> ScoreEngine:
    return ScoreEngine()


def test_weights_validation():
    engine = _make_engine()
    engine.validate_weights(DEFAULT_WEIGHTS)
    try:
        engine.validate_weights({"source_authority": 0.5, "source_consistency": 0.6,
                                 "freshness": 0.0, "completeness": 0.0, "claim_clarity": 0.0})
        assert False, "应抛 ValueError"
    except ValueError:
        pass


def test_unknown_dimension_rejected():
    engine = _make_engine()
    try:
        engine.validate_weights({"foo": 1.0})
        assert False
    except ValueError:
        pass


def test_supported_with_two_official_sources():
    engine = _make_engine()
    evidence = [
        {"source_type": "official", "authority_weight": 1.0, "support_level": "strong",
         "published_at": "2024-01-12", "content_fingerprint": "a", "is_independent": True,
         "claims_about": {"title": "test", "issuing_agency": "国务院", "publish_date": "2024-01-12"}},
        {"source_type": "regulator", "authority_weight": 0.97, "support_level": "strong",
         "published_at": "2024-01-11", "content_fingerprint": "b", "is_independent": True,
         "claims_about": {"title": "test", "issuing_agency": "财政部", "publish_date": "2024-01-11"}},
    ]
    r = engine.score(
        claim="2024年某政策于某日生效", mode="policy", evidence=evidence,
        today=date(2024, 6, 1),
    )
    assert r.verdict == "supported"
    assert r.confidence >= 70
    assert r.has_official_source is True


def test_expired_policy_gating():
    engine = _make_engine()
    evidence = [
        {"source_type": "official", "authority_weight": 0.9, "support_level": "strong",
         "published_at": "2020-03-01", "content_fingerprint": "x", "is_independent": True,
         "claims_about": {"title": "疫情房租减免", "issuing_agency": "武汉市政府"}},
    ]
    r = engine.score(
        claim="2020年武汉房租减免目前仍有效", mode="policy", evidence=evidence,
        policy_meta={"expire_date": "2021-01-01"},
        today=date(2026, 5, 21),
    )
    assert r.verdict == "outdated"
    assert r.policy_status == "expired"
    assert r.confidence <= 25
    assert any("expired" in g for g in r.gating_applied)


def test_contradicted_with_authority():
    engine = _make_engine()
    evidence = [
        {"source_type": "regulator", "authority_weight": 1.0, "support_level": "contradicted",
         "published_at": "2024-01-18", "content_fingerprint": "x", "is_independent": True,
         "claims_about": {"metric_value": "902"}},
    ]
    r = engine.score(
        claim="2023年新生儿1500万", mode="numeric", evidence=evidence,
        today=date(2024, 6, 1),
    )
    assert r.verdict == "contradicted"


def test_no_evidence_unverifiable():
    engine = _make_engine()
    r = engine.score(claim="某政策", mode="policy", evidence=[], today=date(2024, 6, 1))
    assert r.verdict == "unverifiable"
    assert r.confidence <= 30


def test_subjective_out_of_scope():
    engine = _make_engine()
    r = engine.score(claim="上海是中国最适合创业的城市", mode="general", evidence=[],
                     today=date(2024, 6, 1))
    assert r.verdict == "out_of_scope"


def test_consistency_independent_dedup():
    engine = _make_engine()
    evidence = [
        {"source_type": "authoritative_media", "authority_weight": 0.8, "support_level": "strong",
         "published_at": "2024-01-11", "content_fingerprint": "same", "is_independent": True,
         "claims_about": {"metric_value": "949.5"}},
        {"source_type": "mainstream_media", "authority_weight": 0.65, "support_level": "strong",
         "published_at": "2024-01-11", "content_fingerprint": "same", "is_independent": False,
         "claims_about": {}},
        {"source_type": "industry_association", "authority_weight": 0.85, "support_level": "strong",
         "published_at": "2024-01-11", "content_fingerprint": "other", "is_independent": True,
         "claims_about": {"data_source": "中汽协"}},
    ]
    r = engine.score(
        claim="2023 NEV 949.5万辆", mode="numeric", evidence=evidence,
        today=date(2024, 6, 1),
    )
    cons = r.breakdown["source_consistency"].score
    assert cons > 0
