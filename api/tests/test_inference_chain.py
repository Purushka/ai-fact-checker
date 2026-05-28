"""InferenceChain 单元测试 — mock bge model 避免每次跑加载 1.3GB 权重。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from factcheck.extract.inference_chain import (
    InferenceChainBuilder,
    _parse_iso,
)


@pytest.fixture
def builder() -> InferenceChainBuilder:
    # 清掉 class-level model cache，确保每个测试干净
    InferenceChainBuilder._model = None
    return InferenceChainBuilder()


def _ev(url: str, snippet: str, published: str | None = None, source_type: str | None = None, tier: float | None = None) -> dict:
    return {
        "source_url": url,
        "source_name": url,
        "source_type": source_type or ("official" if ".gov.cn" in url else "mainstream_media"),
        "agency": None,
        "authority_weight": tier,
        "published_at": published,
        "snippet": snippet,
    }


def _mock_model(similarity_matrix: np.ndarray) -> MagicMock:
    """构造一个 mock model.encode 调用，返回特定相似度的 embeddings。

    类似 [[1,0], [0.9, 0.43589], [0.45, 0.89]] 这种 unit vectors，
    内积就等于 cosine similarity。
    """
    m = MagicMock()
    # 给定 N 条文本，返回 NxD 的 normalized embedding 矩阵，使内积刚好等于 sim_matrix
    # 简化：直接构造 N 维单位向量，使 e_i · e_j = sim[i][j]
    # 用 Cholesky 分解
    n = similarity_matrix.shape[0]
    # 加微小扰动避免 Cholesky 失败
    sim_psd = similarity_matrix + 1e-6 * np.eye(n)
    try:
        L = np.linalg.cholesky(sim_psd)
    except np.linalg.LinAlgError:
        # 退化：用随机正交矩阵
        L = np.eye(n)

    def encode(texts, normalize_embeddings=True, show_progress_bar=False):
        return L

    m.encode = MagicMock(side_effect=encode)
    return m


# ============ 基础情况 ============

def test_no_evidence_returns_none(builder):
    chain = builder.build([])
    assert chain is None


def test_evidence_without_published_at_returns_none(builder):
    evs = [_ev("https://x.com/a", "foo bar", None)]
    chain = builder.build(evs)
    assert chain is None


def test_evidence_without_snippet_returns_none(builder):
    evs = [_ev("https://x.com/a", "", "2024-08-15")]
    chain = builder.build(evs)
    assert chain is None


def test_single_valid_evidence_skips_embedding(builder):
    """单节点不算 embedding，直接返回 insufficient_data。"""
    evs = [_ev("https://weibo.com/a", "test snippet", "2024-08-15")]
    chain = builder.build(evs)
    assert chain is not None
    assert chain.chain_length == 1
    assert chain.pattern == "insufficient_data"
    assert chain.nodes[0].role == "origin"
    assert chain.origin.source_url == "https://weibo.com/a"
    assert chain.timeline_hours == 0.0


# ============ Embedding chain 构造 ============

def test_two_evidence_with_high_similarity(builder):
    """两条 evidence 都讲同一事件，相似度高 → propagator role + 高 sim_to_source。"""
    sim = np.array([[1.0, 0.92], [0.92, 1.0]])
    evs = [
        _ev("https://weibo.com/origin", "某事件发生在北京", "2024-08-15"),
        _ev("https://news.cn/coverage", "北京发生某事件，详情如下", "2024-08-20"),
    ]
    with patch.object(InferenceChainBuilder, "_get_model", return_value=_mock_model(sim)):
        chain = builder.build(evs)

    assert chain.chain_length == 2
    assert chain.nodes[0].role == "origin"
    assert chain.nodes[1].role in ("propagator", "amplifier")
    # 第二个节点的 likely_source 应指向第一个
    assert chain.nodes[1].likely_source == "https://weibo.com/origin"
    assert chain.nodes[1].similarity_to_source == pytest.approx(0.92, abs=0.01)
    assert chain.avg_similarity == pytest.approx(0.92, abs=0.01)


def test_fact_check_node_gets_debunker_role(builder):
    """piyao 节点 → role=debunker，pattern=fact_check_corrected。

    实证发现：bge-large-zh 对'共享主题词'敏感，实际跑出来辟谣节点 vs
    谣言 origin 的 cos sim 仍能保持 0.6-0.8。debunker 信号靠
    source_category，不能依赖低相似度。
    """
    # 这里 sim 高/低都允许，role 由 category 决定
    sim = np.array(
        [
            [1.0, 0.85, 0.76],
            [0.85, 1.0, 0.72],
            [0.76, 0.72, 1.0],
        ]
    )
    evs = [
        _ev("https://weibo.com/rumor", "网传某事件", "2024-08-15"),
        _ev("https://baijiahao.baidu.com/x", "据网传，某事件详情", "2024-08-16"),
        _ev("https://www.piyao.org.cn/20240820/abc/c.html", "经核实，某事件为虚假信息", "2024-08-22"),
    ]
    with patch.object(InferenceChainBuilder, "_get_model", return_value=_mock_model(sim)):
        chain = builder.build(evs)

    # 最后节点是 piyao → role 必须是 debunker（靠 category 判定）
    assert chain.nodes[2].role == "debunker"
    assert chain.pattern == "fact_check_corrected"
    # 即使 sim 较高（这里 0.76），仍正确识别为辟谣
    assert chain.nodes[2].similarity_to_source is not None


def test_grassroots_viral_pattern(builder):
    """微博起源 → 央媒收尾。"""
    sim = np.array(
        [
            [1.0, 0.88, 0.80, 0.75],
            [0.88, 1.0, 0.82, 0.78],
            [0.80, 0.82, 1.0, 0.85],
            [0.75, 0.78, 0.85, 1.0],
        ]
    )
    evs = [
        _ev("https://weibo.com/post/123", "突发：某地发生 X", "2024-08-15"),
        _ev("https://baijiahao.baidu.com/x", "传 X 事件正在发酵", "2024-08-16"),
        _ev("https://www.thepaper.cn/news", "X 事件实地调查", "2024-08-18"),
        _ev("https://news.cn/breaking", "X 事件多部门联合通报", "2024-08-22"),
    ]
    with patch.object(InferenceChainBuilder, "_get_model", return_value=_mock_model(sim)):
        chain = builder.build(evs)

    assert chain.pattern == "grassroots_viral"
    assert chain.nodes[0].role == "origin"
    assert chain.nodes[0].source_category == "social_media"
    assert chain.nodes[-1].role == "amplifier"
    assert chain.nodes[-1].source_category == "central_official_media"


def test_official_dissemination_pattern(builder):
    """官方 → 央媒 → 主流：正常信息发布。"""
    sim = np.array([[1.0, 0.92, 0.88], [0.92, 1.0, 0.90], [0.88, 0.90, 1.0]])
    evs = [
        _ev("https://www.gov.cn/policy", "国务院印发某通知", "2024-05-20", source_type="official"),
        _ev("https://news.cn/coverage", "新华社解读：通知六大要点", "2024-05-21"),
        _ev("https://www.caixin.com/article", "财新解读：通知的市场含义", "2024-05-23"),
    ]
    with patch.object(InferenceChainBuilder, "_get_model", return_value=_mock_model(sim)):
        chain = builder.build(evs)

    assert chain.pattern == "official_dissemination"
    assert chain.nodes[0].source_category == "official"


# ============ Timeline metrics ============

def test_timeline_hours_calculated_correctly(builder):
    sim = np.array([[1.0, 0.8], [0.8, 1.0]])
    evs = [
        _ev("https://x.com/a", "snip1", "2024-08-15T08:00:00Z"),
        _ev("https://x.com/b", "snip2", "2024-08-16T20:00:00Z"),
    ]
    with patch.object(InferenceChainBuilder, "_get_model", return_value=_mock_model(sim)):
        chain = builder.build(evs)
    # 36 hours
    assert chain.timeline_hours == pytest.approx(36.0, abs=0.1)


def test_date_only_strings_handled(builder):
    sim = np.array([[1.0, 0.8], [0.8, 1.0]])
    evs = [
        _ev("https://x.com/a", "snip1", "2024-08-15"),
        _ev("https://x.com/b", "snip2", "2024-08-22"),
    ]
    with patch.object(InferenceChainBuilder, "_get_model", return_value=_mock_model(sim)):
        chain = builder.build(evs)
    # 7 days = 168 hours
    assert chain.timeline_hours == pytest.approx(168.0, abs=0.1)


# ============ 排序 + role 推断 ============

def test_nodes_sorted_chronologically(builder):
    """无论输入顺序如何，nodes 始终按时间排序。"""
    sim = np.array([[1.0, 0.8, 0.7], [0.8, 1.0, 0.85], [0.7, 0.85, 1.0]])
    evs = [
        _ev("https://x.com/c", "snip3", "2024-08-25"),
        _ev("https://x.com/a", "snip1", "2024-08-15"),
        _ev("https://x.com/b", "snip2", "2024-08-20"),
    ]
    with patch.object(InferenceChainBuilder, "_get_model", return_value=_mock_model(sim)):
        chain = builder.build(evs)
    dates = [n.published_at for n in chain.nodes]
    assert dates == ["2024-08-15", "2024-08-20", "2024-08-25"]


# ============ Fallback (model 加载失败) ============

def test_fallback_when_model_fails(builder):
    """模型加载失败时，仍能返回有 nodes 但无 similarity 的 chain。"""
    evs = [
        _ev("https://weibo.com/a", "snip1", "2024-08-15"),
        _ev("https://news.cn/b", "snip2", "2024-08-22"),
    ]
    with patch.object(InferenceChainBuilder, "_get_model", side_effect=RuntimeError("model load failed")):
        chain = builder.build(evs)

    # build 内部 catch 了异常，走 _build_no_embedding
    assert chain is not None
    assert chain.chain_length == 2
    assert chain.nodes[0].similarity_to_source is None
    assert "bge embedding 不可用" in (chain.note or "")


# ============ ISO date parse 容错 ============

def test_parse_iso_handles_various_formats():
    assert _parse_iso("2024-08-15") is not None
    assert _parse_iso("2024-08-15T08:00:00Z") is not None
    assert _parse_iso("2024-08-15T08:00:00+08:00") is not None
    assert _parse_iso("") is None
    assert _parse_iso("not-a-date") is None


# ============ avg_similarity 计算 ============

def test_avg_similarity_excludes_origin(builder):
    """origin 没有前置节点，不应计入 avg_similarity。"""
    sim = np.array(
        [
            [1.0, 0.90, 0.80],
            [0.90, 1.0, 0.85],
            [0.80, 0.85, 1.0],
        ]
    )
    evs = [
        _ev("https://x.com/a", "s1", "2024-08-15"),
        _ev("https://x.com/b", "s2", "2024-08-16"),
        _ev("https://x.com/c", "s3", "2024-08-17"),
    ]
    with patch.object(InferenceChainBuilder, "_get_model", return_value=_mock_model(sim)):
        chain = builder.build(evs)
    # node[1].sim = max(0.90) = 0.90
    # node[2].sim = max(0.80, 0.85) = 0.85
    # avg = (0.90 + 0.85) / 2 = 0.875
    assert chain.avg_similarity == pytest.approx(0.875, abs=0.005)


# ============ all_similarities 完整记录 ============

def test_all_similarities_recorded(builder):
    """每个非 origin 节点应记录与所有前置节点的相似度。"""
    sim = np.array([[1.0, 0.7, 0.6], [0.7, 1.0, 0.8], [0.6, 0.8, 1.0]])
    evs = [
        _ev("https://x.com/a", "s1", "2024-08-15"),
        _ev("https://x.com/b", "s2", "2024-08-16"),
        _ev("https://x.com/c", "s3", "2024-08-17"),
    ]
    with patch.object(InferenceChainBuilder, "_get_model", return_value=_mock_model(sim)):
        chain = builder.build(evs)
    assert len(chain.nodes[0].all_similarities) == 0  # origin
    assert len(chain.nodes[1].all_similarities) == 1
    assert len(chain.nodes[2].all_similarities) == 2
    # node[2] 选 node[1] 而非 node[0]，因为 0.8 > 0.6
    assert chain.nodes[2].likely_source == "https://x.com/b"
    assert chain.nodes[2].similarity_to_source == pytest.approx(0.8, abs=0.01)
