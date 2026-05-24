"""博查 AI Search 适配。国内中文搜索源，对政务/中文内容召回更好。"""
from __future__ import annotations

import httpx

from .base import SearchError, SearchProvider, SearchResult


class BochaProvider(SearchProvider):
    name = "bocha"

    def __init__(self, api_key: str, base_url: str = "https://api.bochaai.com/v1") -> None:
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
            raise SearchError("Bocha API key 未配置")

        payload = {
            "query": query,
            "count": limit,
            "freshness": "noLimit",
            "summary": True,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}/web-search", json=payload, headers=headers)
            if resp.status_code >= 400:
                raise SearchError(f"Bocha HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()

        webpages = (data.get("data", {}).get("webPages", {}).get("value", []) or [])
        out: list[SearchResult] = []
        for item in webpages[:limit]:
            url = item.get("url", "")
            if exclude_domains and any(d in url for d in exclude_domains):
                continue
            if include_domains and not any(d in url for d in include_domains):
                continue
            out.append(SearchResult(
                title=item.get("name", ""),
                url=url,
                snippet=item.get("summary") or item.get("snippet", ""),
                published_at=item.get("dateLastCrawled"),
                provider=self.name,
                raw=item,
            ))
        return out
