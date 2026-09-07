"""Тесты модуля парсинга: разбор источников, отсечение повторов, расписание.

Сеть не используется: httpx.MockTransport отдаёт заранее заданные ответы,
поэтому тесты воспроизводимы и не зависят от доступности внешних сайтов.
"""
import os

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["DEMO_MODE"] = "true"
os.environ["PARSER_ENABLED"] = "false"
os.environ["PARSER_SEED_DEFAULTS"] = "false"
os.environ.pop("OPENROUTER_API_KEY", None)

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.main import app, scheduler
from app.models import RawItemRecord, Source
from app.parsing import PollingScheduler, is_due, poll_source
from app.parsing.htmltext import extract_article, extract_links, html_to_text
from app.parsing.http import fetch_page
from app.parsing.rss import parse_feed
from app.parsing.seed import seed_default_sources
from app.parsing.telegram import channel_name, parse_preview

SETTINGS = get_settings()


@pytest.mark.asyncio
async def test_fetch_page_detects_cp1251_without_http_charset():
    text = "Минцифры опубликовало проект регулирования."
    mock = httpx.MockTransport(lambda request: httpx.Response(
        200, content=text.encode("cp1251"), headers={"Content-Type": "text/html"}, request=request,
    ))
    async with httpx.AsyncClient(transport=mock) as client:
        page = await fetch_page(client, "https://example.org/news")
    assert page.text == text

FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <title>Тестовая лента</title>
  <item>
    <title>ЦБ уточнил требования к отчётности</title>
    <link>https://example.org/news/1?utm_source=rss</link>
    <description>&lt;p&gt;Регулятор опубликовал проект указания.&lt;/p&gt;</description>
    <pubDate>Wed, 03 Sep 2026 10:00:00 +0300</pubDate>
    <guid>news-1</guid>
    <author>Пресс-служба</author>
  </item>
  <item>
    <title>Второй материал</title>
    <link>https://example.org/news/2</link>
    <description>Короткий, но непустой текст второго материала.</description>
    <pubDate>Wed, 03 Sep 2026 11:00:00 +0300</pubDate>
  </item>
</channel></rss>
"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom-запись</title>
    <link rel="alternate" href="https://atom.example.org/a"/>
    <id>atom-1</id>
    <updated>2026-09-03T09:00:00Z</updated>
    <content type="html">&lt;div&gt;Содержимое atom-записи.&lt;/div&gt;</content>
    <author><name>Автор Атомов</name></author>
  </entry>
</feed>
"""

TELEGRAM = """
<html><body>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="testchannel/10">
    <div class="tgme_widget_message_text js-message_text">Первая строка поста<br>Вторая строка поста.</div>
    <a class="tgme_widget_message_date" href="https://t.me/testchannel/10">
      <time datetime="2026-09-03T12:00:00+00:00">12:00</time></a>
  </div>
</div>
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="testchannel/11">
    <div class="tgme_widget_message_text js-message_text">Второй пост канала с текстом.</div>
    <a class="tgme_widget_message_date" href="https://t.me/testchannel/11">
      <time datetime="2026-09-03T13:00:00+00:00">13:00</time></a>
  </div>
</div>
</body></html>
"""

INDEX_HTML = """
<html><body>
<nav><a href="/about/">О ведомстве</a></nav>
<ul>
  <li><a href="/news/101/">Регулятор утвердил новый порядок проверок операторов</a></li>
  <li><a href="/news/102/">Опубликован проект постановления о маркировке</a></li>
  <li><a href="/docs/file.pdf">Скачать документ</a></li>
