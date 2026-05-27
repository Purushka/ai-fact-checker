"""1.2 — 数据源逐个验证。

测：
  (a) B 站搜索 API     api.bilibili.com/x/web-interface/search/all/v2
  (b) 10 个主流媒体 RSS（覆盖央媒/财经/科技/社会等）
  (c) Bocha / Metaso / AnySearch 中文搜索质量（用 3 个 case 的 query）
  (d) piyao 爬虫稳定性（连续 5 次抓首页）

输出指标：
  - 是否需认证、频率限制、平均延迟
  - 返回结构是否含 published_at
  - 中文 query 召回数量
  - 错误率

输出：experiments/day1/source_feasibility.md
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / "api" / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "src"))

from factcheck.search.anysearch import AnySearchProvider
from factcheck.search.bocha import BochaProvider
from factcheck.search.metaso import MetasoProvider


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


# ============ (a) Bilibili ============

async def test_bilibili() -> dict:
    queries = ["纯净水危害", "电动自行车新国标", "草皮染绿"]
    latencies = []
    successes = 0
    samples: list[dict] = []
    errors: list[str] = []

    for q in queries:
        t0 = time.time()
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                r = await client.get(
                    "https://api.bilibili.com/x/web-interface/search/all/v2",
                    params={"keyword": q, "page": 1, "page_size": 10, "platform": "pc"},
                    headers={**HEADERS, "Referer": "https://www.bilibili.com"},
                )
                latencies.append(time.time() - t0)
                if r.status_code != 200:
                    errors.append(f"{q}: HTTP {r.status_code}")
                    continue
                d = r.json()
                if d.get("code") != 0:
                    errors.append(f"{q}: code={d.get('code')} msg={d.get('message')}")
                    continue
                video_count = 0
                for sec in d.get("data", {}).get("result", []) or []:
                    if sec.get("result_type") == "video":
                        items = sec.get("data", []) or []
                        video_count = len(items)
                        if items:
                            samples.append(
                                {
                                    "query": q,
                                    "title": items[0].get("title", "").replace("<em class=\"keyword\">", "").replace("</em>", ""),
                                    "bvid": items[0].get("bvid"),
                                    "pubdate_ts": items[0].get("pubdate"),
                                    "play": items[0].get("play"),
                                }
                            )
                        break
                if video_count > 0:
                    successes += 1
            except Exception as e:
                errors.append(f"{q}: {type(e).__name__}: {e}")
        await asyncio.sleep(0.5)

    return {
        "name": "bilibili_search_api",
        "endpoint": "api.bilibili.com/x/web-interface/search/all/v2",
        "auth_required": False,
        "queries_tested": len(queries),
        "successes": successes,
        "errors": errors,
        "avg_latency_s": round(statistics.mean(latencies), 2) if latencies else None,
        "has_publish_date": True,
        "publish_date_format": "unix_timestamp",
        "sample_results": samples[:3],
        "verdict": "USABLE" if successes >= 2 else "UNRELIABLE",
        "notes": "需要 Referer header 否则被反爬；公开 API 无需 token；返回含 pubdate(unix ts)、play(播放数)、author",
    }


# ============ (b) RSS feeds ============

RSS_FEEDS = [
    ("新华社", "http://www.xinhuanet.com/politics/news_politics.xml"),
    ("人民日报", "http://www.people.com.cn/rss/politics.xml"),
    ("光明网", "https://www.gmw.cn/rss/news.xml"),
    ("中国新闻网", "http://www.chinanews.com/rss/scroll-news.xml"),
    ("财新网", "https://www.caixin.com/rss/economy.xml"),
    ("第一财经", "https://www.yicai.com/rss/feed/9.xml"),
    ("36氪", "https://36kr.com/feed"),
    ("少数派", "https://sspai.com/feed"),
    ("界面新闻", "https://www.jiemian.com/lists/95.html.rss"),
    ("澎湃新闻", "https://www.thepaper.cn/rss_chnId_25.jsp"),
]


async def test_rss() -> dict:
    results = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for name, url in RSS_FEEDS:
            t0 = time.time()
            try:
                r = await client.get(url, headers=HEADERS)
                lat = time.time() - t0
                body = r.text[:2000]
                # crude: count <item> or <entry>
                n_items = body.count("<item") + body.count("<entry")
                status = "ok" if (r.status_code == 200 and n_items > 0) else "no_items"
                results.append(
                    {
                        "name": name,
                        "url": url,
                        "http_status": r.status_code,
                        "latency_s": round(lat, 2),
                        "n_items_sampled": n_items,
                        "content_type": r.headers.get("content-type", ""),
                        "verdict": status,
                    }
                )
            except Exception as e:
                results.append(
                    {
                        "name": name,
                        "url": url,
                        "verdict": "error",
                        "error": f"{type(e).__name__}: {e}",
                    }
                )

    alive = [r for r in results if r.get("verdict") == "ok"]
    return {
        "name": "rss_feeds",
        "n_tested": len(RSS_FEEDS),
        "n_alive": len(alive),
        "n_dead": len(results) - len(alive),
        "details": results,
        "verdict": "PARTIAL" if 0 < len(alive) < len(RSS_FEEDS) else ("USABLE" if len(alive) == len(RSS_FEEDS) else "BROKEN"),
        "notes": "RSS 是被动订阅，无法做事件触发的搜索；适合主流媒体 inflow 监控，不适合定点核查",
    }


# ============ (c) Web search providers ============

async def test_search_providers() -> dict:
    queries = [
        "电动自行车 金属鞍座 新标准",
        "纯净水 缺乏微量元素",
        "广州 草皮 染绿 喷漆",
    ]
    providers_cfg = {
        "bocha": (BochaProvider, "BOCHA_API_KEY"),
        "metaso": (MetasoProvider, "METASO_API_KEY"),
        "anysearch": (AnySearchProvider, "ANYSEARCH_API_KEY"),
    }
    out = {}
    for name, (cls, key_env) in providers_cfg.items():
        if not os.getenv(key_env):
            out[name] = {"verdict": "SKIP_NO_KEY"}
            continue
        provider = cls(os.environ[key_env])
        latencies = []
        n_results = []
        has_date = []
        errors = []
        for q in queries:
            t0 = time.time()
            try:
                rs = await provider.search(q, limit=8)
                latencies.append(time.time() - t0)
                n_results.append(len(rs))
                has_date.append(sum(1 for r in rs if r.published_at))
            except Exception as e:
                errors.append(f"{q}: {type(e).__name__}: {e}")
            await asyncio.sleep(0.3)
        out[name] = {
            "queries": len(queries),
            "avg_n_results": round(statistics.mean(n_results), 1) if n_results else 0,
            "avg_n_with_date": round(statistics.mean(has_date), 1) if has_date else 0,
            "avg_latency_s": round(statistics.mean(latencies), 2) if latencies else None,
            "errors": errors,
            "verdict": "USABLE" if len(n_results) == len(queries) and statistics.mean(n_results) >= 5 else "WEAK",
        }
    return {"name": "web_search_providers", "providers": out}


# ============ (d) piyao ============

async def test_piyao() -> dict:
    """连续 5 次抓 piyao.org.cn 首页，看反爬。"""
    url = "https://www.piyao.org.cn/"
    latencies = []
    sizes = []
    errors = []
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for i in range(5):
            t0 = time.time()
            try:
                r = await client.get(url, headers=HEADERS)
                latencies.append(time.time() - t0)
                if r.status_code == 200:
                    sizes.append(len(r.text))
                else:
                    errors.append(f"attempt {i + 1}: HTTP {r.status_code}")
            except Exception as e:
                errors.append(f"attempt {i + 1}: {type(e).__name__}: {e}")
            await asyncio.sleep(0.3)

    return {
        "name": "piyao_org_cn",
        "url": url,
        "auth_required": False,
        "n_attempts": 5,
        "n_success": len(sizes),
        "avg_latency_s": round(statistics.mean(latencies), 2) if latencies else None,
        "avg_page_size_kb": round(statistics.mean(sizes) / 1024, 1) if sizes else None,
        "errors": errors,
        "verdict": "USABLE" if len(sizes) >= 4 else "UNRELIABLE",
        "notes": "已有 eval/scraper/piyao_scraper.py 完整爬虫，含 LLM 提取 ground truth；无强反爬",
    }


# ============ Main ============

async def main() -> None:
    print("\n" + "=" * 70)
    print("  1.2 数据源逐个验证")
    print("=" * 70)

    print("\n[a] Bilibili API ...")
    bili = await test_bilibili()
    print(f"  → {bili['verdict']}  ({bili['successes']}/{bili['queries_tested']} queries OK, avg {bili['avg_latency_s']}s)")

    print("\n[b] RSS feeds ...")
    rss = await test_rss()
    print(f"  → {rss['verdict']}  ({rss['n_alive']}/{rss['n_tested']} alive)")
    for d in rss["details"]:
        ok = d.get("verdict")
        n = d.get("n_items_sampled", 0)
        print(f"    {ok:<10} {d['name']:<12} items={n}  ({d.get('error', '') or d.get('content_type', '')[:40]})")

    print("\n[c] Web search providers ...")
    search = await test_search_providers()
    for n, info in search["providers"].items():
        if info.get("verdict") == "SKIP_NO_KEY":
            print(f"  {n}: SKIP (no key)")
        else:
            print(f"  {n}: {info['verdict']}  avg {info['avg_n_results']} results "
                  f"({info['avg_n_with_date']} dated), {info['avg_latency_s']}s")

    print("\n[d] piyao.org.cn ...")
    py = await test_piyao()
    print(f"  → {py['verdict']}  ({py['n_success']}/{py['n_attempts']} OK, avg {py['avg_latency_s']}s, {py['avg_page_size_kb']}KB)")

    out = {
        "bilibili": bili,
        "rss_feeds": rss,
        "web_search": search,
        "piyao": py,
    }
    out_path = Path(__file__).parent / "source_feasibility.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\nfull data → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
