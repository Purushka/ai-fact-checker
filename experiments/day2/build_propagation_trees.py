"""2.2 — 构造模仿 Ma et al. ACL 2017 微博传播树的合成数据集。

原数据集需向作者申请。本脚本生成结构同构的合成传播树：
  - 每棵树 = 1 个根 post + N 个 retweet/comment（每个有 parent_id + timestamp + author）
  - 4 种典型形态：
      chain     — 线性深层转发（A → B → C → D …）
      star      — 单一中心（很多人直接转发原 post）
      balanced  — 平衡多层树
      viral    — 爆发式：少数节点产生大量子节点

  每棵树同时给出：
  - 真实拓扑（root + edges + node_timestamps）
  - 真实 BFS 顺序（按层）
  - 真实 cascade 特征（depth/breadth/speed）

  下一脚本 validate_chains.py 测试：
  - 按时间戳排序是否能恢复正确顺序
  - 提取的 cascade 特征是否与 ground truth 一致
"""

from __future__ import annotations

import json
import random
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]


@dataclass
class TreeNode:
    node_id: str
    parent_id: str | None
    timestamp: str  # ISO-8601
    author: str
    text: str
    level: int = 0  # 真实层级，0 = root


@dataclass
class PropagationTree:
    tree_id: str
    pattern: str  # chain | star | balanced | viral
    root_text: str
    nodes: list[TreeNode] = field(default_factory=list)
    # ground truth
    gt_depth: int = 0
    gt_breadth: int = 0
    gt_n_nodes: int = 0
    gt_span_hours: float = 0.0
    gt_peak_per_hour: int = 0


def _ts(base: datetime, minutes: float) -> str:
    return (base + timedelta(minutes=minutes)).isoformat() + "Z"


def gen_chain(rng: random.Random, depth: int = 8) -> PropagationTree:
    """A → B → C → ... 线性深层转发链。"""
    t0 = datetime(2024, 8, 15, 10, 0)
    nodes = [TreeNode("n0", None, _ts(t0, 0), "user_0", "原始爆料", level=0)]
    for i in range(1, depth):
        delay = rng.uniform(30, 180)  # 30 ~ 180 分钟一个跳跃
        ts_base = datetime.fromisoformat(nodes[-1].timestamp.rstrip("Z"))
        nodes.append(
            TreeNode(
                f"n{i}",
                f"n{i-1}",
                _ts(ts_base, delay),
                f"user_{i}",
                f"转发 (level {i})",
                level=i,
            )
        )
    span_h = (datetime.fromisoformat(nodes[-1].timestamp.rstrip("Z")) - t0).total_seconds() / 3600
    return PropagationTree(
        "chain_01", "chain", "原始爆料", nodes,
        gt_depth=depth - 1, gt_breadth=1, gt_n_nodes=depth, gt_span_hours=span_h, gt_peak_per_hour=1,
    )


def gen_star(rng: random.Random, n_replies: int = 20) -> PropagationTree:
    """很多人在短时间内直接转发原 post。"""
    t0 = datetime(2024, 8, 15, 10, 0)
    nodes = [TreeNode("n0", None, _ts(t0, 0), "user_0", "原始爆料", level=0)]
    for i in range(1, n_replies + 1):
        delay = rng.uniform(1, 120)  # 2 小时内
        nodes.append(
            TreeNode(f"n{i}", "n0", _ts(t0, delay), f"user_{i}", f"转发原贴", level=1)
        )
    span_h = max(float(n.timestamp[14:16]) for n in nodes[1:]) / 60
    return PropagationTree(
        "star_01", "star", "原始爆料", nodes,
        gt_depth=1, gt_breadth=n_replies, gt_n_nodes=n_replies + 1,
        gt_span_hours=2.0, gt_peak_per_hour=n_replies,
    )


