"""Serper.dev — Google SERP 镜像。"""

from __future__ import annotations

import httpx

from .base import SearchError, SearchProvider, SearchResult


class SerperProvider(SearchProvider):
    name = "serper"

    def __init__(self, api_key: str, base_url: str = "https://google.serper.dev") -> None:
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
            raise SearchError("Serper API key 未配置")
        if include_domains:
            query = query + " " + " OR ".join(f"site:{d}" for d in include_domains)
        if exclude_domains:
            query = query + " " + " ".join(f"-site:{d}" for d in exclude_domains)

        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        payload = {"q": query, "num": limit, "gl": "cn", "hl": "zh-cn"}

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}/search", json=payload, headers=headers)
            if resp.status_code >= 400:
                raise SearchError(f"Serper HTTP {resp.status_code}: {resp.text[:300]}")
            data = resp.json()

        organic = data.get("organic", []) or []
        out = []
        for item in organic[:limit]:
            out.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                    published_at=item.get("date"),
                    provider=self.name,
                    raw=item,
                )
            )
        return out
