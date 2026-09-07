from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, get_db, init_db
from app.integrations.parser_adapter import normalize_parser_payload
from app.models import AnalysisVersion, EventCluster, ManualOverride, RawItemRecord, RegulatoryCase, Source
from app.parsing import PollingScheduler, get_fetcher, seed_default_sources
from app.schemas import (
    AnalyzeBatchRequest,
    ItemPatch,
    ManualItemCreate,
    PollRequest,
    SourceCreate,
    SourcePatch,
)
from app.services.analysis import analyze_record
from app.services.dedup import BaselineDeduplicator
from app.services.llm import make_provider
from app.services.storage import store_raw_items

settings = get_settings()
provider = make_provider(settings)
deduplicator = BaselineDeduplicator()
scheduler = PollingScheduler(SessionLocal, settings)
FIXTURES = Path(__file__).parents[2] / "fixtures"


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.parser_seed_defaults:
        with SessionLocal() as db:
            seed_default_sources(db)
    if settings.parser_enabled:
        await scheduler.start()
    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(title="GosRadar API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _store_items(db: Session, payload: Any) -> dict[str, Any]:
    normalized = normalize_parser_payload(payload)
    report = store_raw_items(db, normalized.accepted, deduplicator=deduplicator)
    ids = report.stored_ids + report.known_ids
    return {
        "accepted": len(ids),
        "item_ids": ids,
        "errors": [error.model_dump() for error in normalized.errors],
    }


def _latest_analysis(record: RawItemRecord) -> AnalysisVersion | None:
    return record.analyses[-1] if record.analyses else None


def _item_view(record: RawItemRecord) -> dict[str, Any]:
    latest = _latest_analysis(record)
    analysis = dict(latest.data) if latest and latest.data else None
    override_map: dict[str, Any] = {}
    for override in record.overrides:
        override_map[override.field] = override.new_value
    if analysis:
        for field in ("summary", "primary_category", "tags"):
            if field in override_map:
                analysis[field] = override_map[field]
    title = override_map.get("title", record.title)
    manual_priority = override_map.get("manual_priority")
    cluster_sources = [
        {
            "item_id": item.id,
            "source": item.source.name,
            "source_type": item.source.type,
            "url": item.url,
            "published_at": item.published_at,
        }
        for item in record.cluster.items
    ]
    return {
        "id": record.id,
        "cluster_id": record.cluster_id,
        "title": title,
        "text": record.text,
        "url": record.url,
        "author": record.author,
        "published_at": record.published_at,
        "fetched_at": record.fetched_at,
        "source": {"id": record.source.id, "name": record.source.name, "type": record.source.type},
        "hidden": record.hidden,
        "analysis": analysis,
        "manual_priority": manual_priority,
        "display_priority": manual_priority or (analysis or {}).get("effective_priority"),
        "cluster_sources": cluster_sources,
        "audit": [
            {
                "id": entry.id,
                "field": entry.field,
                "old_value": entry.old_value,
                "new_value": entry.new_value,
                "reason": entry.reason,
                "created_at": entry.created_at,
                "user": entry.user,
            }
            for entry in reversed(record.overrides)
        ],
        "analysis_meta": {
            "version": latest.id,
            "model": latest.model,
            "prompt_version": latest.prompt_version,
            "latency_ms": latest.latency_ms,
            "created_at": latest.created_at,
        } if latest else None,
    }


def _representatives(db: Session) -> list[RawItemRecord]:
    clusters = db.scalars(select(EventCluster)).all()
    representatives: list[RawItemRecord] = []
    for cluster in clusters:
        visible = [item for item in cluster.items if not item.hidden]
        if visible:
            representatives.append(max(visible, key=lambda item: (bool(item.analyses), item.fetched_at)))
    return sorted(representatives, key=lambda item: item.published_at or item.fetched_at, reverse=True)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "demo_mode": settings.use_mock, "provider": provider.model_name}


