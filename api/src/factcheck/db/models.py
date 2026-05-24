"""SQLAlchemy 模型。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class CheckRecord(Base):
    __tablename__ = "check_records"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    input_text: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(32), index=True)
    verdict: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[int] = mapped_column(Integer)
    has_official_source: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_status: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    gating_applied: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    evidence_items = relationship("EvidenceRecord", back_populates="check", cascade="all, delete-orphan")
    usage = relationship("UsageRecord", back_populates="check", uselist=False, cascade="all, delete-orphan")


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_id: Mapped[str] = mapped_column(String(64), ForeignKey("check_records.id", ondelete="CASCADE"), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(32))
    authority_weight: Mapped[float] = mapped_column()
    agency: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[str | None] = mapped_column(String(32), nullable=True)
    support_level: Mapped[str] = mapped_column(String(16))
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    claims_about: Mapped[dict] = mapped_column(JSON, default=dict)
    check = relationship("CheckRecord", back_populates="evidence_items")


class UsageRecord(Base):
    __tablename__ = "usage_records"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    check_id: Mapped[str] = mapped_column(String(64), ForeignKey("check_records.id", ondelete="CASCADE"), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    search_calls: Mapped[int] = mapped_column(Integer, default=0)
    fetch_calls: Mapped[int] = mapped_column(Integer, default=0)
    breakdown: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    check = relationship("CheckRecord", back_populates="usage")
