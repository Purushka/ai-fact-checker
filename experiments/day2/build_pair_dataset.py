"""2.1 — 构造近似检测 benchmark 数据集。

MCFEND 学术数据集需申请获取。本脚本用已有的 piyao_cases.jsonl 构造等价的
真实分布数据：

  正例（50 对）：piyao.claim ↔ LLM 生成的同义改写（保留事实，换措辞/语序/词汇）
  负例（50 对）：piyao.claim ↔ 另一 case 的 claim（同语料库不同事件）

LLM 生成改写有 3 个挑战目标：
  - 改写 1：替换同义词，保留结构
  - 改写 2：调整语序 + 改主语
  - 改写 3：扩写/缩写，保留事实但换长度

输出：experiments/day2/pairs.jsonl，每行 {a, b, label}
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / "api" / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "api" / "src"))

from factcheck.llm import LLMMessage, get_provider

PARAPHRASE_SYSTEM = """你是中文改写助手。给定一个陈述句，输出该句的同义改写。
要求：
- 保留原始事实和判断
- 换措辞（同义词替换）
- 可调整语序、主语、否定式
- 不增加新事实，不删除关键信息
- 长度在原句 ±30% 内
- 仅输出改写句，不要解释、不要前缀
"""


async def paraphrase(provider, text: str, variant: int) -> str:
    """variant 1: 同义词替换  2: 语序调整  3: 长度变化"""
    if variant == 1:
        user = f"对下句做同义词替换改写，保留全部事实：\n{text}"
    elif variant == 2:
        user = f"对下句做语序/主语调整，保留全部事实：\n{text}"
    else:
        user = f"对下句做扩写或缩写，保留全部事实但改变长度：\n{text}"
    resp = await provider.chat(
        [LLMMessage(role="system", content=PARAPHRASE_SYSTEM), LLMMessage(role="user", content=user)],
        temperature=0.5,
        max_tokens=200,
        timeout=15.0,
    )
    out = resp.text.strip().strip('"').strip('"').strip('"')
    # 去前缀
    for prefix in ("改写：", "改写:", "改写后：", "改写后:"):
        if out.startswith(prefix):
            out = out[len(prefix):].strip()
    return out


async def main() -> None:
    src = Path(__file__).resolve().parents[2] / "eval" / "data" / "piyao_cases.jsonl"
    with open(src, encoding="utf-8") as f:
        cases = [json.loads(line) for line in f]

    if len(cases) < 25:
        print(f"WARN: only {len(cases)} cases available")
    cases = cases[:25]

    provider = get_provider()
    print(f"[provider] {provider.name}")
    print(f"[cases] {len(cases)}")

    # 正例：每 case 生成 2 个 paraphrase → 50 个 positive pair
    print("\n[generating paraphrases for positive pairs]")
    positives: list[dict] = []
    for i, c in enumerate(cases):
        claim = c["claim"]
        tasks = [paraphrase(provider, claim, v) for v in (1, 2)]
        try:
            paras = await asyncio.gather(*tasks)
        except Exception as e:
            print(f"  {c['id']}: paraphrase failed: {e}")
            continue
        for j, p in enumerate(paras):
            positives.append({"a": claim, "b": p, "label": 1, "src_case": c["id"], "variant": j + 1})
        print(f"  {c['id']} ({i+1}/{len(cases)}): claim={claim[:30]}...")
        print(f"    para1: {paras[0][:40]}...")
        print(f"    para2: {paras[1][:40]}...")

    # 负例：跨 case 随机配对 → 50 个 negative
    print("\n[building negative pairs]")
    rng = random.Random(42)
    negatives: list[dict] = []
    n_neg = len(positives)  # 平衡
    seen_pairs: set[tuple[str, str]] = set()
    while len(negatives) < n_neg:
        a, b = rng.sample(cases, 2)
        key = (a["id"], b["id"])
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        negatives.append({"a": a["claim"], "b": b["claim"], "label": 0, "src_case_a": a["id"], "src_case_b": b["id"]})

    out_path = Path(__file__).parent / "pairs.jsonl"
    with open(out_path, "w", encoding="utf-8") as f:
        for r in positives + negatives:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n[done] positives={len(positives)}, negatives={len(negatives)}, total={len(positives)+len(negatives)}")
    print(f"[output] {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
