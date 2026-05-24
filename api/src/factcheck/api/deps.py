"""FastAPI 依赖：鉴权、pipeline 单例。"""

from __future__ import annotations

from fastapi import Header, HTTPException, Request, status

from ..config import get_settings
from ..pipeline import FactCheckPipeline

_pipeline: FactCheckPipeline | None = None


async def get_pipeline() -> FactCheckPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = await FactCheckPipeline.create()
    return _pipeline


async def verify_secret(
    request: Request,
    authorization: str | None = Header(default=None),
    x_factcheck_secret: str | None = Header(default=None),
) -> None:
    s = get_settings()
    if s.env == "dev":
        return
    secret = None
    if authorization and authorization.lower().startswith("bearer "):
        secret = authorization[7:].strip()
    if not secret and x_factcheck_secret:
        secret = x_factcheck_secret.strip()
    if not secret or secret != s.shared_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid secret")

    allowed = [ip.strip() for ip in s.allowed_ips.split(",") if ip.strip()]
    if allowed:
        client_ip = request.client.host if request.client else ""
        if client_ip not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"client IP {client_ip} not in allowlist",
            )
