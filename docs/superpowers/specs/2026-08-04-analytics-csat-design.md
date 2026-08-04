# Design: Analytics & CSAT read-model (v1)

**Дата:** 2026-08-04
**Статус:** утверждён, готов к реализации (решения по реализации приняты автономно)

Ссылка на архитектуру: `docs/architecture.md` §7 (Аналитика и CSAT).

## Контекст

Впервые за сессию — не агент. §7 прямо фиксирует: «Реализуется как read-model поверх Event
Store, без изменения ядра». Никаких LLM-вызовов, никакого `ModelGateway`/`ApprovalGate` — чистые
SQL-проекции над уже накопленными в `events` данными шести построенных агентов.

## Решения

- **Без новой таблицы.** CSAT-оценка — это `csat.recorded`, ещё один тип события в уже
  существующем Event Store (`aggregate_type="incident"`), а не отдельная таблица — тот же
  принцип, что уже применялся к `kb_suggestion.created`/`post_mortem.drafted`: новый вид факта
  не требует новой схемы, если Event Store уже это умеет хранить.
- **CSAT: только capture, без trigger.** §7 говорит «Feedback capture... триггерит CSAT-опрос
  через существующие каналы доставки» — доставка опроса клиенту требует живого канала, которого
  нет (тот же принцип не строить недостроенное, что и у Slack/bugtracker/MCP на всей сессии).
  v1 даёт `record_csat()` — записать уже полученную оценку — и `csat_summary()` — прочитать то,
  что накопилось. Пока каналов нет, `csat_summary()` будет возвращать `count=0` — это ожидаемое,
  а не ошибочное состояние.
- **TTFR (Time To First Response) — приближение по трейлу, не отдельное событие.** Явного
  события «первый ответ клиенту» в схеме нет. В текущей архитектуре первый содержательный ответ
  клиенту — это следующее после `incident.triaged` событие на том же инциденте (`incident.resolved`
  либо `incident.needs_bug_report` — оба несут `advisory_text`, то есть уже отправленный клиенту
  текст). TTFR v1 = время между `incident.triaged` и ближайшим следующим событием того же
  инцидента. Не идеально (не различает «ответили» и «эскалировали»), но измеримо прямо сейчас без
  новой инфраструктуры, и я явно обозначаю приближение как приближение, а не выдаю его за точную
  метрику.
- **Доля self-service resolution** — прямая метрика из §1.1 («доля инцидентов, решённых без
  привлечения разработки»): `count(incident.resolved) / count(incident.triaged)`.
- **Top-topics/тренды** — группировка `incident.triaged.payload->>'topic_tag'`, без изменений
  схемы (topic_tag уже есть в каждом `incident.triaged` с первого дня — то самое «обязательное
  требование к схеме событий с самого начала», о котором явно предупреждает §7).
- **Median считается в Python, не SQL** — `percentile_cont` в Postgres работает, но для объёма
  данных одного тенанта в прототипе (не аналитическая БД под нагрузкой) не даёт ничего, кроме
  лишней сложности запроса; забираем дельты и считаем `statistics.median` на стороне приложения.
- **Простые функции, не классы.** В отличие от `EventPublisher`/`ApprovalGate` (стейтфул-ish,
  логически сгруппированные операции), здесь нет общего состояния между функциями — тот же
  паттерн, что и `knowledge_base.search`/`get_by_id` (модульные функции, не класс).

## Компоненты (`domain/analytics`)

```python
# reporting.py
@dataclass
class TopicCount:
    topic_tag: str | None
    count: int

@dataclass
class ResolutionRate:
    triaged_count: int
    resolved_count: int
    rate: float  # 0.0, если triaged_count == 0 — не деление на ноль

@dataclass
class TtfrStats:
    count: int
    avg_seconds: float | None  # None, если count == 0
    median_seconds: float | None

async def top_topics(tenant_id: str, limit: int = 10) -> list[TopicCount]
async def resolution_rate(tenant_id: str) -> ResolutionRate
async def ttfr_stats(tenant_id: str) -> TtfrStats

# feedback.py
@dataclass
class CsatSummary:
    count: int
    avg_score: float | None

async def record_csat(
    tenant_id: str, incident_id: str, score: int, publisher: EventPublisher, comment: str | None = None
) -> None  # score вне 1..5 -> ValueError, до записи в Event Store
async def csat_summary(tenant_id: str) -> CsatSummary
```

Все запросы через `tenant_connection(tenant_id)` (RLS) — как и весь остальной код сессии.

## Тестирование

Реальные DB-тесты (как `EventPublisher`/`EventReader`/`ApprovalGate` — здесь нет причин
мокать модель, потому что модели нет вовсе): публикуем несколько событий через уже существующий
`EventPublisher`, читаем через новые функции, проверяем агрегаты. `resolution_rate`/`ttfr_stats`/
`csat_summary` — отдельные тесты на пустых данных (`count=0`, без деления на ноль/исключений) и
на нескольких инцидентах. `record_csat` — тест на `ValueError` при score вне диапазона 1..5.

## Коммит

Один коммит — оба файла (`reporting.py`, `feedback.py`) логически одна фича, без
последовательной зависимости друг от друга, которую стоило бы разносить по коммитам.
