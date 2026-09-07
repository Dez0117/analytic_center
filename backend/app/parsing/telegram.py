"""Публичные Telegram-каналы через веб-превью t.me/s/<channel>.

Превью не требует ключа Bot API и отдаёт последние посты канала. Приватные
каналы и каналы с выключенным превью честно возвращают ошибку опроса.
"""
from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlsplit

from app.parsing.base import FetchContext
from app.parsing.dates import parse_datetime
from app.parsing.documents import FetchedDocument, FetchError, FetchResult, SourceSpec
from app.parsing.htmltext import html_to_text
from app.parsing.http import fetch_page

PREVIEW_TEMPLATE = "https://t.me/s/{channel}"
MESSAGE_CLASS = "tgme_widget_message_text"
TITLE_MAX_CHARS = 120


def channel_name(value: str) -> str:
    """`@channel`, `channel`, `t.me/channel`, `t.me/s/channel` -> `channel`."""
    raw = (value or "").strip()
    if not raw:
        raise FetchError("У Telegram-источника не заполнен адрес канала")
    if raw.startswith("@"):
        return raw[1:]
    if "//" in raw or raw.startswith("t.me"):
        path = urlsplit(raw if "//" in raw else f"https://{raw}").path.strip("/")
        parts = [part for part in path.split("/") if part and part != "s"]
        if not parts:
            raise FetchError(f"Не удалось определить канал из адреса {value}")
        return parts[0]
    return raw.split("/")[0]


class _PostParser(HTMLParser):
    """Собирает id поста, текст и дату из разметки веб-превью."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.posts: dict[str, dict[str, str]] = {}
        self._post_id: str | None = None
        self._post_depth = 0
        self._depth = 0
        self._text_depth: int | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "div":
            self._depth += 1
            post_id = values.get("data-post")
            if post_id:
                self._post_id = post_id
                self._post_depth = self._depth
                self.posts.setdefault(post_id, {"text": "", "datetime": ""})
            elif MESSAGE_CLASS in values.get("class", "") and self._post_id and self._text_depth is None:
                self._text_depth = self._depth
                self._buffer = []
        elif tag == "br" and self._text_depth is not None:
            self._buffer.append("\n")
        elif tag == "time" and self._post_id and values.get("datetime"):
            self.posts[self._post_id]["datetime"] = self.posts[self._post_id]["datetime"] or values["datetime"]
        elif tag == "a" and self._text_depth is not None and values.get("href", "").startswith("http"):
            self._buffer.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag != "div":
            return
        if self._text_depth == self._depth and self._post_id:
            existing = self.posts[self._post_id]["text"]
            captured = "".join(self._buffer).strip()
            self.posts[self._post_id]["text"] = existing or captured
            self._text_depth = None
        if self._post_depth == self._depth:
            self._post_id = None
            self._post_depth = 0
        self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._text_depth is not None:
            self._buffer.append(data)


def parse_preview(html: str, channel: str) -> list[FetchedDocument]:
    """Разобрать страницу превью канала в документы."""
    parser = _PostParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - разметка превью иногда битая
        raise FetchError(f"Не удалось разобрать превью канала {channel}: {exc}") from exc

    documents: list[FetchedDocument] = []
    for post_id, payload in parser.posts.items():
        text = html_to_text(payload["text"]).strip()
        if not text:
            continue
        first_line = next((line for line in text.splitlines() if line.strip()), text)
        documents.append(FetchedDocument(
            title=first_line[:TITLE_MAX_CHARS].strip(),
            text=text,
            url=f"https://t.me/{post_id}",
            external_id=post_id,
            published_at=parse_datetime(payload["datetime"] or None),
            metadata={"fetcher": "telegram", "channel": channel, "post_id": post_id},
        ))
    return documents


class TelegramFetcher:
    """Опрос публичного канала одним GET к веб-превью."""

    name = "telegram"

    async def fetch(self, spec: SourceSpec, context: FetchContext) -> FetchResult:
        channel = channel_name(spec.option("channel") or spec.url)
        page = await fetch_page(
            context.client,
            PREVIEW_TEMPLATE.format(channel=channel),
            etag=spec.etag,
            last_modified=spec.last_modified,
        )
        if page.not_modified:
            return FetchResult(not_modified=True, etag=spec.etag, last_modified=spec.last_modified)
        documents = parse_preview(page.text, channel)
        limit = int(spec.option("max_items", context.settings.parser_max_items_per_poll))
        warnings = [] if documents else [
            f"У канала {channel} нет постов в превью: он приватный или превью отключено"
        ]
        return FetchResult(
            documents=documents[-limit:],
            warnings=warnings,
            etag=page.etag,
            last_modified=page.last_modified,
        )
