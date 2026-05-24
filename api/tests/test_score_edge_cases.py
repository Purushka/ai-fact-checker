"""ScoreEngine 边界与失败注入测试。"""
from __future__ import annotations

from datetime import date

import pytest

from factcheck.score.engine import DEFAULT_WEIGHTS, ScoreEngine


@pytest.fixture
def engine() -> ScoreEngine:
    return ScoreEngine()


def _ev(authority=0.9, support="strong", published="2025-01-01", fingerprint="a",
        source_type="official", **claims):
    return {
        "source_type": source_type,
        "authority_weight": authority,
        "support_level": support,
        "published_at": published,
        "content_fingerprint": fingerprint,
        "is_independent": True,
        "claims_about": claims,
    }


def test_custom_weights_override(engine):
    """调用方传入自定义权重应该生效。"""
    custom = {"source_authority": 0.6, "source_consistency": 0.4,
              "freshness": 0.0, "completeness": 0.0, "claim_clarity": 0.0}
    r = engine.score(
        claim="2023年某事件发生", mode="numeric",
        evidence=[_ev(authority=1.0, support="strong")],
        weights=custom, today=date(2024, 6, 1),
    )
    assert r.breakdown["source_authority"].weight == 0.6
    assert r.breakdown["freshness"].weighted == 0.0


def test_unresolved_conflict_becomes_conflicting(engine):
    evidence = [
        _ev(authority=1.0, claims_about={"value": "A"}),
        _ev(authority=0.97, fingerprint="b", claims_about={"value": "B"}),
    ]
    conflicts = [{"field": "value", "values": ["A", "B"], "level": "high", "resolved": False}]
    r = engine.score(
        claim="2025年某事件", mode="policy",
        evidence=evidence, conflicts=conflicts,
        today=date(2025, 6, 1),
    )
    assert r.verdict == "conflicting"
    assert r.confidence <= 35
    assert "conflict_capped_35" in r.gating_applied


def test_resolved_conflict_does_not_block(engine):
    """已仲裁的 conflict 不应该再 cap confidence。"""
    evidence = [
        _ev(authority=1.0, claims_about={"value": "A"}),
        _ev(authority=0.97, fingerprint="b", claims_about={"value": "A"}),
    ]
    conflicts = [{"field": "value", "values": ["A", "B"], "level": "low",
                  "resolved": True, "arbitration_note": "层级仲裁"}]
    r = engine.score(
        claim="2025年某事件发生", mode="policy",
        evidence=evidence, conflicts=conflicts,
        today=date(2025, 6, 1),
    )
    assert r.verdict != "conflicting"
    assert "conflict_capped_35" not in r.gating_applied


def test_require_official_caps_when_only_media(engine):
    evidence = [
        _ev(authority=0.65, source_type="mainstream_media", published="2024-12-01"),
        _ev(authority=0.7, source_type="mainstream_media", fingerprint="b", published="2024-12-02"),
    ]
    r = engine.score(
        claim="2024年某事件 12月", mode="numeric",
        evidence=evidence,
        require_official_source=True,
        today=date(2025, 2, 1),
    )
    assert "no_official_source_capped_40" in r.gating_applied
    assert r.confidence <= 40


def test_policy_no_official_caps_at_35(engine):
    evidence = [
        _ev(authority=0.7, source_type="mainstream_media"),
        _ev(authority=0.55, source_type="industry_report", fingerprint="b"),
    ]
    r = engine.score(
        claim="2025年某政策实施", mode="policy",
        evidence=evidence,
        today=date(2025, 6, 1),
    )
    assert "policy_no_official_capped_35" in r.gating_applied
    assert r.confidence <= 40


def test_fingerprint_dedup_reduces_independent_count(engine):
    """3 条 fingerprint 相同的 evidence 应只算 1 个独立源；0.85 + strong → 65。"""
    evidence = [
        _ev(authority=0.85, fingerprint="x"),
        _ev(authority=0.65, fingerprint="x", source_type="mainstream_media"),
        _ev(authority=0.55, fingerprint="x", source_type="industry_report"),
    ]
    r = engine.score(
        claim="2024年某事件 6月", mode="numeric",
        evidence=evidence,
        today=date(2024, 12, 1),
    )
    assert r.breakdown["source_consistency"].score == 65.0


def test_consistency_neutral_excluded_from_denominator(engine):
    """neutral 的 evidence 不应算入相关源比例。1 strong tier1 + 2 neutral → 70。"""
    evidence = [
        _ev(authority=1.0, fingerprint="a", support="strong"),
        _ev(authority=1.0, fingerprint="b", support="neutral"),
        _ev(authority=0.85, fingerprint="c", support="neutral"),
    ]
    r = engine.score(
        claim="2025年某事件 5月", mode="general", evidence=evidence,
        today=date(2025, 6, 1),
    )
    assert r.breakdown["source_consistency"].score == 70.0


def test_consistency_all_neutral_returns_30(engine):
    """全 neutral 应判 30 分（无可定性）。"""
    evidence = [_ev(authority=0.9, fingerprint=f"e{i}", support="neutral") for i in range(3)]
    r = engine.score(
        claim="2025年某事件 5月", mode="general", evidence=evidence,
        today=date(2025, 6, 1),
    )
    assert r.breakdown["source_consistency"].score == 30.0


