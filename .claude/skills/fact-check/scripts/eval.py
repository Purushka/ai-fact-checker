"""测试集 runner：读 test_cases.jsonl，逐条调用 Skill 输出，与 ground truth 对比。

用法：
    python eval.py --cases ../tests/test_cases.jsonl --out ../reports/run_{timestamp}.json

但实际「跑 Skill」需要 Claude（运行环境）来执行。本脚本生成模板报告框架，
Claude 在 skill 调用中填入每个 case 的 actual 结果，最后用 compare.py 汇总。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cases.append(json.loads(line))
    return cases


def init_report(cases: list[dict]) -> dict:
    return {
        "run_id": datetime.now().strftime("run_%Y%m%d_%H%M%S"),
        "created_at": datetime.now().isoformat(),
        "total_cases": len(cases),
        "cases": [
            {
                "case_id": c.get("id"),
                "claim": c.get("claim"),
                "mode": c.get("mode", "policy"),
                "expected": {
                    "verdict": c.get("expected_verdict"),
                    "policy_status": c.get("expected_policy_status"),
                    "has_official_source": c.get("expected_has_official_source"),
                },
                "ground_truth_note": c.get("ground_truth_note"),
                "category": c.get("category"),
                "skill_actual": None,
                "claude_raw_actual": None,
                "matched": None,
                "claude_raw_matched": None,
                "comments": None,
            }
            for c in cases
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    cases = load_cases(Path(args.cases))
    report = init_report(cases)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已初始化报告: {out_path}（{len(cases)} 个 case）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
