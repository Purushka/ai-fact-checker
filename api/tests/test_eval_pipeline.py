"""eval/run_eval.py 和 compare.py 的单元测试。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "eval"))

from run_eval import compute_metrics, normalize_verdict, per_category_breakdown  # noqa: E402


def test_normalize_verdict():
    assert normalize_verdict("contradicted") == "contradicted"
    assert normalize_verdict("refuted") == "contradicted"
    assert normalize_verdict("supported") == "supported"


def test_compute_metrics_all_correct():
    results = [
        {"ground_truth": "supported", "predicted_verdict": "supported", "tokens": 1000, "elapsed": 20},
        {"ground_truth": "contradicted", "predicted_verdict": "contradicted", "tokens": 1500, "elapsed": 25},
    ]
    m = compute_metrics(results)
    assert m["n_total"] == 2
    assert m["n_correct"] == 2
    assert m["accuracy"] == 1.0
    assert m["per_class"]["supported"]["f1"] == 1.0
    assert m["per_class"]["contradicted"]["f1"] == 1.0
    assert m["avg_tokens"] == 1250
    assert m["avg_latency_sec"] == 22.5


def test_compute_metrics_mixed():
    results = [
        {"ground_truth": "supported", "predicted_verdict": "supported"},
        {"ground_truth": "supported", "predicted_verdict": "contradicted"},
        {"ground_truth": "contradicted", "predicted_verdict": "contradicted"},
        {"ground_truth": "contradicted", "predicted_verdict": "unverifiable"},
    ]
    m = compute_metrics(results)
    assert m["accuracy"] == 0.5  # 2/4
    # supported: 1 TP, 1 FN → recall 0.5, prec 1.0
    # contradicted: 1 TP, 1 FN, 1 FP → recall 0.5, prec 0.5
    assert m["per_class"]["supported"]["recall"] == 0.5
    assert m["per_class"]["supported"]["precision"] == 1.0
    assert m["per_class"]["contradicted"]["precision"] == 0.5


def test_compute_metrics_refuted_alias():
    """refuted 和 contradicted 应该等价。"""
    results = [
        {"ground_truth": "refuted", "predicted_verdict": "contradicted"},
        {"ground_truth": "contradicted", "predicted_verdict": "refuted"},
    ]
    m = compute_metrics(results)
    assert m["accuracy"] == 1.0


def test_per_category_breakdown():
    results = [
        {"category": "policy", "ground_truth": "supported", "predicted_verdict": "supported"},
        {"category": "policy", "ground_truth": "contradicted", "predicted_verdict": "supported"},
        {"category": "health", "ground_truth": "supported", "predicted_verdict": "supported"},
    ]
    by_cat = per_category_breakdown(results)
    assert by_cat["policy"]["n"] == 2
    assert by_cat["policy"]["correct"] == 1
    assert by_cat["policy"]["accuracy"] == 0.5
    assert by_cat["health"]["n"] == 1
    assert by_cat["health"]["accuracy"] == 1.0


def test_compute_metrics_empty_safe():
    """空 results 不应崩。"""
    m = compute_metrics([])
    assert m["n_total"] == 0
    assert m["accuracy"] == 0.0


def test_compute_metrics_cost_estimation():
    """成本估算合理：avg_tokens=10000 时单 case 成本应该 ~¥0.09。"""
    results = [{"ground_truth": "supported", "predicted_verdict": "supported", "tokens": 10000, "elapsed": 20}]
    m = compute_metrics(results)
    assert 0.05 <= m["avg_cost_yuan"] <= 0.20  # ¥0.05/M token + ¥0.04 search ≈ ¥0.09


def test_load_benchmark_format(tmp_path):
    """benchmark JSONL 加载格式正确。"""
    p = tmp_path / "test_bench.jsonl"
    p.write_text(
        json.dumps({"id": "T1", "claim": "test claim", "ground_truth": "supported", "category": "policy"})
        + "\n# comment line\n"
        + json.dumps({"id": "T2", "claim": "test 2", "ground_truth": "contradicted", "category": "health"})
        + "\n",
        encoding="utf-8",
    )
    from run_eval import load_bench
    bench = load_bench(p)
    assert len(bench) == 2
    assert bench[0]["id"] == "T1"


@pytest.mark.asyncio
async def test_run_mock_mode():
    """mock 模式不调真 LLM，应 fallback 把 GT 当 predicted（schema 测试）。"""
    from run_eval import run_mock
    bench = [
        {"id": "T1", "claim": "x", "ground_truth": "supported", "category": "policy"},
        {"id": "T2", "claim": "y", "ground_truth": "contradicted", "category": "health"},
    ]
    results = run_mock(bench, None)
    assert len(results) == 2
    assert results[0]["predicted_verdict"] == "supported"
    assert results[1]["predicted_verdict"] == "contradicted"
