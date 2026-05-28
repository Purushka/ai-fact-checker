"""跑完所有 case 后汇总指标：skill 判断 vs 裸 LLM 判断的四象限对比。

用法：
    python compare.py --report ../reports/run_xxx.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def quadrant(skill_match: bool, bare_match: bool) -> str:
    if skill_match and bare_match:
        return "A_right_B_right"
    if skill_match and not bare_match:
        return "A_right_B_wrong"
    if not skill_match and bare_match:
        return "A_wrong_B_right"
    return "A_wrong_B_wrong"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    p = Path(args.report)
    report = json.loads(p.read_text(encoding="utf-8"))
    cases = report["cases"]

    counters = Counter()
    skill_verdicts = Counter()
    bare_verdicts = Counter()
    category_break = {}

    for c in cases:
        if c.get("matched") is None or c.get("bare_llm_matched") is None:
            counters["pending"] += 1
            continue
        q = quadrant(c["matched"], c["bare_llm_matched"])
        counters[q] += 1
        cat = c.get("category") or "uncategorized"
        category_break.setdefault(cat, Counter())[q] += 1
        if c.get("skill_actual"):
            skill_verdicts[c["skill_actual"].get("verdict", "unknown")] += 1
        if c.get("bare_llm_actual"):
            bare_verdicts[c["bare_llm_actual"].get("verdict", "unknown")] += 1

    total_done = sum(counters[k] for k in ("A_right_B_right", "A_right_B_wrong", "A_wrong_B_right", "A_wrong_B_wrong"))
    summary = {
        "run_id": report.get("run_id"),
        "total_cases": report["total_cases"],
        "completed": total_done,
        "pending": counters["pending"],
        "quadrants": {
            "A_right_B_right": counters["A_right_B_right"],
            "A_right_B_wrong": counters["A_right_B_wrong"],
            "A_wrong_B_right": counters["A_wrong_B_right"],
            "A_wrong_B_wrong": counters["A_wrong_B_wrong"],
        },
        "skill_accuracy": (counters["A_right_B_right"] + counters["A_right_B_wrong"]) / max(1, total_done),
        "bare_llm_accuracy": (counters["A_right_B_right"] + counters["A_wrong_B_right"]) / max(1, total_done),
        "skill_added_value_cases": counters["A_right_B_wrong"],
        "skill_regression_cases": counters["A_wrong_B_right"],
        "verdict_distribution_skill": dict(skill_verdicts),
        "verdict_distribution_bare_llm": dict(bare_verdicts),
        "by_category": {k: dict(v) for k, v in category_break.items()},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sum_path = p.with_name(p.stem + "_summary.json")
    sum_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n汇总已写入：{sum_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
