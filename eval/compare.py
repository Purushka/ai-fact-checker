"""对比两次 eval 报告，高亮 regression。

用法：
    python eval/compare.py --base eval/reports/eval_20260101_120000.json --new eval/reports/eval_20260102_180000.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def fmt_delta(old: float, new: float, suffix: str = "") -> str:
    d = new - old
    if abs(d) < 0.0005:
        return f"{new:.3f}{suffix} (=)"
    sign = "+" if d > 0 else ""
    flag = "✓" if d > 0 else "⚠"
    return f"{new:.3f}{suffix} ({sign}{d:.3f}{suffix} {flag})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--new", required=True)
    args = ap.parse_args()

    base = json.loads(Path(args.base).read_text(encoding="utf-8"))
    new = json.loads(Path(args.new).read_text(encoding="utf-8"))

    bm = base["metrics"]
    nm = new["metrics"]

    print("\n=== Eval Comparison ===")
    print(f"  base: {base['run_id']} ({base['mode']})")
    print(f"  new:  {new['run_id']} ({new['mode']})")
    print()
    print(f"{'Metric':<22} {'base':<12} {'new':<24}")
    print("-" * 60)
    print(f"{'n_total':<22} {bm['n_total']:<12} {nm['n_total']}")
    print(
        f"{'accuracy':<22} {bm['accuracy']:<12} {fmt_delta(bm['accuracy'], nm['accuracy'])}"
    )
    print(
        f"{'macro_f1':<22} {bm['macro_f1']:<12} {fmt_delta(bm['macro_f1'], nm['macro_f1'])}"
    )
    print(
        f"{'avg_latency_sec':<22} {bm['avg_latency_sec']:<12} {nm['avg_latency_sec']:.2f}s ({'+' if nm['avg_latency_sec'] > bm['avg_latency_sec'] else ''}{nm['avg_latency_sec'] - bm['avg_latency_sec']:.2f}s)"
    )
    print(f"{'avg_tokens':<22} {bm['avg_tokens']:<12} {nm['avg_tokens']}")
    print(f"{'avg_cost_yuan':<22} ¥{bm['avg_cost_yuan']:<10} ¥{nm['avg_cost_yuan']}")

    print("\n=== Per-class F1 ===")
    print(f"{'Class':<22} {'base F1':<12} {'new F1':<24}")
    print("-" * 60)
    regressions: list[str] = []
    improvements: list[str] = []
    for cls in bm["per_class"]:
        b_f1 = bm["per_class"][cls]["f1"]
        n_f1 = nm["per_class"].get(cls, {}).get("f1", 0)
        diff = n_f1 - b_f1
        print(f"{cls:<22} {b_f1:<12} {fmt_delta(b_f1, n_f1)}")
        if diff < -0.02:
            regressions.append(f"  ⚠ {cls}: F1 {b_f1} → {n_f1} (Δ={diff:+.3f})")
        elif diff > 0.02:
            improvements.append(f"  ✓ {cls}: F1 {b_f1} → {n_f1} (Δ={diff:+.3f})")

    print("\n=== Per-category accuracy ===")
    print(f"{'Category':<22} {'base':<12} {'new':<24}")
    print("-" * 60)
    cat_regressions: list[str] = []
    cat_improvements: list[str] = []
    for cat in base.get("by_category", {}):
        b_acc = base["by_category"][cat]["accuracy"]
        n_acc = new.get("by_category", {}).get(cat, {}).get("accuracy", 0)
        print(f"{cat:<22} {b_acc:<12} {fmt_delta(b_acc, n_acc)}")
        diff = n_acc - b_acc
        if diff < -0.05:
            cat_regressions.append(
                f"  ⚠ {cat}: accuracy {b_acc} → {n_acc} (Δ={diff:+.3f})"
            )
        elif diff > 0.05:
            cat_improvements.append(
                f"  ✓ {cat}: accuracy {b_acc} → {n_acc} (Δ={diff:+.3f})"
            )

    if regressions or cat_regressions:
        print(
            "\n=== ⚠️ Regressions (per-class F1 drop >0.02 或 per-category accuracy drop >0.05) ==="
        )
        for line in regressions + cat_regressions:
            print(line)
    if improvements or cat_improvements:
        print("\n=== ✓ Improvements ===")
        for line in improvements + cat_improvements:
            print(line)
    if not regressions and not cat_regressions:
        print("\n✓ 无显著 regression（thresholds: F1 0.02, cat acc 0.05）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
