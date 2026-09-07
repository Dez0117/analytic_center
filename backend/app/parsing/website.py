"""Сайты регуляторов и обычные новостные разделы без RSS.

Стратегия простая и проверяемая: страница-индекс -> ссылки-кандидаты ->
страницы материалов. Известные URL не скачиваются повторно, поэтому обычный
опрос стоит один GET, пока на сайте нет новых публикаций.
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import urlsplit

from app.parsing.base import FetchContext
from app.parsing.dates import parse_datetime
from app.parsing.documents import FetchedDocument, FetchError, FetchResult, SourceSpec
from app.parsing.htmltext import DEFAULT_MIN_LINE_CHARS, extract_article, extract_links
from app.parsing.http import fetch_page

BINARY_SUFFIXES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".rar", ".jpg", ".jpeg", ".png", ".gif", ".mp4")
DEFAULT_MIN_ANCHOR_CHARS = 20
DEFAULT_MIN_TEXT_CHARS = 200


def _candidate_links(spec: SourceSpec, index_url: str, html: str) -> tuple[list[str], list[str]]:
    """Ссылки, похожие на материалы, и предупреждения о настройках источника."""
    warnings: list[str] = []
    pattern: re.Pattern[str] | None = None
    raw_pattern = spec.option("link_pattern")
    if raw_pattern:
        try:
            pattern = re.compile(raw_pattern)
        except re.error as exc:
            warnings.append(f"link_pattern не скомпилирован ({exc}), используется фильтр по домену")
    same_host = bool(spec.option("same_host", True))
    min_anchor = int(spec.option("min_anchor_chars", DEFAULT_MIN_ANCHOR_CHARS))
    host = urlsplit(index_url).netloc.casefold()

    links: list[str] = []
    for url, anchor in extract_links(html, index_url):
        if url.rstrip("/") == index_url.rstrip("/") or url.casefold().endswith(BINARY_SUFFIXES):
            continue
        if same_host and urlsplit(url).netloc.casefold() != host:
            continue
        if pattern is not None:
            if not pattern.search(url):
                continue
        elif len(anchor) < min_anchor:
            continue
        links.append(url)
    return links, warnings


async def load_article(url: str, spec: SourceSpec, context: FetchContext) -> FetchedDocument | str:
    """Документ или текст предупреждения, если страница не пригодилась.

    Используется и опросом сайта, и дозагрузкой полного текста для RSS.
    """
    min_text = int(spec.option("min_text_chars", DEFAULT_MIN_TEXT_CHARS))
    try:
        page = await fetch_page(context.client, url)
    except FetchError as exc:
        return str(exc)
    article = extract_article(
        page.text,
        max_chars=int(spec.option("max_text_chars", 20000)),
        min_line_chars=int(spec.option("min_line_chars", DEFAULT_MIN_LINE_CHARS)),
    )
    if len(article.text) < min_text:
        return f"{url}: текст короче {min_text} символов, страница пропущена"
    return FetchedDocument(
        title=article.title or article.text[:120],
        text=article.text,
        url=page.url,
        external_id=page.url,
        author=article.author,
        published_at=parse_datetime(article.published_raw),
        metadata={"fetcher": "website", "index_url": spec.url},
    )


class WebsiteFetcher:
    """Опрос сайта: индекс + страницы материалов с ограничением параллелизма."""

    name = "website"

    async def fetch(self, spec: SourceSpec, context: FetchContext) -> FetchResult:
        index = await fetch_page(context.client, spec.url, etag=spec.etag, last_modified=spec.last_modified)
        if index.not_modified:
            return FetchResult(not_modified=True, etag=spec.etag, last_modified=spec.last_modified)

        links, warnings = _candidate_links(spec, index.url, index.text)
        fresh = [url for url in links if not context.is_known_url(url)]
        limit = int(spec.option("max_items", context.settings.parser_max_items_per_poll))
        selected = fresh[:limit]

        semaphore = asyncio.Semaphore(context.settings.parser_concurrency)

        async def load(url: str) -> FetchedDocument | str:
            async with semaphore:
                return await load_article(url, spec, context)

        outcomes = await asyncio.gather(*(load(url) for url in selected))
        documents = [outcome for outcome in outcomes if isinstance(outcome, FetchedDocument)]
        warnings.extend(outcome for outcome in outcomes if isinstance(outcome, str))
        if not links:
            warnings.append(f"На {spec.url} не найдено ссылок-кандидатов; уточните link_pattern")
        return FetchResult(
            documents=documents,
            warnings=warnings,
            etag=index.etag,
            last_modified=index.last_modified,
            skipped_known=len(links) - len(fresh),
        )
