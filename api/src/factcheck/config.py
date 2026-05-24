"""配置加载层。所有配置从环境变量读，避免硬编码。"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: str = "production"
    log_level: str = "INFO"
    service_name: str = "factcheck"
    workers: int = 2

    shared_secret: str = "change_me"
    allowed_ips: str = "127.0.0.1"

    llm_default_provider: str = "deepseek"
    llm_default_model: str = "deepseek-chat"
    llm_fallback_provider: str = "qwen"
    llm_fallback_model: str = "qwen-plus"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    qwen_api_key: str = ""
    qwen_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    moonshot_api_key: str = ""
    moonshot_base_url: str = "https://api.moonshot.cn/v1"
    glm_api_key: str = ""
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    hunyuan_secret_id: str = ""
    hunyuan_secret_key: str = ""
    hunyuan_region: str = "ap-shanghai"

    search_primary: str = "tavily"
    search_secondary: str = "bocha"
    search_fallback: str = "bing"
    tavily_api_key: str = ""
    bocha_api_key: str = ""
    serper_api_key: str = ""
    bing_api_key: str = ""
    metaso_api_key: str = ""
    anysearch_api_key: str = ""

    max_search_calls_per_claim: int = 5
    max_fetch_parallel: int = 8
    http_fetch_timeout_sec: int = 8
    total_timeout_sec: int = 30
    playwright_enabled: bool = True
    playwright_pool_size: int = 4

    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_historical_sec: int = 30 * 24 * 3600
    cache_ttl_policy_active_sec: int = 7 * 24 * 3600
    cache_ttl_state_sec: int = 24 * 3600
    cache_ttl_real_time_sec: int = 0

    database_url: str = "postgresql+asyncpg://factcheck:CHANGEME@localhost:5432/factcheck"
    db_pool_size: int = 10

    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    prometheus_port: int = 9090
    sentry_dsn: str = ""

    content_safety_enabled: bool = False
    content_safety_provider: str = "local"

    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parents[2])
    data_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parent / "score" / "data")


@lru_cache
def get_settings() -> Settings:
    return Settings()
