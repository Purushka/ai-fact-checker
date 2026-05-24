"""秘塔 AI 搜索（metaso.cn）适配。

特色：响应里自带 authorityType 字段（"government" / "news" 等），可用作权威性提示信号。
价格：约 ¥0.03/查询。
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from .base import SearchError, SearchProvider, SearchResult


class MetasoProvider(SearchProvider):
    name = "metaso"

    def __init__(self, api_key: str, base_url: str = "https://metaso.cn/api/open") -> None:
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
        timeout: float = 30.0,
    ) -> list[SearchResult]:
        if not self.api_key:
            raise SearchError("Metaso API key 未配置")

        if include_domains:
            query = query + " " + " ".join(f"site:{d}" for d in include_domains)
        if exclude_domains:
            query = query + " " + " ".join(f"-site:{d}" for d in exclude_domains)

        payload = {
            "question": query,
            "lang": "zh",
            "stream": False,
            "newEngine": False,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{self.base_url}/search/v2", json=payload, headers=headers)
            if resp.status_code >= 400:
                raise SearchError(f"Metaso HTTP {resp.status_code}: {resp.text[:300]}")
            envelope = resp.json()

        if envelope.get("errCode") not in (0, None):
            raise SearchError(f"Metaso errCode={envelope.get('errCode')}: {envelope.get('errMsg', '')}")
        data = envelope.get("data") or envelope

        results: list[SearchResult] = []
        for item in (data.get("references") or [])[:limit]:
            published_at = None
            pd = item.get("publish_date")
            if pd:
                try:
                    ts = int(pd)
                    published_at = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
                except (ValueError, OSError, TypeError):
                    published_at = item.get("date")
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("title", ""),
                published_at=published_at,
                provider=self.name,
                raw=item,
            ))
        return results
