# Контракт AI-анализа

`LLMProvider.analyze(RawItem, BusinessContext) -> ArticleAnalysis` — стабильная граница между приложением и моделью. Доступны `OpenRouterLLMProvider` и детерминированный `MockLLMProvider`.

Ответ модели проверяется Pydantic-схемой. Затем код проверяет дословное присутствие каждой evidence quote в исходном тексте, длину саммари и чувствительные темы. Модель предлагает `suggested_priority`; приложение вычисляет `effective_priority`; правка человека хранится отдельно как `manual_priority` и audit record.

Кэш-ключ: `content_hash + prompt_version + model`. `reanalyze` сохраняет новую версию. Текст источника передаётся как недоверенное содержимое внутри `<source_document>`; модель не должна выполнять инструкции из него или выдавать юридическое заключение.
