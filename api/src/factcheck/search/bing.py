"""Microsoft Bing Web Search API。"""
from __future__ import annotations

import httpx

from .base import SearchError, SearchProvider, SearchResult


class BingProvider(SearchProvider):
    name = "bing"

    def __init__(self, api_key: str, base_url: str = "https://api.bing.microsoft.com/v7.0") -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

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
            raise SearchError("Bing API key 未配置")
        if include_domains:
            query = query + " " + " OR ".join(f"site:{d}" for d in include_domains)
        if exclude_domains:
            query = query + " " + " ".join(f"-site:{d}" for d in exclude_domains)

        headers = {"Ocp-Apim-Subscription-Key": self.api_key}
        params = {"q": query, "count": limit, "mkt": "zh-CN", "responseFilter": "Webpages"}

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{self.base_url}/search", params=params, headers=headers)
            if resp.status_code >= 400:
                raise SearchError(f"Bing HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()

        pages = data.get("webPages", {}).get("value", []) or []
        out = []
        for item in pages[:limit]:
            out.append(SearchResult(
                title=item.get("name", ""),
                url=item.get("url", ""),
                snippet=item.get("snippet", ""),
                published_at=item.get("dateLastCrawled"),
                provider=self.name,
                raw=item,
            ))
        return out
