"""Извлечение текста и ссылок из HTML средствами стандартной библиотеки.

Специально без внешних зависимостей: нужен предсказуемый и объяснимый
baseline, а не полноценный readability. Всё, что вырезается, перечислено
в `SKIP_TAGS` — это правило можно прочитать и оспорить.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin

SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe"})
BLOCK_TAGS = frozenset({
    "p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "td", "th", "pre", "figcaption",
})
MAIN_TAGS = ("article", "main")
SENTENCE_ENDINGS = (".", "!", "?", "…", "»", ":", ";")
DEFAULT_MIN_LINE_CHARS = 40
_WHITESPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n{3,}")


def _clean(value: str) -> str:
    return _BLANK_LINES.sub("\n\n", _WHITESPACE.sub(" ", value)).strip()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._parts.append(data)

    @property
    def text(self) -> str:
        lines = [_WHITESPACE.sub(" ", line).strip() for line in "".join(self._parts).split("\n")]
        return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line)).strip()


def html_to_text(value: str | None) -> str:
    """HTML -> плоский текст. Пустая строка, если разобрать нечего."""
    if not value:
        return ""
    parser = _TextParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception:  # HTMLParser падает только на совсем битой разметке
        return _clean(re.sub(r"<[^>]+>", " ", value))
    return parser.text


@dataclass(slots=True)
class Article:
    title: str
    text: str
    published_raw: str | None = None
    author: str | None = None
    meta: dict[str, str] = field(default_factory=dict)


class _ArticleParser(HTMLParser):
    """Собирает заголовок, метатеги и текст основной части страницы."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title = ""
        self.time_value: str | None = None
        self._skip_depth = 0
        self._capture: str | None = None
        self._buffer: list[str] = []
        self._main_depth = 0
        self._main_parts: list[str] = []
        self._body_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "meta":
            name = (values.get("property") or values.get("name") or "").lower()
            if name and values.get("content"):
                self.meta.setdefault(name, values["content"].strip())
        elif tag == "time" and not self.time_value:
            self.time_value = values.get("datetime") or None
        if tag in SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag in MAIN_TAGS or "article" in values.get("itemprop", ""):
            self._main_depth += 1
        if tag in ("title", "h1") and not self._capture:
            self._capture = tag
            self._buffer = []
        if tag in BLOCK_TAGS:
            self._append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in MAIN_TAGS and self._main_depth:
            self._main_depth -= 1
        if self._capture == tag:
            candidate = _WHITESPACE.sub(" ", "".join(self._buffer)).strip()
            if candidate and (not self.title or tag == "h1"):
                self.title = candidate
            self._capture = None
        if tag in BLOCK_TAGS:
            self._append("\n")

    def handle_data(self, data: str) -> None:
        if self._capture:
            self._buffer.append(data)
        if not self._skip_depth:
            self._append(data)

    def _append(self, chunk: str) -> None:
        self._body_parts.append(chunk)
        if self._main_depth:
            self._main_parts.append(chunk)

    @staticmethod
    def _finish(parts: list[str]) -> str:
        lines = [_WHITESPACE.sub(" ", line).strip() for line in "".join(parts).split("\n")]
        return _BLANK_LINES.sub("\n\n", "\n".join(line for line in lines if line)).strip()

    @property
    def article_text(self) -> str:
        main = self._finish(self._main_parts)
        body = self._finish(self._body_parts)
        return main if len(main) >= 200 else body


def extract_article(html: str, *, max_chars: int = 20000, min_line_chars: int = DEFAULT_MIN_LINE_CHARS) -> Article:
    """Заголовок и основной текст страницы; `<article>`/`<main>` в приоритете."""
    parser = _ArticleParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return Article(title="", text=_clean(re.sub(r"<[^>]+>", " ", html))[:max_chars])
    title = parser.meta.get("og:title") or parser.title
    text = drop_boilerplate(parser.article_text, min_line_chars)
    description = parser.meta.get("og:description") or parser.meta.get("description")
    if description and len(text) < len(description):
        text = description
    published = (
        parser.meta.get("article:published_time")
        or parser.meta.get("published_time")
        or parser.meta.get("pubdate")
        or parser.time_value
    )
    return Article(
        title=title.strip(),
        text=text[:max_chars].strip(),
        published_raw=published,
        author=parser.meta.get("author") or parser.meta.get("article:author"),
        meta=parser.meta,
    )


def drop_boilerplate(text: str, min_line_chars: int = DEFAULT_MIN_LINE_CHARS) -> str:
    """Убрать навигацию сайта из извлечённого текста.

    Правило одно и намеренно простое: строка остаётся, если она длинная или
    заканчивается как предложение. Пункты меню («Экономика», «Показать»)
    отсекаются, короткие реплики («Он отказался.») — нет. `min_line_chars=0`
    отключает фильтр для источников, где он мешает.
    """
    if min_line_chars <= 0:
        return text
    kept: list[str] = []
    seen: set[str] = set()
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped or stripped in seen:
            continue
        if len(stripped) >= min_line_chars or stripped.endswith(SENTENCE_ENDINGS):
            kept.append(stripped)
            seen.add(stripped)
    return "\n".join(kept)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        self._flush()
        self._href = href.strip() if href else None
        self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._buffer.append(data)

    def _flush(self) -> None:
        if self._href:
            self.links.append((self._href, _WHITESPACE.sub(" ", "".join(self._buffer)).strip()))
        self._href = None
        self._buffer = []

    def close(self) -> None:
        super().close()
        self._flush()


def extract_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Абсолютные http(s)-ссылки страницы вместе с текстом якоря, без повторов."""
    parser = _LinkParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return []
    seen: set[str] = set()
    links: list[tuple[str, str]] = []
    for href, anchor in parser.links:
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        absolute = urldefrag(urljoin(base_url, href)).url
        if not absolute.startswith(("http://", "https://")) or absolute in seen:
            continue
        seen.add(absolute)
        links.append((absolute, anchor))
    return links
