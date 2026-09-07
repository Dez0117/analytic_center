"""Разбор дат из источников: RSS отдаёт RFC 822, Atom и Telegram — ISO 8601."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from pydantic import TypeAdapter, ValidationError

_DATETIME = TypeAdapter(datetime)


def parse_datetime(value: str | None) -> datetime | None:
    """Вернуть aware-datetime или None, если дату разобрать не удалось."""
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    for parser in (_rfc822, _iso, _pydantic):
        parsed = parser(text)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _rfc822(text: str) -> datetime | None:
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def _iso(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pydantic(text: str) -> datetime | None:
    try:
        return _DATETIME.validate_python(text)
    except ValidationError:
        return None
