# Design: Triage Agent (v1)

**Дата:** 2026-08-04
**Статус:** утверждён, готов к реализации

Ссылка на архитектуру: `docs/architecture.md` §4.1 (Triage Agent), §8.2 (Event Bus), §5 (Knowledge Base).

## Контекст

Первый шаг v1-среза (`CLAUDE.md`): один канал → Triage Agent → Diagnostic Collector → Event Store.
Diagnostic Collector — отдельный спек, вне этого документа.

Triage Agent требует двух вспомогательных функций, которых ещё нет в кодовой базе:
publish-сторону Event Bus и поиск по Knowledge Base. Обе — маленькие функции, а не
подсистемы, поэтому входят в этот же спек и план, но реализуются и коммитятся отдельно
от самого агента.

## 1. `EventPublisher` (`platform_core/event_bus`)

```python
class EventPublisher:
    async def publish(
        self,
        tenant_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict,
        metadata: dict | None = None,
    ) -> None
```

- INSERT в `events` через существующий `tenant_connection(tenant_id)` (RLS-скоуп).
- Без абстрактного ABC/интерфейса publish/subscribe — единственная реализация, нет
  подписчиков. LISTEN/NOTIFY и outbox добавляются отдельным шагом, когда появится
  первый реальный потребитель (вероятно Diagnostic Collector), без изменения
  сигнатуры `publish()`.
- Тесты — на реальном dev Postgres (`tenant_connection`), проверяют что строка
  попадает в `events` с правильными `tenant_id`/`event_type`/`aggregate_type`/`aggregate_id`.

## 2. `KnowledgeBase.search` (`domain/knowledge_base`)

```python
async def search(tenant_id: str, query: str, limit: int = 5) -> list[KbArticle]
```

- `SELECT ... FROM knowledge_base_articles WHERE title ILIKE %query% OR body ILIKE %query%
  OR topic_tag ILIKE %query% ORDER BY updated_at DESC LIMIT %limit%`.
- На пустой таблице возвращает `[]` — ожидаемое поведение на v1, не баг.
- `KbArticle` — dataclass: `id, kind, title, body, topic_tag`.
- Полнотекстовый поиск (tsvector) — не сейчас, YAGNI; апгрейд возможен без изменения
  сигнатуры функции.

## 3. Triage Agent (`domain/diagnostics/triage`)

Простая async-функция, не LangGraph-граф (нет ветвления/циклов состояний — LangGraph
вводим когда появится агент, которому это реально нужно, вероятно Diagnostic Collector).
Это не отступление от §10 архитектуры, а последовательность внедрения.

```python
async def triage(tenant_id: str, raw_message: str) -> TriageResult
```

Вход v1: только текст сообщения + `tenant_id` (без channel/customer — не подключены
по-настоящему, добавятся когда появится реальный канал).

Классификация: `severity` — фиксированный enum (`critical | high | medium | low`),
`topic_tag` — свободный текст от модели (каталог тем ещё не курирован).

Шаги:

1. `articles = await knowledge_base.search(tenant_id, raw_message)`.
2. FAST-tier `ModelRequest` к `ModelGateway`. System prompt требует вернуть **только**
   JSON `{severity, topic_tag, kb_closable, kb_article_id, reasoning}`; найденные статьи
   KB передаются в user-контент, чтобы модель обосновывала `kb_closable`/`kb_article_id`
   реальными совпадениями, а не угадывала.
3. Парсинг: `json.loads` + Pydantic-валидация (`severity` — Literal-enum,
   `kb_article_id` должен быть одним из id реально переданных статей — модель не может
   сослаться на статью, которую ей не показали). Один retry с сообщением об ошибке при
   невалидном JSON/схеме; вторая неудача — исключение наверх, без тихого fallback
   (агент не решает за человека, что делать при сбое классификации).
4. `incident_id = uuid4()`. Публикация `incident.triaged` через `EventPublisher`:
   `aggregate_type="incident"`, `aggregate_id=incident_id`,
   `payload={raw_message, severity, topic_tag, kb_closable, kb_article_id, reasoning}`,
   `metadata={"model_provider": response.provider, "model": response.model}`.
5. Возврат `TriageResult(incident_id, severity, topic_tag, kb_closable, kb_article_id)`.

Не используем tool-calling/forced structured output от провайдера — `ModelGateway`/
`ModelRequest` сейчас поддерживают только текстовые запросы, и расширять эту абстракцию
под один одношаговый классификатор преждевременно (YAGNI). Кандидат на структурированный
вывод в будущем — Bug Report Composer (STRONG tier, выше цена ошибки).

## 4. Тестирование

По образцу существующих тестов `ModelGateway`:

- Юнит-тесты Triage Agent: `FakeProvider` с заданным JSON-ответом + fake `EventPublisher`,
  фиксирующий вызов. Сценарии: успешная классификация; `kb_closable=true/false`
  в зависимости от найденных статей; retry на невалидный JSON; двойной сбой → исключение;
  корректность полей опубликованного события.
- `EventPublisher.publish` и `KnowledgeBase.search` — тесты на реальном dev Postgres.
- Без закоммиченного E2E-теста с живым Anthropic API для агента — только ручная проверка
  (как раньше с `ModelGateway`), чтобы CI не зависел от сети/ключа.

## Коммиты

Три отдельных коммита: `EventPublisher` → `KnowledgeBase.search` → `Triage Agent`.
