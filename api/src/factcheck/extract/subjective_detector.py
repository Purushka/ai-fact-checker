"""主观 claim 早期检测 — 避免无谓搜索 + 错误降级。"""

from __future__ import annotations

from ..llm import LLMMessage, LLMProvider
from ..utils.json_parse import parse_json_lenient
from ..utils.prompts import SUBJECTIVE_DETECTOR_SYSTEM
from ..utils.token_counter import TokenAccumulator

SUBJECTIVE_KEYWORDS = [
    "最适合",
    "最好的",
    "最佳",
    "最差",
    "最强",
    "更有前景",
    "更值得",
    "应该选",
    "建议选",
    "推荐",
    "可能成为",
    "可能会",
    "有望",
    "前途光明",
    "前景广阔",
    "潜力",
    "倾向于",
]


def _heuristic_is_subjective(claim: str) -> bool:
    """快速启发式筛选，命中明显主观词直接判主观。"""
    if any(kw in claim for kw in SUBJECTIVE_KEYWORDS):
        return True
    return "更" in claim and "比" in claim


class SubjectiveDetector:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider

    async def is_subjective(self, claim: str, token_acc: TokenAccumulator) -> tuple[bool, str]:
        if _heuristic_is_subjective(claim):
            return True, "heuristic_subjective_keywords"

        if self.provider is None:
            return False, "no_llm_check"

        resp = await self.provider.chat(
            [
                LLMMessage(role="system", content=SUBJECTIVE_DETECTOR_SYSTEM),
                LLMMessage(role="user", content=f"声明：{claim}"),
            ],
            temperature=0.0,
            max_tokens=120,
            timeout=15.0,
        )
        token_acc.add_llm(
            provider=resp.provider,
            model=resp.model,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
            total_tokens=resp.total_tokens,
            label="subjective_detector",
        )
        parsed = parse_json_lenient(resp.text) or {}
        if isinstance(parsed, dict):
            return bool(parsed.get("is_subjective")), parsed.get("reason", "")
        return False, ""
