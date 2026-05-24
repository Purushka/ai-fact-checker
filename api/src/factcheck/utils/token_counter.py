"""Token 用量记账。本服务按 token 向父项目报销，所以每次 LLM 调用都要累加。"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TokenAccumulator:
    provider: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    search_calls: int = 0
    fetch_calls: int = 0
    breakdown: list[dict] = field(default_factory=list)

    def add_llm(self, *, provider: str, model: str, prompt_tokens: int, completion_tokens: int, total_tokens: int, label: str = "") -> None:
        if not self.provider:
            self.provider = provider
        if not self.model:
            self.model = model
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.total_tokens += total_tokens
        self.breakdown.append({
            "label": label, "provider": provider, "model": model,
            "prompt": prompt_tokens, "completion": completion_tokens, "total": total_tokens,
        })

    def add_search(self, n: int = 1) -> None:
        self.search_calls += n

    def add_fetch(self, n: int = 1) -> None:
        self.fetch_calls += n

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "search_calls": self.search_calls,
            "fetch_calls": self.fetch_calls,
            "breakdown": self.breakdown,
        }
