"""Соответствие типа источника и fetcher-а."""
from __future__ import annotations

from app.parsing.base import Fetcher
from app.parsing.rss import RssFetcher
from app.parsing.telegram import TelegramFetcher
from app.parsing.website import WebsiteFetcher

FETCHERS: dict[str, Fetcher] = {
    "rss": RssFetcher(),
    "website": WebsiteFetcher(),
    "regulator": WebsiteFetcher(),
    "telegram": TelegramFetcher(),
}
POLLABLE_TYPES = frozenset(FETCHERS)


def get_fetcher(source_type: str) -> Fetcher | None:
    """Fetcher типа источника или None для `manual`/`unknown`."""
    return FETCHERS.get((source_type or "").casefold())
