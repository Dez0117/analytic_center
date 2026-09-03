# Контракт parser adapter

Единственная точка входа внешнего парсера — `normalize_parser_payload(payload)`. Она принимает объект, массив или wrapper `{"items": [...]}` / `{"data": [...]}` и возвращает принятые `RawItem` отдельно от ошибок отдельных записей.

## Канонический объект

```json
{
  "id": "source-item-42",
  "source": {"id": "source-1", "name": "Источник", "type": "website"},
  "url": "https://example.org/item/42",
  "title": "Заголовок",
  "text": "Полный текст материала",
  "published_at": "2026-09-03T10:00:00+03:00",
  "fetched_at": "2026-09-03T10:01:00+03:00",
  "metadata": {"parser_version": "1"}
}
```

## Альтернативные формы

```json
{"headline":"Заголовок","body":"Текст","link":"https://example.org/a","date":"2026-09-03","source":"RSS лента"}
```

```json
{"data":[{"name":"Заголовок","description":"Текст","source_url":"https://example.org/b","source":{"name":"Регулятор","type":"regulator"}}]}
```

Поддерживаемые aliases: `text/content/body/description`, `url/link/source_url`, `title/headline/name`, `published_at/published/date/created_at`. Неизвестные поля сохраняются в `metadata`, а исходный объект — в `raw_payload`. После получения реальной схемы меняется только [parser_adapter.py](../backend/app/integrations/parser_adapter.py) и его fixture-тесты.
