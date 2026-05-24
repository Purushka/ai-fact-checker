"""AnySearch (anysearch.com) 适配。

特色：
- 专为 AI Agent 设计，返回结构化 + quality_score
- zone="cn" 优先返回国内权威源（实测 top1 经常是 gov.cn 原文）
- 1000 calls/day 免费配额
- 价格付费档位待查询官方
"""

from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .base import SearchError, SearchProvider, SearchResult


class AnySearchProvider(SearchProvider):
    name = "anysearch"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.anysearch.com",
        zone: str = "cn",
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.zone = zone

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
        timeout: float = 30.0,
    ) -> list[SearchResult]:
        if not self.api_key:
            raise SearchError("AnySearch API key 未配置")

        if include_domains:
            query = query + " " + " ".join(f"site:{d}" for d in include_domains)
        if exclude_domains:
            query = query + " " + " ".join(f"-site:{d}" for d in exclude_domains)

        payload = {
            "query": query,
            "max_results": min(limit, 20),
            "zone": self.zone,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}/v1/search", json=payload, headers=headers)
            if resp.status_code >= 400:
                raise SearchError(f"AnySearch HTTP {resp.status_code}: {resp.text[:300]}")
            envelope = resp.json()

        if envelope.get("code") not in (0, 200, None):
            raise SearchError(f"AnySearch code={envelope.get('code')}: {envelope.get('message', '')}")
        data = envelope.get("data") or envelope
        items = data.get("results") or []

        out: list[SearchResult] = []
        for item in items[:limit]:
            out.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description") or item.get("content", "")[:300],
                    published_at=item.get("published_at"),
                    provider=self.name,
                    raw_score=item.get("quality_score") or item.get("score"),
                    raw=item,
                )
            )
        return out
