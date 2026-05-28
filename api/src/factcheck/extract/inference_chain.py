"""InferenceChain — 用 bge-large-zh embedding 推演 evidence 的传播链。

与 PropagationTimeline 互补：
  - PropagationTimeline 用 URL 域名分类启发式判 pattern（cheap，无模型）
  - InferenceChain 用 sentence-transformers 计算 evidence snippet 的 embedding，
    每个节点 vs 前置节点的 cosine 相似度，定位最可能的"信息来源"

最后一个节点（如 piyao 辟谣）和 origin 的相似度低（如 0.45），role=debunker，
这种"低相似度 + 时间靠后 + 权威源"的组合本身就是辟谣信号。

模型懒加载（首次调用时载入 ~1.3 GB 权重），无 evidence 或全无日期时返回 None。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import numpy as np

from ..schemas import ChainNode, InferenceChain
from .timeline_builder import _domain_to_category

logger = logging.getLogger("factcheck.inference_chain")


class InferenceChainBuilder:
    """单例式 builder — 模型在首次 build() 调用时懒加载并复用。"""

    _model = None  # class-level cache

    @classmethod
    def _get_model(cls):
        if cls._model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("loading bge-large-zh-v1.5 (lazy, first call only)")
            cls._model = SentenceTransformer("BAAI/bge-large-zh-v1.5")
        return cls._model

    def build(self, evidences: list[dict[str, Any]]) -> InferenceChain | None:
        """从 evidence 列表构造 InferenceChain。

        evidences 需要的字段：source_url, snippet, published_at（必），
        source_name/source_type/authority_weight（可选）。

        返回 None 表示无足够数据（< 2 个带日期 + snippet 的 evidence）。
        """
        valid = [e for e in evidences if e.get("source_url") and e.get("snippet") and e.get("published_at")]

        if len(valid) == 0:
            return None

        # 按时间排序（ISO 格式字符串可直接比较）
        valid.sort(key=lambda e: e["published_at"])

        # 单节点：直接返回不算 embedding（节省时间）
        if len(valid) == 1:
            e = valid[0]
            domain = urlparse(e["source_url"]).hostname or ""
            cat = _domain_to_category(domain, e.get("source_type"))
            node = ChainNode(
                source_url=e["source_url"],
                source_name=e.get("source_name") or domain,
                source_category=cat,  # type: ignore[arg-type]
                published_at=e["published_at"],
                snippet_preview=(e.get("snippet") or "")[:200] or None,
                tier=e.get("authority_weight"),
                role="origin",
            )
            return InferenceChain(
                nodes=[node],
                origin=node,
                chain_length=1,
                avg_similarity=0.0,
                pattern="insufficient_data",
                timeline_hours=0.0,
                note="单节点，无法构造传播链",
            )

        # ≥ 2 节点：计算 embeddings
        snippets = [(e["snippet"] or "")[:500] for e in valid]
        try:
            model = self._get_model()
            embs = model.encode(snippets, normalize_embeddings=True, show_progress_bar=False)
        except Exception as exc:
            logger.warning("bge model failed: %s; returning chain without similarities", exc)
            return self._build_no_embedding(valid)

        # 构造 nodes
        nodes: list[ChainNode] = []
        for i, e in enumerate(valid):
            domain = urlparse(e["source_url"]).hostname or ""
            cat = _domain_to_category(domain, e.get("source_type"))

            # 和前面每个节点的相似度
            sims: list[dict[str, Any]] = []
            for j in range(i):
                cos_sim = float(np.dot(embs[i], embs[j]))
                sims.append(
                    {
                        "from": valid[j]["source_url"],
                        "similarity": round(cos_sim, 3),
                    }
                )

            likely_source: str | None = None
            sim_to_source: float | None = None
            if sims:
                best = max(sims, key=lambda s: s["similarity"])
                likely_source = best["from"]
                sim_to_source = best["similarity"]

            role = self._classify_role(
                idx=i,
                total=len(valid),
                category=cat,
                sim_to_prev=sim_to_source,
            )

            nodes.append(
                ChainNode(
                    source_url=e["source_url"],
                    source_name=e.get("source_name") or domain,
                    source_category=cat,  # type: ignore[arg-type]
                    published_at=e["published_at"],
                    snippet_preview=(e.get("snippet") or "")[:200] or None,
                    tier=e.get("authority_weight"),
                    role=role,
                    likely_source=likely_source,
                    similarity_to_source=sim_to_source,
                    all_similarities=sims,
                )
            )

        # 平均相似度（不含 origin）
        sims_only = [n.similarity_to_source for n in nodes if n.similarity_to_source is not None]
        avg_sim = round(sum(sims_only) / len(sims_only), 3) if sims_only else 0.0

        # 时间跨度（小时）
        timeline_hours = self._compute_timeline_hours(valid[0]["published_at"], valid[-1]["published_at"])

        # 模式分类
        pattern, note = self._classify_pattern(nodes, timeline_hours, avg_sim)

        return InferenceChain(
            nodes=nodes,
            origin=nodes[0],
            chain_length=len(nodes),
            avg_similarity=avg_sim,
            pattern=pattern,  # type: ignore[arg-type]
            timeline_hours=timeline_hours,
            note=note,
        )

    def _build_no_embedding(self, valid: list[dict]) -> InferenceChain:
        """fallback when bge model load fails — no similarity, just timeline structure."""
        nodes: list[ChainNode] = []
        for i, e in enumerate(valid):
            domain = urlparse(e["source_url"]).hostname or ""
            cat = _domain_to_category(domain, e.get("source_type"))
            nodes.append(
                ChainNode(
                    source_url=e["source_url"],
                    source_name=e.get("source_name") or domain,
                    source_category=cat,  # type: ignore[arg-type]
                    published_at=e["published_at"],
                    snippet_preview=(e.get("snippet") or "")[:200] or None,
                    tier=e.get("authority_weight"),
                    role=self._classify_role(i, len(valid), cat, None),
                )
            )
        return InferenceChain(
            nodes=nodes,
            origin=nodes[0] if nodes else None,
            chain_length=len(nodes),
            avg_similarity=0.0,
            pattern="insufficient_data",
            timeline_hours=self._compute_timeline_hours(valid[0]["published_at"], valid[-1]["published_at"]),
            note="bge embedding 不可用，仅时间线结构",
        )

    @staticmethod
    def _classify_role(idx: int, total: int, category: str, sim_to_prev: float | None) -> str:
        """决定节点的 role：origin / debunker / amplifier / propagator。"""
        if idx == 0:
            return "origin"
        # 含 piyao 等辟谣源 → debunker（相似度通常低，因为是反驳）
        if category == "fact_check_platform":
            return "debunker"
        # 最末节点 + 权威 → amplifier（终局放大）
        if idx == total - 1 and category in ("central_official_media", "official"):
            return "amplifier"
        # 否则 propagator
        return "propagator"

    @staticmethod
    def _compute_timeline_hours(t0_str: str, tn_str: str) -> float | None:
        try:
            t0 = _parse_iso(t0_str)
            tn = _parse_iso(tn_str)
            if t0 is None or tn is None:
                return None
            return round((tn - t0).total_seconds() / 3600, 1)
        except Exception:
            return None

    @staticmethod
    def _classify_pattern(
        nodes: list[ChainNode], timeline_hours: float | None, avg_sim: float
    ) -> tuple[str, str]:
        """根据节点序列 + 相似度判断传播模式。"""
        cats = [n.source_category for n in nodes]
        has_factcheck = any(c == "fact_check_platform" for c in cats)
        first_cat = cats[0]
        last_cat = cats[-1]

        UPSTREAM = {"social_media", "self_media", "aggregator", "encyclopedia"}
        AUTHORITATIVE = {"central_official_media", "official", "mainstream_media"}

        if has_factcheck:
            # 实证发现：bge-large-zh 对"主题词共享"非常敏感，辟谣节点
            # 与谣言 origin 的相似度通常仍较高（~0.7+），因为两者共享话题词。
            # 因此辟谣信号主要靠 source_category 识别，不能依赖低相似度。
            return (
                "fact_check_corrected",
                f"含辟谣节点（category=fact_check_platform）；avg_sim={avg_sim}",
            )

        if first_cat in AUTHORITATIVE:
            return (
                "official_dissemination",
                f"起源即权威 {first_cat}，常规信息发布",
            )

        if timeline_hours is not None and timeline_hours <= 72 and len(nodes) >= 3 and first_cat in UPSTREAM:
            return (
                "coordinated",
                f"{len(nodes)} 个非权威源在 {timeline_hours:.1f}h 内集中出现",
            )

        if first_cat in UPSTREAM and last_cat in AUTHORITATIVE:
            return (
                "grassroots_viral",
                f"起源 {first_cat} → 终至 {last_cat}，典型病毒传播",
            )

        if first_cat in AUTHORITATIVE and last_cat in UPSTREAM:
            return (
                "narrative_distortion",
                f"起源权威 {first_cat} → 终至 {last_cat}，疑似转述变形",
            )

        return ("insufficient_data", f"起 {first_cat} → 终 {last_cat}，模式不明显")


def _parse_iso(s: str) -> datetime | None:
    """容忍多种 ISO 格式：'2024-08-15' / '2026-05-20T08:00:00Z' / 含 +08:00 时区。"""
    if not s:
        return None
    s = s.strip()
    # 去掉末尾 Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # 仅日期
        try:
            return datetime.fromisoformat(s[:10])
        except ValueError:
            return None
