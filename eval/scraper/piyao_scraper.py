"""piyao.org.cn 爬虫 — 从中国互联网联合辟谣平台多个栏目抓辟谣 case。

策略：
  1. 遍历多个栏目页（科技/消防/网络辟谣/今日辟谣等），提取所有文章 URL
  2. 对每个 URL，httpx 抓正文 + trafilatura 清洗
  3. DeepSeek 从正文提取：rumor claim、truth、category
  4. 输出 eval/data/benchmark_cn.jsonl，每条 {id, claim, ground_truth=contradicted, category, source_url, evidence_summary}

用法：
    python eval/scraper/piyao_scraper.py --max 80
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
import trafilatura

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "api" / "src"))

env_path = ROOT / "api" / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

from factcheck.llm.base import LLMMessage  # noqa: E402
from factcheck.llm.openai_compatible import OpenAICompatibleProvider  # noqa: E402
from factcheck.utils.json_parse import parse_json_lenient  # noqa: E402

BASE = "https://www.piyao.org.cn"
SECTIONS = [
    "/wlpysl/index.html",  # 网络辟谣实录
    "/kjzyj/index.html",  # 科技 辟谣
    "/xfaq/index.html",  # 消防 辟谣
    "/jrpy/index.htm",  # 今日辟谣
    "/sq/index.htm",  # 社区辟谣
]
ARTICLE_PATTERN = re.compile(r"https?://www\.piyao\.org\.cn/20\d{6}/[a-f0-9]+/c\.html")

EXTRACTOR_PROMPT = """\
你是辟谣文章的结构化提取助手。给定一篇中国互联网联合辟谣平台 (piyao.org.cn) 的辟谣文章正文，
请抽取出：
1. **谣言原话（rumor claim）**：被辟谣的具体声明，一句话，含数字/日期/事件等具体细节，保持原文措辞
2. **真相（truth）**：官方权威给出的反驳/真相，一句话总结
3. **分类（category）**：从 ["policy", "health", "social", "tech", "safety", "finance", "education", "other"] 中选一个最贴近的

如果文章不是真辟谣（如：辟谣活动新闻、培训通知、政策解读、辟谣经验分享），返回 {"skip": true, "reason": "..."}。

只返回 JSON：
{
  "claim": "...",         // 谣言原话，可直接作为 fact-check 的 claim 输入
  "truth": "...",         // 真相一句话
  "category": "policy",   // 八选一
  "evidence_summary": "..." // 一两句话说明权威反驳依据，<=120字
}

如果原文里有多个 claim，选最核心的那个。如果 claim 太抽象无法独立 fact-check（如"网传 XX 是假的"未说 XX 内容），也 skip。"""


def fetch_article_urls() -> list[str]:
    seen: set[str] = set()
    for sec in SECTIONS:
        for n_suffix in ["", "_1", "_2", "_3", "_4", "_5"]:
            u = BASE + sec.replace(".html", f"{n_suffix}.html").replace(
                ".htm", f"{n_suffix}.htm"
            )
            try:
                r = httpx.get(u, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                if r.status_code != 200:
                    continue
                text = r.content.decode("utf-8", errors="replace")
                for art in ARTICLE_PATTERN.findall(text):
                    seen.add(art)
            except Exception:
                continue
    return sorted(seen)


def fetch_article_body(url: str) -> tuple[str, str] | None:
    """返回 (title_from_meta, cleaned_body)。失败返回 None。"""
    try:
        r = httpx.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        text = r.content.decode("utf-8", errors="replace")
    except Exception:
        return None
    desc = re.search(r'<meta[^>]+name="description"[^>]+content="([^"]+)"', text)
    title = (desc.group(1) if desc else "").strip().rstrip("-").strip()
    body = trafilatura.extract(text, include_comments=False, favor_recall=True) or ""
    body = body.strip()
    if len(body) < 80:
        return None
    return title, body


async def llm_extract(
    provider: OpenAICompatibleProvider, title: str, body: str, url: str
) -> dict | None:
    user_msg = (
        f"文章 URL: {url}\n标题/简介: {title}\n\n正文（≤3500 字）:\n{body[:3500]}"
    )
    try:
        resp = await provider.chat(
            [
                LLMMessage(role="system", content=EXTRACTOR_PROMPT),
                LLMMessage(role="user", content=user_msg),
            ],
            temperature=0.0,
            max_tokens=600,
            timeout=30.0,
        )
    except Exception as e:
        print(f"  LLM err: {e}", file=sys.stderr)
        return None
    parsed = parse_json_lenient(resp.text) or {}
    if not isinstance(parsed, dict) or parsed.get("skip"):
        return None
    if not parsed.get("claim") or not parsed.get("truth"):
        return None
    return parsed


async def amain(max_n: int, out_path: Path) -> int:
    # 优先用 DeepSeek 直连；失败则 fallback OpenRouter（也走 deepseek-chat）
    if os.environ.get("OPENROUTER_API_KEY"):
        provider = OpenAICompatibleProvider(
            name="openrouter-deepseek",
            base_url=os.environ.get(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ),
            api_key=os.environ["OPENROUTER_API_KEY"],
            default_model="deepseek/deepseek-chat",
        )
    else:
        provider = OpenAICompatibleProvider(
            name="deepseek",
            base_url="https://api.deepseek.com/v1",
            api_key=os.environ["DEEPSEEK_API_KEY"],
            default_model="deepseek-chat",
        )

    print("1. 抓取栏目页 → 收集文章 URL", file=sys.stderr)
    urls = fetch_article_urls()
    print(f"   收集到 {len(urls)} 个 article URL", file=sys.stderr)

    print(f"2. 对每篇 LLM 提取结构化辟谣 case（上限 {max_n} 条）", file=sys.stderr)
    cases: list[dict] = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(urls, 1):
        if len(cases) >= max_n:
            break
        print(f"  [{i}/{len(urls)}] {url[-40:]}", file=sys.stderr, flush=True)
        body = fetch_article_body(url)
        if not body:
            print("    skip (no body)", file=sys.stderr)
            time.sleep(0.3)
            continue
        title, content = body
        extracted = await llm_extract(provider, title, content, url)
        if not extracted:
            print("    skip (LLM filtered)", file=sys.stderr)
            time.sleep(0.3)
            continue
        cid = f"P{len(cases) + 1:03d}"
        case = {
            "id": cid,
            "claim": extracted["claim"],
            "ground_truth": "contradicted",  # piyao 全是辟谣，都是 contradicted
            "category": extracted.get("category", "other"),
            "source_url": url,
            "evidence_summary": extracted.get("evidence_summary")
            or extracted.get("truth", ""),
            "truth": extracted.get("truth", ""),
            "source": "piyao.org.cn",
            "scraped_at": time.strftime("%Y-%m-%d"),
        }
        cases.append(case)
        print(f"    ✓ {extracted['claim'][:60]}", file=sys.stderr)
        # 增量写
        with out_path.open("w", encoding="utf-8") as f:
            for c in cases:
                f.write(json.dumps(c, ensure_ascii=False) + "\n")
        time.sleep(0.3)

    print(f"\n完成: {len(cases)} 条辟谣 case → {out_path}", file=sys.stderr)
    by_cat: dict[str, int] = {}
    for c in cases:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
    print(f"category 分布: {by_cat}", file=sys.stderr)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=80)
    ap.add_argument("--out", default="eval/data/piyao_cases.jsonl")
    args = ap.parse_args()
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    return asyncio.run(amain(args.max, out_path))


if __name__ == "__main__":
    sys.exit(main())
