"""2.2 — 在合成传播树上验证链路构建 + 级联特征提取。

测试目标：
  1. **时间戳排序准确率**：仅给 (node_id, timestamp) 列表（去掉 parent 边），
     按时间排序，看是否能恢复正确的层级顺序（level 0 → 1 → 2 → …）
     measure: Kendall's tau 排序相关系数；level 1 节点全部排在 level 2 之前的比例

  2. **级联特征提取**：从 parent_id 边重建树，提取：
     - depth：最长 root→leaf 路径长度
     - breadth：任一层最大节点数
     - speed：第一小时内 + 峰值小时内的节点数
     输出每棵树的预测值 vs ground truth

  3. **若只有时间戳无 parent_id**：测试时间窗口启发式（30 分钟内的 retweet 默认指向最近 root）
     的能力上限。
"""

from __future__ import annotations

import json
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.rstrip("Z"))


def kendall_tau(a: list[int], b: list[int]) -> float:
    """简化 Kendall's tau — 计算 a 和 b 的成对一致度。"""
    if len(a) != len(b) or len(a) < 2:
        return 0.0
    concordant = 0
    discordant = 0
    n = len(a)
    for i in range(n):
        for j in range(i + 1, n):
            sign_a = (a[i] - a[j]) * (b[i] - b[j])
            if sign_a > 0:
                concordant += 1
            elif sign_a < 0:
                discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else 0.0


def test_timestamp_ordering(tree: dict) -> dict:
    """给定 nodes 按时间戳排序，检查 level 是否单调非降。"""
    nodes = tree["nodes"]
    nodes_sorted = sorted(nodes, key=lambda n: _parse_ts(n["timestamp"]))
    levels_in_order = [n["level"] for n in nodes_sorted]
    # 完美：levels 单调非降
    n_violations = sum(1 for i in range(1, len(levels_in_order)) if levels_in_order[i] < levels_in_order[i - 1])
    perfect = n_violations == 0
    # Kendall's tau between sorted-by-time levels and sorted-ascending levels
    tau = kendall_tau(levels_in_order, sorted(levels_in_order))
    return {
        "n_nodes": len(nodes),
        "n_violations": n_violations,
        "monotone": perfect,
        "kendall_tau": tau,
    }


def extract_features(tree: dict) -> dict:
    """从 parent_id 边重建树，提取 depth/breadth/speed。"""
    nodes = tree["nodes"]
    nodes_by_id = {n["node_id"]: n for n in nodes}
    children = defaultdict(list)
    root_id: str | None = None
    for n in nodes:
        if n["parent_id"] is None:
            root_id = n["node_id"]
        else:
            children[n["parent_id"]].append(n["node_id"])

    if root_id is None:
        return {"depth": 0, "breadth": 0, "n_nodes": len(nodes), "first_hour_n": 0, "peak_hour_n": 0}

    # BFS 算 depth & breadth
    from collections import deque

    level_counts: dict[int, int] = {}
    q = deque([(root_id, 0)])
    max_depth = 0
    while q:
        nid, lvl = q.popleft()
        level_counts[lvl] = level_counts.get(lvl, 0) + 1
        max_depth = max(max_depth, lvl)
        for c in children.get(nid, []):
            q.append((c, lvl + 1))

    max_breadth = max(level_counts.values())

    # speed：先按时间戳排序，统计第一小时内 + 1 小时滚动窗口峰值
    ts_sorted = sorted(_parse_ts(n["timestamp"]) for n in nodes)
    t0 = ts_sorted[0]
    first_hour_n = sum(1 for t in ts_sorted if (t - t0).total_seconds() <= 3600)
    # 峰值：任一 1 小时窗口
    peak = 0
    for i in range(len(ts_sorted)):
        # count j s.t. ts_sorted[j] - ts_sorted[i] <= 3600
        cnt = 0
        for j in range(i, len(ts_sorted)):
            if (ts_sorted[j] - ts_sorted[i]).total_seconds() <= 3600:
                cnt += 1
            else:
                break
        peak = max(peak, cnt)

    return {
        "depth": max_depth,
        "breadth": max_breadth,
        "n_nodes": len(nodes),
        "first_hour_n": first_hour_n,
        "peak_hour_n": peak,
    }


