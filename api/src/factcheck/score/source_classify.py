"""URL → source_type + authority_weight。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

DATA_DIR = Path(__file__).resolve().parent / "data"
AUTHORITY_PATH = DATA_DIR / "source_authority.json"
BLACKLIST_PATH = DATA_DIR / "content_farm_blacklist.json"


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

    def discardable(self) -> bool:
        return self.is_blacklisted or self.authority_weight == 0


@lru_cache(maxsize=1)
def _load() -> tuple[dict, dict, list[dict]]:
    with AUTHORITY_PATH.open(encoding="utf-8") as f:
        auth = json.load(f)
    with BLACKLIST_PATH.open(encoding="utf-8") as f:
        bl = json.load(f)
    sources_sorted = sorted(auth.get("sources", []), key=lambda s: -len(s["domain"]))
    return auth, bl, sources_sorted


def _host(url: str) -> str:
    if "://" not in url:
        url = "http://" + url
    return (urlparse(url).hostname or "").lower()


def _suffix_match(host: str, target: str) -> bool:
    return host == target or host.endswith("." + target)


class SourceClassifier:
    def __init__(self) -> None:
        self._auth, self._bl, self._sources_sorted = _load()

    def reload(self) -> None:
        _load.cache_clear()
        self._auth, self._bl, self._sources_sorted = _load()

    def classify(self, url: str) -> Classification:
        host = _host(url)
        if not host:
            return Classification(url, "", "unknown", 0.0, "unknown", None, None, "invalid_url", False)

        for bad in self._bl.get("domains", []):
            if "/" in bad:
                bad_host, _, bad_path = bad.partition("/")
                if _suffix_match(host, bad_host) and bad_path in url:
                    return Classification(
                        url, host, "content_farm", 0.0, "blacklist", None, None, f"blacklist:{bad}", True
                    )
            elif _suffix_match(host, bad):
                return Classification(
                    url, host, "content_farm", 0.0, "blacklist", None, None, f"blacklist:{bad}", True
                )

        for s in self._sources_sorted:
            if _suffix_match(host, s["domain"]):
                return Classification(
                    url=url,
                    domain=host,
                    source_type=s["source_type"],
                    authority_weight=s["authority_weight"],
                    tier=s.get("tier", "unknown"),
                    agency=s.get("agency"),
                    agency_level=s.get("agency_level"),
                    matched_by=f"exact:{s['domain']}",
                    is_blacklisted=False,
                )

        for p in self._auth.get("patterns", []):
            pat = p["pattern"]
            if pat.startswith("*."):
                suffix = pat[2:]
                if host.endswith("." + suffix) or host == suffix:
                    return Classification(
                        url=url,
                        domain=host,
                        source_type=p["source_type"],
                        authority_weight=p["authority_weight"],
                        tier="pattern_fallback",
                        agency=None,
                        agency_level=None,
                        matched_by=f"pattern:{pat}",
                        is_blacklisted=False,
                    )

        return Classification(url, host, "unknown", 0.30, "unknown", None, None, "default_fallback", False)

    def classify_batch(self, urls: list[str]) -> list[Classification]:
        return [self.classify(u) for u in urls]
