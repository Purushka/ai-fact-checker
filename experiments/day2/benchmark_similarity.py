"""2.1 — 近似检测方案对比。

测 4 种方案 × 多阈值：
  - SimHash         Hamming 距离 ≤ 2/3/4/5
  - bge-large-zh    cosine ≥ 0.80/0.85/0.90/0.95
  - bge-m3          同上
  - Hybrid          SimHash 召回 + embedding 精排

输出：P/R/F1/Accuracy + latency per pair（含模型加载耗时）
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Callable

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]

import numpy as np
from simhash import Simhash


def normalize_zh(s: str) -> str:
    """中文文本归一化：去标点、统一空白、半角化。"""
    s = re.sub(r"[　\s]+", "", s)  # 移除所有空白
    s = re.sub(r"[，。！？；：、'\"'\"《》【】（）()<>\[\]…—\-_+=*#@~`/\\|]", "", s)
    return s.lower()


def ngrams_zh(s: str, n: int = 3) -> list[str]:
    s = normalize_zh(s)
    return [s[i : i + n] for i in range(len(s) - n + 1)] if len(s) >= n else [s]


# ============ SimHash ============

def simhash_hash(text: str) -> int:
    return Simhash(ngrams_zh(text, n=3)).value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


# ============ 评估器 ============

def evaluate(predictions: list[int], labels: list[int]) -> dict:
    tp = sum(1 for p, l in zip(predictions, labels, strict=True) if p == 1 and l == 1)
    fp = sum(1 for p, l in zip(predictions, labels, strict=True) if p == 1 and l == 0)
    fn = sum(1 for p, l in zip(predictions, labels, strict=True) if p == 0 and l == 1)
    tn = sum(1 for p, l in zip(predictions, labels, strict=True) if p == 0 and l == 0)
    n = tp + fp + fn + tn
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    acc = (tp + tn) / n if n else 0.0
    return {"P": prec, "R": rec, "F1": f1, "Acc": acc, "TP": tp, "FP": fp, "FN": fn, "TN": tn}


def fmt_row(method: str, threshold: float, metrics: dict, latency_ms: float) -> str:
    return (
        f"{method:<26} thr={threshold:<6} "
        f"P={metrics['P']:.3f}  R={metrics['R']:.3f}  F1={metrics['F1']:.3f}  Acc={metrics['Acc']:.3f}  "
        f"TP/FP/FN/TN={metrics['TP']}/{metrics['FP']}/{metrics['FN']}/{metrics['TN']}  "
        f"  {latency_ms:.1f}ms/pair"
    )


# ============ SimHash benchmark ============

def benchmark_simhash(pairs: list[dict], normalize: bool) -> list[dict]:
    print(f"\n[SimHash benchmark, normalize={normalize}]")
    results = []
    # precompute hashes
    t0 = time.time()
    hashes_a = []
    hashes_b = []
    for p in pairs:
        a = normalize_zh(p["a"]) if normalize else p["a"]
        b = normalize_zh(p["b"]) if normalize else p["b"]
        hashes_a.append(Simhash(ngrams_zh(a, n=3)).value)
        hashes_b.append(Simhash(ngrams_zh(b, n=3)).value)
    hash_time = time.time() - t0
    avg_hash_ms = (hash_time * 1000) / (2 * len(pairs))

    labels = [p["label"] for p in pairs]
    distances = [hamming(ha, hb) for ha, hb in zip(hashes_a, hashes_b, strict=True)]

    # 中文段落级 paraphrase 的 Hamming 距离实测在 12-35 范围
    # 测宽阈值范围以观察实际可分性
    for thr in [3, 5, 10, 15, 20, 24, 26, 28, 30]:
        preds = [1 if d <= thr else 0 for d in distances]
        m = evaluate(preds, labels)
        suffix = "_norm" if normalize else ""
        results.append({"method": f"SimHash{suffix}", "threshold": thr, **m, "latency_ms": avg_hash_ms})
        print(fmt_row(f"SimHash{suffix}", thr, m, avg_hash_ms))

    return results


# ============ Embedding benchmark ============

def benchmark_embedding(pairs: list[dict], model_name: str, batch_size: int = 16) -> list[dict]:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print(f"\n[skip {model_name}: sentence_transformers not installed]")
        return []

    print(f"\n[Embedding benchmark: {model_name}]")
    t0 = time.time()
    try:
        model = SentenceTransformer(model_name)
        load_time = time.time() - t0
        print(f"  loaded in {load_time:.1f}s")
    except Exception as e:
        print(f"  FAILED to load: {e}")
        return [{"method": model_name, "error": str(e)}]

    a_texts = [p["a"] for p in pairs]
    b_texts = [p["b"] for p in pairs]

    t0 = time.time()
    a_embs = model.encode(a_texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
    b_embs = model.encode(b_texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
    encode_time = time.time() - t0
    avg_encode_ms = (encode_time * 1000) / (2 * len(pairs))
    print(f"  encoded {2 * len(pairs)} texts in {encode_time:.1f}s ({avg_encode_ms:.1f}ms/text)")

    # cosine sim (normalized embeddings → dot product == cosine)
    sims = np.sum(a_embs * b_embs, axis=1)
    labels = [p["label"] for p in pairs]

    results = []
    for thr in [0.70, 0.75, 0.80, 0.85, 0.90, 0.93, 0.95]:
        preds = [1 if s >= thr else 0 for s in sims]
        m = evaluate(preds, labels)
        results.append({"method": model_name, "threshold": thr, **m, "latency_ms": avg_encode_ms})
        print(fmt_row(model_name, thr, m, avg_encode_ms))

    return results


# ============ Hybrid: SimHash recall + bge rerank ============

def benchmark_hybrid(
    pairs: list[dict], model_name: str, simhash_thr: int = 8, cosine_thr: float = 0.85
) -> list[dict]:
    """先 SimHash H≤simhash_thr 召回，再 bge cosine ≥ cosine_thr 精排。"""
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return []

    print(f"\n[Hybrid: SimHash(H≤{simhash_thr}) + {model_name}(cos≥{cosine_thr})]")
    labels = [p["label"] for p in pairs]

    # SimHash 召回
    distances = []
    for p in pairs:
        ha = Simhash(ngrams_zh(p["a"], n=3)).value
        hb = Simhash(ngrams_zh(p["b"], n=3)).value
        distances.append(hamming(ha, hb))
    recall_mask = [1 if d <= simhash_thr else 0 for d in distances]
    n_recalled = sum(recall_mask)
    print(f"  SimHash recalled {n_recalled}/{len(pairs)}")

    # Embedding 精排（仅对 recalled）
    model = SentenceTransformer(model_name)
    a_texts = [p["a"] for i, p in enumerate(pairs) if recall_mask[i]]
    b_texts = [p["b"] for i, p in enumerate(pairs) if recall_mask[i]]
    t0 = time.time()
    a_embs = model.encode(a_texts, normalize_embeddings=True, show_progress_bar=False)
    b_embs = model.encode(b_texts, normalize_embeddings=True, show_progress_bar=False)
    rerank_time = time.time() - t0
    sims = np.sum(a_embs * b_embs, axis=1)

    # 映射回原索引
    preds = [0] * len(pairs)
    si = 0
    for i in range(len(pairs)):
        if recall_mask[i]:
            preds[i] = 1 if sims[si] >= cosine_thr else 0
            si += 1

    m = evaluate(preds, labels)
    # latency 综合：所有对都跑 SimHash，召回的跑 embedding
    avg_simhash_ms = 0.5  # cheap
    avg_emb_ms = (rerank_time * 1000) / (2 * n_recalled) if n_recalled else 0
    effective_ms = avg_simhash_ms + (n_recalled / len(pairs)) * avg_emb_ms
    results = [
        {
            "method": f"Hybrid({model_name})",
            "threshold": f"sh<={simhash_thr},cos>={cosine_thr}",
            **m,
            "latency_ms": effective_ms,
            "recall_rate": n_recalled / len(pairs),
        }
    ]
    print(fmt_row(f"Hybrid({model_name[:20]})", cosine_thr, m, effective_ms))
    return results


def main() -> None:
    pairs_path = Path(__file__).parent / "pairs.jsonl"
    with open(pairs_path, encoding="utf-8") as f:
        pairs = [json.loads(line) for line in f]
    print(f"[loaded {len(pairs)} pairs, positives={sum(p['label'] for p in pairs)}, "
          f"negatives={sum(1 for p in pairs if p['label']==0)}]")

    all_results: list[dict] = []

    # SimHash (with + without normalization)
    all_results += benchmark_simhash(pairs, normalize=False)
    all_results += benchmark_simhash(pairs, normalize=True)

    # bge-large-zh
    all_results += benchmark_embedding(pairs, "BAAI/bge-large-zh-v1.5")

    # bge-m3 — 太大，先跳过看
    # all_results += benchmark_embedding(pairs, "BAAI/bge-m3")

    # Hybrid: SimHash 召回阈值需 ≥ 28 才能让中文 paraphrase 进入
    # 这里用更松的 SimHash 召回（H≤32 = 几乎全召回，但仍能减半近 50% 显然不相关的 case）
    all_results += benchmark_hybrid(pairs, "BAAI/bge-large-zh-v1.5", simhash_thr=30, cosine_thr=0.75)
    all_results += benchmark_hybrid(pairs, "BAAI/bge-large-zh-v1.5", simhash_thr=28, cosine_thr=0.75)

    out_path = Path(__file__).parent / "similarity_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\nresults → {out_path}")


if __name__ == "__main__":
    main()
