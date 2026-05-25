"""1.1 — 对 3 个真实 piyao 谣言 case，用 Bocha/Metaso/AnySearch + Bilibili API
搜索真实传播路径，记录每个 URL + 标题 + 时间戳 + 来源域名。

输出：experiments/day1/ground_truth.jsonl（每行一个 case 含 traces）
       experiments/day1/raw_search_results.json（完整原始结果留档）
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

import httpx
from dotenv import load_dotenv

# 加载 api/.env
load_dotenv(Path(__file__).resolve().parents[2] / "api" / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "src"))

from factcheck.extract.timeline_builder import _domain_to_category
from factcheck.search.anysearch import AnySearchProvider
from factcheck.search.bocha import BochaProvider
from factcheck.search.metaso import MetasoProvider


CASES = [
    {
        "id": "P019",
        "category": "policy_standard",
        "claim": "新标准要求电动自行车安装金属鞍座",
        "piyao_url": "https://www.piyao.org.cn/20251204/fa4811499ff84d40ac50ffa08cbc5bbf/c.html",
        "truth": "新标准并未要求电动自行车安装金属鞍座，而是要求座椅材料符合防火阻燃要求",
        "search_queries": [
            "电动自行车 金属鞍座 新标准",
            "电动车 金属座椅 国家标准",
        ],
    },
    {
        "id": "P015",
        "category": "health",
        "claim": "喝纯净水容易缺乏微量元素",
        "piyao_url": "https://www.piyao.org.cn/20251126/578be70a8e4642b8a1fc77cd9de810e1/c.html",
        "truth": "饮用纯净水并不会影响人体对微量元素的吸收，主要吸收途径是饮食而非饮水",
        "search_queries": [
            "纯净水 缺乏微量元素",
            "长期喝纯净水 危害",
        ],
    },
    {
        "id": "P021",
        "category": "social",
        "claim": "广州街头草皮被人工染绿",
        "piyao_url": "https://www.piyao.org.cn/20251205/6774450795b346ddbc1618efd78298f3/c.html",
        "truth": "喷洒的是园林养护药剂'莱恩坪安坪绿美'，对人体和动物无危害，用于草坪景观优化和促进生长",
        "search_queries": [
            "广州 草皮 染绿 喷漆",
            "白云区 草坪 染色 应付检查",
        ],
    },
]


# ============ Bilibili API ============

async def bilibili_search(query: str, limit: int = 8) -> list[dict]:
    """B 站搜索 API — 公开端点，无需 token。"""
    url = "https://api.bilibili.com/x/web-interface/search/all/v2"
    params = {
        "keyword": query,
        "page": 1,
        "page_size": limit,
        "platform": "pc",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://www.bilibili.com",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(url, params=params, headers=headers)
            if r.status_code != 200:
                return [{"_error": f"HTTP {r.status_code}", "_body": r.text[:200]}]
            data = r.json()
            if data.get("code") != 0:
                return [{"_error": f"bilibili_code={data.get('code')}", "_message": data.get("message")}]
            results = data.get("data", {}).get("result", [])
            # 找 video 类型
            videos: list[dict] = []
            for section in results:
                if section.get("result_type") == "video":
                    for item in section.get("data", []) or []:
                        videos.append(
                            {
                                "url": f"https://www.bilibili.com/video/{item.get('bvid', '')}",
                                "title": _strip_em(item.get("title", "")),
                                "author": item.get("author", ""),
                                "pubdate": _ts_to_date(item.get("pubdate")),
                                "play": item.get("play", 0),
                                "snippet": _strip_em(item.get("description", ""))[:200],
                                "source": "bilibili",
                            }
                        )
                    break
            return videos[:limit]
        except (httpx.HTTPError, ValueError) as e:
            return [{"_error": str(e)}]


def _strip_em(s: str) -> str:
    return s.replace("<em class=\"keyword\">", "").replace("</em>", "")


def _ts_to_date(ts: int | None) -> str | None:
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), UTC).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return None


# ============ 主流程 ============

async def trace_one_case(case: dict, providers: dict) -> dict:
    print(f"\n=== Tracing case {case['id']}: {case['claim'][:40]} ===")
    traces: list[dict] = []
    raw: dict[str, list] = {}

    for q in case["search_queries"]:
        print(f"  query: {q}")

        # Web search providers (并发)
        web_tasks = {}
        if "bocha" in providers:
            web_tasks["bocha"] = providers["bocha"].search(q, limit=8)
        if "metaso" in providers:
            web_tasks["metaso"] = providers["metaso"].search(q, limit=8)
        if "anysearch" in providers:
            web_tasks["anysearch"] = providers["anysearch"].search(q, limit=8)
        web_tasks["bilibili"] = bilibili_search(q, limit=6)

        web_results = await asyncio.gather(*web_tasks.values(), return_exceptions=True)
        for name, res in zip(web_tasks.keys(), web_results, strict=True):
            if isinstance(res, Exception):
                print(f"    [{name}] ERROR: {res}")
                raw.setdefault(name, []).append({"query": q, "error": str(res)})
                continue
            if name == "bilibili":
                vids = res
                raw.setdefault("bilibili", []).append({"query": q, "results": vids})
                for v in vids:
                    if "_error" in v:
                        continue
                    domain = urlparse(v["url"]).hostname or "bilibili.com"
                    traces.append(
                        {
                            "url": v["url"],
                            "title": v["title"],
                            "published_at": v.get("pubdate"),
                            "domain": domain,
                            "source_category": _domain_to_category(domain),
                            "snippet": v.get("snippet", ""),
                            "found_via": "bilibili_api",
                            "query": q,
                        }
                    )
                print(f"    [bilibili] {len([v for v in vids if '_error' not in v])} videos")
            else:
                items = res
                raw.setdefault(name, []).append({"query": q, "results": [_sr_to_dict(r) for r in items]})
                for r in items:
                    domain = urlparse(r.url).hostname or ""
                    traces.append(
                        {
                            "url": r.url,
                            "title": r.title,
                            "published_at": r.published_at,
                            "domain": domain,
                            "source_category": _domain_to_category(domain),
                            "snippet": (r.snippet or "")[:200],
                            "found_via": name,
                            "query": q,
                        }
                    )
                print(f"    [{name}] {len(items)} results")
        await asyncio.sleep(0.3)  # rate limit courtesy

    # 去重（按 url）
    seen = set()
    deduped: list[dict] = []
    for t in traces:
        if t["url"] in seen:
            continue
        seen.add(t["url"])
        deduped.append(t)

    return {
        "case_id": case["id"],
        "category": case["category"],
        "claim": case["claim"],
        "truth": case["truth"],
        "piyao_url": case["piyao_url"],
        "n_traces_raw": len(traces),
        "n_traces_dedup": len(deduped),
        "traces": deduped,
        "_raw": raw,
    }


def _sr_to_dict(r) -> dict:
    return {
        "url": r.url,
        "title": r.title,
        "snippet": r.snippet,
        "published_at": r.published_at,
        "provider": r.provider,
    }


async def main() -> None:
    providers: dict = {}
    if os.getenv("BOCHA_API_KEY"):
        providers["bocha"] = BochaProvider(os.environ["BOCHA_API_KEY"])
    if os.getenv("METASO_API_KEY"):
        providers["metaso"] = MetasoProvider(os.environ["METASO_API_KEY"])
    if os.getenv("ANYSEARCH_API_KEY"):
        providers["anysearch"] = AnySearchProvider(os.environ["ANYSEARCH_API_KEY"])

    print(f"providers loaded: {list(providers.keys())} + bilibili")

    t0 = time.time()
    results = []
    for case in CASES:
        r = await trace_one_case(case, providers)
        results.append(r)

    elapsed = time.time() - t0

    # 写 ground_truth.jsonl（去掉 _raw 字段）
    out_dir = Path(__file__).parent
    with open(out_dir / "ground_truth.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            r_clean = {k: v for k, v in r.items() if k != "_raw"}
            f.write(json.dumps(r_clean, ensure_ascii=False) + "\n")

    # 写 raw 完整结果
    with open(out_dir / "raw_search_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 打印 summary
    print(f"\n{'=' * 60}")
    print(f"DONE in {elapsed:.1f}s")
    print(f"{'=' * 60}")
    for r in results:
        print(f"\n{r['case_id']} [{r['category']}]: {r['claim'][:40]}")
        print(f"  raw traces: {r['n_traces_raw']}, dedup: {r['n_traces_dedup']}")
        # source distribution
        from collections import Counter
        cats = Counter(t["source_category"] for t in r["traces"])
        print(f"  category dist: {dict(cats)}")
        # 时间分布
        dates = sorted([t["published_at"] for t in r["traces"] if t.get("published_at")])
        if dates:
            print(f"  date range: {dates[0]} → {dates[-1]} ({len(dates)} dated)")


if __name__ == "__main__":
    asyncio.run(main())
