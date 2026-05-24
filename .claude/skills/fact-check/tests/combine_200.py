"""把 v4 base (50) + v5 generated (89) + v5 extra (84) 合并为 200 case 标准测试集。

策略：50 base 全部保留 + 150 从 173 generated 中按 verdict 分层抽样
"""
import json
import random
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent


def load(p):
    out = []
    with (ROOT / p).open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(json.loads(line))
    return out


base = load("test_cases_v4_balanced.jsonl")
gen = load("test_cases_v5_generated.jsonl") + load("test_cases_v5_extra.jsonl")

# 给 base case 补 ground_truth_url（空，因为是手工 case）
for c in base:
    c.setdefault("ground_truth_url", "")
    c.setdefault("ground_truth_evidence", "")
    c.setdefault("topic", c.get("category", "manual"))
    c.setdefault("source_authority", None)

# Sample 150 from generated, stratified by verdict
rng = random.Random(42)
by_v = {}
for c in gen:
    by_v.setdefault(c["ground_truth"], []).append(c)

target_total = 150
target_per_v = {
    "supported": int(target_total * 0.30),
    "contradicted": int(target_total * 0.30),
    "outdated": int(target_total * 0.15),
    "partially_supported": int(target_total * 0.25),
}
sampled = []
for v, n in target_per_v.items():
    pool = by_v.get(v, [])
    take = min(n, len(pool))
    sampled.extend(rng.sample(pool, take))

# 合并
all_cases = base + sampled
rng.shuffle(all_cases)  # 防止 order bias

print(f"Base: {len(base)}, Sampled from gen: {len(sampled)}, Total: {len(all_cases)}", file=sys.stderr)
by_v_final = {}
for c in all_cases:
    by_v_final[c["ground_truth"]] = by_v_final.get(c["ground_truth"], 0) + 1
print(f"Final verdict 分布: {by_v_final}", file=sys.stderr)

out_path = ROOT / "test_cases_v6_combined.jsonl"
with out_path.open("w", encoding="utf-8") as f:
    for c in all_cases:
        f.write(json.dumps(c, ensure_ascii=False) + "\n")
print(f"写入: {out_path}", file=sys.stderr)
