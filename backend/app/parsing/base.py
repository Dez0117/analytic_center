"""Общий контракт fetcher-ов."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

import httpx

from app.config import Settings
from app.parsing.documents import FetchResult, SourceSpec


@dataclass(slots=True)
class FetchContext:
    """Всё, что fetcher-у нужно снаружи, — без доступа к сессии БД.

    `is_known_url` позволяет не скачивать статьи, которые уже лежат в базе:
    отсечение повторов начинается ещё до загрузки страницы.
    """

    client: httpx.AsyncClient
    settings: Settings
    is_known_url: Callable[[str], bool] = field(default=lambda url: False)


class Fetcher(Protocol):
    name: str

    async def fetch(self, spec: SourceSpec, context: FetchContext) -> FetchResult: ...
