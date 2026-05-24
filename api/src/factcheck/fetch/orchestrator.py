"""Fetch 编排：HTTP 优先并发抓，失败的批量降级到 Playwright。"""
from __future__ import annotations

import asyncio

from ..config import get_settings
from .base import FetchedPage
from .http_fetcher import HttpFetcher


class FetchOrchestrator:
    def __init__(self) -> None:
        s = get_settings()
        self._http = HttpFetcher()
        self._timeout = s.http_fetch_timeout_sec
        self._parallel = s.max_fetch_parallel
        self._playwright_enabled = s.playwright_enabled
        self._playwright = None

    async def _ensure_playwright(self):
        if not self._playwright_enabled:
            return None
        if self._playwright is None:
            try:
                from .playwright_fetcher import PlaywrightFetcher
                s = get_settings()
                self._playwright = PlaywrightFetcher(pool_size=s.playwright_pool_size)
            except RuntimeError:
                self._playwright_enabled = False
                return None
        return self._playwright

    async def fetch_many(self, urls: list[str]) -> list[FetchedPage]:
        sem = asyncio.Semaphore(self._parallel)

        async def _one(u: str) -> FetchedPage:
            async with sem:
                return await self._http.fetch(u, timeout=self._timeout)

        results = await asyncio.gather(*[_one(u) for u in urls])

        needs_pw = [r for r in results if r.status in ("blocked", "empty", "timeout")]
        if needs_pw and self._playwright_enabled:
            pw = await self._ensure_playwright()
            if pw is not None:
                pw_results = await asyncio.gather(*[pw.fetch(r.url, timeout=15.0) for r in needs_pw])
                pw_map = {r.url: r for r in pw_results}
                for i, r in enumerate(results):
                    if r.url in pw_map and pw_map[r.url].status == "ok":
                        results[i] = pw_map[r.url]

        return results

    async def aclose(self) -> None:
        if self._playwright is not None:
            await self._playwright.aclose()
