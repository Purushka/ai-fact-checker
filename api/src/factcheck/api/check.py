"""POST /v1/check — 通用核查接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..pipeline import FactCheckPipeline
from ..schemas import BatchCheckRequest, BatchCheckResponse, CheckRequest, CheckResponse, TokenUsage
from .deps import get_pipeline, verify_secret

router = APIRouter(prefix="/v1", tags=["check"], dependencies=[Depends(verify_secret)])


@router.post("/check", response_model=CheckResponse)
async def check(req: CheckRequest, pipeline: FactCheckPipeline = Depends(get_pipeline)) -> CheckResponse:
    if not req.claim and not req.question:
        raise HTTPException(status_code=400, detail="claim 或 question 必须填一个")
    if req.scoring_weights:
        try:
            req.scoring_weights.validate_sum()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return await pipeline.run(req)


@router.post("/check/batch", response_model=BatchCheckResponse)
async def check_batch(req: BatchCheckRequest, pipeline: FactCheckPipeline = Depends(get_pipeline)) -> BatchCheckResponse:
    import asyncio
    import uuid

    async def _one(r: CheckRequest):
        try:
            return await pipeline.run(r)
        except Exception as e:
            return {"error": str(e), "input": r.input_text()}

    results = await asyncio.gather(*[_one(r) for r in req.items])
    total = TokenUsage(provider="", model="")
    for r in results:
        if isinstance(r, CheckResponse) and r.token_usage:
            total.prompt_tokens += r.token_usage.prompt_tokens
            total.completion_tokens += r.token_usage.completion_tokens
            total.total_tokens += r.token_usage.total_tokens
            total.search_calls += r.token_usage.search_calls
            total.fetch_calls += r.token_usage.fetch_calls
            if not total.provider:
                total.provider = r.token_usage.provider
                total.model = r.token_usage.model
    return BatchCheckResponse(
        batch_id=str(uuid.uuid4()),
        status="completed",
        results=results,
        total_token_usage=total,
    )
