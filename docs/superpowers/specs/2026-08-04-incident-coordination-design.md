# Design: Incident Coordination Agent (v1)

**Дата:** 2026-08-04
**Статус:** утверждён, готов к реализации (решения по реализации приняты автономно)

Ссылка на архитектуру: `docs/architecture.md` §6.1 (Incident Coordination Agent), §8.2 (Event Bus).

## Контекст

Первый агент домена Escalation & Sync. В отличие от всех предыдущих агентов, работает не с
одним инцидентом, а сразу со **всеми** активными инцидентами тенанта за окно времени —
архитектура прямо называет это «осознанным исключением из принципа минимального доступа» (§6.1).

Для этого `EventReader` нужно расширить: текущий `read_aggregate_events` читает один
aggregate_id, а корреляции нужен **межагрегатный** запрос — «все `incident.triaged` за
последние N минут, вне зависимости от инцидента».

## Решения

- **Расширяю `EventReader`, не создаю параллельный тип.** Новый метод
  `read_events_by_type(tenant_id, event_type, since=None) -> list[StoredEvent]`. Заодно
  добавляю `aggregate_id` в сам `StoredEvent` — раньше это поле не требовалось, потому что
  `read_aggregate_events` уже знал aggregate_id от вызывающего кода, но для кросс-агрегатного
  чтения без него не обойтись, и в целом это пробел в абстракции «событие» (у события
  event-sourced системы aggregate_id — не опциональная деталь). Правлю впервые заданный тип,
  а не тащу его ограничение дальше — это небольшое, точечное исправление уже существующего
  кода, которое требуется для текущей задачи (см. правило работы в существующей кодовой базе:
  улучшать то, что мешает текущей работе, не более).
- **Окно времени — параметр вызывающего кода, не дефолт функции.** `since` вычисляется внутри
  тела функции (`now() - window`), не как значение параметра по умолчанию — иначе дефолт
  «застынет» на момент определения функции (первого импорта модуля), а не будет пересчитываться
  при каждом вызове.
- **Модель — STANDARD-tier.** Задача — сопоставление по времени/теме, не глубокая диагностика и
  не критичный по цене ошибки текст (как Bug Report Composer).
- **Короткое замыкание без LLM-вызова, если инцидентов меньше двух** — сравнивать нечего,
  вызывать модель бессмысленно и не бесплатно.
- **Не подавляет повторную обработку дублирующих инцидентов.** «master-инцидент» и merge — это
  обнаружение и разметка связи, не изменение маршрутизации остальных агентов (Triage/Diagnostic
  Collector и т.д. по-прежнему обрабатывают каждый инцидент независимо) — для реальной
  оркестрации нужен живой канал и pipeline-слой, которых пока нет (тот же принцип, что уже
  применялся к MCP Gateway/bugtracker/Slack).
- **Два типа событий**, оба укладываются в «Write: только merge и статус» (§6.1):
  `incident.merged` (на каждом не-master инциденте группы, ссылка на master) и
  `incident.status_published` (на master-инциденте, сводный статус для всех участников группы).
- **Anti-hallucination:** `master_incident_id`, предложенный моделью, обязан быть одним из
  `incident_id` в её же группе — иначе валидация отклоняет ответ (тот же паттерн, что и
  `kb_article_id` в Triage).

## Компоненты

### 1. Расширение `EventReader` (`platform_core/event_bus`)

```python
@dataclass
class StoredEvent:
    aggregate_id: str  # новое поле
    event_type: str
    payload: dict
    metadata: dict
    created_at: datetime

class EventReader:
    async def read_aggregate_events(...) -> list[StoredEvent]  # без изменений в сигнатуре
    async def read_events_by_type(
        self, tenant_id: str, event_type: str, since: datetime | None = None
    ) -> list[StoredEvent]
```

### 2. Incident Coordination Agent (`domain/escalation_sync/incident_coordination`)

```python
async def coordinate(
    tenant_id: str,
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    read_events_by_type=EventReader().read_events_by_type,
    window_minutes: int = 60,
) -> CoordinationResult
```

1. `since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)`.
2. `triaged = await read_events_by_type(tenant_id, "incident.triaged", since=since)`.
3. Если `len(triaged) < 2` — `CoordinationResult(correlated=False, groups=[])`, без LLM-вызова.
4. STANDARD-tier запрос со списком `{incident_id, severity, topic_tag, raw_message, triaged_at}`
   по каждому инциденту. Модель возвращает JSON:
   `{"groups": [{"incident_ids": [...], "master_incident_id": "...", "status_summary": "..."}],
   "reasoning": "..."}` (`groups` — пустой список, если корреляций нет). Тот же
   markdown-fence-strip + один retry.
5. Для каждой группы: `master_incident_id` должен входить в `incident_ids` этой группы —
   иначе тот же retry-цикл, что и на невалидном JSON (не отдельная ошибка). Публикуются
   `incident.merged` на каждый не-master `incident_id` (`payload={merged_into: master_id,
   status_summary}`) и одно `incident.status_published` на `master_incident_id`
   (`payload={member_incident_ids, status_summary}`).
6. Возврат `CoordinationResult(correlated=bool(groups), groups=[IncidentGroup(...)])`.

## Тестирование

Юнит-тесты `coordinate()`: короткое замыкание при <2 инцидентах (без вызова FakeProvider),
корреляция найдена → оба события опубликованы с корректными aggregate_id, корреляции не найдено
(пустой `groups`) → ничего не публикуется, невалидный `master_incident_id` (не входит в группу)
→ retry, двойной сбой → исключение. Реальный DB-тест для `EventReader.read_events_by_type`
(пишем через `EventPublisher`/несколько aggregate_id, проверяем что все найдены и что `since`
фильтрует старые). Ручная проверка на живом API после выполнения плана.

## Коммиты

Два отдельных коммита: расширение `EventReader` → Incident Coordination Agent.
