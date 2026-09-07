"""Единая точка сетевых вызовов парсера: таймаут, User-Agent, условный GET."""
from __future__ import annotations

from dataclasses import dataclass
import re

import httpx

from app.config import Settings
from app.parsing.documents import FetchError

NOT_MODIFIED = 304
ENCODING_RE = re.compile(br"(?i)(?:charset\s*=|encoding\s*=\s*)[\"']?\s*([a-z0-9._-]+)")


@dataclass(frozen=True, slots=True)
class HttpPage:
    url: str
    status_code: int
    text: str
    etag: str | None = None
    last_modified: str | None = None

    @property
    def not_modified(self) -> bool:
        return self.status_code == NOT_MODIFIED


def build_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(settings.parser_timeout_seconds),
        follow_redirects=True,
        headers={"User-Agent": settings.parser_user_agent, "Accept-Language": "ru,en;q=0.8"},
    )


def _decode(response: httpx.Response) -> str:
    content = response.content
    declared = response.charset_encoding
    embedded = ENCODING_RE.search(content[:4096])
    candidates = [declared, embedded.group(1).decode("ascii") if embedded else None, "utf-8", "cp1251"]
    for encoding in dict.fromkeys(value for value in candidates if value):
        try:
            return content.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return content.decode("utf-8", errors="replace")


async def fetch_page(
    client: httpx.AsyncClient,
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> HttpPage:
    """GET с условными заголовками. 304 возвращается как обычный результат."""
    headers: dict[str, str] = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        # У таймаутов httpx пустой str(): без имени класса сообщение бесполезно.
        reason = str(exc) or type(exc).__name__
        raise FetchError(f"Не удалось получить {url}: {reason}") from exc
    if response.status_code == NOT_MODIFIED:
        return HttpPage(url=url, status_code=NOT_MODIFIED, text="", etag=etag, last_modified=last_modified)
    if response.status_code >= 400:
        raise FetchError(f"{url} ответил {response.status_code}")
    return HttpPage(
        url=str(response.url),
        status_code=response.status_code,
        text=_decode(response),
        etag=response.headers.get("etag"),
        last_modified=response.headers.get("last-modified"),
    )
