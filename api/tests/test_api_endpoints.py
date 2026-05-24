"""API endpoint 测试 — 用 FastAPI TestClient 验证 request/response 格式 + 错误处理 + 参数校验。

不调用真实 LLM/Search，全部 mock pipeline.run()。
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("ENV", "dev")  # 跳过 verify_secret

from factcheck.main import create_app  # noqa: E402
from factcheck.pipeline import FactCheckPipeline  # noqa: E402
from factcheck.schemas import (  # noqa: E402
    CheckResponse,
    ConfidenceInterval,
    DimScore,
    ScoreBreakdown,
    TokenUsage,
)


def _fake_response(verdict: str = "supported", confidence: int = 85, mode: str = "policy") -> CheckResponse:
    """构造一个合法的 CheckResponse 对象用于 mock。"""
    bd = ScoreBreakdown(
        source_authority=DimScore(score=90, weight=0.4, weighted=36),
        source_consistency=DimScore(score=80, weight=0.3, weighted=24),
        freshness=DimScore(score=85, weight=0.2, weighted=17),
        completeness=DimScore(score=70, weight=0.1, weighted=7),
        claim_clarity=DimScore(score=100, weight=0.0, weighted=0),
    )
    return CheckResponse(
        request_id="test-req-123",
        input="测试 claim",
        mode=mode,  # type: ignore[arg-type]
        verdict=verdict,  # type: ignore[arg-type]
        confidence=confidence,
        confidence_level="high",
        confidence_interval=ConfidenceInterval(lo=78, hi=92, method="bootstrap_5dim_n200"),
        has_official_source=True,
        score_breakdown=bd,
        reasoning_summary="测试推理",
        collected_at="2026-05-22T10:00:00Z",
        token_usage=TokenUsage(provider="mock", model="mock-1", total_tokens=100),
    )


@pytest.fixture
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mocked_pipeline():
    """Mock FactCheckPipeline.run，返回固定 response。"""
    with patch.object(FactCheckPipeline, "run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = _fake_response()
        yield mock_run


# ============ Meta endpoints ============

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["service"] == "factcheck"
    assert "time" in data
    assert "llm_providers" in data


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "AI Fact Checker"
    assert "/health" in data["health"]


# ============ /v1/check ============

def test_check_supported(client, mocked_pipeline):
    r = client.post("/v1/check", json={"claim": "测试声明 2025年某事件", "mode": "general"})
    assert r.status_code == 200
    data = r.json()
    assert data["verdict"] == "supported"
    assert 0 <= data["confidence"] <= 100
    assert data["confidence_interval"]["lo"] <= data["confidence"] <= data["confidence_interval"]["hi"]
    assert data["request_id"]
    mocked_pipeline.assert_called_once()


def test_check_missing_claim_and_question(client):
    r = client.post("/v1/check", json={"mode": "general"})
    assert r.status_code == 400
    assert "claim 或 question" in r.json()["detail"]


def test_check_empty_string_rejected(client):
    r = client.post("/v1/check", json={"claim": "   ", "mode": "general"})
    assert r.status_code == 422  # Pydantic validator rejects empty after strip


def test_check_invalid_mode_rejected(client):
    r = client.post("/v1/check", json={"claim": "x", "mode": "invalid_mode"})
    assert r.status_code == 422


def test_check_extra_field_rejected(client):
    """CheckRequest 是 extra='forbid'。"""
    r = client.post("/v1/check", json={"claim": "x", "mode": "general", "unknown_field": 123})
    assert r.status_code == 422


def test_check_scoring_weights_invalid_sum(client, mocked_pipeline):
    r = client.post("/v1/check", json={
        "claim": "x", "mode": "general",
        "scoring_weights": {
            "source_authority": 0.5, "source_consistency": 0.6,
            "freshness": 0.0, "completeness": 0.0, "claim_clarity": 0.0,
        },
    })
    assert r.status_code == 400
    assert "权重和必须为 1.0" in r.json()["detail"]


def test_check_valid_custom_weights_accepted(client, mocked_pipeline):
    r = client.post("/v1/check", json={
        "claim": "x 2025年", "mode": "general",
        "scoring_weights": {
            "source_authority": 0.4, "source_consistency": 0.3,
            "freshness": 0.2, "completeness": 0.1, "claim_clarity": 0.0,
        },
    })
    assert r.status_code == 200


def test_check_question_input(client, mocked_pipeline):
    r = client.post("/v1/check", json={"question": "X 法当前是哪年版本？", "mode": "policy"})
    assert r.status_code == 200


# ============ /v1/check/batch ============

def test_batch_check(client, mocked_pipeline):
    r = client.post("/v1/check/batch", json={
        "items": [
            {"claim": "测试 1 2025年", "mode": "general"},
            {"claim": "测试 2 2025年", "mode": "numeric"},
        ],
    })
    assert r.status_code == 200
    data = r.json()
    assert "batch_id" in data
    assert data["status"] == "completed"
    assert len(data["results"]) == 2
    assert mocked_pipeline.call_count == 2


def test_batch_empty_items(client):
    r = client.post("/v1/check/batch", json={"items": []})
    # 应该返回 200 with 空 results 或 422
    assert r.status_code in (200, 422)


# ============ /v1/policy-check ============

def test_policy_check_forces_mode(client, mocked_pipeline):
    r = client.post("/v1/policy-check", json={"claim": "某政策 2025年5月施行"})
    assert r.status_code == 200
    # mode 被强制为 policy
    called_req = mocked_pipeline.call_args[0][0]
    assert called_req.mode == "policy"
    assert called_req.options.require_official_source is True


# ============ /v1/solution-audit ============

def test_solution_audit_claims_list(client, mocked_pipeline):
    r = client.post("/v1/solution-audit", json={
        "claims": ["事实 1 2025年", "事实 2 2025年", "事实 3 2025年"],
        "mode": "general",
    })
    assert r.status_code == 200
    data = r.json()
    assert len(data["results"]) == 3
    assert mocked_pipeline.call_count == 3


def test_solution_audit_empty_rejected(client):
    r = client.post("/v1/solution-audit", json={"claims": [], "mode": "general"})
    assert r.status_code == 400


# ============ /v1/source-profiles ============

def test_list_source_profiles(client):
    r = client.get("/v1/source-profiles")
    assert r.status_code == 200
    data = r.json()
    assert "tiers" in data
    assert data["source_count"] > 100


def test_list_sources_with_type_filter(client):
    r = client.get("/v1/source-profiles/sources?source_type=official&limit=10")
    assert r.status_code == 200
    data = r.json()
    assert "sources" in data
    assert all(s["source_type"] == "official" for s in data["sources"])


def test_classify_url_endpoint(client):
    r = client.get("/v1/source-profiles/classify?url=https://www.gov.cn/test")
    assert r.status_code == 200
    data = r.json()
    assert data["source_type"] == "official"
    assert data["authority_weight"] >= 0.85


def test_classify_url_empty_rejected(client):
    r = client.get("/v1/source-profiles/classify?url=")
    assert r.status_code == 400


# ============ /v1/check/async ============

def test_async_job_create_and_query(client, mocked_pipeline):
    """创建异步任务 + 查询状态。"""
    r = client.post("/v1/check/async", json={
        "job_type": "check_batch",
        "payload": {"items": [{"claim": "测试异步 2025年", "mode": "general"}]},
    })
    assert r.status_code == 200
    data = r.json()
    job_id = data["job_id"]
    assert data["status"] in ("pending", "running", "completed")

    # 查询
    r2 = client.get(f"/v1/tasks/{job_id}")
    assert r2.status_code == 200
    assert r2.json()["job_id"] == job_id


def test_async_job_not_found(client):
    r = client.get("/v1/tasks/nonexistent-id")
    assert r.status_code == 404
