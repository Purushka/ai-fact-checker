"""POST /v1/solution-audit — 行业方案审计，批量核查多个事实声明。"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, HTTPException

from ..pipeline import FactCheckPipeline
from ..schemas import BatchCheckResponse, CheckRequest, CheckResponse, SolutionAuditRequest, TokenUsage
from .deps import get_pipeline, verify_secret

router = APIRouter(prefix="/v1", tags=["audit"], dependencies=[Depends(verify_secret)])


@router.post("/solution-audit", response_model=BatchCheckResponse)
async def solution_audit(
    req: SolutionAuditRequest, pipeline: FactCheckPipeline = Depends(get_pipeline)
) -> BatchCheckResponse:
    if not req.claims:
        raise HTTPException(status_code=400, detail="claims 不能为空")

    sub_requests = [
        CheckRequest(claim=c, mode=req.mode, scoring_weights=req.scoring_weights, options=req.options)
        for c in req.claims
    ]

    async def _one(r: CheckRequest):
        try:
            return await pipeline.run(r)
        except Exception as e:
            return {"error": str(e), "input": r.input_text()}

    results = await asyncio.gather(*[_one(r) for r in sub_requests])
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
        batch_id=req.document_id or str(uuid.uuid4()),
        status="completed",
        results=results,
        total_token_usage=total,
    )
