"""Celery worker 入口。生产环境部署用：

    celery -A factcheck.worker worker --concurrency=4 --loglevel=info
"""
from __future__ import annotations

from celery import Celery

from .config import get_settings

_settings = get_settings()
app = Celery(
    "factcheck",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)


@app.task(name="factcheck.run_check")
def run_check(payload: dict) -> dict:
    """同步入口（Celery worker 内开 asyncio 跑 pipeline）。"""
    import asyncio

    from .pipeline import FactCheckPipeline
    from .schemas import CheckRequest

    async def _run() -> dict:
        pipeline = await FactCheckPipeline.create()
        result = await pipeline.run(CheckRequest(**payload))
        return result.model_dump()

    return asyncio.run(_run())
