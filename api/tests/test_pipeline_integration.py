"""Pipeline 集成测试 — 用 mock 出 LLM/search/fetch，验证 pipeline 编排逻辑端到端。

测试矩阵覆盖：
- 政策类 supported / outdated(expired) / outdated(superseded) / contradicted / unverifiable
- 通用 supported
- claim 主观 → out_of_scope
- 单源孤证 → confidence 低
"""
from __future__ import annotations

import asyncio
import json

import pytest

from factcheck.pipeline import FactCheckPipeline
from factcheck.schemas import CheckOptions, CheckRequest
from factcheck.score.engine import ScoreEngine
from factcheck.score.source_classify import SourceClassifier

from .conftest import MockFetcher, MockLLM, MockSearch


def _qp_response(entities, queries):
    return json.dumps({
        "entities": entities,
        "time_anchor": "2025",
        "region": {"level": "national", "name": "中国"},
        "intent_category": "policy_query",
        "sub_claims": [{"id": "sc1", "text": "test"}],
        "search_queries": queries,
    }, ensure_ascii=False)


def _fe_response(support_level, claims_about=None, published_at="2025-04-30"):
    return json.dumps({
        "support_level": support_level,
        "snippet": "测试 snippet",
        "claims_about": claims_about or {"title": "测试政策", "issuing_agency": "国务院",
                                          "publish_date": published_at, "applicable_subjects": ["个体"]},
        "published_at": published_at,
        "agency": "国务院",
        "agency_level": "national",
        "document_number": "国发〔2025〕XX号",
        "is_secondary_citation": False,
        "primary_source_hint": None,
    }, ensure_ascii=False)


def _cv_response(conflicts=None, missing=None, policy_meta=None):
    return json.dumps({
        "conflicts": conflicts or [],
        "warnings": [],
        "missing_fields": missing or [],
        "policy_meta": policy_meta or {
            "title": "测试政策",
            "issuing_agency": "国务院",
            "agency_level": "national",
            "publish_date": "2025-04-30",
            "effective_date": "2025-05-20",
            "expire_date": None,
            "superseded_by": None,
            "applicable_region": ["中国"],
            "applicable_subjects": ["个体"],
            "benefit_amount": "5000元",
        },
    }, ensure_ascii=False)


class _PatchedPipeline(FactCheckPipeline):
    """注入 mock providers 而不影响 production code。"""
    def __init__(self, llm, search, fetcher) -> None:
        super().__init__(scorer=ScoreEngine(), classifier=SourceClassifier())
        self._mock_llm = llm
        self._mock_search = search
        self.fetch = fetcher

    async def run(self, req):
        from factcheck.extract import FactExtractor, QueryPlanner
        from factcheck.verify import CrossValidator
        import factcheck.pipeline as pmod

        original_get_provider = pmod.get_provider
        pmod.get_provider = lambda name=None: self._mock_llm
        original_search = self.search
        self.search = self._mock_search
        try:
            return await super().run(req)
        finally:
            pmod.get_provider = original_get_provider
            self.search = original_search


@pytest.mark.asyncio
async def test_supported_with_official_sources(mock_search_results, mock_fetched_pages):
    llm = MockLLM(responses_by_label={
        "query_planner": _qp_response(["测试政策"], ["测试政策 国务院 2025"]),
        "fact_extractor": _fe_response("strong"),
        "cross_validator": _cv_response(),
    })
    search = type("Orch", (), {})()
    async def search_method(queries, *, per_query_limit=8, total_cap=25):
        from factcheck.search.orchestrator import OrchestratedResult
        return OrchestratedResult(
            results=mock_search_results, used_providers=["mock"],
            total_calls=2, discarded_count=0,
        )
    search.search = search_method

    pipeline = _PatchedPipeline(llm, search, MockFetcher(mock_fetched_pages))
    req = CheckRequest(claim="2025年某政策于5月20日施行，补贴5000元", mode="policy",
                       options=CheckOptions(use_cache=False, require_official_source=True))
    result = await pipeline.run(req)

    assert result.verdict == "supported", f"got {result.verdict}"
    assert result.confidence >= 70
    assert result.has_official_source is True
    assert result.policy_status == "active"
    assert result.token_usage.total_tokens > 0
    assert result.token_usage.search_calls > 0


@pytest.mark.asyncio
async def test_no_evidence_unverifiable(mock_search_results, mock_fetched_pages):
    llm = MockLLM(responses_by_label={
        "query_planner": _qp_response([], ["不会命中的查询"]),
        "fact_extractor": _fe_response("neutral"),
        "cross_validator": _cv_response(missing=["issuing_agency", "publish_date"]),
    })
    search = type("Orch", (), {})()
    async def empty_search(queries, *, per_query_limit=8, total_cap=25):
        from factcheck.search.orchestrator import OrchestratedResult
        return OrchestratedResult([], ["mock"], 1, 0)
    search.search = empty_search

    pipeline = _PatchedPipeline(llm, search, MockFetcher({}))
    req = CheckRequest(claim="某非常具体的不存在的事实", mode="policy",
                       options=CheckOptions(use_cache=False))
    result = await pipeline.run(req)
    assert result.verdict == "unverifiable"
    assert result.confidence <= 30


