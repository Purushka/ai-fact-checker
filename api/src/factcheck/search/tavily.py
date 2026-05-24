"""Tavily Search API 适配。Tavily 专为 LLM 优化、返回 cleaned content，是默认主搜索源。"""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .base import SearchError, SearchProvider, SearchResult


class TavilyProvider(SearchProvider):
    name = "tavily"

    def __init__(self, api_key: str, base_url: str = "https://api.tavily.com") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(2),
        reraise=True,
    )
    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        timeout: float = 10.0,
    ) -> list[SearchResult]:
        if not self.api_key:
            raise SearchError("Tavily API key 未配置")

        payload: dict = {
            "api_key": self.api_key,
            "query": query,
            "search_depth": "basic",
            "max_results": limit,
            "include_answer": False,
            "include_raw_content": False,
        }
        if include_domains:
            payload["include_domains"] = include_domains
        if exclude_domains:
            payload["exclude_domains"] = exclude_domains

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}/search", json=payload)
            if resp.status_code >= 400:
                raise SearchError(f"Tavily HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()

        out: list[SearchResult] = []
        for item in data.get("results", []):
            out.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", "") or item.get("snippet", ""),
                    published_at=item.get("published_date"),
                    provider=self.name,
                    raw_score=item.get("score"),
                    raw=item,
                )
            )
        return out
