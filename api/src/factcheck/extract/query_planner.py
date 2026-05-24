"""QueryPlanner — pipeline 步骤 1。用 LLM 解析 claim/question，生成搜索词。"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..llm import LLMMessage, LLMProvider
from ..utils.json_parse import parse_json_lenient
from ..utils.prompts import QUERY_PLANNER_SYSTEM
from ..utils.token_counter import TokenAccumulator


@dataclass
class QueryPlan:
    entities: list[str] = field(default_factory=list)
    time_anchor: str | None = None
    region: dict | None = None
    intent_category: str = "general"
    sub_claims: list[dict] = field(default_factory=list)
    search_queries: list[str] = field(default_factory=list)


class QueryPlanner:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def plan(self, input_text: str, *, mode: str, token_acc: TokenAccumulator) -> QueryPlan:
        user_msg = f"模式: {mode}\n输入文本:\n{input_text}\n\n请按 system 描述返回 JSON。"
        resp = await self.provider.chat(
            [LLMMessage(role="system", content=QUERY_PLANNER_SYSTEM),
             LLMMessage(role="user", content=user_msg)],
            temperature=0.0, max_tokens=800, timeout=20.0,
        )
        token_acc.add_llm(
            provider=resp.provider, model=resp.model,
            prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
            total_tokens=resp.total_tokens, label="query_planner",
        )
        parsed = parse_json_lenient(resp.text) or {}
        if not isinstance(parsed, dict):
            parsed = {}
        queries = parsed.get("search_queries") or []
        if not queries:
            queries = [input_text]
        return QueryPlan(
            entities=parsed.get("entities") or [],
            time_anchor=parsed.get("time_anchor"),
            region=parsed.get("region"),
            intent_category=parsed.get("intent_category", "general"),
            sub_claims=parsed.get("sub_claims") or [],
            search_queries=queries[:8],
        )
