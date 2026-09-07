import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["DEMO_MODE"] = "true"
os.environ["PARSER_ENABLED"] = "false"
os.environ["PARSER_SEED_DEFAULTS"] = "false"
os.environ.pop("OPENROUTER_API_KEY", None)

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.integrations.parser_adapter import normalize_parser_payload
from app.main import app
from app.schemas import ArticleAnalysis, BusinessContext, Evidence, RawItem, SourceRef
from app.services.analysis import _validate_and_apply_policy


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client


def sample(**changes):
    value = {
        "source": {"name": "Тест", "type": "website"},
        "title": "Тестовый материал",
        "text": "Источник сообщил проверяемый факт. Указан следующий шаг.",
        "url": "https://example.org/item",
        "published_at": "2026-09-03T10:00:00+03:00",
    }
    value.update(changes)
    return value


def test_canonical_payload_becomes_raw_item():
    result = normalize_parser_payload(sample(id="canonical-1"))
    assert result.errors == []
    assert result.accepted[0].id == "canonical-1"
    assert result.accepted[0].fetched_at.tzinfo is not None


def test_aliases_and_wrapper_are_supported():
    result = normalize_parser_payload({"data": [{
        "source": "Лента", "headline": "Alias", "body": "Непустой текст.",
        "link": "https://example.org/a?utm_source=x", "date": "2026-09-03",
    }]})
    assert result.accepted[0].title == "Alias"
    assert result.accepted[0].url == "https://example.org/a"


def test_unknown_fields_are_preserved():
    result = normalize_parser_payload(sample(custom_parser_value=42))
    assert result.accepted[0].metadata["custom_parser_value"] == 42
    assert result.accepted[0].raw_payload["custom_parser_value"] == 42


def test_empty_text_has_readable_error():
    result = normalize_parser_payload({"title": "Без текста", "text": ""})
    assert not result.accepted
    assert "не должно быть пустым" in result.errors[0].message


def test_bad_batch_entry_does_not_hide_good_one():
    result = normalize_parser_payload([sample(), {"title": "broken"}])
    assert len(result.accepted) == 1
    assert len(result.errors) == 1
    assert result.errors[0].index == 1


def test_content_hash_and_generated_id_are_deterministic():
    first = normalize_parser_payload(sample(url=None)).accepted[0]
    second = normalize_parser_payload(sample(url=None)).accepted[0]
    assert first.content_hash == second.content_hash
    assert first.id == second.id


def test_exact_content_duplicates_share_one_event(client):
    first = sample(id="dup-a", url="https://example.org/a")
    second = sample(id="dup-b", url="https://example.org/b")
    assert client.post("/api/ingest/parser", json=[first, second]).status_code == 201
    items = client.get("/api/items").json()
    assert len(items) == 1
    assert len(items[0]["cluster_sources"]) == 2
    client.post("/api/items/dup-a/analyze")
    client.post("/api/items/dup-b/analyze")
    assert client.get("/api/items/dup-a").json()["analysis"]
    assert client.get("/api/items/dup-b").json()["analysis"]


def test_sensitive_low_is_upgraded_and_requires_review(client):
    payload = sample(
        id="critical-low",
        text="Опубликованы обязательные требования к ПО для объектов КИИ. Назначено обсуждение.",
        metadata={"force_low": True},
    )
    client.post("/api/ingest/parser", json=payload)
    response = client.post("/api/items/critical-low/analyze")
    analysis = response.json()["analysis"]
    assert analysis["suggested_priority"] == "low"
    assert analysis["effective_priority"] == "medium"
    assert analysis["review_required"] is True


def test_unmatched_evidence_requires_review():
    item = RawItem(
        id="x", source=SourceRef(name="x"), title="x", text="Исходный факт.",
        content_hash="hash",
    )
    analysis = ArticleAnalysis(
        item_id="x", entity_type="news", short_title="x",
        summary="Первое предложение. Второе предложение. Третье предложение.",
        what="факт", primary_category="trends", impact_type="unknown",
        impact_for_gs_labs="не указано", suggested_priority="low", effective_priority="low",
        priority_reason="нет", evidence=[Evidence(claim="x", quote="Выдуманная цитата")],
    )
    context = BusinessContext(products=[], topics=[], sensitive_risks=[], competitors=[], organizations=[])
    checked = _validate_and_apply_policy(analysis, item, context)
    assert checked.review_required is True
    assert any("Цитата не найдена" in reason for reason in checked.uncertainties)


def test_patch_creates_audit_and_survives_get(client):
    client.post("/api/ingest/parser", json=sample(id="editable"))
    client.post("/api/items/editable/analyze")
    patched = client.patch("/api/items/editable", json={
        "summary": "Исправленное саммари.", "manual_priority": "high", "tags": ["ручная правка"],
        "reason": "Проверено аналитиком",
    })
    assert patched.status_code == 200
    fetched = client.get("/api/items/editable").json()
    assert fetched["analysis"]["summary"] == "Исправленное саммари."
    assert fetched["display_priority"] == "high"
    assert len(fetched["audit"]) == 3


def test_missing_key_uses_honest_demo_mode(client):
    health = client.get("/api/health").json()
    assert health["demo_mode"] is True
    assert health["provider"].startswith("mock-")
