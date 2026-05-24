"""把 piyao_cases.jsonl + manual_cases.jsonl 合并为 benchmark_cn.jsonl。"""

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

HERE = Path(__file__).resolve().parent


def load(name: str) -> list[dict]:
    p = HERE / name
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out


def main() -> int:
    piyao = load("piyao_cases.jsonl")
    manual = load("manual_cases.jsonl")
    all_cases = piyao + manual
    out = HERE / "benchmark_cn.jsonl"
    with out.open("w", encoding="utf-8") as f:
        for c in all_cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    by_gt: dict[str, int] = {}
    by_cat: dict[str, int] = {}
    by_src: dict[str, int] = {}
    for c in all_cases:
        by_gt[c["ground_truth"]] = by_gt.get(c["ground_truth"], 0) + 1
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
        by_src[c.get("source", "manual")] = by_src.get(c.get("source", "manual"), 0) + 1
    print(f"合计: {len(all_cases)} cases → {out.name}")
    print(f"  ground_truth: {by_gt}")
    print(f"  category:     {by_cat}")
    print(f"  source:       {by_src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