def test_clarity_zero_when_subjective(engine):
    r = engine.score(
        claim="北京是中国最适合居住的城市", mode="general",
        evidence=[_ev()],
        today=date(2024, 6, 1),
    )
    assert r.verdict == "out_of_scope"


def test_clarity_drops_with_vague_words(engine):
    r = engine.score(
        claim="近年来某行业销量大幅增长", mode="general",
        evidence=[_ev()],
        today=date(2024, 6, 1),
    )
    assert r.breakdown["claim_clarity"].score < 80


def test_freshness_for_old_permanent_law(engine):
    """6 年以上、无 expire_date 的政策（永久性法规）freshness 仍较高。"""
    evidence = [_ev(authority=0.95, source_type="regulator", published="2019-01-01")]
    r = engine.score(
        claim="2019年某政策", mode="policy",
        evidence=evidence, policy_meta={"expire_date": None, "superseded_by": None},
        today=date(2025, 6, 1),
    )
    assert r.breakdown["freshness"].score >= 72
    assert r.policy_status == "active"


def test_freshness_for_old_time_limited(engine):
    """有 expire_date 的旧政策 freshness 走原 buckets。"""
    evidence = [_ev(authority=0.95, source_type="regulator", published="2019-01-01")]
    r = engine.score(
        claim="2019年某限时政策", mode="policy",
        evidence=evidence, policy_meta={"expire_date": "2027-12-31", "deadline": "2027-12-31"},
        today=date(2025, 6, 1),
    )
    assert r.breakdown["freshness"].score == 35.0
    assert r.policy_status == "active"


def test_single_tier1_authority_consistency_70(engine):
    """单 tier1 source（authority>=0.95 + support=strong）consistency 70，不再是孤证 50。"""
    evidence = [_ev(authority=1.0, support="strong", source_type="official")]
    r = engine.score(
        claim="2025年某事件 5月", mode="general",
        evidence=evidence,
        today=date(2025, 6, 1),
    )
    assert r.breakdown["source_consistency"].score == 70.0


def test_freshness_for_state_check_recent(engine):
    evidence = [_ev(authority=0.8, source_type="authoritative_media", published="2025-04-01")]
    r = engine.score(
        claim="2025年 4月某机构负责人", mode="entity_status",
        evidence=evidence,
        today=date(2025, 6, 1),
    )
    assert r.breakdown["freshness"].score == 90.0


def test_invalid_weights_sum_raises(engine):
    with pytest.raises(ValueError) as ex:
        engine.score(
            claim="x", mode="general", evidence=[_ev()],
            weights={"source_authority": 0.5, "source_consistency": 0.5,
                     "freshness": 0.5, "completeness": 0.0, "claim_clarity": 0.0},
        )
    assert "权重和必须为 1.0" in str(ex.value)


def test_completeness_with_missing_required_fields(engine):
    """policy mode 要求 title/issuing_agency/document_number/publish_date/applicable_region/applicable_subjects 必填。
    全缺时 completeness penalty 应被应用。"""
    evidence = [_ev(authority=0.85, source_type="industry_association", source_url="x")]
    r = engine.score(
        claim="2024年某政策", mode="policy",
        evidence=evidence,
        today=date(2024, 6, 1),
    )
    assert r.breakdown["completeness"].score < 30
    assert any("缺必填" in n for n in r.notes if "[completeness]" in n)


def test_multiple_high_authority_sources_bonus(engine):
    """≥2 个 ≥0.9 权威源应该有 bonus。"""
    evidence = [
        _ev(authority=1.0, fingerprint="a"),
        _ev(authority=0.97, fingerprint="b"),
        _ev(authority=0.95, fingerprint="c"),
    ]
    r = engine.score(
        claim="2024年某事件 6月", mode="numeric",
        evidence=evidence,
        today=date(2024, 12, 1),
    )
    assert r.breakdown["source_authority"].score == 100  # 1.0*100 capped


def test_only_contradicted_evidence_gives_zero_authority(engine):
    """所有 evidence 都 contradicted 时，authority_score 应为 0（非反向高分）。"""
    evidence = [
        _ev(authority=1.0, support="contradicted"),
        _ev(authority=0.97, support="contradicted", fingerprint="b"),
    ]
    r = engine.score(
        claim="某错误事实", mode="numeric",
        evidence=evidence,
        today=date(2024, 6, 1),
    )
    assert r.breakdown["source_authority"].score == 0
    assert r.verdict == "contradicted"


def test_partial_supported_with_medium_consistency(engine):
    """3 个独立源、2 支持 1 反驳 → consistency 中等。"""
    evidence = [
        _ev(authority=0.9, fingerprint="a", support="strong"),
        _ev(authority=0.85, fingerprint="b", support="strong"),
        _ev(authority=0.65, fingerprint="c", support="contradicted", source_type="mainstream_media"),
    ]
    r = engine.score(
        claim="2024年某事件 5月", mode="numeric",
        evidence=evidence,
        today=date(2024, 8, 1),
    )
    # 2 / 3 ≈ 0.67 → score = 70
    assert 50 <= r.breakdown["source_consistency"].score <= 75
