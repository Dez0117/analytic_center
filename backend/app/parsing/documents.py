"""Единицы обмена между fetcher-ами и слоем хранения.

`FetchedDocument` — материал, найденный в источнике, ещё не нормализованный.
Нормализация одна на весь проект и живёт в `app.integrations.parser_adapter`,
поэтому документ умеет только превращать себя в payload канонической формы.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

from app.models import Source


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Снимок настроек источника, с которым работает fetcher вне сессии БД."""

    id: str
    name: str
    type: str
    url: str
    config: dict[str, Any] = field(default_factory=dict)
    etag: str | None = None
    last_modified: str | None = None

    @classmethod
    def from_record(cls, source: Source) -> SourceSpec:
        return cls(
            id=source.id,
            name=source.name,
            type=source.type,
            url=(source.url or "").strip(),
            config=dict(source.config or {}),
            etag=source.etag,
            last_modified=source.last_modified,
        )

    def option(self, key: str, default: Any = None) -> Any:
        value = self.config.get(key)
        return default if value in (None, "") else value


@dataclass(frozen=True, slots=True)
class FetchedDocument:
    title: str
    text: str
    url: str | None = None
    external_id: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    attachments: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_payload(self, spec: SourceSpec, fetched_at: datetime | None = None) -> dict[str, Any]:
        """Payload в форме, которую понимает `normalize_parser_payload`.

        Даты передаются строками: `raw_payload` уезжает в JSON-колонку.
        """
        moment = fetched_at or datetime.now(timezone.utc)
        return {
            "source": {"id": spec.id, "name": spec.name, "type": spec.type},
            "external_id": self.external_id,
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "author": self.author,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "fetched_at": moment.isoformat(),
            "attachments": list(self.attachments),
            "metadata": {**self.metadata, "collected_by": "parser", "source_id": spec.id},
        }


@dataclass(slots=True)
class FetchResult:
    """Что fetcher увидел в источнике за один опрос."""

    documents: list[FetchedDocument] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    not_modified: bool = False
    etag: str | None = None
    last_modified: str | None = None
    skipped_known: int = 0


class FetchError(RuntimeError):
    """Источник недоступен или отдал то, что нельзя разобрать."""


def with_metadata(document: FetchedDocument, **extra: Any) -> FetchedDocument:
    return replace(document, metadata={**document.metadata, **extra})
