"""Prometheus metrics。"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

check_requests_total = Counter(
    "factcheck_requests_total",
    "事实核查请求总数",
    labelnames=("endpoint", "mode", "verdict"),
)
check_latency_seconds = Histogram(
    "factcheck_latency_seconds",
    "事实核查端到端延迟",
    labelnames=("endpoint", "mode"),
    buckets=(0.5, 1, 2, 5, 8, 12, 20, 30, 60),
)
token_usage_total = Counter(
    "factcheck_tokens_total",
    "LLM token 总消耗",
    labelnames=("provider", "model", "kind"),
)
search_calls_total = Counter(
    "factcheck_search_calls_total",
    "搜索 API 调用次数",
    labelnames=("provider",),
)
cache_hits_total = Counter("factcheck_cache_hits_total", "缓存命中次数")
cache_misses_total = Counter("factcheck_cache_misses_total", "缓存未命中次数")
