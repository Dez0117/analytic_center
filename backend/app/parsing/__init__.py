"""Модуль парсинга: опрос источников из БД по расписанию и запись новых материалов.

Публичная поверхность модуля намеренно узкая — планировщик, разовый опрос
источника и посев списка источников по умолчанию.
"""
from app.parsing.documents import FetchedDocument, FetchError, FetchResult, SourceSpec
from app.parsing.poller import PollReport, poll_source
from app.parsing.registry import POLLABLE_TYPES, get_fetcher
from app.parsing.scheduler import PollingScheduler, is_due
from app.parsing.seed import DEFAULT_SOURCES_PATH, seed_default_sources

__all__ = [
    "DEFAULT_SOURCES_PATH",
    "FetchError",
    "FetchResult",
    "FetchedDocument",
    "POLLABLE_TYPES",
    "PollReport",
    "PollingScheduler",
    "SourceSpec",
    "get_fetcher",
    "is_due",
    "poll_source",
    "seed_default_sources",
]
