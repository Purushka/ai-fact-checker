"""二次分析 — 按 piyao 发布日期窗口过滤 traces，剔除历史无关结果。

发现：宽泛关键词搜索会返回十几年前的相似话题文章（"纯净水危害"自 2007 就有）。
对单个事件的传播链追溯，应限定在 piyao 发文日期前后窗口内。
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "src"))

from factcheck.extract.timeline_builder import TimelineBuilder

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
PIYAO_DATE_RE = re.compile(r"piyao\.org\.cn/(\d{8})/")

# 窗口：piyao 发文日期前 90 天 → 后 30 天
WINDOW_BEFORE = 90
WINDOW_AFTER = 30


def _normalize_date(d: str | None) -> str | None:
    if not d:
        return None
    m = DATE_RE.search(d)
    return m.group(0) if m else None


def _piyao_date(url: str) -> date | None:
    m = PIYAO_DATE_RE.search(url)
    if not m:
        return None
    s = m.group(1)
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def main() -> None:
    in_path = Path(__file__).parent / "ground_truth.jsonl"
    with open(in_path, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f]

    print("=" * 78)
    print("  时间窗口过滤后的传播链分析（piyao 日期 ± 窗口）")
    print("=" * 78)

    summary_rows = []
    for case in cases:
        pd = _piyao_date(case["piyao_url"])
        if pd is None:
            print(f"\n[skip {case['case_id']}: 无法解析 piyao 日期]")
            continue
        win_lo = pd - timedelta(days=WINDOW_BEFORE)
        win_hi = pd + timedelta(days=WINDOW_AFTER)

        print(f"\n\n### {case['case_id']} [{case['category']}] {case['claim']}")
        print(f"### piyao 发文: {pd.isoformat()}   窗口: [{win_lo} → {win_hi}]")

        filtered: list[dict] = []
        no_date = 0
        out_of_window = 0
        for t in case["traces"]:
            nd = _normalize_date(t.get("published_at"))
            if not nd:
                no_date += 1
                continue
            try:
                td = date.fromisoformat(nd)
            except ValueError:
                no_date += 1
                continue
            if win_lo <= td <= win_hi:
                filtered.append({**t, "_norm_date": nd})
            else:
                out_of_window += 1

        print(f"### 总 {len(case['traces'])}  在窗口内 {len(filtered)}  窗口外 {out_of_window}  无日期 {no_date}")

        if not filtered:
            print(">> 窗口内无 trace → 无法分析传播链")
            summary_rows.append((case["case_id"], 0, "no_traces_in_window", "-"))
            continue

        evs = [
            {
                "source_url": t["url"],
                "source_name": t.get("title", "")[:40],
                "source_type": "mainstream_media",
                "agency": None,
                "published_at": t["_norm_date"],
                "snippet": t.get("snippet", "")[:200],
            }
            for t in filtered
        ]
        timeline = TimelineBuilder().build(evs)
        print(f"\n[pattern]   {timeline.pattern}  (confidence={timeline.confidence})")
        print(f"[note]      {timeline.note}")
        print(f"[date_rng]  {timeline.earliest_date}  →  {timeline.latest_date}  (n_stops={timeline.n_stops})")

        print(f"\n  {'date':<12} {'category':<24} {'domain':<28} title")
        print(f"  {'-' * 12} {'-' * 24} {'-' * 28} {'-' * 30}")
        for s in timeline.stops[:15]:
            flag = ""
            if s.is_likely_origin:
                flag = "  [ORIGIN]"
            if s.is_likely_amplifier:
                flag = "  [AMPLIFIER]"
            dom = (urlparse(s.source_url).hostname or "")[:26]
            title = (s.source_name or "")[:30]
            print(f"  {s.earliest_seen or '-':<12} {s.source_category:<24} {dom:<28} {title}{flag}")

        if len(timeline.stops) > 15:
            print(f"  ... ({len(timeline.stops) - 15} more)")

        summary_rows.append((case["case_id"], len(filtered), timeline.pattern, timeline.confidence))

    print(f"\n\n{'=' * 78}")
    print("  汇总")
    print("=" * 78)
    print(f"{'case':<8} {'n_in_window':<14} {'pattern':<28} confidence")
    for cid, n, pat, conf in summary_rows:
        print(f"{cid:<8} {n:<14} {pat:<28} {conf}")


if __name__ == "__main__":
    main()
