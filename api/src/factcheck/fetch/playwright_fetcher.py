"""Playwright fallback — 仅在 HTTP 抓取失败/疑似反爬/JS 渲染页时调用。

可选依赖：pip install playwright && playwright install chromium

设计：维护一个固定大小的 browser context 池，避免每次启动 Chromium。
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager

from .base import FetchedPage, Fetcher


def _extract_with_trafilatura(html: str) -> str:
    import trafilatura
    return trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=True) or ""

try:
    from playwright.async_api import Browser, Playwright, async_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    Browser = Playwright = None


class PlaywrightFetcher(Fetcher):
    def __init__(self, pool_size: int = 4, headless: bool = True) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            raise RuntimeError("Playwright 未安装。pip install playwright && playwright install chromium")
        self.pool_size = pool_size
        self.headless = headless
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._semaphore = asyncio.Semaphore(pool_size)
        self._started = False
        self._start_lock = asyncio.Lock()

    async def _ensure_started(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
            self._started = True

    @asynccontextmanager
    async def _context(self):
        await self._ensure_started()
        assert self._browser is not None
        ctx = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="zh-CN",
            viewport={"width": 1280, "height": 800},
        )
        try:
            yield ctx
        finally:
            await ctx.close()

    async def fetch(self, url: str, *, timeout: float = 15.0) -> FetchedPage:
        async with self._semaphore:
            try:
                async with self._context() as ctx:
                    page = await ctx.new_page()
                    try:
                        resp = await page.goto(url, timeout=int(timeout * 1000), wait_until="domcontentloaded")
                    except Exception as e:
                        await page.close()
                        return FetchedPage(url=url, status="timeout", method="playwright", error_message=str(e))
                    await page.wait_for_timeout(800)
                    html = await page.content()
                    title = await page.title()
                    await page.close()
            except Exception as e:
                return FetchedPage(url=url, status="error", method="playwright", error_message=str(e))

        content = _extract_with_trafilatura(html)
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        if not title and m:
            title = m.group(1).strip()

        status = "ok" if content.strip() else "empty"
        return FetchedPage(
            url=url, final_url=url, title=title,
            content=content[:50000], html=html[:5000],
            http_status=resp.status if resp else None,
            status=status, method="playwright",
        )

    async def aclose(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._started = False
