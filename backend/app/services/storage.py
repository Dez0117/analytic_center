"""Сохранение нормализованных материалов и отсечение повторов.

Два разных режима намеренно разведены:

* ingest из внешнего парсера (`/api/ingest/parser`) сохраняет все присланные
  записи и складывает совпадающие в один event cluster — источники видны все;
* модуль парсинга (`skip_duplicates=True`) отсекает повтор по каноническому URL
  и хешу текста ещё до записи, чтобы один материал не приезжал дважды из
  разных каналов и не дублировался на каждом опросе.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import RawItemRecord, Source
from app.schemas import RawItem
from app.services.dedup import BaselineDeduplicator, Deduplicator


@dataclass(slots=True)
class DuplicateSkip:
    item_id: str
    reason: str
    url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"item_id": self.item_id, "reason": self.reason, "url": self.url}


@dataclass(slots=True)
class StoreReport:
    stored_ids: list[str] = field(default_factory=list)
    known_ids: list[str] = field(default_factory=list)
    duplicates: list[DuplicateSkip] = field(default_factory=list)

    @property
    def stored(self) -> int:
        return len(self.stored_ids)

    @property
    def skipped(self) -> int:
        return len(self.known_ids) + len(self.duplicates)


def source_id_for(item: RawItem) -> str:
    """Стабильный id источника: из payload или из пары тип+имя."""
    return item.source.id or "src-" + hashlib.sha256(
        f"{item.source.type}:{item.source.name}".casefold().encode()
    ).hexdigest()[:16]


def _upsert_source(db: Session, item: RawItem) -> Source:
    source_id = source_id_for(item)
    source = db.get(Source, source_id)
    if source is None:
        source = Source(
            id=source_id,
            name=item.source.name,
            type=item.source.type,
            url=item.url,
            enabled=True,
            last_success=datetime.now(timezone.utc),
        )
        db.add(source)
        db.flush()
    else:
        source.last_success = datetime.now(timezone.utc)
    return source


def _existing_urls_and_hashes(db: Session, items: list[RawItem]) -> tuple[set[str], set[str]]:
    urls = {item.url for item in items if item.url}
    hashes = {item.content_hash for item in items}
    known_urls: set[str] = set()
    if urls:
        known_urls = {url for url in db.scalars(select(RawItemRecord.url).where(RawItemRecord.url.in_(urls))) if url}
    known_hashes: set[str] = set(
        db.scalars(select(RawItemRecord.content_hash).where(RawItemRecord.content_hash.in_(hashes)))
    )
    return known_urls, known_hashes


def store_raw_items(
    db: Session,
    items: Iterable[RawItem],
    *,
    deduplicator: Deduplicator | None = None,
    skip_duplicates: bool = False,
    commit: bool = True,
) -> StoreReport:
    """Записать материалы; вернуть, что сохранено и что отсечено как повтор."""
    clusterer = deduplicator or BaselineDeduplicator()
    batch = list(items)
    report = StoreReport()
    known_urls, known_hashes = _existing_urls_and_hashes(db, batch) if skip_duplicates else (set(), set())

    for item in batch:
        if db.get(RawItemRecord, item.id) is not None:
            report.known_ids.append(item.id)
            continue
        if skip_duplicates:
            if item.url and item.url in known_urls:
                report.duplicates.append(DuplicateSkip(item.id, "url", item.url))
                continue
            if item.content_hash in known_hashes:
                report.duplicates.append(DuplicateSkip(item.id, "content_hash", item.url))
                continue
        source = _upsert_source(db, item)
        cluster = clusterer.cluster_for(db, item)
        db.add(RawItemRecord(
            id=item.id,
            external_id=item.external_id,
            source_id=source.id,
            cluster_id=cluster.id,
            url=item.url,
            title=item.title,
            text=item.text,
            author=item.author,
            published_at=item.published_at,
            fetched_at=item.fetched_at,
            language=item.language,
            attachments=item.attachments,
            metadata_json=item.metadata,
            raw_payload=item.raw_payload,
            content_hash=item.content_hash,
        ))
        db.flush()
        report.stored_ids.append(item.id)
        if item.url:
            known_urls.add(item.url)
        known_hashes.add(item.content_hash)

    if commit:
        db.commit()
    return report