</ul>
</body></html>
"""


def article_html(number: int) -> str:
    body = f"Материал номер {number}. " * 20
    return f"""
    <html><head><title>Материал {number}</title>
    <meta property="article:published_time" content="2026-09-03T08:0{number}:00+03:00"></head>
    <body><nav>меню сайта</nav><script>var x = 1;</script>
    <article><h1>Заголовок материала {number}</h1><p>{body}</p></article></body></html>
    """


def transport(routes: dict[str, httpx.Response], calls: list[str] | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        for prefix, response in routes.items():
            if str(request.url).startswith(prefix):
                if response.status_code == 304 and not request.headers.get("if-none-match"):
                    return httpx.Response(200, text="")
                return httpx.Response(
                    response.status_code,
                    text=response.text,
                    headers=dict(response.headers),
                    request=request,
                )
        return httpx.Response(404, text="not found", request=request)

    return httpx.MockTransport(handler)


def client_factory(mock: httpx.MockTransport):
    return lambda settings: httpx.AsyncClient(transport=mock, follow_redirects=True)


@pytest.fixture
def db_reset():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def client(db_reset):
    with TestClient(app) as test_client:
        yield test_client


def add_source(**changes) -> str:
    values = {
        "id": "src-test",
        "name": "Тестовая лента",
        "type": "rss",
        "url": "https://example.org/feed.xml",
        "enabled": True,
        "poll_interval_minutes": 30,
        "config": {},
    }
    values.update(changes)
    with SessionLocal() as db:
        db.add(Source(**values))
        db.commit()
    return values["id"]


async def poll(source_id: str, mock: httpx.MockTransport):
    async with httpx.AsyncClient(transport=mock) as http_client:
        return await poll_source(SessionLocal, source_id, client=http_client, settings=SETTINGS)


# --- разбор источников -------------------------------------------------------

def test_rss_entries_are_parsed_with_dates_and_html_stripped():
    documents = parse_feed(FEED)
    assert len(documents) == 2
    first = documents[0]
    assert first.title == "ЦБ уточнил требования к отчётности"
    assert first.text == "Регулятор опубликовал проект указания."
    assert first.published_at == datetime(2026, 9, 3, 10, 0, tzinfo=timezone(timedelta(hours=3)))
    assert first.author == "Пресс-служба"
    assert first.external_id == "news-1"


def test_atom_feed_uses_alternate_link_and_content():
    document = parse_feed(ATOM)[0]
    assert document.url == "https://atom.example.org/a"
    assert document.text == "Содержимое atom-записи."
    assert document.author == "Автор Атомов"


def test_broken_feed_reports_readable_error():
    from app.parsing.documents import FetchError

    with pytest.raises(FetchError, match="валидным XML"):
        parse_feed("<rss><channel>")


def test_telegram_preview_becomes_documents():
    documents = parse_preview(TELEGRAM, "testchannel")
    assert [document.url for document in documents] == [
        "https://t.me/testchannel/10",
        "https://t.me/testchannel/11",
    ]
    assert documents[0].title == "Первая строка поста"
    assert "Вторая строка поста." in documents[0].text
    assert documents[0].published_at == datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", ["@channel", "channel", "t.me/channel", "https://t.me/s/channel"])
def test_channel_name_accepts_every_written_form(value):
    assert channel_name(value) == "channel"


def test_html_to_text_drops_scripts_and_navigation():
    text = html_to_text("<div><script>alert(1)</script><nav>меню</nav><p>Основной текст.</p></div>")
    assert text == "Основной текст."


def test_extract_article_prefers_main_content_and_meta_date():
    article = extract_article(article_html(1))
    assert article.title == "Заголовок материала 1"
    assert "меню сайта" not in article.text
    assert article.published_raw == "2026-09-03T08:01:00+03:00"


def test_extract_links_resolves_relative_urls_and_skips_anchors():
    links = extract_links('<a href="/news/1">Новость</a><a href="#top">Наверх</a>', "https://example.org/index")
    assert links == [("https://example.org/news/1", "Новость")]


# --- опрос и отсечение повторов ---------------------------------------------

@pytest.mark.asyncio
async def test_poll_stores_items_and_updates_source_status(db_reset):
    source_id = add_source()
    report = await poll(source_id, transport({"https://example.org/feed.xml": httpx.Response(200, text=FEED)}))
    assert report.status == "ok"
    assert report.fetched == 2 and report.stored == 2
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        assert source.last_status == "ok" and source.last_error is None
        assert source.last_polled_at is not None
        stored = db.query(RawItemRecord).all()
        assert {item.url for item in stored} == {"https://example.org/news/1", "https://example.org/news/2"}
        assert stored[0].metadata_json["fetcher"] == "rss"


@pytest.mark.asyncio
async def test_second_poll_of_same_feed_adds_nothing(db_reset):
    source_id = add_source()
    mock = transport({"https://example.org/feed.xml": httpx.Response(200, text=FEED)})
    first = await poll(source_id, mock)
    second = await poll(source_id, mock)
    assert first.stored == 2
    assert second.stored == 0 and second.fetched == 2
    with SessionLocal() as db:
        assert db.query(RawItemRecord).count() == 2


@pytest.mark.asyncio
async def test_same_text_from_another_channel_is_cut_by_hash(db_reset):
    """Один материал не приезжает дважды из RSS и из Telegram."""
    rss_id = add_source()
    await poll(rss_id, transport({"https://example.org/feed.xml": httpx.Response(200, text=FEED)}))

    telegram_html = TELEGRAM.replace(
        "Первая строка поста<br>Вторая строка поста.",
        "Регулятор опубликовал проект указания.",
    )
    telegram_id = add_source(id="src-tg", name="Канал", type="telegram", url="https://t.me/testchannel")
    report = await poll(telegram_id, transport({"https://t.me/s/testchannel": httpx.Response(200, text=telegram_html)}))

    assert report.fetched == 2
    assert report.stored == 1
    assert report.duplicates == 1
    with SessionLocal() as db:
        assert db.query(RawItemRecord).count() == 3


@pytest.mark.asyncio
async def test_same_url_from_another_source_is_cut(db_reset):
    add_source()
    await poll("src-test", transport({"https://example.org/feed.xml": httpx.Response(200, text=FEED)}))
    mirror = FEED.replace("Регулятор опубликовал проект указания.", "Полностью другой пересказ той же ссылки.")
    add_source(id="src-mirror", name="Зеркало", url="https://example.org/mirror.xml")
    report = await poll("src-mirror", transport({"https://example.org/mirror.xml": httpx.Response(200, text=mirror)}))
    assert report.fetched == 2
    assert report.stored == 0
    assert report.known + report.duplicates == 2
    with SessionLocal() as db:
        assert db.query(RawItemRecord).count() == 2


@pytest.mark.asyncio
async def test_not_modified_response_skips_work_and_keeps_etag(db_reset):
    source_id = add_source()
    routes = {"https://example.org/feed.xml": httpx.Response(200, text=FEED, headers={"ETag": 'W/"v1"'})}
    await poll(source_id, transport(routes))
    with SessionLocal() as db:
        assert db.get(Source, source_id).etag == 'W/"v1"'

    routes["https://example.org/feed.xml"] = httpx.Response(304, text="")
    report = await poll(source_id, transport(routes))
    assert report.status == "not_modified"
    assert report.stored == 0
    with SessionLocal() as db:
        assert db.get(Source, source_id).etag == 'W/"v1"'


@pytest.mark.asyncio
async def test_unavailable_source_is_recorded_as_error(db_reset):
    source_id = add_source()
    report = await poll(source_id, transport({"https://example.org/feed.xml": httpx.Response(503, text="")}))
    assert report.status == "error"
    assert "503" in report.error
    with SessionLocal() as db:
        source = db.get(Source, source_id)
        assert source.last_status == "error" and "503" in source.last_error


@pytest.mark.asyncio
async def test_manual_source_is_skipped_not_failed(db_reset):
    source_id = add_source(id="src-manual", type="manual", url=None)
    report = await poll(source_id, transport({}))
    assert report.status == "skipped"
    assert "вручную" in report.error


# --- сайты регуляторов -------------------------------------------------------

@pytest.mark.asyncio
async def test_website_follows_index_links_and_skips_binary(db_reset):
    source_id = add_source(
        id="src-site", name="Регулятор", type="regulator", url="https://reg.example.org/news/",
        config={"fetcher": "website", "link_pattern": r"/news/\d+/"},
    )
    calls: list[str] = []
    routes = {
        "https://reg.example.org/news/101/": httpx.Response(200, text=article_html(1)),
        "https://reg.example.org/news/102/": httpx.Response(200, text=article_html(2)),
        "https://reg.example.org/news/": httpx.Response(200, text=INDEX_HTML),
    }
    report = await poll(source_id, transport(routes, calls))
    assert report.stored == 2
    assert not any(call.endswith(".pdf") for call in calls)

    report = await poll(source_id, transport(routes, calls := []))
    assert report.stored == 0
    assert report.skipped_known_urls == 2
    assert calls == ["https://reg.example.org/news/"]


# --- расписание --------------------------------------------------------------

def test_is_due_respects_per_source_interval():
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    source = Source(id="s", name="s", type="rss", url="https://example.org/f", enabled=True,
                    poll_interval_minutes=30, config={})
    assert is_due(source, now, 30) is True
    source.last_polled_at = now - timedelta(minutes=10)
    assert is_due(source, now, 30) is False
    source.last_polled_at = now - timedelta(minutes=31)
    assert is_due(source, now, 30) is True


def test_is_due_ignores_disabled_and_unpollable_sources():
    now = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    disabled = Source(id="a", name="a", type="rss", url="https://example.org/f", enabled=False, config={})
    manual = Source(id="b", name="b", type="manual", url="https://example.org/f", enabled=True, config={})
    empty = Source(id="c", name="c", type="rss", url=None, enabled=True, config={})
    assert not any(is_due(source, now, 30) for source in (disabled, manual, empty))


@pytest.mark.asyncio
async def test_scheduler_polls_only_due_sources(db_reset):
    add_source()
    add_source(id="src-fresh", name="Свежий", url="https://example.org/fresh.xml")
    with SessionLocal() as db:
        db.get(Source, "src-fresh").last_polled_at = datetime.now(timezone.utc)
        db.commit()
    mock = transport({
        "https://example.org/feed.xml": httpx.Response(200, text=FEED),
        "https://example.org/fresh.xml": httpx.Response(200, text=FEED),
    })
    test_scheduler = PollingScheduler(SessionLocal, SETTINGS, client_factory=client_factory(mock))
    reports = await test_scheduler.run_due()
    assert [report.source_id for report in reports] == ["src-test"]
    assert test_scheduler.status()["runs"] == 1


# --- API ---------------------------------------------------------------------

def test_poll_endpoint_returns_report(client, monkeypatch):
    add_source()
    mock = transport({"https://example.org/feed.xml": httpx.Response(200, text=FEED)})
    monkeypatch.setattr(scheduler, "client_factory", client_factory(mock))
    response = client.post("/api/sources/src-test/poll")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["stored"] == 2
    assert len(client.get("/api/items").json()) == 2


def test_parser_run_endpoint_polls_due_sources(client, monkeypatch):
    add_source()
    mock = transport({"https://example.org/feed.xml": httpx.Response(200, text=FEED)})
    monkeypatch.setattr(scheduler, "client_factory", client_factory(mock))
    body = client.post("/api/parser/run", json={}).json()
    assert body["polled"] == 1 and body["stored"] == 2
    repeat = client.post("/api/parser/run", json={"force": True}).json()
    assert repeat["stored"] == 0


def test_poll_endpoint_404_for_unknown_source(client):
    assert client.post("/api/sources/nope/poll").status_code == 404


def test_source_crud_exposes_schedule_and_resets_conditional_cache(client):
    created = client.post("/api/sources", json={
        "name": "Новый источник", "type": "rss", "url": "https://example.org/new.xml",
        "poll_interval_minutes": 5, "config": {"max_items": 3},
    }).json()
    assert created["poll_interval_minutes"] == 5 and created["pollable"] is True
    with SessionLocal() as db:
        db.get(Source, created["id"]).etag = 'W/"cached"'
        db.commit()
    patched = client.patch(f"/api/sources/{created['id']}", json={"url": "https://example.org/other.xml"}).json()
    assert patched["url"] == "https://example.org/other.xml"
    with SessionLocal() as db:
        assert db.get(Source, created["id"]).etag is None


def test_parser_status_reports_configuration(client):
    body = client.get("/api/parser/status").json()
    assert body["tick_seconds"] == SETTINGS.parser_tick_seconds
    assert body["running"] is False


def test_default_sources_cover_three_categories(db_reset):
    with SessionLocal() as db:
        report = seed_default_sources(db)
        assert not report.existing
        types = {source.type for source in db.query(Source).all()}
        assert {"rss", "regulator", "telegram"} <= types
        again = seed_default_sources(db)
        assert not again.created and len(again.existing) == len(report.created)


# --- дозагрузка полного текста и дедупликация на уровне хранения ---------------

@pytest.mark.asyncio
async def test_rss_full_text_is_loaded_only_for_short_entries(db_reset):
    """Лента отдаёт заголовок — статья догружается со страницы материала."""
    short_feed = FEED.replace("<description>&lt;p&gt;Регулятор опубликовал проект указания.&lt;/p&gt;</description>",
                              "<description>Коротко</description>")
    source_id = add_source(config={"fetch_full_text": True, "full_text_threshold": 100})
    calls: list[str] = []
    routes = {
        "https://example.org/feed.xml": httpx.Response(200, text=short_feed),
        "https://example.org/news/1": httpx.Response(200, text=article_html(1)),
        "https://example.org/news/2": httpx.Response(200, text=article_html(2)),
    }
    report = await poll(source_id, transport(routes, calls))
    assert report.stored == 2
    with SessionLocal() as db:
        first = db.get(RawItemRecord, [i for i in report.item_ids if i][0])
        assert "Материал номер" in first.text
        assert first.metadata_json["full_text"] is True
        assert first.title == "ЦБ уточнил требования к отчётности"


@pytest.mark.asyncio
async def test_full_text_is_not_requested_for_known_urls(db_reset):
    source_id = add_source(config={"fetch_full_text": True, "full_text_threshold": 100})
    routes = {
        "https://example.org/feed.xml": httpx.Response(200, text=FEED),
        "https://example.org/news/1": httpx.Response(200, text=article_html(1)),
        "https://example.org/news/2": httpx.Response(200, text=article_html(2)),
    }
    await poll(source_id, transport(routes))
    await poll(source_id, transport(routes, calls := []))
    assert calls == ["https://example.org/feed.xml"]


def test_store_skips_duplicate_url_from_another_source(db_reset):
    """Разные id и разный текст, один URL — материал сохраняется один раз."""
    from app.integrations.parser_adapter import normalize_parser_payload
    from app.services.storage import store_raw_items

    def payload(item_id: str, source: str, text: str) -> dict:
        return {
            "id": item_id,
            "source": {"name": source, "type": "rss"},
            "title": "Один и тот же материал",
            "text": text,
            "url": "https://example.org/shared/1",
        }

    with SessionLocal() as db:
        first = normalize_parser_payload(payload("a-1", "Канал A", "Текст первого канала.")).accepted
        assert store_raw_items(db, first, skip_duplicates=True).stored == 1
        second = normalize_parser_payload(payload("b-1", "Канал B", "Совсем другой пересказ.")).accepted
        report = store_raw_items(db, second, skip_duplicates=True)
        assert report.stored == 0
        assert [skip.reason for skip in report.duplicates] == ["url"]
        assert db.query(RawItemRecord).count() == 1


def test_store_without_skip_keeps_all_sources_in_cluster(db_reset):
    """Ingest внешнего парсера сохраняет оба источника: поведение не изменилось."""
    from app.integrations.parser_adapter import normalize_parser_payload
    from app.services.storage import store_raw_items

    items = normalize_parser_payload([
        {"id": "x-1", "source": {"name": "A", "type": "rss"}, "title": "Т", "text": "Один текст.",
         "url": "https://example.org/x1"},
        {"id": "x-2", "source": {"name": "B", "type": "rss"}, "title": "Т", "text": "Один текст.",
         "url": "https://example.org/x2"},
    ]).accepted
    with SessionLocal() as db:
        report = store_raw_items(db, items)
        assert report.stored == 2
        records = db.query(RawItemRecord).all()
        assert len({record.cluster_id for record in records}) == 1


def test_boilerplate_filter_keeps_sentences_and_drops_menu_items():
    from app.parsing.htmltext import drop_boilerplate

    text = "Экономика\nПоказать\nОн отказался.\nДлинная строка настоящего абзаца новости про регулятора и рынок.\nЭкономика"
    assert drop_boilerplate(text).split("\n") == [
        "Он отказался.",
        "Длинная строка настоящего абзаца новости про регулятора и рынок.",
    ]
    assert drop_boilerplate(text, min_line_chars=0) == text
