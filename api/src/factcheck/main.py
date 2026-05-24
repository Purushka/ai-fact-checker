"""FastAPI 主入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

from .api import async_task, audit, check, policy, profiles
from .config import get_settings
from .llm import list_providers
from .utils.logger import get_logger, setup_logging

logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    setup_logging(s.log_level)
    providers = list_providers()
    logger.info("service_starting", env=s.env, llm_providers=providers,
                search_primary=s.search_primary, search_secondary=s.search_secondary)
    if not providers:
        logger.warning("no_llm_provider_configured", message="请在 .env 中配置至少一个 LLM API key")
    yield
    logger.info("service_stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title="AI Fact Checker",
        description="OpenClaw 创业平台准确性基础设施。提供事实核查、政策核查、行业方案审计 API。",
        version="0.1.0",
        default_response_class=ORJSONResponse,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(check.router)
    app.include_router(policy.router)
    app.include_router(audit.router)
    app.include_router(async_task.router)
    app.include_router(profiles.router)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {
            "status": "ok",
            "service": "factcheck",
            "version": "0.1.0",
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "llm_providers": list_providers(),
        }

    @app.get("/", tags=["meta"])
    async def root() -> dict:
        return {
            "service": "AI Fact Checker",
            "docs": "/docs",
            "health": "/health",
            "endpoints": [
                "POST /v1/check",
                "POST /v1/check/batch",
                "POST /v1/check/async",
                "POST /v1/policy-check",
                "POST /v1/solution-audit",
                "GET  /v1/tasks/{job_id}",
                "GET  /v1/source-profiles",
                "GET  /v1/source-profiles/classify?url=...",
            ],
        }

    return app


app = create_app()