@pytest.mark.asyncio
async def test_expired_policy(mock_search_results, mock_fetched_pages):
    llm = MockLLM(responses_by_label={
        "query_planner": _qp_response(["旧政策"], ["旧政策"]),
        "fact_extractor": _fe_response("strong", published_at="2020-03-01"),
        "cross_validator": _cv_response(policy_meta={
            "title": "测试旧政策",
            "issuing_agency": "武汉市政府",
            "agency_level": "city",
            "publish_date": "2020-03-01",
            "expire_date": "2021-01-01",
            "superseded_by": None,
            "applicable_region": ["武汉市"],
            "applicable_subjects": ["中小微企业"],
            "benefit_amount": "9个月减免",
        }),
    })
    search = type("Orch", (), {})()
    async def s(queries, *, per_query_limit=8, total_cap=25):
        from factcheck.search.orchestrator import OrchestratedResult
        return OrchestratedResult(mock_search_results, ["mock"], 2, 0)
    search.search = s
    pipeline = _PatchedPipeline(llm, search, MockFetcher(mock_fetched_pages))
    req = CheckRequest(claim="2020 武汉房租减免目前仍有效", mode="policy",
                       options=CheckOptions(use_cache=False, require_official_source=True))
    result = await pipeline.run(req)
    assert result.verdict == "outdated"
    assert result.policy_status == "expired"
    assert result.confidence <= 25


@pytest.mark.asyncio
async def test_superseded_policy(mock_search_results, mock_fetched_pages):
    llm = MockLLM(responses_by_label={
        "query_planner": _qp_response(["合同法"], ["合同法 当前有效"]),
        "fact_extractor": _fe_response("contradicted"),
        "cross_validator": _cv_response(policy_meta={
            "title": "中华人民共和国合同法",
            "issuing_agency": "全国人大",
            "agency_level": "national",
            "publish_date": "1999-03-15",
            "expire_date": None,
            "superseded_by": "中华人民共和国民法典合同编 2021-01-01",
            "applicable_region": ["全国"],
        }),
    })
    search = type("Orch", (), {})()
    async def s(queries, *, per_query_limit=8, total_cap=25):
        from factcheck.search.orchestrator import OrchestratedResult
        return OrchestratedResult(mock_search_results, ["mock"], 2, 0)
    search.search = s
    pipeline = _PatchedPipeline(llm, search, MockFetcher(mock_fetched_pages))
    req = CheckRequest(claim="合同法是当前调整合同关系的基本法律", mode="policy",
                       options=CheckOptions(use_cache=False, require_official_source=True))
    result = await pipeline.run(req)
    assert result.verdict == "outdated"
    assert result.policy_status == "superseded"
    assert result.confidence <= 30


@pytest.mark.asyncio
async def test_contradicted_by_authority(mock_search_results, mock_fetched_pages):
    llm = MockLLM(responses_by_label={
        "query_planner": _qp_response(["新生儿"], ["2023 新生儿数量"]),
        "fact_extractor": _fe_response("contradicted", claims_about={
            "metric_value": "902万", "data_source": "国家统计局",
        }),
        "cross_validator": _cv_response(),
    })
    search = type("Orch", (), {})()
    async def s(queries, *, per_query_limit=8, total_cap=25):
        from factcheck.search.orchestrator import OrchestratedResult
        return OrchestratedResult(mock_search_results, ["mock"], 2, 0)
    search.search = s
    pipeline = _PatchedPipeline(llm, search, MockFetcher(mock_fetched_pages))
    req = CheckRequest(claim="2023年新生儿1500万", mode="numeric",
                       options=CheckOptions(use_cache=False))
    result = await pipeline.run(req)
    assert result.verdict == "contradicted"


@pytest.mark.asyncio
async def test_subjective_out_of_scope(mock_search_results, mock_fetched_pages):
    llm = MockLLM(responses_by_label={
        "query_planner": _qp_response(["上海", "创业"], ["上海 创业"]),
        "fact_extractor": _fe_response("neutral"),
        "cross_validator": _cv_response(),
    })
    search = type("Orch", (), {})()
    async def s(queries, *, per_query_limit=8, total_cap=25):
        from factcheck.search.orchestrator import OrchestratedResult
        return OrchestratedResult(mock_search_results, ["mock"], 2, 0)
    search.search = s
    pipeline = _PatchedPipeline(llm, search, MockFetcher(mock_fetched_pages))
    req = CheckRequest(claim="上海是中国最适合创业的城市", mode="general",
                       options=CheckOptions(use_cache=False))
    result = await pipeline.run(req)
    assert result.verdict == "out_of_scope"


@pytest.mark.asyncio
async def test_token_accounting_aggregates(mock_search_results, mock_fetched_pages):
    llm = MockLLM(responses_by_label={
        "query_planner": _qp_response(["test"], ["test"]),
        "fact_extractor": _fe_response("strong"),
        "cross_validator": _cv_response(),
    })
    search = type("Orch", (), {})()
    async def s(queries, *, per_query_limit=8, total_cap=25):
        from factcheck.search.orchestrator import OrchestratedResult
        return OrchestratedResult(mock_search_results, ["mock"], 2, 0)
    search.search = s
    pipeline = _PatchedPipeline(llm, search, MockFetcher(mock_fetched_pages))
    req = CheckRequest(claim="测试claim带时间锚点的2025年", mode="policy",
                       options=CheckOptions(use_cache=False, require_official_source=True))
    result = await pipeline.run(req)
    assert result.token_usage.provider == "mock"
    assert result.token_usage.total_tokens > 0
    assert result.token_usage.search_calls > 0
    assert result.token_usage.fetch_calls > 0
    assert len(llm.calls) >= 3
