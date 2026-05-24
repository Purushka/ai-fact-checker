"""FactExtractor — pipeline 步骤 4。给定原始 claim 和单个抓取页面，LLM 抽取结构化 evidence。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..fetch import FetchedPage
from ..llm import LLMMessage, LLMProvider
from ..score.source_classify import Classification
from ..utils.json_parse import parse_json_lenient
from ..utils.prompts import FACT_EXTRACTOR_SYSTEM
from ..utils.token_counter import TokenAccumulator

TEMPLATES_PATH = Path(__file__).resolve().parent.parent / "score" / "data" / "completeness_templates.json"


def _load_template(mode: str) -> dict:
    with TEMPLATES_PATH.open(encoding="utf-8") as f:
        templates = json.load(f)["templates"]
    return templates.get(mode, templates["general"])


def _fingerprint(content: str) -> str:
    if not content:
        return ""
    head = (content[:600] or "").strip()
    return hashlib.md5(head.encode("utf-8")).hexdigest()[:16]


@dataclass
class ExtractedEvidence:
    source_url: str
    source_name: str | None
    source_type: str
    authority_weight: float
    agency: str | None
    agency_level: str | None
    published_at: str | None
    snippet: str
    support_level: str
    is_primary: bool
    is_independent: bool
    content_fingerprint: str
    claims_about: dict = field(default_factory=dict)


class FactExtractor:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def _extract_one(
        self,
        claim: str,
        mode: str,
        page: FetchedPage,
        classification: Classification,
        token_acc: TokenAccumulator,
    ) -> ExtractedEvidence | None:
        if page.status != "ok" or not page.content.strip():
            return None

        template_fields = [f["key"] for f in _load_template(mode)["fields"]]
        content_excerpt = page.content[:6000]

        user_msg = (
            f"原始 claim:\n{claim}\n\n"
            f"模式: {mode}\n"
            f"字段模板（用作 claims_about 的 key 列表）：{template_fields}\n\n"
            f"页面信息:\n"
            f"- 标题: {page.title}\n"
            f"- 来源类型: {classification.source_type}\n"
            f"- 权威分: {classification.authority_weight}\n"
            f"- URL: {page.url}\n\n"
            f"正文（≤6000 字）:\n{content_excerpt}\n\n"
            f"请按 system 描述返回 JSON。"
        )

        resp = await self.provider.chat(
            [
                LLMMessage(role="system", content=FACT_EXTRACTOR_SYSTEM),
                LLMMessage(role="user", content=user_msg),
            ],
            temperature=0.0,
            max_tokens=1200,
            timeout=30.0,
        )
        token_acc.add_llm(
            provider=resp.provider,
            model=resp.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            total_tokens=resp.total_tokens,
            label="fact_extractor",
        )

        parsed = parse_json_lenient(resp.text) or {}
        if not isinstance(parsed, dict):
            return None

        is_secondary = bool(parsed.get("is_secondary_citation"))
        authority = classification.authority_weight
        if is_secondary:
            authority = round(authority * 0.7, 3)

        return ExtractedEvidence(
            source_url=page.url,
            source_name=classification.agency or classification.domain,
            source_type=classification.source_type,
            authority_weight=authority,
            agency=parsed.get("agency") or classification.agency,
            agency_level=parsed.get("agency_level") or classification.agency_level,
            published_at=parsed.get("published_at"),
            snippet=parsed.get("snippet") or "",
            support_level=parsed.get("support_level", "neutral"),
            is_primary=not is_secondary,
            is_independent=True,
            content_fingerprint=_fingerprint(page.content),
            claims_about=parsed.get("claims_about") or {},
        )

    async def extract_all(
        self,
        claim: str,
        mode: str,
        pages: list[FetchedPage],
        classifications: list[Classification],
        token_acc: TokenAccumulator,
    ) -> list[ExtractedEvidence]:
        pairs = list(zip(pages, classifications, strict=False))
        results = await asyncio.gather(
            *[self._extract_one(claim, mode, p, c, token_acc) for p, c in pairs],
            return_exceptions=True,
        )
        out: list[ExtractedEvidence] = []
        for r in results:
            if isinstance(r, ExtractedEvidence):
                out.append(r)

        seen_fps: set[str] = set()
        for e in out:
            if e.content_fingerprint and e.content_fingerprint in seen_fps:
                e.is_independent = False
            elif e.content_fingerprint:
                seen_fps.add(e.content_fingerprint)
        return out
