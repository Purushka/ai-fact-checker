"""共享测试 fixtures：Mock LLM、Mock Search、Mock Fetch。"""
from __future__ import annotations

from typing import Any

import pytest

from factcheck.fetch.base import FetchedPage
from factcheck.llm.base import LLMMessage, LLMProvider, LLMResponse
from factcheck.search.base import SearchProvider, SearchResult


class MockLLM(LLMProvider):
    name = "mock"

    def __init__(self, responses_by_label: dict[str, str] | None = None,
                 default: str = '{"support_level":"neutral","snippet":"","claims_about":{}}') -> None:
        self.responses = responses_by_label or {}
        self.default = default
        self.calls: list[dict] = []

    async def chat(self, messages, *, model=None, temperature=0.2, max_tokens=None,
                   response_format=None, timeout=30.0):
        sys_content = next((m.content for m in messages if m.role == "system"), "")
        label = "unknown"
        if "查询规划助手" in sys_content:
            label = "query_planner"
        elif "证据抽取助手" in sys_content:
            label = "fact_extractor"
        elif "交叉验证助手" in sys_content:
            label = "cross_validator"
        elif "答案生成助手" in sys_content:
            label = "answer_generator"
        self.calls.append({"label": label, "messages_len": len(messages)})
        text = self.responses.get(label, self.default)
        if callable(text):
            user = next((m.content for m in messages if m.role == "user"), "")
            text = text(user)
        return LLMResponse(
            text=text, prompt_tokens=120, completion_tokens=80, total_tokens=200,
            model="mock-model", provider="mock",
        )


class MockSearch(SearchProvider):
    name = "mock"

    def __init__(self, results: list[SearchResult] | None = None) -> None:
        self._results = results or []
        self.calls: list[str] = []

    async def search(self, query, *, limit=8, include_domains=None, exclude_domains=None, timeout=10.0):
        self.calls.append(query)
        return list(self._results)[:limit]


class MockFetcher:
    def __init__(self, pages: dict[str, FetchedPage] | None = None) -> None:
        self.pages = pages or {}

    async def fetch_many(self, urls: list[str]) -> list[FetchedPage]:
        return [self.pages.get(u, FetchedPage(url=u, status="error", error_message="not mocked"))
                for u in urls]

    async def aclose(self) -> None:
        pass


@pytest.fixture
def mock_llm():
    return MockLLM()


@pytest.fixture
def mock_search_results():
    return [
        SearchResult(title="国务院政策原文", url="https://www.gov.cn/test/policy.htm",
                     snippet="测试政策原文摘要", provider="mock"),
        SearchResult(title="人社部解读", url="https://www.mohrss.gov.cn/test/jiedu.htm",
                     snippet="测试政策解读", provider="mock"),
    ]


@pytest.fixture
def mock_fetched_pages():
    return {
        "https://www.gov.cn/test/policy.htm": FetchedPage(
            url="https://www.gov.cn/test/policy.htm",
            final_url="https://www.gov.cn/test/policy.htm",
            title="测试政策原文",
            content="本政策自2025年5月20日起施行。补贴标准为5000元。",
            status="ok", http_status=200, method="http",
        ),
        "https://www.mohrss.gov.cn/test/jiedu.htm": FetchedPage(
            url="https://www.mohrss.gov.cn/test/jiedu.htm",
            final_url="https://www.mohrss.gov.cn/test/jiedu.htm",
            title="政策解读",
            content="人社部解读：补贴 5000 元，自 2025-05-20 实施。",
            status="ok", http_status=200, method="http",
        ),
    }
