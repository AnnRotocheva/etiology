# Design: Approval Gate + Knowledge Curator Agent (v1)

**Дата:** 2026-08-04
**Статус:** утверждён, готов к реализации (решения по реализации приняты автономно —
режим работы сессии изменён на полную автономию, см. `CLAUDE.md`)

Ссылка на архитектуру: `docs/architecture.md` §8.1 (Approval Gate), §5 (Knowledge Curator Agent).

## Контекст

Первая фича вне v1-среза Diagnostics. Knowledge Curator Agent зависит от Approval Gate
(«Не публикует в KB напрямую» — выход только через очередь предложений), а Approval Gate ещё
не реализован (только таблица `approval_gate`, миграция 0003, уже есть) — поэтому это снова один
спек на два коммита, как Triage Agent зависел от EventPublisher/KB search.

## Решения

- **Approval Gate — платформенный сервис, без доменной логики**, как и зафиксировано в §8.1:
  `submit`/`list_pending`/`approve`/`reject` над таблицей `approval_gate`. Без `approve`/`reject`
  цикл «черновик → человек → публикация» был бы незамкнутым (одобрять было бы нечем) — включаю
  все четыре операции в этот план, не только `submit`, который нужен Curator'у напрямую.
- **Без Slack-уведомления.** §8.1 упоминает «уведомление через существующий канал (Slack)» —
  Slack-интеграции в кодовой базе нет (channels/ — пустой стаб), тот же принцип, что уже применён
  к `bugtracker.create_report` в Bug Report Composer: без живой интеграции — не строим её ради
  одной фичи, очередь в Postgres и есть артефакт v1.
- **Материализация одобренного предложения в `knowledge_base_articles` — вне кода, вручную.**
  Это согласуется с уже существующим принципом: каталог команд и библиотека скриншотов тоже
  «пополняются только вручную» человеком-куратором. `approve()` переводит статус в `approved` —
  дальше человек сам добавляет статью в KB, как и раньше. Building an automated "publish on
  approve" path сейчас — расширение вне того, что просили и вне того, что нужно для этой фичи.
- **Curator анализирует один закрытый инцидент, не кросс-инцидентные паттерны.** Архитектура
  говорит «анализ закрытых инцидентов, поиск паттернов» — полноценный кросс-инцидентный анализ
  (кластеризация по множеству инцидентов) требует агрегации через историю, которой сейчас нет
  инфраструктуры для эффективного построения (Event Store — не аналитическая БД). v1: получить
  `incident_id`, прочитать его трейл целиком (переиспользуем `EventReader` из Bug Report
  Composer), решить — представляет ли *именно этот* инцидент достаточно общий, переиспользуемый
  паттерн для KB, или это одноразовый случай. Кросс-инцидентная кластеризация — материал для
  отдельной будущей итерации, не тихая заглушка сейчас, а осознанно меньший объём.
- **Curator читает KB перед предложением** (§5, «Read: KB, Event Store» — прямо в правах
  доступа) — переиспользуем `knowledge_base.search`, чтобы модель могла решить `should_propose=false`
  и сослаться на уже существующую статью вместо дублирования, а не проверять это вслепую.
- **Модель — STANDARD-tier.** Архитектура не фиксирует уровень явно для этого агента; ставлю
  между FAST (Triage — чистая классификация) и STRONG (Bug Report Composer — качество текста
  прямо определяет скорость фикса). Человек всё равно проверяет каждое предложение через Approval
  Gate — цена ошибки модели ниже, чем у Bug Report Composer, но выше, чем у Triage.
- **Не LangGraph** — снова один проход, тот же аргумент, что и у трёх агентов Diagnostics.

## Компоненты

### 1. `ApprovalGate` (`platform_core/approval_gate`)

```python
@dataclass
class ApprovalItem:
    id: str
    object_type: str
    payload: dict
    status: str  # "pending" | "approved" | "rejected"
    created_by: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime

class ApprovalGate:
    async def submit(self, tenant_id: str, object_type: str, payload: dict, created_by: str) -> str
    async def list_pending(self, tenant_id: str, object_type: str | None = None) -> list[ApprovalItem]
    async def approve(self, tenant_id: str, approval_id: str, reviewed_by: str) -> None
    async def reject(self, tenant_id: str, approval_id: str, reviewed_by: str) -> None
```

Все запросы через `tenant_connection` (RLS). `submit` — `INSERT ... RETURNING id`, статус по
умолчанию `pending` (задаётся схемой). `approve`/`reject` — `UPDATE ... SET status=..., reviewed_by=...,
reviewed_at=now() WHERE id=$1 AND status='pending'` (нельзя повторно ревьюить уже решённый пункт —
`UPDATE` затрагивает 0 строк, вызывающий код может это проверить по возвращаемому статусу
подключения; для v1 просто не бросаем исключение на 0 затронутых строк — не самая частая гонка
для одного человека в команде, а не многопользовательской системы).

### 2. Knowledge Curator Agent (`domain/knowledge_base/curator.py`)

```python
async def curate(
    tenant_id: str,
    incident_id: str,
    *,
    gateway: ModelGateway,
    approval_gate: ApprovalGate,
    publisher: EventPublisher,
    read_aggregate_events=EventReader().read_aggregate_events,
    kb_search=knowledge_base.search,
) -> CuratorResult
```

1. `events = await read_aggregate_events(tenant_id, "incident", incident_id)`.
2. Терминальное событие — последнее `bug_report.created`, если есть, иначе последнее
   `incident.resolved`. Если ни того ни другого нет — `CurationError` (Curator анализирует
   только закрытые инциденты, не тихий no-op).
3. `existing = await kb_search(tenant_id, <тема из triaged/терминального события>)` — найденные
   статьи передаются в промпт, чтобы модель могла сослаться на них при отказе предлагать
   дубликат.
4. STANDARD-tier запрос: `{should_propose: bool, title, body, topic_tag, reasoning}`. Если модель
   считает случай слишком специфичным для конкретного клиента/не несущим переиспользуемого
   паттерна — `should_propose=false`, `title`/`body`/`topic_tag` — `null`. Тот же
   markdown-fence-strip + один retry, что и в трёх агентах Diagnostics.
5. Если `should_propose=true`: `suggestion_id = await approval_gate.submit(tenant_id, "kb_suggestion",
   {title, body, topic_tag, source_incident_id, reasoning}, created_by="knowledge_curator_agent")`,
   затем публикуется `kb_suggestion.created` (`aggregate_type="kb_suggestion"`,
   `aggregate_id=suggestion_id`) — тот же принцип двойной записи, что и у остальных агентов:
   Approval Gate — очередь для человека, Event Store — неизменяемый аудит-лог.
6. Если `should_propose=false` — не ошибка, легитимный результат: ничего не публикуется и не
   отправляется на approval, `CuratorResult(proposed=False, ...)`.

## Тестирование

По устоявшемуся образцу: `FakeProvider`/`FakePublisher`/фейковые `read_aggregate_events`/`kb_search`
для юнит-тестов `curate()` (should_propose true/false, отсутствие терминального события →
исключение, retry на невалидный JSON). `ApprovalGate` — реальные DB-тесты для всех четырёх
методов (`etiology_app` имеет SELECT/INSERT/UPDATE на `approval_gate` — полный CRUD кроме DELETE,
без ограничений, в отличие от diagnostic_command_catalog/screenshot_library). Ручная проверка на
живом API после выполнения плана.

## Коммиты

Два отдельных коммита: `ApprovalGate` → `Knowledge Curator Agent`.
