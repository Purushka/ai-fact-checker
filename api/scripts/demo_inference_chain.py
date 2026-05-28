"""演示 — 用真实 bge-large-zh 模型跑一次 InferenceChain。

首次跑会加载 ~1.3GB 权重（已 cached），约 5-10s。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

from factcheck.extract.inference_chain import InferenceChainBuilder


# 模拟一个典型辟谣链路
EVIDENCES = [
    {
        "source_url": "https://weibo.com/u/1234/post/abc",
        "source_name": "@某网友爆料",
        "source_type": "social_media",
        "authority_weight": 0.15,
        "published_at": "2025-08-10",
        "snippet": "刚在小区门口看到草坪被喷了绿色油漆，怀疑是应付检查",
    },
    {
        "source_url": "https://baijiahao.baidu.com/s?id=9876543210",
        "source_name": "百家号·某情感号",
        "source_type": "content_farm",
        "authority_weight": 0.25,
        "published_at": "2025-08-11",
        "snippet": "震惊！广州街头草皮被人工染绿应付检查，市民拍下惊人画面",
    },
    {
        "source_url": "https://mp.weixin.qq.com/s/abcdef",
        "source_name": "微信公众号·吃瓜日报",
        "source_type": "content_farm",
        "authority_weight": 0.25,
        "published_at": "2025-08-12",
        "snippet": "全网热议的广州草皮染色事件，真相到底如何？官方回应来了",
    },
    {
        "source_url": "https://www.piyao.org.cn/20250815/12345abc/c.html",
        "source_name": "中国互联网联合辟谣平台",
        "source_type": "official",
        "authority_weight": 0.95,
        "published_at": "2025-08-15",
        "snippet": "经核实，所谓'草皮被染绿应付检查'实为正常的草坪养护喷洒着色剂，是园林惯例非造假",
    },
]


def main() -> None:
    print("=" * 78)
    print("  InferenceChain 演示 — 用真实 bge-large-zh-v1.5 跑一次")
    print("=" * 78)

    builder = InferenceChainBuilder()
    chain = builder.build(EVIDENCES)
    if chain is None:
        print("无 chain（数据不足）")
        return

    print(f"\n[模式]     {chain.pattern}")
    print(f"[节点数]   {chain.chain_length}")
    print(f"[时间跨度] {chain.timeline_hours} 小时")
    print(f"[平均相似度] {chain.avg_similarity}")
    print(f"[备注]     {chain.note}")
    print(f"[模型]     {chain.embedding_model}")

    print(f"\n{'date':<12} {'role':<11} {'category':<24} {'sim→':<6} source")
    print(f"{'-' * 12} {'-' * 11} {'-' * 24} {'-' * 6} {'-' * 40}")
    for n in chain.nodes:
        sim = f"{n.similarity_to_source:.3f}" if n.similarity_to_source else "  -"
        src = (n.source_name or "")[:38]
        print(f"{n.published_at:<12} {n.role:<11} {n.source_category:<24} {sim:<6} {src}")

    print("\n[详细相似度矩阵 — 每节点 vs 前置节点]")
    for i, n in enumerate(chain.nodes):
        if not n.all_similarities:
            continue
        print(f"  {n.source_name}:")
        for s in n.all_similarities:
            print(f"    sim={s['similarity']:.3f} ← {s['from']}")

    print("\n[关键观察]")
    last = chain.nodes[-1]
    if last.role == "debunker":
        # 实证发现：bge-large-zh 对"共享主题词"很敏感，
        # 辟谣节点 vs 谣言 origin 仍能保持较高 cosine（~0.6-0.8）
        sim_to_origin = next(
            (s["similarity"] for s in last.all_similarities if s["from"] == chain.origin.source_url),
            None,
        )
        print(f"  最末节点 {last.source_name}  role=debunker")
        print(f"  与 origin 相似度 = {sim_to_origin}")
        print(f"  ⚠ 注意：实测 bge-large-zh 即使是辟谣节点，与谣言 origin 的 cos sim 仍较高")
        print(f"    （因为共享主题词），辟谣信号靠 source_category=fact_check_platform 识别，")
        print(f"    不能依赖低相似度。这驳斥了 InferenceChain 设计假设中的一条。")


if __name__ == "__main__":
    main()
