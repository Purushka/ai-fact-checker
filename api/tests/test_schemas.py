"""schema 校验测试。"""
from __future__ import annotations

import pytest

from factcheck.schemas import CheckRequest, ScoringWeights


def test_scoring_weights_default_sum_is_one():
    w = ScoringWeights()
    assert abs(w.total() - 1.0) < 0.001
    w.validate_sum()


def test_scoring_weights_invalid_sum():
    w = ScoringWeights(source_authority=0.5, source_consistency=0.5, freshness=0.5,
                       completeness=0.0, claim_clarity=0.0)
    with pytest.raises(ValueError):
        w.validate_sum()


def test_scoring_weights_negative_rejected():
    with pytest.raises(ValueError):
        ScoringWeights(source_authority=-0.1, source_consistency=0.4, freshness=0.4,
                       completeness=0.3, claim_clarity=0.0)


def test_check_request_either_claim_or_question():
    r = CheckRequest(claim="测试声明")
    assert r.input_text() == "测试声明"
    assert not r.is_question()

    r2 = CheckRequest(question="某政策是否还有效？")
    assert r2.is_question()


def test_check_request_strip_empty():
    with pytest.raises(ValueError):
        CheckRequest(claim="   ")
