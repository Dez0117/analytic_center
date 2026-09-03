from __future__ import annotations

import hashlib
import re
from datetime import timedelta, timezone
from difflib import SequenceMatcher
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.integrations.parser_adapter import canonical_url
from app.models import EventCluster, RawItemRecord
from app.schemas import RawItem


class Deduplicator(Protocol):
    def cluster_for(self, db: Session, item: RawItem) -> EventCluster: ...


def _title(value: str) -> str:
    return re.sub(r"[^a-zа-яё0-9]+", " ", value.casefold()).strip()


class BaselineDeduplicator:
    threshold = 0.88
    window = timedelta(days=3)

    def cluster_for(self, db: Session, item: RawItem) -> EventCluster:
        existing = db.scalars(select(RawItemRecord)).all()
        item_url = canonical_url(item.url)
        for candidate in existing:
            if (item_url and canonical_url(candidate.url) == item_url) or candidate.content_hash == item.content_hash:
                return candidate.cluster

        opinion = any(word in _title(item.title) for word in ("мнение", "позиция", "комментарий"))
        if not opinion:
            for candidate in existing:
                candidate_date = candidate.published_at or candidate.fetched_at
                item_date = item.published_at or item.fetched_at
                if candidate_date.tzinfo is None:
                    candidate_date = candidate_date.replace(tzinfo=timezone.utc)
                if item_date.tzinfo is None:
                    item_date = item_date.replace(tzinfo=timezone.utc)
                if abs(item_date - candidate_date) <= self.window:
                    score = SequenceMatcher(None, _title(item.title), _title(candidate.title)).ratio()
                    if score >= self.threshold:
                        return candidate.cluster

        cluster_id = "evt-" + hashlib.sha256(f"{item.id}:{item.title}".encode()).hexdigest()[:16]
        cluster = EventCluster(id=cluster_id, canonical_title=item.title)
        db.add(cluster)
        db.flush()
        return cluster
