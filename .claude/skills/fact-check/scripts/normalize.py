"""Claim 归一化与缓存键计算。

CLI 用法：
    python normalize.py "原始 claim 文本"
    或：echo "claim 文本" | python normalize.py -

输出 JSON：
    { "normalized": "...", "cache_key": "sha256...", "entities_hint": [...], "time_hint": "..." }
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


CN_NUM_MAP = {
    "零": "0", "一": "1", "二": "2", "两": "2", "三": "3", "四": "4",
    "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
    "百": "00", "千": "000", "万": "0000", "亿": "00000000",
}

UNIT_NORM = [
    (r"(\d+(?:\.\d+)?)\s*万元", lambda m: f"{int(float(m.group(1)) * 10000)}元"),
    (r"(\d+(?:\.\d+)?)\s*亿元", lambda m: f"{int(float(m.group(1)) * 100000000)}元"),
    (r"(\d+(?:\.\d+)?)\s*千元", lambda m: f"{int(float(m.group(1)) * 1000)}元"),
]

PUNCT_TRANS = str.maketrans({
    "，": ",", "。": ".", "、": ",", "；": ";", "：": ":",
    "（": "(", "）": ")", "【": "[", "】": "]",
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "！": "!", "？": "?", "—": "-",
})


@dataclass
class NormalizeResult:
    original: str
    normalized: str
    cache_key: str
    entities_hint: list[str]
    time_hint: str | None
    region_hint: str | None
    has_numeric: bool


def normalize_text(text: str) -> str:
    text = text.strip()
    text = text.translate(PUNCT_TRANS)
    text = re.sub(r"\s+", " ", text)
    for pattern, repl in UNIT_NORM:
        text = re.sub(pattern, repl, text)
    text = text.lower()
    return text


def extract_entities_hint(text: str) -> list[str]:
    out: list[str] = []
    out.extend(re.findall(r"[一-龥]{2,4}(?:市|省|区|县|自治区|特别行政区)", text))
    out.extend(re.findall(r"[一-龥]{2,8}(?:部|委|局|办|协会|总署|总局|银行)", text))
    out.extend(re.findall(r"[A-Z][A-Za-z0-9\-]{2,}", text))
    out.extend(re.findall(r"[国发|国办发|.*?字][〔【\[]20\d{2}[〕】\]]\s?第?\s?\d+号", text))
    seen = set()
    deduped = []
    for item in out:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def extract_time_hint(text: str) -> str | None:
    m = re.search(r"(20\d{2})\s*[-年]?\s*(\d{1,2})?\s*[-月]?\s*(\d{1,2})?", text)
    if not m:
        return None
    year = m.group(1)
    month = m.group(2)
    day = m.group(3)
    if year and month and day:
        return f"{year}-{int(month):02d}-{int(day):02d}"
    if year and month:
        return f"{year}-{int(month):02d}"
    return year


REGION_KEYWORDS = [
    "北京", "上海", "天津", "重庆", "广州", "深圳", "杭州", "南京", "苏州",
    "青岛", "济南", "成都", "武汉", "西安", "郑州", "厦门", "宁波", "无锡",
    "长沙", "合肥", "福州", "昆明", "大连", "石家庄", "全国",
]

PROVINCE_KEYWORDS = [
    "广东", "浙江", "江苏", "山东", "河南", "河北", "湖北", "湖南", "福建",
    "安徽", "江西", "四川", "云南", "贵州", "陕西", "甘肃", "青海", "辽宁",
    "吉林", "黑龙江", "山西", "宁夏", "新疆", "内蒙古", "西藏", "广西", "海南",
]


def extract_region_hint(text: str) -> str | None:
    for kw in REGION_KEYWORDS:
        if kw in text:
            return kw
    for kw in PROVINCE_KEYWORDS:
        if kw in text:
            return kw
    return None


def compute_cache_key(normalized: str, entities: list[str], time_hint: str | None) -> str:
    h = hashlib.sha256()
    h.update(normalized.encode("utf-8"))
    h.update(b"|")
    h.update("|".join(sorted(entities)).encode("utf-8"))
    h.update(b"|")
    if time_hint:
        h.update(time_hint.encode("utf-8"))
    return h.hexdigest()[:32]


def normalize(claim: str) -> NormalizeResult:
    normalized = normalize_text(claim)
    entities = extract_entities_hint(claim)
    time_hint = extract_time_hint(claim)
    region_hint = extract_region_hint(claim)
    cache_key = compute_cache_key(normalized, entities, time_hint)
    has_numeric = bool(re.search(r"\d", claim))
    return NormalizeResult(
        original=claim,
        normalized=normalized,
        cache_key=cache_key,
        entities_hint=entities,
        time_hint=time_hint,
        region_hint=region_hint,
        has_numeric=has_numeric,
    )


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: normalize.py '<claim>' | normalize.py -", file=sys.stderr)
        return 2
    if argv[1] == "-":
        claim = sys.stdin.read().strip()
    else:
        claim = argv[1]
    result = normalize(claim)
    sys.stdout.write(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
