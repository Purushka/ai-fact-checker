"""SimHash benchmark only (no model download required)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

sys.path.insert(0, str(Path(__file__).parent))
from benchmark_similarity import benchmark_simhash


def main() -> None:
    pairs_path = Path(__file__).parent / "pairs.jsonl"
    with open(pairs_path, encoding="utf-8") as f:
        pairs = [json.loads(line) for line in f]
    print(f"[loaded {len(pairs)} pairs, positives={sum(p['label'] for p in pairs)}, "
          f"negatives={sum(1 for p in pairs if p['label']==0)}]")

    all_results = []
    all_results += benchmark_simhash(pairs, normalize=False)
    all_results += benchmark_simhash(pairs, normalize=True)

    out_path = Path(__file__).parent / "simhash_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nresults → {out_path}")


if __name__ == "__main__":
    main()
