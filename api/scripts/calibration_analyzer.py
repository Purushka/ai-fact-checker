"""Calibration 分析 — confidence 是否可靠？CI 是否覆盖真实情况？

输入：跑测结果 JSON（含 verdict / confidence / confidence_interval / ground_truth）
输出：
  - Expected Calibration Error (ECE)
  - Reliability diagram (per bin)
  - CI coverage: ground_truth 是否在 [lo, hi] 内的比例
  - CI 平均宽度（窄=稳健）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def compute_ece(results: list[dict], bins: list[tuple[int, int]]) -> tuple[float, list[dict]]:
    """Expected Calibration Error 计算。

    ECE = Σ (n_bin / N) × |accuracy_in_bin - mean_conf_in_bin|

    对每个 confidence bin：
    - 计算该 bin 内的 mean confidence
    - 计算该 bin 内的 accuracy（match=1 / total）
    - 误差 = |confidence - accuracy|
    - 加权（按 bin size / total N）
    """
    bin_stats = []
    N = len(results)
    ece = 0.0
    for lo, hi in bins:
        in_bin = [r for r in results if lo <= r["confidence"] <= hi]
        if not in_bin:
            bin_stats.append({"range": (lo, hi), "n": 0, "mean_conf": None, "accuracy": None, "error": None})
            continue
        n = len(in_bin)
        mean_conf = sum(r["confidence"] for r in in_bin) / n
        n_correct = sum(1 for r in in_bin if r["match"])
        accuracy = n_correct / n
        err = abs(mean_conf - accuracy * 100)
        ece += (n / N) * err
        bin_stats.append({
            "range": (lo, hi),
            "n": n,
            "mean_conf": round(mean_conf, 1),
            "accuracy": round(accuracy * 100, 1),
            "error": round(err, 1),
        })
    return round(ece, 2), bin_stats


def ci_coverage_analysis(results: list[dict]) -> dict:
    """CI 覆盖分析。

    对每个 case：
    - actual_accuracy_signal = match ? 100 : 0
    - 检查 actual_accuracy_signal 是否落在 [ci_lo, ci_hi] 内（产品语义：系统是否对自己的不确定性估计准确）

    更有意义的指标：
    - CI 宽度分布（窄 = 稳健 / 宽 = 系统知道自己不确定）
    - 不同 CI 宽度下的准确率
    """
    widths = []
    narrow_correct = 0; narrow_total = 0
    wide_correct = 0; wide_total = 0
    covers = 0
    for r in results:
        ci = r.get("ci")
        if not ci:
            continue
        lo, hi = ci[0], ci[1]
        widths.append(hi - lo)
        actual = 100 if r["match"] else 0
        if lo <= actual <= hi:
            covers += 1
        if (hi - lo) < 20:  # 窄区间
            narrow_total += 1
            if r["match"]: narrow_correct += 1
        elif (hi - lo) > 40:  # 宽区间
            wide_total += 1
            if r["match"]: wide_correct += 1

    avg_width = sum(widths) / len(widths) if widths else 0
    return {
        "n_with_ci": len(widths),
        "avg_width": round(avg_width, 1),
        "coverage_rate": round(covers / max(1, len(widths)) * 100, 1),
        "narrow_ci_acc": round(narrow_correct / max(1, narrow_total) * 100, 1) if narrow_total else None,
        "narrow_ci_n": narrow_total,
        "wide_ci_acc": round(wide_correct / max(1, wide_total) * 100, 1) if wide_total else None,
        "wide_ci_n": wide_total,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="跑测结果 JSON")
    ap.add_argument("--out", help="写报告到 markdown 文件")
    args = ap.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))

    # 提取所有"系统判断"（结果集合可能含 system / hybrid / bare 多个判断，按 key 拆）
    by_judge = {}
    for r in data:
        for key, mkey in [("system", "system_match"), ("bare", "bare_match"), ("hybrid", "hybrid_match")]:
            if key not in r or mkey not in r:
                continue
            entry = r[key]
            if isinstance(entry, dict):
                conf = entry.get("confidence", 0)
                ci = entry.get("ci") or entry.get("confidence_interval")
                if isinstance(ci, dict):
                    ci = (ci.get("lo"), ci.get("hi"))
                # 兼容新格式 ci_lo / ci_hi
                if ci is None and entry.get("ci_lo") is not None:
                    ci = (entry.get("ci_lo"), entry.get("ci_hi"))
                by_judge.setdefault(key, []).append({
                    "case_id": r.get("case_id"),
                    "confidence": conf,
                    "match": r.get(mkey, False),
                    "ci": ci,
                    "ground_truth": r.get("ground_truth"),
                    "verdict": entry.get("verdict"),
                })

    bins = [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)]

    lines = []
    lines.append("# Calibration 分析报告\n")
    lines.append(f"输入: {args.input}\n")

    for judge_name, results in by_judge.items():
        if not results:
            continue
        ece, bin_stats = compute_ece(results, bins)
        N = len(results)
        n_correct = sum(1 for r in results if r["match"])

        lines.append(f"\n## {judge_name} (n={N}, acc={n_correct/N*100:.1f}%)\n")
        lines.append(f"### Expected Calibration Error (ECE) = **{ece}**\n")
        lines.append(f"（ECE 越接近 0 越准。ECE < 10 算 well-calibrated，> 20 算 poorly calibrated。）\n")
        lines.append("\n### Reliability Diagram (per bin)\n")
        lines.append("| Conf Bin | n | Mean Conf | Actual Acc | |Conf-Acc| |")
        lines.append("|---|---:|---:|---:|---:|")
        for b in bin_stats:
            r = f"{b['range'][0]}-{b['range'][1]}"
            if b["n"] == 0:
                lines.append(f"| {r} | 0 | — | — | — |")
            else:
                lines.append(f"| {r} | {b['n']} | {b['mean_conf']} | {b['accuracy']}% | {b['error']:.1f} |")

        # CI coverage（只对 hybrid/system 有 CI）
        ci_data = [r for r in results if r["ci"]]
        if ci_data:
            ci_stats = ci_coverage_analysis(results)
            lines.append("\n### Confidence Interval 分析\n")
            lines.append(f"- 有 CI 的 case: {ci_stats['n_with_ci']}")
            lines.append(f"- 平均 CI 宽度: {ci_stats['avg_width']}")
            lines.append(f"- 覆盖率（实际 match 是否在 [lo, hi]）: {ci_stats['coverage_rate']}%")
            if ci_stats['narrow_ci_n']:
                lines.append(f"- 窄 CI (<20) accuracy: {ci_stats['narrow_ci_acc']}% (n={ci_stats['narrow_ci_n']})")
            if ci_stats['wide_ci_n']:
                lines.append(f"- 宽 CI (>40) accuracy: {ci_stats['wide_ci_acc']}% (n={ci_stats['wide_ci_n']})")

    report = "\n".join(lines)
    print(report)

    if args.out:
        Path(args.out).write_text(report, encoding="utf-8")
        print(f"\n报告写入: {args.out}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
