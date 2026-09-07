"""Стартовый список источников трёх категорий из ТЗ.

Посев только добавляет отсутствующие источники и никогда не перезаписывает
пользовательские правки: список источников принадлежит пользователю.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models import Source

DEFAULT_SOURCES_PATH = Path(__file__).parents[2] / "config" / "default_sources.json"
ALLOWED_FIELDS = ("name", "type", "url", "enabled", "poll_interval_minutes", "config")


@dataclass(slots=True)
class SeedReport:
    created: list[str]
    existing: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"created": self.created, "existing": self.existing}


def load_default_sources(path: Path | None = None) -> list[dict[str, Any]]:
    payload = json.loads((path or DEFAULT_SOURCES_PATH).read_text(encoding="utf-8"))
    return list(payload.get("sources", []))


def seed_default_sources(db: Session, path: Path | None = None) -> SeedReport:
    """Добавить недостающие источники по умолчанию."""
    report = SeedReport(created=[], existing=[])
    for entry in load_default_sources(path):
        source_id = entry["id"]
        if db.get(Source, source_id) is not None:
            report.existing.append(source_id)
            continue
        fields = {key: entry[key] for key in ALLOWED_FIELDS if key in entry}
        db.add(Source(id=source_id, **fields))
        report.created.append(source_id)
    db.commit()
    return report
