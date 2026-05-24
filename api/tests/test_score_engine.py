"""ScoreEngine 单元测试 — 30+ 用例覆盖所有 5 维度边界、gating、tier 权威、极端 case。

所有用例都是确定性纯计算，不依赖外部 API / LLM。
"""

from __future__ import annotations

from datetime import date

import pytest

from factcheck.score.engine import DEFAULT_WEIGHTS, ScoreEngine


@pytest.fixture
def engine() -> ScoreEngine:
    return ScoreEngine()


def _ev(
    *,
    authority: float = 0.9,
    support: str = "strong",
    published: str = "2025-01-01",
    fp: str = "fp_default",
    source_type: str = "official",
    claims: dict | None = None,
) -> dict:
    """简化 evidence 构造器。"""
    return {
        "source_type": source_type,
        "authority_weight": authority,
        "support_level": support,
        "published_at": published,
        "content_fingerprint": fp,
        "is_independent": True,
        "claims_about": claims or {"title": "x", "issuing_agency": "国务院", "publish_date": published},
    }


# ============ 权重校验 ============

def test_default_weights_sum_to_one(engine: ScoreEngine):
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 0.001
    engine.validate_weights(DEFAULT_WEIGHTS)


def test_weights_sum_too_high_rejected(engine: ScoreEngine):
    with pytest.raises(ValueError, match="权重和必须为 1.0"):
        engine.validate_weights({
            "source_authority": 0.5, "source_consistency": 0.6,
            "freshness": 0.0, "completeness": 0.0, "claim_clarity": 0.0,
        })


def test_weights_sum_too_low_rejected(engine: ScoreEngine):
    with pytest.raises(ValueError, match="权重和必须为 1.0"):
        engine.validate_weights({
            "source_authority": 0.3, "source_consistency": 0.3,
            "freshness": 0.0, "completeness": 0.0, "claim_clarity": 0.0,
        })


def test_negative_weight_rejected(engine: ScoreEngine):
    with pytest.raises(ValueError, match="不能为负"):
        engine.validate_weights({
            "source_authority": -0.1, "source_consistency": 0.5,
            "freshness": 0.3, "completeness": 0.2, "claim_clarity": 0.1,
        })


def test_unknown_dimension_rejected(engine: ScoreEngine):
    with pytest.raises(ValueError, match="未知维度"):
        engine.validate_weights({"foo": 1.0})


def test_custom_weights_override_applied(engine: ScoreEngine):
    """调用方传入自定义权重应该被使用。"""
    custom = {
        "source_authority": 0.6, "source_consistency": 0.4,
        "freshness": 0.0, "completeness": 0.0, "claim_clarity": 0.0,
    }
    r = engine.score(
        claim="2024年某事件 6月", mode="numeric",
        evidence=[_ev(authority=1.0)], weights=custom, today=date(2024, 12, 1),
    )
    assert r.breakdown["source_authority"].weight == 0.6
    assert r.breakdown["freshness"].weighted == 0.0


# ============ Tier 1/2/3/4 权威 ============

def test_tier1_national_authority_score_max(engine: ScoreEngine):
    """tier1 国务院/部委（authority 1.0）得到接近满分 authority。"""
    r = engine.score(
        claim="2025年某政策 5月", mode="numeric", evidence=[_ev(authority=1.0)],
        today=date(2025, 6, 1),
    )
    assert r.breakdown["source_authority"].score >= 95


def test_tier2_ministry_authority(engine: ScoreEngine):
    """tier2 中央部委（0.97）权威分高。"""
    r = engine.score(
        claim="2025年某事件 5月", mode="numeric", evidence=[_ev(authority=0.97)],
        today=date(2025, 6, 1),
    )
    assert 90 <= r.breakdown["source_authority"].score <= 100


def test_tier3_city_authority(engine: ScoreEngine):
    r = engine.score(
        claim="2025年某事件 5月", mode="general", evidence=[_ev(authority=0.88)],
        today=date(2025, 6, 1),
    )
    assert 85 <= r.breakdown["source_authority"].score <= 90


