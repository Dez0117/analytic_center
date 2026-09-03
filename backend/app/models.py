from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(300))
    type: Mapped[str] = mapped_column(String(30), default="unknown")
    url: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)


class EventCluster(Base):
    __tablename__ = "event_clusters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_title: Mapped[str] = mapped_column(Text)
    possible_duplicate: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    items: Mapped[list[RawItemRecord]] = relationship(back_populates="cluster")


class RawItemRecord(Base):
    __tablename__ = "raw_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    external_id: Mapped[str | None] = mapped_column(String(300))
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    cluster_id: Mapped[str] = mapped_column(ForeignKey("event_clusters.id"))
    url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(300))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str] = mapped_column(String(10), default="ru")
    attachments: Mapped[list[str]] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)

    source: Mapped[Source] = relationship()
    cluster: Mapped[EventCluster] = relationship(back_populates="items")
    analyses: Mapped[list[AnalysisVersion]] = relationship(back_populates="item", order_by="AnalysisVersion.id")
    overrides: Mapped[list[ManualOverride]] = relationship(back_populates="item", order_by="ManualOverride.id")


class AnalysisVersion(Base):
    __tablename__ = "analysis_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("raw_items.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(50))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    cache_key: Mapped[str] = mapped_column(String(200), index=True)

    item: Mapped[RawItemRecord] = relationship(back_populates="analyses")


class ManualOverride(Base):
    __tablename__ = "manual_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("raw_items.id"), index=True)
    field: Mapped[str] = mapped_column(String(50))
    old_value: Mapped[Any] = mapped_column(JSON)
    new_value: Mapped[Any] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    user: Mapped[str] = mapped_column(String(100), default="demo-user")

    item: Mapped[RawItemRecord] = relationship(back_populates="overrides")


class RegulatoryCase(Base):
    __tablename__ = "regulatory_cases"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    identifier: Mapped[str | None] = mapped_column(String(200), index=True)
    title: Mapped[str] = mapped_column(Text)
    stage: Mapped[str | None] = mapped_column(String(200))
    effective_date: Mapped[date | None] = mapped_column(Date)
    next_checkpoint: Mapped[date | None] = mapped_column(Date)
    timeline: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    item_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
