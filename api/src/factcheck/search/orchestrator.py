"""Search 编排：多 provider 并行 + URL 去重 + 内容农场过滤 + 黑名单。

按 settings.search_primary / secondary / fallback 顺序：
- primary 主调用
- secondary 用于补充召回（中文政务必走博查）
- fallback 仅在前两者均失败时使用
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from ..config import get_settings
from ..score.source_classify import SourceClassifier
from .base import SearchError, SearchProvider, SearchResult
from .bing import BingProvider
from .bocha import BochaProvider
from .anysearch import AnySearchProvider
from .metaso import MetasoProvider
from .serper import SerperProvider
from .tavily import TavilyProvider


@dataclass
class OrchestratedResult:
    results: list[SearchResult]
    used_providers: list[str]
    total_calls: int
    discarded_count: int


def _build_providers() -> dict[str, SearchProvider]:
    s = get_settings()
    out: dict[str, SearchProvider] = {}
    if s.tavily_api_key:
        out["tavily"] = TavilyProvider(s.tavily_api_key)
    if s.bocha_api_key:
        out["bocha"] = BochaProvider(s.bocha_api_key)
    if s.serper_api_key:
        out["serper"] = SerperProvider(s.serper_api_key)
    if s.bing_api_key:
        out["bing"] = BingProvider(s.bing_api_key)
    if s.metaso_api_key:
        out["metaso"] = MetasoProvider(s.metaso_api_key)
    if s.anysearch_api_key:
        out["anysearch"] = AnySearchProvider(s.anysearch_api_key)
    return out


class SearchOrchestrator:
    def __init__(self) -> None:
        self._providers = _build_providers()
        self._classifier = SourceClassifier()

    @property
    def available_providers(self) -> list[str]:
        return list(self._providers.keys())

    async def search_one(self, provider_name: str, query: str, limit: int = 8) -> list[SearchResult]:
        p = self._providers.get(provider_name)
        if not p:
            return []
        try:
            return await p.search(query, limit=limit)
        except SearchError:
            return []

    async def search(
        self,
        queries: list[str],
        *,
        per_query_limit: int = 8,
        total_cap: int = 25,
    ) -> OrchestratedResult:
        s = get_settings()
        primary = self._providers.get(s.search_primary)
        secondary = self._providers.get(s.search_secondary)
        fallback = self._providers.get(s.search_fallback)
        if not primary and not secondary:
            primary = next(iter(self._providers.values()), None)
        if not primary:
            return OrchestratedResult([], [], 0, 0)

        tasks: list = []
        used: list[str] = []
        for q in queries:
            for p in (primary, secondary):
                if p is None:
                    continue
                tasks.append(p.search(q, limit=per_query_limit))
                used.append(p.name)

        try:
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            gathered = []

        all_results: list[SearchResult] = []
        ok_count = 0
        for r in gathered:
            if isinstance(r, list):
                ok_count += 1
                all_results.extend(r)

        if ok_count == 0 and fallback:
            for q in queries[:2]:
                try:
                    all_results.extend(await fallback.search(q, limit=per_query_limit))
                except SearchError:
                    pass
            used.append(fallback.name)

        seen_urls: set[str] = set()
        deduped: list[SearchResult] = []
        discarded = 0
        for r in all_results:
            url = r.url.strip()
            if not url or url in seen_urls:
                continue
            cls = self._classifier.classify(url)
            if cls.discardable():
                discarded += 1
                continue
            seen_urls.add(url)
            deduped.append(r)
            if len(deduped) >= total_cap:
                break

        def _sort_key(r: SearchResult) -> float:
            cls = self._classifier.classify(r.url)
            return -cls.authority_weight
        deduped.sort(key=_sort_key)

        return OrchestratedResult(
            results=deduped,
            used_providers=list(dict.fromkeys(used)),
            total_calls=len(tasks),
            discarded_count=discarded,
        )
