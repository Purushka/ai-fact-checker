"""CrossValidator — pipeline 步骤 5。4 phase 检查 + 仲裁。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..extract.fact_extractor import ExtractedEvidence
from ..llm import LLMMessage, LLMProvider
from ..utils.json_parse import parse_json_lenient
from ..utils.prompts import CROSS_VALIDATOR_SYSTEM
from ..utils.token_counter import TokenAccumulator


@dataclass
class ValidationResult:
    conflicts: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    policy_meta: dict = field(default_factory=dict)


class CrossValidator:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def _serialize_evidence(self, evidence: list[ExtractedEvidence]) -> str:
        compact = []
        for e in evidence:
            compact.append({
                "source_url": e.source_url,
                "source_type": e.source_type,
                "authority_weight": e.authority_weight,
                "agency": e.agency,
                "agency_level": e.agency_level,
                "published_at": e.published_at,
                "support_level": e.support_level,
                "snippet": (e.snippet or "")[:300],
                "claims_about": e.claims_about,
                "is_primary": e.is_primary,
                "is_independent": e.is_independent,
            })
        return json.dumps(compact, ensure_ascii=False, indent=2)

    async def validate(
        self,
        claim: str,
        mode: str,
        evidence: list[ExtractedEvidence],
        token_acc: TokenAccumulator,
    ) -> ValidationResult:
        if not evidence:
            return ValidationResult()

        user_msg = (
            f"原始 claim:\n{claim}\n\n"
            f"模式: {mode}\n\n"
            f"已抽取的 evidence 列表（JSON）：\n{self._serialize_evidence(evidence)}\n\n"
            f"请按 system 描述返回 JSON。"
        )
        resp = await self.provider.chat(
            [LLMMessage(role="system", content=CROSS_VALIDATOR_SYSTEM),
             LLMMessage(role="user", content=user_msg)],
            temperature=0.0, max_tokens=1500, timeout=30.0,
        )
        token_acc.add_llm(
            provider=resp.provider, model=resp.model,
            prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
            total_tokens=resp.total_tokens, label="cross_validator",
        )

        parsed = parse_json_lenient(resp.text) or {}
        if not isinstance(parsed, dict):
            return ValidationResult()

        return ValidationResult(
            conflicts=parsed.get("conflicts") or [],
            warnings=parsed.get("warnings") or [],
            missing_fields=parsed.get("missing_fields") or [],
            policy_meta=parsed.get("policy_meta") or {},
        )
