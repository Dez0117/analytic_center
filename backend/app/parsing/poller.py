"""Один опрос одного источника: сеть -> нормализация -> запись -> статус.

Сессия БД не удерживается на время сетевых вызовов: сначала читаются настройки
источника, затем идёт загрузка, и только потом открывается сессия на запись.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.parser_adapter import canonical_url, normalize_parser_payload
from app.models import RawItemRecord, Source
from app.parsing.base import FetchContext
from app.parsing.documents import FetchError, FetchResult, SourceSpec
from app.parsing.registry import get_fetcher
from app.services.storage import store_raw_items

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


@dataclass(slots=True)
class PollReport:
    """Результат опроса: то, что видит пользователь в интерфейсе источников."""

    source_id: str
    source_name: str
    source_type: str
    status: str = "ok"
    fetched: int = 0
    stored: int = 0
    duplicates: int = 0
    known: int = 0
    invalid: int = 0
    skipped_known_urls: int = 0
    item_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    duration_ms: int = 0
    polled_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_type": self.source_type,
            "status": self.status,
            "fetched": self.fetched,
            "stored": self.stored,
            "duplicates": self.duplicates,
            "known": self.known,
            "invalid": self.invalid,
            "skipped_known_urls": self.skipped_known_urls,
            "item_ids": self.item_ids,
            "warnings": self.warnings,
            "error": self.error,
            "duration_ms": self.duration_ms,
            "polled_at": self.polled_at,
        }


def known_urls(db: Session) -> frozenset[str]:
    """Канонические URL, уже лежащие в базе: их не нужно скачивать заново."""
    return frozenset(url for url in db.scalars(select(RawItemRecord.url)) if url)


def _known_url_check(seen: frozenset[str]) -> Callable[[str], bool]:
    """Ссылку с индексной страницы приводим к той же канонической форме, что и в БД."""
    return lambda url: canonical_url(url) in seen


def _resolve_fetcher(spec: SourceSpec):
    """Тип источника задаёт fetcher; `config.fetcher` переопределяет его.

    Нужно для лент регуляторов: тип остаётся `regulator`, а разбор идёт как RSS.
    """
    override = spec.option("fetcher")
    return get_fetcher(override) if override else get_fetcher(spec.type)


def _apply_status(source: Source, report: PollReport, result: FetchResult | None) -> None:
    source.last_polled_at = report.polled_at
    source.last_status = report.status
    source.last_item_count = report.stored
    if report.status == "error":
        source.last_error = report.error
        return
    source.last_error = None
    source.last_success = report.polled_at
    if result is not None and not result.not_modified:
        source.etag = result.etag
        source.last_modified = result.last_modified


async def poll_source(
    session_factory: SessionFactory,
    source_id: str,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> PollReport:
    """Опросить источник и записать новые материалы. Исключения не выбрасывает."""
    started = datetime.now(timezone.utc)
    with session_factory() as db:
        source = db.get(Source, source_id)
        if source is None:
            raise LookupError(f"Источник {source_id} не найден")
        spec = SourceSpec.from_record(source)
        report = PollReport(source_id=source.id, source_name=source.name, source_type=source.type)
        seen = known_urls(db)

    fetcher = _resolve_fetcher(spec)
    if fetcher is None or not spec.url:
        report.status = "skipped"
        report.error = (
            f"Тип «{spec.type}» опрашивается вручную" if fetcher is None else "У источника не заполнен URL"
        )
        return _finalize(session_factory, report, None, started)

    context = FetchContext(client=client, settings=settings, is_known_url=_known_url_check(seen))
    try:
        result = await fetcher.fetch(spec, context)
    except FetchError as exc:
        report.status, report.error = "error", str(exc)
        return _finalize(session_factory, report, None, started)
    except Exception as exc:  # noqa: BLE001 - падение одного источника не должно ронять цикл
        logger.exception("Опрос источника %s завершился ошибкой", spec.id)
        report.status, report.error = "error", f"{type(exc).__name__}: {exc}"
        return _finalize(session_factory, report, None, started)

    report.warnings = list(result.warnings)
    report.skipped_known_urls = result.skipped_known
    if result.not_modified:
        report.status = "not_modified"
        return _finalize(session_factory, report, result, started)

    report.fetched = len(result.documents)
    payload = [document.to_payload(spec, started) for document in result.documents]
    normalized = normalize_parser_payload(payload)
    report.invalid = len(normalized.errors)
    report.warnings.extend(f"Запись {error.index}: {error.message}" for error in normalized.errors)

    with session_factory() as db:
        stored = store_raw_items(db, normalized.accepted, skip_duplicates=True, commit=False)
        report.stored = stored.stored
        report.item_ids = stored.stored_ids
        report.duplicates = len(stored.duplicates)
        report.known = len(stored.known_ids)
        source = db.get(Source, spec.id)
        if source is not None:
            _apply_status(source, report, result)
        db.commit()
    report.duration_ms = _elapsed_ms(started)
    return report


def _finalize(
    session_factory: SessionFactory,
    report: PollReport,
    result: FetchResult | None,
    started: datetime,
) -> PollReport:
    """Записать статус опроса, когда материалов не появилось."""
    report.duration_ms = _elapsed_ms(started)
    with session_factory() as db:
        source = db.get(Source, report.source_id)
        if source is not None:
            _apply_status(source, report, result)
            db.commit()
    return report


def _elapsed_ms(started: datetime) -> int:
    return int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
