"""Search provider 抽象。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class SearchError(Exception):
    pass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    published_at: str | None = None
    provider: str = ""
    raw_score: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class SearchProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def search(
        self,
        query: str,
        *,
        limit: int = 8,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        timeout: float = 10.0,
    ) -> list[SearchResult]: ...
