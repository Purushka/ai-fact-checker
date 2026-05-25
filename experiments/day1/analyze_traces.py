"""分析 trace_3_cases.py 的输出 — 跑 TimelineBuilder 看真实传播模式。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "src"))

from factcheck.extract.timeline_builder import TimelineBuilder


DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _normalize_date(d: str | None) -> str | None:
    if not d:
        return None
    m = DATE_RE.search(d)
    return m.group(0) if m else None


def main() -> None:
    in_path = Path(__file__).parent / "ground_truth.jsonl"
    with open(in_path, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f]

    print("=" * 78)
    print("  真实传播路径分析 — 3 piyao case via Bocha/Metaso/AnySearch/Bilibili")
    print("=" * 78)

    for case in cases:
        print(f"\n\n### {case['case_id']} [{case['category']}] {case['claim']}")
        print(f"### Truth: {case['truth']}")
        print(f"### Piyao URL: {case['piyao_url']}")

        # 构造 evidence dict 给 TimelineBuilder
        evs: list[dict] = []
        for t in case["traces"]:
            evs.append(
                {
                    "source_url": t["url"],
                    "source_name": t.get("title", "")[:40],
                    "source_type": "mainstream_media",
                    "agency": None,
                    "published_at": _normalize_date(t.get("published_at")),
                    "snippet": t.get("snippet", "")[:200],
                }
            )

        timeline = TimelineBuilder().build(evs)
        print(f"\n[pattern]   {timeline.pattern}  (confidence={timeline.confidence})")
        print(f"[note]      {timeline.note}")
        print(f"[date_rng]  {timeline.earliest_date}  →  {timeline.latest_date}  (n_stops={timeline.n_stops})")

        # 按时间排序的前 12 个 stop
        print(f"\n  {'date':<12} {'category':<24} {'domain':<28} title")
        print(f"  {'-' * 12} {'-' * 24} {'-' * 28} {'-' * 30}")
        for s in timeline.stops[:12]:
            flag = ""
            if s.is_likely_origin:
                flag = " [ORIGIN]"
            if s.is_likely_amplifier:
                flag = " [AMPLIFIER]"
            from urllib.parse import urlparse
            dom = (urlparse(s.source_url).hostname or "")[:26]
            title = (s.source_name or "")[:30]
            print(f"  {s.earliest_seen or '-':<12} {s.source_category:<24} {dom:<28} {title}{flag}")

        if len(timeline.stops) > 12:
            print(f"  ... ({len(timeline.stops) - 12} more)")


if __name__ == "__main__":
    main()
