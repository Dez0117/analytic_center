from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AnalysisVersion, RawItemRecord, RegulatoryCase
from app.schemas import ArticleAnalysis, BusinessContext, RawItem, SourceRef
from app.services.llm import LLMProvider, PROMPT_VERSION

PROFILE_PATH = Path(__file__).parents[2] / "config" / "gs_labs_profile.json"


def load_business_context() -> BusinessContext:
    return BusinessContext.model_validate_json(PROFILE_PATH.read_text(encoding="utf-8"))


def record_to_schema(record: RawItemRecord) -> RawItem:
    return RawItem(
        id=record.id,
        external_id=record.external_id,
        source=SourceRef(id=record.source.id, name=record.source.name, type=record.source.type),
        url=record.url,
        title=record.title,
        text=record.text,
        author=record.author,
        published_at=record.published_at,
        fetched_at=record.fetched_at,
        language=record.language,
        attachments=record.attachments,
        metadata=record.metadata_json,
        raw_payload=record.raw_payload,
        content_hash=record.content_hash,
    )


def _validate_and_apply_policy(analysis: ArticleAnalysis, item: RawItem, context: BusinessContext) -> ArticleAnalysis:
    diagnostics: list[str] = []
    source = re.sub(r"\s+", " ", item.text).casefold()
    if not analysis.evidence:
        diagnostics.append("Модель не вернула evidence")
    for evidence in analysis.evidence:
        quote = re.sub(r"\s+", " ", evidence.quote).casefold().strip()
        if not quote or quote not in source:
            diagnostics.append(f"Цитата не найдена в источнике: {evidence.quote[:80]}")
    sentence_count = len([part for part in re.split(r"(?<=[.!?])\s+", analysis.summary.strip()) if part])
    if not 3 <= sentence_count <= 5:
        diagnostics.append(f"Саммари содержит {sentence_count} предложений вместо 3–5")

    sensitive_terms = [term for term in context.sensitive_risks if term.casefold() in source]
    if analysis.suggested_priority == "low" and sensitive_terms:
        analysis.effective_priority = "medium"
        analysis.review_required = True
        diagnostics.append("Policy не разрешила low: " + ", ".join(sensitive_terms))
    else:
        analysis.effective_priority = analysis.suggested_priority
    if diagnostics:
        analysis.review_required = True
        analysis.uncertainties = list(dict.fromkeys([*analysis.uncertainties, *diagnostics]))
    return analysis


def _update_regulatory_case(db: Session, record: RawItemRecord, analysis: ArticleAnalysis) -> None:
    if analysis.entity_type != "regulation" or not analysis.regulation:
        return
    info = analysis.regulation
    case_id = info.identifier or f"reg-{record.id}"
    case = db.get(RegulatoryCase, case_id)
    event = {
        "item_id": record.id,
        "date": (record.published_at or record.fetched_at).date().isoformat(),
        "stage": info.stage or "не указано",
        "title": analysis.short_title,
        "url": record.url,
    }
    if not case:
        case = RegulatoryCase(
            id=case_id,
            identifier=info.identifier,
            title=analysis.short_title,
            stage=info.stage,
            effective_date=info.effective_date,
            next_checkpoint=info.next_checkpoint,
            timeline=[event],
            item_ids=[record.id],
        )
        db.add(case)
    elif record.id not in case.item_ids:
        case.title = analysis.short_title
        case.stage = info.stage or case.stage
        case.effective_date = info.effective_date or case.effective_date
        case.next_checkpoint = info.next_checkpoint or case.next_checkpoint
        case.timeline = [*case.timeline, event]
        case.item_ids = [*case.item_ids, record.id]


async def analyze_record(db: Session, record: RawItemRecord, provider: LLMProvider, force: bool = False) -> AnalysisVersion:
    cache_key = hashlib.sha256(f"{record.content_hash}:{PROMPT_VERSION}:{provider.model_name}".encode()).hexdigest()
    if not force:
        cached = db.scalar(select(AnalysisVersion).where(
            AnalysisVersion.cache_key == cache_key,
            AnalysisVersion.error.is_(None),
            AnalysisVersion.item_id == record.id,
        ).order_by(AnalysisVersion.id.desc()))
        if cached:
            return cached
        shared = db.scalar(select(AnalysisVersion).where(
            AnalysisVersion.cache_key == cache_key,
            AnalysisVersion.error.is_(None),
        ).order_by(AnalysisVersion.id.desc()))
        if shared:
            data = dict(shared.data)
            data["item_id"] = record.id
            cached = AnalysisVersion(
                item_id=record.id,
                data=data,
                model=shared.model,
                prompt_version=shared.prompt_version,
                latency_ms=0,
                error=None,
                cache_key=cache_key,
            )
            db.add(cached)
            db.commit()
            db.refresh(cached)
            return cached
    started = time.perf_counter()
    try:
        context = load_business_context()
        analysis = await provider.analyze(record_to_schema(record), context)
        analysis = _validate_and_apply_policy(analysis, record_to_schema(record), context)
        data = analysis.model_dump(mode="json")
    except Exception as exc:
        failed = AnalysisVersion(
            item_id=record.id,
            data={},
            model=provider.model_name,
            prompt_version=PROMPT_VERSION,
            latency_ms=round((time.perf_counter() - started) * 1000),
            error=str(exc),
            cache_key=cache_key,
        )
        db.add(failed)
        db.commit()
        raise
    version = AnalysisVersion(
        item_id=record.id,
        data=data,
        model=provider.model_name,
        prompt_version=PROMPT_VERSION,
        latency_ms=round((time.perf_counter() - started) * 1000),
        error=None,
        cache_key=cache_key,
    )
    db.add(version)
    _update_regulatory_case(db, record, analysis)
    db.commit()
    db.refresh(version)
    return version