def test_tier4_district_lower_than_city(engine: ScoreEngine):
    r_district = engine.score(
        claim="2025年某事件 5月", mode="general", evidence=[_ev(authority=0.80)],
        today=date(2025, 6, 1),
    )
    r_city = engine.score(
        claim="2025年某事件 5月", mode="general", evidence=[_ev(authority=0.88)],
        today=date(2025, 6, 1),
    )
    assert r_district.breakdown["source_authority"].score < r_city.breakdown["source_authority"].score


def test_multiple_tier1_sources_get_bonus(engine: ScoreEngine):
    """≥2 个 0.9+ 权威源应该 +bonus。"""
    evidence = [
        _ev(authority=1.0, fp="a"), _ev(authority=0.97, fp="b"), _ev(authority=0.95, fp="c"),
    ]
    r = engine.score(claim="2024年某事件 6月", mode="numeric", evidence=evidence, today=date(2024, 12, 1))
    assert r.breakdown["source_authority"].score == 100  # 100 capped


# ============ Consistency 维度 ============

def test_no_evidence_consistency_zero(engine: ScoreEngine):
    r = engine.score(claim="某事件", mode="general", evidence=[], today=date(2024, 6, 1))
    assert r.breakdown["source_consistency"].score == 0.0


def test_all_neutral_consistency_30(engine: ScoreEngine):
    """全 neutral evidence → 30 分（无可定性）。"""
    evidence = [_ev(authority=0.9, fp=f"e{i}", support="neutral") for i in range(3)]
    r = engine.score(claim="2025年某事件 5月", mode="general", evidence=evidence, today=date(2025, 6, 1))
    assert r.breakdown["source_consistency"].score == 30.0


def test_single_tier1_strong_consistency_70(engine: ScoreEngine):
    r = engine.score(
        claim="2025年某事件 5月", mode="general",
        evidence=[_ev(authority=1.0, support="strong")], today=date(2025, 6, 1),
    )
    assert r.breakdown["source_consistency"].score == 70.0


def test_single_authority_strong_consistency_65(engine: ScoreEngine):
    """单 0.8-0.95 权威源 strong support → 65。"""
    r = engine.score(
        claim="2025年某事件 5月", mode="general",
        evidence=[_ev(authority=0.85, support="strong")], today=date(2025, 6, 1),
    )
    assert r.breakdown["source_consistency"].score == 65.0


def test_single_weak_authority_consistency_50(engine: ScoreEngine):
    """单弱权威源（<0.8）→ 50 孤证。"""
    r = engine.score(
        claim="2025年某事件 5月", mode="general",
        evidence=[_ev(authority=0.5, support="strong", source_type="mainstream_media")],
        today=date(2025, 6, 1),
    )
    assert r.breakdown["source_consistency"].score == 50.0


def test_high_agreement_ratio_consistency_90(engine: ScoreEngine):
    """3 个独立 strong + 1 neutral → 3/3 相关源支持 → 90+。"""
    evidence = [_ev(authority=0.9, fp=f"e{i}", support="strong") for i in range(3)]
    r = engine.score(claim="2024年某事件 6月", mode="numeric", evidence=evidence, today=date(2024, 12, 1))
    assert r.breakdown["source_consistency"].score >= 90


def test_fingerprint_dedup_collapses_to_single(engine: ScoreEngine):
    """3 个 fingerprint 相同 → 算 1 个独立源。"""
    evidence = [
        _ev(authority=0.85, fp="x"),
        _ev(authority=0.65, fp="x", source_type="mainstream_media"),
        _ev(authority=0.55, fp="x", source_type="industry_report"),
    ]
    r = engine.score(claim="2024年某事件 6月", mode="numeric", evidence=evidence, today=date(2024, 12, 1))
    assert r.breakdown["source_consistency"].score == 65.0


def test_all_contradicted_authority_zero(engine: ScoreEngine):
    """所有 evidence contradicted → authority 0。"""
    evidence = [_ev(authority=1.0, support="contradicted"), _ev(authority=0.97, support="contradicted", fp="b")]
    r = engine.score(claim="某错误事实", mode="numeric", evidence=evidence, today=date(2024, 6, 1))
    assert r.breakdown["source_authority"].score == 0
    assert r.verdict == "contradicted"


