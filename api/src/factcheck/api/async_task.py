"""异步任务接口骨架。生产部署用 Celery，这里给出内存版作为占位。"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ..pipeline import FactCheckPipeline
from ..schemas import AsyncJobRequest, AsyncJobResponse, CheckRequest
from .deps import get_pipeline, verify_secret

router = APIRouter(prefix="/v1", tags=["async"], dependencies=[Depends(verify_secret)])

_jobs: dict[str, dict[str, Any]] = {}
_tasks: dict[str, asyncio.Task] = {}


async def _run_job(job_id: str, payload: dict, job_type: str, pipeline: FactCheckPipeline) -> None:
    try:
        _jobs[job_id]["status"] = "running"
        if job_type == "check_batch":
            items = [CheckRequest(**i) for i in payload.get("items", [])]
            results = []
            for i, r in enumerate(items):
                results.append((await pipeline.run(r)).model_dump())
                _jobs[job_id]["progress"] = (i + 1) / max(1, len(items))
            _jobs[job_id]["result"] = {"results": results}
        elif job_type == "solution_audit":
            claims = payload.get("claims", [])
            results = []
            for i, c in enumerate(claims):
                r = CheckRequest(claim=c, mode=payload.get("mode", "general"))
                results.append((await pipeline.run(r)).model_dump())
                _jobs[job_id]["progress"] = (i + 1) / max(1, len(claims))
            _jobs[job_id]["result"] = {"results": results}
        else:
            raise ValueError(f"unsupported job_type: {job_type}")
        _jobs[job_id]["status"] = "completed"
    except Exception as e:
        _jobs[job_id]["status"] = "failed"
        _jobs[job_id]["error"] = str(e)


@router.post("/check/async", response_model=AsyncJobResponse)
async def create_async_job(
    req: AsyncJobRequest, pipeline: FactCheckPipeline = Depends(get_pipeline)
) -> AsyncJobResponse:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "status": "pending",
        "created_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "progress": 0.0,
        "result": None,
        "error": None,
    }
    _tasks[job_id] = asyncio.create_task(_run_job(job_id, req.payload, req.job_type, pipeline))
    return AsyncJobResponse(job_id=job_id, status="pending", created_at=_jobs[job_id]["created_at"])


@router.get("/tasks/{job_id}", response_model=AsyncJobResponse)
async def get_job(job_id: str) -> AsyncJobResponse:
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="job not found")
    j = _jobs[job_id]
    return AsyncJobResponse(
        job_id=job_id,
        status=j["status"],
        created_at=j["created_at"],
        progress=j["progress"],
        result=j["result"],
        result_url=f"/v1/tasks/{job_id}/result" if j["status"] == "completed" else None,
        error=j["error"],
    )


@router.get("/tasks/{job_id}/result")
async def get_job_result(job_id: str) -> dict:
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="job not found")
    j = _jobs[job_id]
    if j["status"] != "completed":
        raise HTTPException(status_code=409, detail=f"job not completed: {j['status']}")
    return j["result"] or {}
