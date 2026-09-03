# GS Radar

Рабочий вертикальный срез аналитического центра PR/GR: входной JSON любой близкой формы нормализуется в неизменяемый `RawItem`, объединяется в события, анализируется через OpenRouter или честный deterministic mock, проходит evidence/policy-проверки, сохраняется в SQLite и отображается в русском React-интерфейсе.

## Быстрый запуск

Требуются Python 3.12 и Node.js. Из корня проекта:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".\backend[test]"
cd frontend
npm install
```

Backend, терминал 1:

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Frontend, терминал 2:

```powershell
cd frontend
npm run dev
```

Открыть `http://localhost:5173`. Нажать «Загрузить demo-данные», затем «Проанализировать последние». OpenAPI: `http://localhost:8000/docs`.

## Проверки

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
npm run build
```

## Дамп проекта

```powershell
.\scripts\create-project-dump.ps1
```

Архив создаётся в `dumps/`. Можно передать свой путь через `-OutputPath`. Секретный `.env`, Git-метаданные, зависимости, сборки, кэши и локальные БД в дамп не включаются.

## OpenRouter

По умолчанию и без ключа используется `MockLLMProvider`, а интерфейс явно показывает «Демо-режим».

```powershell
Copy-Item .env.example .env
```

В `.env` установить:

```env
OPENROUTER_API_KEY=your-key
DEMO_MODE=false
LLM_PROVIDER=openrouter
LLM_MODEL=google/gemini-3.7-flash
```

Ключ остаётся только в backend. Провайдер использует строгую JSON Schema, Pydantic-валидацию, timeout и один retry. Модель и prompt version входят в кэш-ключ.

## API-пример

```powershell
curl.exe -X POST http://localhost:8000/api/ingest/parser `
  -H "Content-Type: application/json" `
  --data-binary "@fixtures/parser_payload_alternative.json"

curl.exe -X POST http://localhost:8000/api/items/analyze-batch `
  -H "Content-Type: application/json" `
  -d '{"limit":20}'
```

Основные endpoints: ingest, manual add, list/detail/filter/search, analyze/reanalyze/batch, patch/hide, НПА, CRUD источников, статистика и health. Полный контракт доступен в OpenAPI.

## Где подключать реальный parser JSON

Вся внешняя mapping-логика изолирована в [`backend/app/integrations/parser_adapter.py`](backend/app/integrations/parser_adapter.py). Канонический контракт и aliases описаны в [`docs/parser-contract.md`](docs/parser-contract.md). При появлении точной схемы меняются только адаптер и его fixture-тесты; AI, БД, API и UI читают `RawItem`.

## Что реализовано

- 12 явно маркированных demo-материалов: шум, две пары дублей, новости, чувствительные темы, два НПА и обновление одного дела;
- точный URL/hash и ограниченный fuzzy dedup с сохранением всех исходников в event cluster;
- `MockLLMProvider` и `OpenRouterLLMProvider` за одним `LLMProvider`;
- доказательное `ArticleAnalysis`, проверка дословных цитат и безопасный запрет `critical → low`;
- версии анализа, кэш, reanalyze, ручные overrides и audit без изменения исходного `RawItem`;
- SQLite/SQLAlchemy, FastAPI, фильтры и поиск;
- страницы «Сегодня», «НПА на контроле», «Источники», ручное добавление и detail drawer;
- loading/empty/error, demo mode, сохранение правок после перезагрузки.

## Допущения и честные ограничения demo

- Это локальный hackathon MVP без авторизации; автор правок — `demo-user`.
- Таблицы создаются через SQLAlchemy `create_all`; Alembic нужен при первой общей/production БД.
- CRUD источников — management shell, реальные RSS/Telegram/site scrapers принадлежат другой части команды.
- Fuzzy dedup — объяснимый baseline на заголовках за три дня; разные мнения не схлопываются. Embeddings нужны только после размеченных ошибок baseline.
- Mock-анализ детерминирован и не выдаётся за настоящий AI. Fixtures вымышлены и помечены `demo`.
- Реальный OpenRouter-вызов не проверяется без пользовательского секрета; локальный fallback и весь остальной vertical slice работают без сети.
- Юридические выводы система не делает: чувствительные карточки направляются специалисту на проверку.
- Интерфейс desktop-first; production-инфраструктура, сложные роли, мобильный клиент и реальные уведомления вне MVP.
