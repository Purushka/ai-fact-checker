"""POST /v1/policy-check — 政策专用核查，强制 mode=policy + require_official_source=true。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..pipeline import FactCheckPipeline
from ..schemas import CheckResponse, PolicyCheckRequest
from .deps import get_pipeline, verify_secret

router = APIRouter(prefix="/v1", tags=["policy"], dependencies=[Depends(verify_secret)])


@router.post("/policy-check", response_model=CheckResponse)
async def policy_check(
    req: PolicyCheckRequest, pipeline: FactCheckPipeline = Depends(get_pipeline)
) -> CheckResponse:
    req.mode = "policy"
    req.options.require_official_source = True
    if not req.claim and not req.question:
        raise HTTPException(status_code=400, detail="claim 或 question 必须填一个")
    if req.scoring_weights:
        try:
            req.scoring_weights.validate_sum()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return await pipeline.run(req)