def gen_balanced(rng: random.Random, branching: int = 3, depth: int = 4) -> PropagationTree:
    """平衡 k-叉树。"""
    t0 = datetime(2024, 8, 15, 10, 0)
    nodes: list[TreeNode] = [TreeNode("n0", None, _ts(t0, 0), "user_0", "原始爆料", level=0)]
    current_level = [("n0", 0)]
    cnt = 1
    for d in range(1, depth + 1):
        next_level: list[tuple[str, float]] = []
        for parent_id, parent_t in current_level:
            for _ in range(branching):
                delay = rng.uniform(15, 90)
                t = parent_t + delay
                nodes.append(
                    TreeNode(f"n{cnt}", parent_id, _ts(t0, t), f"user_{cnt}", f"转发 (d={d})", level=d)
                )
                next_level.append((f"n{cnt}", t))
                cnt += 1
        current_level = next_level

    max_breadth = max(branching**i for i in range(depth + 1))
    span_h = (datetime.fromisoformat(nodes[-1].timestamp.rstrip("Z")) - t0).total_seconds() / 3600
    return PropagationTree(
        "balanced_01", "balanced", "原始爆料", nodes,
        gt_depth=depth, gt_breadth=max_breadth, gt_n_nodes=cnt, gt_span_hours=span_h,
        gt_peak_per_hour=max_breadth,
    )


def gen_viral(rng: random.Random) -> PropagationTree:
    """爆发式：少数节点产生大量子节点。"""
    t0 = datetime(2024, 8, 15, 10, 0)
    nodes: list[TreeNode] = [TreeNode("n0", None, _ts(t0, 0), "user_0", "原始爆料", level=0)]
    # 5 个一级转发
    cnt = 1
    level1: list[tuple[str, float]] = []
    for i in range(5):
        delay = rng.uniform(5, 30)
        nodes.append(TreeNode(f"n{cnt}", "n0", _ts(t0, delay), f"user_{cnt}", "转发", level=1))
        level1.append((f"n{cnt}", delay))
        cnt += 1
    # 选一个"病毒源"，让它有 30 个子转发
    viral_id, viral_t = level1[2]
    for i in range(30):
        delay = viral_t + rng.uniform(20, 200)
        nodes.append(
            TreeNode(f"n{cnt}", viral_id, _ts(t0, delay), f"user_{cnt}", "病毒转发", level=2)
        )
        cnt += 1
    # 其他一级转发只产生 1-2 个子
    for parent, pt in level1:
        if parent == viral_id:
            continue
        for _ in range(rng.randint(1, 2)):
            delay = pt + rng.uniform(30, 240)
            nodes.append(TreeNode(f"n{cnt}", parent, _ts(t0, delay), f"user_{cnt}", "转发", level=2))
            cnt += 1

    span_h = (datetime.fromisoformat(nodes[-1].timestamp.rstrip("Z")) - t0).total_seconds() / 3600
    return PropagationTree(
        "viral_01", "viral", "原始爆料", nodes,
        gt_depth=2, gt_breadth=30, gt_n_nodes=cnt, gt_span_hours=span_h, gt_peak_per_hour=30,
    )


def main() -> None:
    rng = random.Random(42)
    trees: list[PropagationTree] = []

    # 多样化：每种 pattern 生成 5 棵
    for i in range(5):
        rng2 = random.Random(42 + i)
        t = gen_chain(rng2, depth=6 + i)
        t.tree_id = f"chain_{i+1:02}"
        trees.append(t)

    for i in range(5):
        rng2 = random.Random(100 + i)
        t = gen_star(rng2, n_replies=10 + i * 5)
        t.tree_id = f"star_{i+1:02}"
        trees.append(t)

    for i in range(5):
        rng2 = random.Random(200 + i)
        t = gen_balanced(rng2, branching=2 + i % 2, depth=3 + i % 2)
        t.tree_id = f"balanced_{i+1:02}"
        trees.append(t)

    for i in range(5):
        rng2 = random.Random(300 + i)
        t = gen_viral(rng2)
        t.tree_id = f"viral_{i+1:02}"
        trees.append(t)

    print(f"Generated {len(trees)} propagation trees:")
    for t in trees:
        print(
            f"  {t.tree_id:<15} pattern={t.pattern:<10} "
            f"n_nodes={t.gt_n_nodes:<4} depth={t.gt_depth:<3} breadth={t.gt_breadth:<3} "
            f"span={t.gt_span_hours:.1f}h"
        )

    out_path = Path(__file__).parent / "propagation_trees.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for t in trees:
            d = asdict(t)
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    print(f"\n→ {out_path}")


if __name__ == "__main__":
    main()