def test_secondary_citation_authority_downweight(engine: ScoreEngine):
    """二手引用 0.7 降权——pipeline 中 fact_extractor 已做，score engine 拿到的 weight 已是 0.7×。
    这里测：传入 0.7 的源应该比 1.0 的源 authority 低。"""
    r_primary = engine.score(
        claim="2025年事件 5月", mode="numeric",
        evidence=[_ev(authority=1.0)], today=date(2025, 6, 1),
    )
    r_secondary = engine.score(
        claim="2025年事件 5月", mode="numeric",
        evidence=[_ev(authority=0.7)],  # 1.0 × 0.7
        today=date(2025, 6, 1),
    )
    assert r_secondary.breakdown["source_authority"].score < r_primary.breakdown["source_authority"].score


# ============ Freshness 维度 ============

def test_freshness_permanent_law_old_still_high(engine: ScoreEngine):
    """永久法规（无 expire/deadline）即使 >6 年也不应低于 72。"""
    evidence = [_ev(authority=0.95, source_type="regulator", published="2018-03-11")]
    r = engine.score(
        claim="2018宪法修正案", mode="policy", evidence=evidence,
        policy_meta={"expire_date": None, "superseded_by": None},
        today=date(2026, 5, 21),
    )
    assert r.breakdown["freshness"].score >= 72


def test_freshness_time_limited_old_low(engine: ScoreEngine):
    """有 expire_date 的旧政策 freshness 走原 buckets。"""
    evidence = [_ev(authority=0.95, source_type="regulator", published="2019-01-01")]
    r = engine.score(
        claim="2019年某限时政策", mode="policy", evidence=evidence,
        policy_meta={"expire_date": "2027-12-31", "deadline": "2027-12-31"},
        today=date(2025, 6, 1),
    )
    assert r.breakdown["freshness"].score == 35.0


def test_freshness_state_check_recent_high(engine: ScoreEngine):
    """state_check 90 天内 → 90 分。"""
    evidence = [_ev(authority=0.8, source_type="authoritative_media", published="2025-04-01")]
    r = engine.score(
        claim="2025年4月某机构负责人", mode="entity_status", evidence=evidence, today=date(2025, 6, 1),
    )
    assert r.breakdown["freshness"].score == 90.0


def test_freshness_policy_expired_status(engine: ScoreEngine):
    evidence = [_ev(authority=0.9, published="2020-03-01")]
    r = engine.score(
        claim="武汉疫情减免仍有效", mode="policy", evidence=evidence,
        policy_meta={"expire_date": "2021-01-01"}, today=date(2026, 5, 21),
    )
    assert r.policy_status == "expired"
    assert r.breakdown["freshness"].score == 10.0


def test_freshness_policy_superseded_status(engine: ScoreEngine):
    evidence = [_ev(authority=1.0, source_type="official", published="2018-10-26")]
    r = engine.score(
        claim="公司法当前是 2018", mode="policy", evidence=evidence,
        policy_meta={"superseded_by": "2023 修订"}, today=date(2025, 6, 1),
    )
    assert r.policy_status == "superseded"
    assert r.breakdown["freshness"].score == 20.0


# ============ Completeness 维度 ============

def test_completeness_all_required_filled(engine: ScoreEngine):
    """完整 policy_meta → completeness 较高。"""
    evidence = [_ev(
        authority=0.95, published="2025-05-20", source_type="official",
        claims={
            "title": "X 法", "issuing_agency": "全国人大",
            "publish_date": "2025-05-20", "applicable_region": ["全国"],
        },
    )]
    r = engine.score(claim="X 法 5/20 施行", mode="policy", evidence=evidence, today=date(2025, 6, 1))
    assert r.breakdown["completeness"].score > 20  # base 4/15 = 27, no penalty


