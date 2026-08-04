# Design: Bug Report Composer Agent (v1)

**Дата:** 2026-08-04
**Статус:** утверждён, готов к реализации (владелец задал следующую фичу, дальнейшие решения по
реализации приняты автономно — см. `CLAUDE.md` про изменённый режим работы в этой сессии)

Ссылка на архитектуру: `docs/architecture.md` §4.3 (Bug Report Composer), §8.2 (Event Bus).
Продолжение среза: `docs/superpowers/specs/2026-08-04-triage-agent-design.md`,
`docs/superpowers/specs/2026-08-04-diagnostic-collector-design.md`.

## Контекст

Третий агент домена Diagnostics. Вход — `incident.needs_bug_report` (тот путь, где Diagnostic
Collector не смог закрыть инцидент по KB), выход — `bug_report.created`. MCP Gateway
(`bugtracker.create_report`, §9) ещё не построен (сознательно отложен, `CLAUDE.md`) — реального
внешнего трекера пока нет ни у кого. Тот же принцип, что уже применён для каталога команд в
Diagnostic Collector: без живой интеграции агент производит артефакт и кладёт его в Event Store,
а не выполняет внешний side-effect, которого не с чем выполнять.

## Решения

- **Источник контекста — Event Store, не прямая передача результата.** В отличие от
  Triage→Diagnostic Collector (где `TriageResult` передаётся напрямую в `collect()`), архитектура
  явно фиксирует для этого агента: «Read: весь диагностический трейл инцидента» (§4.3) — то есть
  контракт именно «читай историю событий», а не «прими объект от предыдущего шага». Нужна
  читающая сторона Event Bus, которой ещё нет — `EventReader.read_aggregate_events()`.
- **Нет реального `bugtracker.create_report`.** v1 публикует `bug_report.created` с полным
  содержимым спецификации в payload — это и есть артефакт. Подключение к реальному трекеру
  Keitaro — отдельная задача после того, как появится MCP Gateway/client-режим (§9.2).
- **Модель — STRONG-tier**, как зафиксировано в архитектуре («качество спецификации напрямую
  определяет скорость фикса»).
- **Не LangGraph** — снова один проход (прочитать трейл → один LLM-вызов → публикация), тот же
  аргумент, что и у предыдущих двух агентов v1: ветвления/циклов с ожиданием внешнего ввода нет.
- **Анти-галлюцинаторная дисциплина** — та же, что в Triage/Diagnostic Collector: модель обязана
  опираться только на данные из трейла, шаги воспроизведения не изобретаются из воздуха; если
  данных недостаточно, это явно отмечается в самой спецификации, а не скрывается.

## Компоненты

### 1. `EventReader` (`platform_core/event_bus`)

```python
@dataclass
class StoredEvent:
    event_type: str
    payload: dict
    metadata: dict
    created_at: datetime

class EventReader:
    async def read_aggregate_events(self, tenant_id: str, aggregate_type: str, aggregate_id: str) -> list[StoredEvent]
```

`SELECT event_type, payload, metadata, created_at FROM events WHERE aggregate_type=$1 AND
aggregate_id=$2::uuid ORDER BY created_at ASC` через `tenant_connection`. `payload`/`metadata` —
jsonb, приходят от asyncpg как текст (см. прецедент в тесте `EventPublisher`) — распаковываются
`json.loads`.

### 2. Bug Report Composer (`domain/diagnostics/bug_report_composer`)

```python
async def compose(
    tenant_id: str,
    incident_id: str,
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    read_aggregate_events: Callable = EventReader().read_aggregate_events,
) -> BugReportResult
```

1. `events = await read_aggregate_events(tenant_id, "incident", incident_id)`.
2. Найти последнее событие `incident.needs_bug_report` в трейле. Если его нет — исключение
   `BugReportCompositionError` (агент вызывается только после реальной эскалации, без тихого
   fallback на пустой отчёт). Найти также последнее `incident.triaged` (если есть) для исходного
   сообщения клиента/классификации.
3. STRONG-tier `ModelRequest`: system-промпт требует собрать «исчерпывающую тех.спецификацию, а
   не жалобу» (ключевая формулировка ценности AoR, §1.1) строго на основе переданных данных
   трейла (`raw_message`, `severity`, `topic_tag` из triaged; `advisory_text`, `matched_command`,
   `screenshot_refs`, `escalated_to_human` из needs_bug_report). JSON-ответ:
   `{title, severity, environment, steps_to_reproduce, expected_behavior, actual_behavior,
   diagnostic_summary}`. Тот же паттерн, что уже дважды подтверждён на живом API: снятие
   markdown code fence перед парсингом, один retry на невалидный JSON/схему, вторая неудача —
   исключение.
4. Публикация `bug_report.created` (`aggregate_type="incident"`, `aggregate_id=incident_id`),
   payload — все поля спецификации.
5. Возврат `BugReportResult` с теми же полями.

## Тестирование

По уже устоявшемуся образцу: `FakeProvider`/`FakePublisher` + фейковый `read_aggregate_events`
для юнит-тестов `compose()` (успех, отсутствие `incident.needs_bug_report` в трейле → исключение,
retry на невалидный JSON, двойной сбой → исключение). Реальный DB-тест для
`EventReader.read_aggregate_events` (пишем через `EventPublisher`, читаем через `EventReader`,
проверяем порядок и распаковку payload/metadata). Ручная проверка на живом API после выполнения
плана — как и с предыдущими двумя агентами.

## Коммиты

Два отдельных коммита: `EventReader` → `Bug Report Composer`.