def main() -> None:
    path = Path(__file__).parent / "propagation_trees.jsonl"
    with open(path, encoding="utf-8") as f:
        trees = [json.loads(line) for line in f]

    print("=" * 92)
    print("  Test 1: 时间戳排序能否恢复层级顺序？")
    print("=" * 92)
    print(f"  {'tree_id':<14} {'pattern':<10} {'n':<4} {'violations':<12} {'monotone':<10} {'tau':<6}")
    tau_by_pattern: dict[str, list[float]] = defaultdict(list)
    monotone_by_pattern: dict[str, list[bool]] = defaultdict(list)
    for t in trees:
        r = test_timestamp_ordering(t)
        tau_by_pattern[t["pattern"]].append(r["kendall_tau"])
        monotone_by_pattern[t["pattern"]].append(r["monotone"])
        print(f"  {t['tree_id']:<14} {t['pattern']:<10} {r['n_nodes']:<4} {r['n_violations']:<12} {str(r['monotone']):<10} {r['kendall_tau']:.3f}")

    print()
    print(f"  {'pattern':<10} {'mean_tau':<10} {'monotone_rate':<14}")
    for pat in ["chain", "star", "balanced", "viral"]:
        taus = tau_by_pattern[pat]
        mons = monotone_by_pattern[pat]
        print(f"  {pat:<10} {statistics.mean(taus):.3f}     {sum(mons)/len(mons):.0%}")

    print("\n" + "=" * 92)
    print("  Test 2: 级联特征提取 (depth/breadth/speed) — pred vs ground_truth")
    print("=" * 92)
    print(f"  {'tree_id':<14} {'pattern':<10} {'depth':<14} {'breadth':<14} {'speed':<22}")
    err_depth: list[int] = []
    err_breadth: list[int] = []
    for t in trees:
        f = extract_features(t)
        d_err = abs(f["depth"] - t["gt_depth"])
        b_err = abs(f["breadth"] - t["gt_breadth"])
        err_depth.append(d_err)
        err_breadth.append(b_err)
        print(
            f"  {t['tree_id']:<14} {t['pattern']:<10} "
            f"{str(f['depth']) + '/' + str(t['gt_depth']):<14} "
            f"{str(f['breadth']) + '/' + str(t['gt_breadth']):<14} "
            f"first_h={f['first_hour_n']}  peak_h={f['peak_hour_n']}"
        )
    print()
    print(f"  mean abs depth err   = {statistics.mean(err_depth):.2f}")
    print(f"  mean abs breadth err = {statistics.mean(err_breadth):.2f}")
    print(f"  perfect depth        = {sum(1 for e in err_depth if e == 0)}/{len(err_depth)}")
    print(f"  perfect breadth      = {sum(1 for e in err_breadth if e == 0)}/{len(err_breadth)}")

    # 输出 JSON
    output = {
        "test_1_timestamp_ordering": {
            pat: {
                "mean_kendall_tau": round(statistics.mean(tau_by_pattern[pat]), 3),
                "monotone_rate": round(sum(monotone_by_pattern[pat]) / len(monotone_by_pattern[pat]), 3),
            }
            for pat in ["chain", "star", "balanced", "viral"]
        },
        "test_2_feature_extraction": {
            "mean_abs_depth_err": round(statistics.mean(err_depth), 3),
            "mean_abs_breadth_err": round(statistics.mean(err_breadth), 3),
            "perfect_depth_rate": round(sum(1 for e in err_depth if e == 0) / len(err_depth), 3),
            "perfect_breadth_rate": round(sum(1 for e in err_breadth if e == 0) / len(err_breadth), 3),
        },
    }
    out_path = Path(__file__).parent / "chain_validation_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nresults → {out_path}")


if __name__ == "__main__":
    main()
