"""LLM provider 注册表。按 settings 配置实例化，按名字取。"""

from __future__ import annotations

from functools import lru_cache

from ..config import get_settings
from .base import LLMProvider
from .openai_compatible import OpenAICompatibleProvider


@lru_cache(maxsize=1)
def _build_providers() -> dict[str, LLMProvider]:
    s = get_settings()
    providers: dict[str, LLMProvider] = {}
    if s.deepseek_api_key:
        providers["deepseek"] = OpenAICompatibleProvider(
            "deepseek",
            s.deepseek_base_url,
            s.deepseek_api_key,
            "deepseek-chat",
        )
    if s.qwen_api_key:
        providers["qwen"] = OpenAICompatibleProvider(
            "qwen",
            s.qwen_base_url,
            s.qwen_api_key,
            "qwen-plus",
        )
    if s.moonshot_api_key:
        providers["moonshot"] = OpenAICompatibleProvider(
            "moonshot",
            s.moonshot_base_url,
            s.moonshot_api_key,
            "moonshot-v1-32k",
        )
    if s.glm_api_key:
        providers["glm"] = OpenAICompatibleProvider(
            "glm",
            s.glm_base_url,
            s.glm_api_key,
            "glm-4-flash",
        )
    return providers


def get_provider(name: str | None = None) -> LLMProvider:
    s = get_settings()
    providers = _build_providers()
    name = name or s.llm_default_provider
    if name in providers:
        return providers[name]
    fb = s.llm_fallback_provider
    if fb in providers:
        return providers[fb]
    raise RuntimeError(
        "未配置任何 LLM provider。请在 .env 中至少填写以下之一的 API key："
        "DEEPSEEK_API_KEY / QWEN_API_KEY / MOONSHOT_API_KEY / GLM_API_KEY",
    )


def list_providers() -> list[str]:
    return list(_build_providers().keys())