@app.post("/api/ingest/parser", status_code=status.HTTP_201_CREATED)
def ingest_parser(payload: Any = Body(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    return _store_items(db, payload)


@app.post("/api/demo/load", status_code=status.HTTP_201_CREATED)
def load_demo(db: Session = Depends(get_db)) -> dict[str, Any]:
    payload = json.loads((FIXTURES / "parser_payload_canonical.json").read_text(encoding="utf-8"))
    return _store_items(db, payload)


@app.post("/api/items/manual", status_code=status.HTTP_201_CREATED)
def create_manual(payload: ManualItemCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    result = _store_items(db, {
        "source": {"name": payload.source_name, "type": "manual"},
        "title": payload.title,
        "text": payload.text,
        "url": payload.url,
        "published_at": payload.published_at.isoformat() if payload.published_at else None,
    })
    if result["errors"]:
        raise HTTPException(status_code=422, detail=result["errors"])
    return _item_view(db.get(RawItemRecord, result["item_ids"][0]))


@app.get("/api/items")
def list_items(
    q: str | None = None,
    published_date: date | None = None,
    source: str | None = None,
    category: str | None = None,
    priority: str | None = None,
    entity_type: str | None = None,
    review_required: bool | None = None,
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    views = [_item_view(record) for record in _representatives(db)]
    if published_date:
        views = [view for view in views if (view["published_at"] or view["fetched_at"]).date() == published_date]
    if source:
        views = [view for view in views if view["source"]["id"] == source]
    if category:
        views = [view for view in views if (view["analysis"] or {}).get("primary_category") == category]
    if priority:
        views = [view for view in views if view["display_priority"] == priority]
    if entity_type:
        views = [view for view in views if (view["analysis"] or {}).get("entity_type") == entity_type]
    if review_required is not None:
        views = [view for view in views if bool((view["analysis"] or {}).get("review_required")) == review_required]
    if q:
        needle = q.casefold()
        views = [
            view for view in views
            if needle in " ".join([
                view["title"], view["text"], (view["analysis"] or {}).get("summary", ""),
                " ".join((view["analysis"] or {}).get("tags", [])),
                " ".join((view["analysis"] or {}).get("who", [])),
            ]).casefold()
        ]
    rank = {"high": 0, "medium": 1, "low": 2, None: 3}
    views.sort(key=lambda view: (
        rank[view["display_priority"]],
        -(view["published_at"] or view["fetched_at"]).timestamp(),
    ))
    return views


@app.get("/api/items/{item_id}")
def get_item(item_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    record = db.get(RawItemRecord, item_id)
    if not record:
        raise HTTPException(status_code=404, detail="Материал не найден")
    return _item_view(record)


@app.post("/api/items/{item_id}/analyze")
async def analyze_item(item_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    record = db.get(RawItemRecord, item_id)
    if not record:
        raise HTTPException(status_code=404, detail="Материал не найден")
    try:
        await analyze_record(db, record, provider)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка анализа: {exc}") from exc
    return _item_view(record)


@app.post("/api/items/{item_id}/reanalyze")
async def reanalyze_item(item_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    record = db.get(RawItemRecord, item_id)
    if not record:
        raise HTTPException(status_code=404, detail="Материал не найден")
    try:
        await analyze_record(db, record, provider, force=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Ошибка анализа: {exc}") from exc
    return _item_view(record)


@app.post("/api/items/analyze-batch")
async def analyze_batch(payload: AnalyzeBatchRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    if payload.item_ids:
        records = [record for item_id in payload.item_ids if (record := db.get(RawItemRecord, item_id))]
    else:
        records = db.scalars(select(RawItemRecord).order_by(RawItemRecord.fetched_at.desc()).limit(payload.limit)).all()
    results: list[dict[str, Any]] = []
    for record in records:
        try:
            version = await analyze_record(db, record, provider)
            results.append({"item_id": record.id, "ok": True, "analysis_version": version.id})
        except Exception as exc:
            results.append({"item_id": record.id, "ok": False, "error": str(exc)})
    return {"total": len(records), "succeeded": sum(result["ok"] for result in results), "results": results}


@app.patch("/api/items/{item_id}")
def patch_item(item_id: str, payload: ItemPatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    record = db.get(RawItemRecord, item_id)
    if not record:
        raise HTTPException(status_code=404, detail="Материал не найден")
    changes = payload.model_dump(exclude_none=True, exclude={"reason"})
    if not changes:
        raise HTTPException(status_code=422, detail="Нет полей для изменения")
    current = _item_view(record)
    for field, new_value in changes.items():
        if field == "title":
            old_value = current["title"]
        elif field == "manual_priority":
            old_value = current["manual_priority"]
        else:
            old_value = (current["analysis"] or {}).get(field)
        if old_value != new_value:
            db.add(ManualOverride(
                item_id=record.id,
                field=field,
                old_value=old_value,
                new_value=new_value,
                reason=payload.reason,
                user="demo-user",
            ))
    db.commit()
    db.refresh(record)
    return _item_view(record)


@app.post("/api/items/{item_id}/hide")
def hide_item(item_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    record = db.get(RawItemRecord, item_id)
    if not record:
        raise HTTPException(status_code=404, detail="Материал не найден")
    record.hidden = True
    db.add(ManualOverride(item_id=record.id, field="hidden", old_value=False, new_value=True, user="demo-user"))
    db.commit()
    return {"hidden": True}


@app.get("/api/regulations")
def list_regulations(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [
        {
            "id": case.id,
            "identifier": case.identifier,
            "title": case.title,
            "stage": case.stage,
            "effective_date": case.effective_date,
            "next_checkpoint": case.next_checkpoint,
            "timeline": case.timeline,
            "item_ids": case.item_ids,
        }
        for case in db.scalars(select(RegulatoryCase)).all()
    ]


def _item_counts(db: Session) -> dict[str, int]:
    """Сколько материалов собрано по каждому источнику."""
    rows = db.execute(
        select(RawItemRecord.source_id, func.count()).group_by(RawItemRecord.source_id)
    ).all()
    return {source_id: count for source_id, count in rows}


def _source_view(source: Source, items_count: int = 0) -> dict[str, Any]:
    return {
        "id": source.id,
        "items_count": items_count,
        "name": source.name,
        "type": source.type,
        "url": source.url,
        "enabled": source.enabled,
        "last_success": source.last_success,
        "last_error": source.last_error,
        "poll_interval_minutes": source.poll_interval_minutes,
        "config": source.config or {},
        "last_polled_at": source.last_polled_at,
        "last_status": source.last_status,
        "last_item_count": source.last_item_count,
        "pollable": get_fetcher((source.config or {}).get("fetcher") or source.type) is not None,
    }


@app.get("/api/sources")
def list_sources(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    counts = _item_counts(db)
    return [
        _source_view(source, counts.get(source.id, 0))
        for source in db.scalars(select(Source).order_by(Source.name)).all()
    ]


@app.post("/api/sources", status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    source_id = "src-" + hashlib.sha256(f"{payload.type}:{payload.name}:{payload.url}".casefold().encode()).hexdigest()[:16]
    if db.get(Source, source_id):
        raise HTTPException(status_code=409, detail="Источник уже существует")
    source = Source(id=source_id, **payload.model_dump())
    db.add(source)
    db.commit()
    return _source_view(source)


@app.patch("/api/sources/{source_id}")
def patch_source(source_id: str, payload: SourcePatch, db: Session = Depends(get_db)) -> dict[str, Any]:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Источник не найден")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(source, field, value)
    if {"url", "type", "config"} & set(payload.model_dump(exclude_none=True)):
        source.etag = None
        source.last_modified = None
    db.commit()
    return _source_view(source, _item_counts(db).get(source.id, 0))


@app.delete("/api/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: str, db: Session = Depends(get_db)) -> None:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Источник не найден")
    used = db.scalar(select(func.count()).select_from(RawItemRecord).where(RawItemRecord.source_id == source_id))
    if used:
        raise HTTPException(status_code=409, detail="Источник связан с материалами; отключите его вместо удаления")
    db.delete(source)
    db.commit()


@app.post("/api/sources/{source_id}/poll")
async def poll_single_source(source_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    if not db.get(Source, source_id):
        raise HTTPException(status_code=404, detail="Источник не найден")
    reports = await scheduler.run_sources([source_id])
    if not reports:
        raise HTTPException(status_code=404, detail="Источник не найден")
    return reports[0].as_dict()


@app.post("/api/sources/import-defaults", status_code=status.HTTP_201_CREATED)
def import_default_sources(db: Session = Depends(get_db)) -> dict[str, Any]:
    return seed_default_sources(db).as_dict()


@app.post("/api/parser/run")
async def run_parser(payload: PollRequest | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    request = payload or PollRequest()
    if request.source_ids:
        source_ids = request.source_ids
    elif request.force:
        source_ids = [
            source.id
            for source in db.scalars(select(Source).where(Source.enabled.is_(True))).all()
            if get_fetcher((source.config or {}).get("fetcher") or source.type) and source.url
        ]
    else:
        source_ids = scheduler.due_source_ids()
    reports = await scheduler.run_sources(source_ids)
    return {
        "polled": len(reports),
        "stored": sum(report.stored for report in reports),
        "duplicates": sum(report.duplicates for report in reports),
        "errors": sum(report.status == "error" for report in reports),
        "reports": [report.as_dict() for report in reports],
    }


@app.get("/api/parser/status")
def parser_status() -> dict[str, Any]:
    return scheduler.status()


@app.get("/api/stats")
def stats(db: Session = Depends(get_db)) -> dict[str, Any]:
    records = db.scalars(select(RawItemRecord)).all()
    representatives = _representatives(db)
    views = [_item_view(record) for record in representatives]
    latencies = db.scalars(select(AnalysisVersion.latency_ms)).all()
    return {
        "raw_items": len(records),
        "events": len(representatives),
        "analyzed": sum(bool(view["analysis"]) for view in views),
        "high": sum(view["display_priority"] == "high" for view in views),
        "review": sum(bool((view["analysis"] or {}).get("review_required")) for view in views),
        "regulations": db.scalar(select(func.count()).select_from(RegulatoryCase)) or 0,
        "average_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
        "demo_mode": settings.use_mock,
        "provider": provider.model_name,
    }
