"""URL → source_type + authority_weight。

匹配顺序：精确域名 > 后缀域名匹配 > pattern 匹配 > 兜底规则。

CLI 用法：
    python source_classify.py https://qd.gov.cn/abc.html
    python source_classify.py --batch urls.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
AUTHORITY_PATH = ROOT / "data" / "source_authority.json"
BLACKLIST_PATH = ROOT / "data" / "content_farm_blacklist.json"


@dataclass
class Classification:
    url: str
    domain: str
    source_type: str
    authority_weight: float
    tier: str
    agency: str | None
    agency_level: str | None
    matched_by: str
    is_blacklisted: bool


def _load_data() -> tuple[dict, dict]:
    with AUTHORITY_PATH.open(encoding="utf-8") as f:
        auth = json.load(f)
    with BLACKLIST_PATH.open(encoding="utf-8") as f:
        bl = json.load(f)
    return auth, bl


def _extract_host(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host


def _suffix_match(host: str, target: str) -> bool:
    """target 是某个 'domain' 字段。host 等于 target 或 host 以 .target 结尾。"""
    return host == target or host.endswith("." + target)


def _classify(url: str, auth: dict, bl: dict) -> Classification:
    host = _extract_host(url)
    if not host:
        return Classification(
            url=url, domain="", source_type="unknown", authority_weight=0.0,
            tier="unknown", agency=None, agency_level=None,
            matched_by="invalid_url", is_blacklisted=False,
        )

    for bad in bl.get("domains", []):
        if "/" in bad:
            bad_host, _, _ = bad.partition("/")
            if _suffix_match(host, bad_host) and bad.partition("/")[2] in url:
                return Classification(
                    url=url, domain=host, source_type="content_farm",
                    authority_weight=0.0, tier="blacklist",
                    agency=None, agency_level=None,
                    matched_by=f"blacklist:{bad}", is_blacklisted=True,
                )
        else:
            if _suffix_match(host, bad):
                return Classification(
                    url=url, domain=host, source_type="content_farm",
                    authority_weight=0.0, tier="blacklist",
                    agency=None, agency_level=None,
                    matched_by=f"blacklist:{bad}", is_blacklisted=True,
                )

    sources = auth.get("sources", [])
    sources_sorted = sorted(sources, key=lambda s: -len(s["domain"]))
    for s in sources_sorted:
        if _suffix_match(host, s["domain"]):
            return Classification(
                url=url, domain=host,
                source_type=s["source_type"],
                authority_weight=s["authority_weight"],
                tier=s.get("tier", "unknown"),
                agency=s.get("agency"),
                agency_level=s.get("agency_level"),
                matched_by=f"exact:{s['domain']}",
                is_blacklisted=False,
            )

    patterns = auth.get("patterns", [])
    for p in patterns:
        pat = p["pattern"]
        if pat.startswith("*."):
            suffix = pat[2:]
            if host.endswith("." + suffix) or host == suffix:
                return Classification(
                    url=url, domain=host,
                    source_type=p["source_type"],
                    authority_weight=p["authority_weight"],
                    tier="pattern_fallback",
                    agency=None, agency_level=None,
                    matched_by=f"pattern:{pat}",
                    is_blacklisted=False,
                )

    return Classification(
        url=url, domain=host,
        source_type="unknown",
        authority_weight=0.30,
        tier="unknown",
        agency=None, agency_level=None,
        matched_by="default_fallback",
        is_blacklisted=False,
    )


def classify(url: str) -> Classification:
    auth, bl = _load_data()
    return _classify(url, auth, bl)


def classify_batch(urls: list[str]) -> list[Classification]:
    auth, bl = _load_data()
    return [_classify(u, auth, bl) for u in urls]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?")
    ap.add_argument("--batch", help="文件路径，每行一个 URL")
    args = ap.parse_args()

    if args.batch:
        urls = [ln.strip() for ln in Path(args.batch).read_text(encoding="utf-8").splitlines() if ln.strip()]
        results = classify_batch(urls)
        print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
    elif args.url:
        result = classify(args.url)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
