"""Периодический опрос источников.

Планировщик живёт в event loop приложения: каждые `parser_tick_seconds` он
выбирает источники, у которых истёк их собственный интервал, и опрашивает их
с ограничением параллелизма. Список источников читается из БД на каждом тике,
поэтому правки через интерфейс подхватываются без перезапуска.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Sequence

import httpx
from sqlalchemy import select

from app.config import Settings
from app.models import Source
from app.parsing.http import build_client
from app.parsing.poller import PollReport, SessionFactory, poll_source
from app.parsing.registry import POLLABLE_TYPES

logger = logging.getLogger(__name__)
ClientFactory = Callable[[Settings], httpx.AsyncClient]


def is_due(source: Source, now: datetime, default_interval: int) -> bool:
    """Пора ли опрашивать источник по его собственному интервалу."""
    if not source.enabled or not (source.url or "").strip():
        return False
    if source.type not in POLLABLE_TYPES and not (source.config or {}).get("fetcher"):
        return False
    if source.last_polled_at is None:
        return True
    last = source.last_polled_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    minutes = source.poll_interval_minutes or default_interval
    return now - last >= timedelta(minutes=max(1, minutes))


class PollingScheduler:
    """Фоновая задача опроса плюс ручной запуск из API."""

    def __init__(
        self,
        session_factory: SessionFactory,
        settings: Settings,
        *,
        client_factory: ClientFactory = build_client,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        # Публичный шов для подмены транспорта в тестах и для DI.
        self.client_factory = client_factory
        self._task: asyncio.Task[None] | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self.started_at: datetime | None = None
        self.last_run_at: datetime | None = None
        self.runs = 0
        self.last_reports: list[PollReport] = []

    def _serial_lock(self) -> asyncio.Lock:
        """Замок принадлежит текущему event loop: приложение и тесты используют разные."""
        loop = asyncio.get_running_loop()
        lock = self._lock
        if lock is None or self._lock_loop is not loop:
            lock, self._lock_loop = asyncio.Lock(), loop
            self._lock = lock
        return lock

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        self.started_at = datetime.now(timezone.utc)
        self._task = asyncio.create_task(self._loop(), name="gs-radar-parser")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _loop(self) -> None:
        while True:
            try:
                await self.run_due()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - цикл переживает любую ошибку тика
                logger.exception("Тик планировщика парсинга завершился ошибкой")
            await asyncio.sleep(self._settings.parser_tick_seconds)

    def due_source_ids(self, now: datetime | None = None) -> list[str]:
        moment = now or datetime.now(timezone.utc)
        with self._session_factory() as db:
            sources = db.scalars(select(Source).where(Source.enabled.is_(True))).all()
            return [
                source.id
                for source in sources
                if is_due(source, moment, self._settings.parser_default_interval_minutes)
            ]

    async def run_due(self, now: datetime | None = None) -> list[PollReport]:
        """Опросить источники, у которых истёк интервал."""
        return await self.run_sources(self.due_source_ids(now))

    async def run_sources(self, source_ids: Sequence[str]) -> list[PollReport]:
        """Опросить конкретные источники независимо от их расписания."""
        if not source_ids:
            self.last_run_at = datetime.now(timezone.utc)
            return []
        async with self._serial_lock():
            reports = await self._poll_all(source_ids)
        self.runs += 1
        self.last_run_at = datetime.now(timezone.utc)
        self.last_reports = reports
        return reports

    async def _poll_all(self, source_ids: Iterable[str]) -> list[PollReport]:
        semaphore = asyncio.Semaphore(self._settings.parser_concurrency)
        async with self.client_factory(self._settings) as client:

            async def poll(source_id: str) -> PollReport | None:
                async with semaphore:
                    try:
                        return await poll_source(
                            self._session_factory, source_id, client=client, settings=self._settings
                        )
                    except LookupError:
                        logger.warning("Источник %s исчез до опроса", source_id)
                        return None

            results = await asyncio.gather(*(poll(source_id) for source_id in source_ids))
        return [report for report in results if report is not None]

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self._settings.parser_enabled,
            "running": self.running,
            "tick_seconds": self._settings.parser_tick_seconds,
            "default_interval_minutes": self._settings.parser_default_interval_minutes,
            "started_at": self.started_at,
            "last_run_at": self.last_run_at,
            "runs": self.runs,
            "due_now": len(self.due_source_ids()),
            "last_reports": [report.as_dict() for report in self.last_reports],
        }
