"""httpx + trafilatura 主路径抓取。失败时返回 status=blocked，由 orchestrator 决定是否升级到 Playwright。"""

from __future__ import annotations

import re

import httpx

from .base import FetchedPage, Fetcher


def _extract_with_trafilatura(html: str) -> str:
    import trafilatura

    return trafilatura.extract(html, include_comments=False, include_tables=True, favor_recall=True) or ""


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def _is_likely_blocked(html: str) -> bool:
    if not html or len(html) < 200:
        return True
    indicators = [
        "captcha",
        "人机验证",
        "请完成验证",
        "访问被拒绝",
        "403 Forbidden",
        "Access Denied",
        "cf-browser-verification",
    ]
    low = html.lower()
    return any(s.lower() in low for s in indicators)


class HttpFetcher(Fetcher):
    def __init__(self, ua_rotation: bool = True) -> None:
        self._ua_idx = 0
        self._ua_rotation = ua_rotation

    def _next_ua(self) -> str:
        ua = USER_AGENTS[self._ua_idx % len(USER_AGENTS)]
        if self._ua_rotation:
            self._ua_idx += 1
        return ua

    async def fetch(self, url: str, *, timeout: float = 8.0) -> FetchedPage:
        headers = {
            "User-Agent": self._next_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=headers) as client:
                resp = await client.get(url)
        except httpx.TimeoutException:
            return FetchedPage(url=url, status="timeout", error_message="HTTP 超时")
        except httpx.HTTPError as e:
            return FetchedPage(url=url, status="error", error_message=str(e))

        if resp.status_code >= 400:
            return FetchedPage(
                url=url,
                status="blocked",
                http_status=resp.status_code,
                error_message=f"HTTP {resp.status_code}",
            )

        try:
            html = resp.text
        except Exception as e:
            return FetchedPage(url=url, status="error", error_message=f"decode: {e}")

        if _is_likely_blocked(html):
            return FetchedPage(
                url=url,
                status="blocked",
                http_status=resp.status_code,
                html=html[:2000],
                error_message="疑似反爬/人机验证",
            )

        content = _extract_with_trafilatura(html)
        title = ""
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        if m:
            title = m.group(1).strip()

        if not content.strip():
            return FetchedPage(
                url=url,
                final_url=str(resp.url),
                status="empty",
                http_status=resp.status_code,
                html=html[:2000],
                title=title,
                error_message="trafilatura 提取正文为空",
            )

        return FetchedPage(
            url=url,
            final_url=str(resp.url),
            title=title,
            content=content[:50000],
            html=html[:5000],
            http_status=resp.status_code,
            status="ok",
            method="http",
        )
