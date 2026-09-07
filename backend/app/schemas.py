from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

SourceType = Literal["rss", "website", "regulator", "telegram", "manual", "unknown"]
Priority = Literal["high", "medium", "low"]
Category = Literal["regulation", "reputation", "competitors", "trends"]


class SourceRef(BaseModel):
    id: str | None = None
    name: str
    type: SourceType = "unknown"


class RawItem(BaseModel):
    id: str
    external_id: str | None = None
    source: SourceRef
    url: str | None = None
    title: str
    text: str
    author: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    language: str = "ru"
    attachments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    content_hash: str

    @field_validator("published_at", "fetched_at")
    @classmethod
    def aware_datetime(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class NormalizationError(BaseModel):
    index: int
    message: str


class NormalizationResult(BaseModel):
    accepted: list[RawItem] = Field(default_factory=list)
    errors: list[NormalizationError] = Field(default_factory=list)


class BusinessContext(BaseModel):
    products: list[str]
    topics: list[str]
    sensitive_risks: list[str]
    competitors: list[str]
    organizations: list[str]


class Evidence(BaseModel):
    claim: str
    quote: str


class RegulationInfo(BaseModel):
    identifier: str | None = None
    document_type: str | None = None
    stage: str | None = None
    effective_date: date | None = None
    next_checkpoint: date | None = None


class ArticleAnalysis(BaseModel):
    item_id: str
    entity_type: Literal["news", "regulation"]
    short_title: str
    summary: str
    who: list[str] = Field(default_factory=list)
    what: str
    when: str | None = None
    consequences: list[str] = Field(default_factory=list)
    affected_products: list[str] = Field(default_factory=list)
    primary_category: Category
    tags: list[str] = Field(default_factory=list)
    impact_type: Literal["risk", "opportunity", "mixed", "none", "unknown"]
    impact_for_gs_labs: str
    suggested_priority: Priority
    effective_priority: Priority
    priority_reason: str
    review_required: bool = False
    evidence: list[Evidence] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    regulation: RegulationInfo | None = None


class AnalyzeBatchRequest(BaseModel):
    item_ids: list[str] | None = None
    limit: int = Field(default=40, ge=1, le=100)


class ItemPatch(BaseModel):
    title: str | None = None
    summary: str | None = None
    primary_category: Category | None = None
    manual_priority: Priority | None = None
    tags: list[str] | None = None
    reason: str | None = None


class ManualItemCreate(BaseModel):
    title: str
    text: str
    url: str | None = None
    source_name: str = "Ручное добавление"
    published_at: datetime | None = None


class SourceCreate(BaseModel):
    name: str
    type: SourceType = "website"
    url: str | None = None
    enabled: bool = True
    poll_interval_minutes: int = Field(default=30, ge=1, le=1440)
    config: dict[str, Any] = Field(default_factory=dict)


class SourcePatch(BaseModel):
    name: str | None = None
    type: SourceType | None = None
    url: str | None = None
    enabled: bool | None = None
    poll_interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    config: dict[str, Any] | None = None


class PollRequest(BaseModel):
    """Ручной запуск опроса. Без `source_ids` опрашиваются источники по расписанию."""

    source_ids: list[str] | None = None
    force: bool = False