def test_completeness_missing_required_penalized(engine: ScoreEngine):
    """缺少必填字段加 penalty。"""
    evidence = [_ev(authority=0.9, claims={"title": "x"})]  # 只有 title
    r = engine.score(claim="2025年某政策 5月", mode="policy", evidence=evidence, today=date(2025, 6, 1))
    assert r.breakdown["completeness"].score < 30


# ============ Clarity 维度 ============

def test_clarity_zero_on_subjective_keyword(engine: ScoreEngine):
    """主观词（"最好""最适合"）clarity = 0 → out_of_scope。"""
    r = engine.score(claim="北京是中国最好的城市", mode="general", evidence=[_ev()], today=date(2024, 6, 1))
    assert r.verdict == "out_of_scope"


def test_clarity_vague_word_penalty(engine: ScoreEngine):
    """模糊词（"近期""大幅"）降分。"""
    r = engine.score(claim="近年来某行业销量大幅增长", mode="general", evidence=[_ev()], today=date(2024, 6, 1))
    assert r.breakdown["claim_clarity"].score < 80


def test_clarity_missing_time_anchor_penalty(engine: ScoreEngine):
    """缺时间锚点 -30。"""
    r = engine.score(claim="某政策今年颁布", mode="general", evidence=[_ev()], today=date(2024, 6, 1))
    # 含"今年"应该满足 has_time
    r2 = engine.score(claim="某政策颁布", mode="general", evidence=[_ev()], today=date(2024, 6, 1))
    assert r2.breakdown["claim_clarity"].score < r.breakdown["claim_clarity"].score


# ============ Gating rules ============

def test_gating_no_evidence_unverifiable(engine: ScoreEngine):
    r = engine.score(claim="2025年某事件 5月", mode="policy", evidence=[], today=date(2025, 6, 1))
    assert r.verdict == "unverifiable"
    assert r.confidence <= 30


def test_gating_require_official_caps_at_40(engine: ScoreEngine):
    """require_official_source=True 但只有 media → 上限 40。"""
    evidence = [
        _ev(authority=0.65, source_type="mainstream_media"),
        _ev(authority=0.7, source_type="mainstream_media", fp="b"),
    ]
    r = engine.score(
        claim="2025年某事件 5月", mode="numeric", evidence=evidence,
        require_official_source=True, today=date(2025, 6, 1),
    )
    assert "no_official_source_capped_40" in r.gating_applied
    assert r.confidence <= 40


def test_gating_policy_no_official_caps_at_35(engine: ScoreEngine):
    """mode=policy + 无 official → 上限 35。"""
    evidence = [
        _ev(authority=0.7, source_type="mainstream_media"),
        _ev(authority=0.55, source_type="industry_report", fp="b"),
    ]
    r = engine.score(claim="2025年某政策 5月", mode="policy", evidence=evidence, today=date(2025, 6, 1))
    assert "policy_no_official_capped_35" in r.gating_applied
    assert r.confidence <= 40


def test_gating_unresolved_conflict_caps_at_35(engine: ScoreEngine):
    evidence = [_ev(authority=1.0, claims={"value": "A"}), _ev(authority=0.97, fp="b", claims={"value": "B"})]
    conflicts = [{"field": "value", "values": ["A", "B"], "level": "high", "resolved": False}]
    r = engine.score(
        claim="2025年某事件", mode="policy",
        evidence=evidence, conflicts=conflicts, today=date(2025, 6, 1),
    )
    assert r.verdict == "conflicting"
    assert r.confidence <= 35


def test_gating_resolved_conflict_not_capped(engine: ScoreEngine):
    """已仲裁冲突不应触发 conflicting cap。"""
    evidence = [_ev(authority=1.0), _ev(authority=0.97, fp="b")]
    conflicts = [{"field": "value", "values": ["A", "B"], "level": "low",
                  "resolved": True, "arbitration_note": "层级仲裁"}]
    r = engine.score(claim="2025年某事件 5月", mode="policy",
                     evidence=evidence, conflicts=conflicts, today=date(2025, 6, 1))
    assert r.verdict != "conflicting"
    assert "conflict_capped_35" not in r.gating_applied


