from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import TypeAdapter, ValidationError

from app.schemas import NormalizationError, NormalizationResult, RawItem, SourceRef

TEXT_KEYS = ("text", "content", "body", "description")
URL_KEYS = ("url", "link", "source_url")
TITLE_KEYS = ("title", "headline", "name")
DATE_KEYS = ("published_at", "published", "date", "created_at")
KNOWN_KEYS = {
    "id", "external_id", "source", "source_name", "source_type", "author",
    "fetched_at", "language", "attachments", "metadata", "raw_payload",
    "content_hash", *TEXT_KEYS, *URL_KEYS, *TITLE_KEYS, *DATE_KEYS,
}


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((payload[key] for key in keys if payload.get(key) not in (None, "")), None)


def canonical_url(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_")])
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def _hash_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def _datetime(value: Any, fallback: datetime | None = None) -> datetime | None:
    if value in (None, ""):
        return fallback
    parsed = TypeAdapter(datetime).validate_python(value)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _source(value: Any, payload: dict[str, Any]) -> SourceRef:
    if isinstance(value, str):
        return SourceRef(name=value, type=payload.get("source_type", "unknown"))
    if isinstance(value, dict):
        data = dict(value)
        data.setdefault("name", data.get("title") or "Неизвестный источник")
        return SourceRef.model_validate(data)
    return SourceRef(
        name=payload.get("source_name") or "Неизвестный источник",
        type=payload.get("source_type", "unknown"),
    )


def _normalize(payload: dict[str, Any]) -> RawItem:
    text = str(_first(payload, TEXT_KEYS) or "").strip()
    if not text:
        raise ValueError("Поле text/content/body/description не должно быть пустым")
    url = canonical_url(_first(payload, URL_KEYS))
    content_hash = payload.get("content_hash") or _hash_text(text)
    stable_id = str(payload.get("id") or hashlib.sha256((url or content_hash).encode()).hexdigest()[:24])
    title = str(_first(payload, TITLE_KEYS) or text[:120]).strip()
    extras = {key: value for key, value in payload.items() if key not in KNOWN_KEYS}
    metadata = {**(payload.get("metadata") or {}), **extras}
    return RawItem(
        id=stable_id,
        external_id=payload.get("external_id"),
        source=_source(payload.get("source"), payload),
        url=url,
        title=title,
        text=text,
        author=payload.get("author"),
        published_at=_datetime(_first(payload, DATE_KEYS)),
        fetched_at=_datetime(payload.get("fetched_at"), datetime.now(timezone.utc)),
        language=payload.get("language", "ru"),
        attachments=payload.get("attachments") or [],
        metadata=metadata,
        raw_payload=dict(payload),
        content_hash=content_hash,
    )


def normalize_parser_payload(payload: Any) -> NormalizationResult:
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        entries = payload["items"]
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        entries = payload["data"]
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = [payload]

    result = NormalizationResult()
    for index, entry in enumerate(entries):
        try:
            if not isinstance(entry, dict):
                raise ValueError("Ожидался JSON-объект")
            result.accepted.append(_normalize(entry))
        except (ValueError, ValidationError, TypeError) as exc:
            result.errors.append(NormalizationError(index=index, message=str(exc)))
    return result
