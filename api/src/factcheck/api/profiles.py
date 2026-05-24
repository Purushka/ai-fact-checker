"""GET /v1/source-profiles — 查询当前内置 source profile（运维台用）。

由于初版只有一个共享的 source_authority.json，不做用户级 CRUD。
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from .deps import verify_secret

router = APIRouter(prefix="/v1", tags=["profiles"], dependencies=[Depends(verify_secret)])

DATA_DIR = Path(__file__).resolve().parent.parent / "score" / "data"


@router.get("/source-profiles")
async def list_profiles() -> dict:
    with (DATA_DIR / "source_authority.json").open(encoding="utf-8") as f:
        auth = json.load(f)
    return {
        "version": auth.get("version"),
        "tiers": auth.get("tiers"),
        "source_count": len(auth.get("sources", [])),
        "patterns": auth.get("patterns", []),
    }


@router.get("/source-profiles/sources")
async def list_sources(source_type: str | None = None, limit: int = 200) -> dict:
    with (DATA_DIR / "source_authority.json").open(encoding="utf-8") as f:
        auth = json.load(f)
    sources = auth.get("sources", [])
    if source_type:
        sources = [s for s in sources if s.get("source_type") == source_type]
    return {"count": len(sources), "sources": sources[:limit]}


@router.get("/source-profiles/classify")
async def classify(url: str) -> dict:
    from ..score.source_classify import SourceClassifier

    if not url:
        raise HTTPException(status_code=400, detail="url 不能为空")
    c = SourceClassifier()
    cls = c.classify(url)
    return {
        "url": cls.url,
        "domain": cls.domain,
        "source_type": cls.source_type,
        "authority_weight": cls.authority_weight,
        "tier": cls.tier,
        "agency": cls.agency,
        "agency_level": cls.agency_level,
        "matched_by": cls.matched_by,
        "is_blacklisted": cls.is_blacklisted,
    }
