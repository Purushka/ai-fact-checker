"""Fetcher 抽象。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal


class FetchError(Exception):
    pass


@dataclass
class FetchedPage:
    url: str
    final_url: str = ""
    title: str = ""
    content: str = ""
    html: str = ""
    fetched_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
    status: Literal["ok", "blocked", "empty", "timeout", "error"] = "ok"
    method: Literal["http", "playwright"] = "http"
    http_status: int | None = None
    error_message: str | None = None


class Fetcher(ABC):
    @abstractmethod
    async def fetch(self, url: str, *, timeout: float = 8.0) -> FetchedPage: ...
