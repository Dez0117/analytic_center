"""RSS 2.0 и Atom: СМИ, новостные агрегаторы и ленты регуляторов."""
from __future__ import annotations

from xml.etree import ElementTree

import asyncio
from dataclasses import replace

from app.parsing.base import FetchContext
from app.parsing.dates import parse_datetime
from app.parsing.documents import FetchedDocument, FetchError, FetchResult, SourceSpec
from app.parsing.htmltext import html_to_text
from app.parsing.http import fetch_page
from app.parsing.website import load_article

CONTENT_NS = "{http://purl.org/rss/1.0/modules/content/}"
DC_NS = "{http://purl.org/dc/elements/1.1/}"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ENTRY_TAGS = ("item", "entry")
TITLE_TAGS = ("title",)
TEXT_TAGS = (f"{CONTENT_NS}encoded", "content", "description", "summary", "subtitle")
DATE_TAGS = ("pubDate", "published", "updated", f"{DC_NS}date", "date")
AUTHOR_TAGS = ("author", f"{DC_NS}creator", "creator")
ID_TAGS = ("guid", "id")


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find(element: ElementTree.Element, names: tuple[str, ...]) -> ElementTree.Element | None:
    wanted = [_localname(name) for name in names]
    children = {_localname(child.tag): child for child in reversed(list(element))}
    for name in wanted:
        child = children.get(name)
        # Узел с вложенными элементами (atom `<author><name>`) собственного текста не имеет.
        if child is not None and ((child.text or "").strip() or len(child)):
            return child
    return None


def _value(element: ElementTree.Element, names: tuple[str, ...]) -> str | None:
    child = _find(element, names)
    if child is None:
        return None
    if _localname(child.tag) == "author" and (name := _find(child, ("name",))) is not None:
        return (name.text or "").strip()
    return (child.text or "").strip() or None


def _link(entry: ElementTree.Element) -> str | None:
    alternate: str | None = None
    for child in entry:
        if _localname(child.tag) != "link":
            continue
        href = child.get("href")
        if href and child.get("rel", "alternate") == "alternate":
            return href.strip()
        alternate = alternate or href
        if (child.text or "").strip():
            return child.text.strip()
    if alternate:
        return alternate
    guid = _find(entry, ("guid",))
    if guid is not None and (guid.text or "").startswith("http"):
        return guid.text.strip()
    return None


def _attachments(entry: ElementTree.Element) -> tuple[str, ...]:
    urls = [
        child.get("url") or child.get("href") or ""
        for child in entry
        if _localname(child.tag) in ("enclosure", "content") and (child.get("url") or child.get("href"))
    ]
    return tuple(url for url in urls if url.startswith("http"))


def parse_feed(xml_text: str) -> list[FetchedDocument]:
    """Разобрать ленту в документы. Пустые по тексту записи пропускаются."""
    try:
        root = ElementTree.fromstring(xml_text.strip())
    except ElementTree.ParseError as exc:
        raise FetchError(f"Лента не является валидным XML: {exc}") from exc

    documents: list[FetchedDocument] = []
    for entry in (node for node in root.iter() if _localname(node.tag) in ENTRY_TAGS):
        title = html_to_text(_value(entry, TITLE_TAGS) or "")
        body = html_to_text(_value(entry, TEXT_TAGS) or "")
        text = body or title
        if not text:
            continue
        url = _link(entry)
        guid = _value(entry, ID_TAGS)
        documents.append(FetchedDocument(
            title=title or text[:120],
            text=text,
            url=url,
            external_id=guid or url,
            author=_value(entry, AUTHOR_TAGS),
            published_at=parse_datetime(_value(entry, DATE_TAGS)),
            attachments=_attachments(entry),
            metadata={"fetcher": "rss"},
        ))
    return documents


DEFAULT_FULL_TEXT_THRESHOLD = 400


async def _with_full_text(
    documents: list[FetchedDocument],
    spec: SourceSpec,
    context: FetchContext,
) -> tuple[list[FetchedDocument], list[str]]:
    """Дозагрузить текст материалов для лент, отдающих только заголовок.

    Включается опцией источника `fetch_full_text`. Уже известные ссылки не
    скачиваются: их всё равно отсечёт дедупликация.
    """
    threshold = int(spec.option("full_text_threshold", DEFAULT_FULL_TEXT_THRESHOLD))
    semaphore = asyncio.Semaphore(context.settings.parser_concurrency)

    async def enrich(document: FetchedDocument) -> tuple[FetchedDocument, str | None]:
        if not document.url or len(document.text) >= threshold or context.is_known_url(document.url):
            return document, None
        async with semaphore:
            outcome = await load_article(document.url, spec, context)
        if isinstance(outcome, str):
            return document, outcome
        return replace(
            document,
            text=outcome.text,
            title=document.title or outcome.title,
            author=document.author or outcome.author,
            published_at=document.published_at or outcome.published_at,
            metadata={**document.metadata, "full_text": True},
        ), None

    results = await asyncio.gather(*(enrich(document) for document in documents))
    return [document for document, _ in results], [warning for _, warning in results if warning]


class RssFetcher:
    """Опрос RSS/Atom-ленты одним условным GET."""

    name = "rss"

    async def fetch(self, spec: SourceSpec, context: FetchContext) -> FetchResult:
        page = await fetch_page(context.client, spec.url, etag=spec.etag, last_modified=spec.last_modified)
        if page.not_modified:
            return FetchResult(not_modified=True, etag=spec.etag, last_modified=spec.last_modified)
        documents = parse_feed(page.text)
        limit = int(spec.option("max_items", context.settings.parser_max_items_per_poll))
        documents = documents[:limit]
        warnings = [] if documents else [f"В ленте {spec.url} не найдено записей"]
        if documents and spec.option("fetch_full_text"):
            documents, enrich_warnings = await _with_full_text(documents, spec, context)
            warnings.extend(enrich_warnings)
        return FetchResult(
            documents=documents,
            etag=page.etag,
            last_modified=page.last_modified,
            warnings=warnings,
        )
