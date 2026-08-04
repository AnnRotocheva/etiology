# Design: Diagnostic Collector Agent (v1)

**Дата:** 2026-08-04
**Статус:** утверждён, готов к реализации

Ссылка на архитектуру: `docs/architecture.md` §4.2 (Diagnostic Collector), §4.4 (Command Safety Gate), §5 (Knowledge Base).
Продолжение среза после Triage Agent: `docs/superpowers/specs/2026-08-04-triage-agent-design.md`.

## Контекст

Второй и последний агент v1-среза (`CLAUDE.md`): `incident.triaged` → Diagnostic Collector →
`incident.resolved` либо `incident.needs_bug_report`. Bug Report Composer — вне этого среза.

Канал (реальный диалог с клиентом: запросить логи, дождаться ответа) ещё не подключён — как и
при проектировании Triage Agent, это ограничивает v1 одним проходом без ожидания ввода от
клиента между шагами.

## Решения

- **LangGraph:** не сейчас. Без живого канала нет реального multi-turn цикла — v1 делает один
  проход (KB → каталог команд/скриншоты → решение) за один вызов, ветвление — обычный if/else.
  LangGraph вводится, когда появится канал с ожиданием ответа клиента между шагами.
- **Серверная диагностика через `diagnostic_command_catalog`:** только lookup + advisory-текст
  в payload события. Нет канала, чтобы передать команду клиенту и получить результат её
  выполнения — агент только предлагает команду, клиент исполняет сам (как и всегда, §4.4).
- **Вход:** функция принимает `TriageResult` (не заново `tenant_id`+`raw_message` с нуля) —
  продолжает тот же `incident_id`/aggregate, не создаёт новый инцидент.
- **Решение resolved/needs_bug_report:** `kb_closable=True` (из Triage) → `resolved`, иначе
  → `needs_bug_report`. Без живого канала нет способа получить от клиента подтверждение
  «решено» — доверяем классификации по KB как единственному источнику self-closure в v1.

## Компоненты

### 1. `KnowledgeBase.get_by_id` (`domain/knowledge_base`)

```python
async def get_by_id(tenant_id: str, article_id: str) -> KbArticle | None
```

`TriageResult` несёт только `kb_article_id`, не полный объект статьи — нужна отдельная
функция получить статью целиком для использования её текста как advisory.

### 2. `diagnostic_command_catalog.search` (`domain/diagnostics/diagnostic_collector`)

```python
async def search(tenant_id: str, query: str, limit: int = 1) -> list[DiagnosticCommand]
```

Тот же ILIKE-паттерн, что и `KnowledgeBase.search`, по колонке `scenario`.
`DiagnosticCommand` — dataclass: `id, scenario, command, environment_version, is_read_only`.

### 3. `screenshot_library.search` (`domain/diagnostics/diagnostic_collector`)

```python
async def search(tenant_id: str, query: str, limit: int = 3) -> list[Screenshot]
```

ILIKE по `step_description`. `Screenshot` — dataclass: `id, ui_version, step_description, image_ref`.

### 4. Diagnostic Collector (`domain/diagnostics/diagnostic_collector`)

```python
async def collect(
    tenant_id: str,
    raw_message: str,
    triage_result: TriageResult,
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    kb_get_by_id: Callable = kb_get_by_id_default,
    command_search: Callable = command_search_default,
    screenshot_search: Callable = screenshot_search_default,
) -> DiagnosticResult
```

**Ветка `triage_result.kb_closable is True`:**
1. `article = await kb_get_by_id(tenant_id, triage_result.kb_article_id)`.
2. Без LLM-вызова: `advisory_text = article.body` (курируемый контент уже клиентоориентирован —
   лишний вызов модели не нужен, бережнее по тратам согласно стратегии Apliteni).
3. Публикация `incident.resolved` (`aggregate_id = triage_result.incident_id`), payload
   `{resolution: "kb_article", kb_article_id, advisory_text}`.

**Ветка `triage_result.kb_closable is False`:**
1. `commands = await command_search(tenant_id, triage_result.topic_tag)`,
   `screenshots = await screenshot_search(tenant_id, triage_result.topic_tag)`.
2. STANDARD-tier `ModelRequest`: система просит собрать диагностическую сводку для клиента,
   опираясь **только** на переданные найденные команду (если есть) и скриншоты (если есть) —
   те же анти-галлюцинаторные гарантии, что у Triage: модель не может сослаться на команду или
   скриншот, которых не было в контексте. JSON-ответ `{advisory_text, escalated_to_human}`.
   `escalated_to_human=true`, если `commands` пуст — модель не придумывает команду вне каталога
   (§4.4 — единственное жёсткое исключение "Curator предлагает, человек утверждает": сюда агент
   вообще не имеет доступа мимо каталога).
3. Один retry на невалидный JSON, как в Triage; вторая неудача — исключение
   `DiagnosticCollectionError`, без тихого fallback.
4. Публикация `incident.needs_bug_report`, payload
   `{advisory_text, matched_command, screenshot_refs, escalated_to_human}`.

## Тестирование

По образцу Triage Agent: `FakeProvider`/`FakePublisher` для юнит-тестов `collect()` (обе ветки,
retry на невалидный JSON, escalated_to_human при пустом каталоге); реальные DB-тесты для трёх
новых lookup-функций. Ручная проверка на живом API после выполнения плана — как и с Triage,
не как закоммиченный тест.

## Коммиты

Четыре отдельных коммита: `KnowledgeBase.get_by_id` → `diagnostic_command_catalog.search` →
`screenshot_library.search` → `Diagnostic Collector`.