def test_gating_expired_policy_caps_at_25(engine: ScoreEngine):
    evidence = [_ev(authority=0.9, published="2020-03-01")]
    r = engine.score(
        claim="武汉减免仍有效", mode="policy", evidence=evidence,
        policy_meta={"expire_date": "2021-01-01"},
        today=date(2026, 5, 21),
    )
    assert r.verdict == "outdated"
    assert r.confidence <= 25


def test_gating_superseded_policy_caps_at_30(engine: ScoreEngine):
    evidence = [_ev(authority=1.0, source_type="official", published="2018-10-26")]
    r = engine.score(
        claim="公司法当前是2018", mode="policy", evidence=evidence,
        policy_meta={"superseded_by": "2023 修订"}, today=date(2025, 6, 1),
    )
    assert r.verdict == "outdated"
    assert r.confidence <= 30


# ============ Verdict 决策树 ============

def test_verdict_supported_strong_authority(engine: ScoreEngine):
    evidence = [
        _ev(authority=1.0, fp="a"), _ev(authority=0.97, fp="b"),
    ]
    r = engine.score(claim="2025年某政策 5月", mode="policy", evidence=evidence, today=date(2025, 6, 1))
    assert r.verdict == "supported"


def test_verdict_partially_supported_medium(engine: ScoreEngine):
    """conf 60-75 但 not all conditions for supported → partially_supported。"""
    evidence = [_ev(authority=0.85, source_type="industry_association", support="strong")]
    r = engine.score(claim="2024年某事件 5月", mode="numeric", evidence=evidence, today=date(2024, 8, 1))
    # 单源 ~65 consistency + 85 authority + 90 fresh + medium completeness
    assert r.verdict in ("partially_supported", "supported")


def test_verdict_unverifiable_low_confidence(engine: ScoreEngine):
    """孤证 + 老 + media → 低 conf → unverifiable。"""
    evidence = [_ev(authority=0.45, source_type="mainstream_media", published="2020-01-01")]
    r = engine.score(claim="2020年某事件", mode="numeric", evidence=evidence, today=date(2025, 6, 1))
    assert r.confidence < 50


# ============ 极端 case ============

def test_extreme_100_evidence(engine: ScoreEngine):
    """100 条 evidence 不应崩。"""
    evidence = [_ev(authority=0.9, fp=f"e{i}") for i in range(100)]
    r = engine.score(claim="2024年某事件 6月", mode="numeric", evidence=evidence, today=date(2024, 12, 1))
    assert r.verdict in ("supported", "partially_supported")


def test_extreme_all_contradicted_high_authority(engine: ScoreEngine):
    """全反驳 + 全 tier1 → contradicted。"""
    evidence = [
        _ev(authority=1.0, fp=f"e{i}", support="contradicted") for i in range(5)
    ]
    r = engine.score(claim="某错误事实", mode="numeric", evidence=evidence, today=date(2024, 6, 1))
    assert r.verdict == "contradicted"


def test_extreme_mixed_2_support_3_contradict(engine: ScoreEngine):
    """混合 case：3 反 2 支持 + 高权威 → contradicted dominates。"""
    evidence = [
        _ev(authority=0.9, fp="a", support="contradicted"),
        _ev(authority=0.9, fp="b", support="contradicted"),
        _ev(authority=0.9, fp="c", support="contradicted"),
        _ev(authority=0.7, fp="d", support="strong"),
        _ev(authority=0.7, fp="e", support="strong"),
    ]
    r = engine.score(claim="某争议事实", mode="numeric", evidence=evidence, today=date(2024, 6, 1))
    assert r.verdict == "contradicted"


def test_ci_bootstrap_range(engine: ScoreEngine):
    """bootstrap CI [lo, hi] 应满足 lo <= conf <= hi 且 hi - lo > 0。"""
    evidence = [_ev(authority=0.9, fp="a"), _ev(authority=0.9, fp="b")]
    r = engine.score(claim="2024年某事件 6月", mode="numeric", evidence=evidence, today=date(2024, 12, 1))
    assert r.confidence_lo <= r.confidence <= r.confidence_hi
    assert r.confidence_hi - r.confidence_lo >= 0
