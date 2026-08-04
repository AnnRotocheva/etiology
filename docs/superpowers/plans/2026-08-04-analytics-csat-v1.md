# Analytics & CSAT read-model v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать `domain/analytics` — чистые SQL-проекции над Event Store (top-topics,
resolution rate, TTFR-приближение) и CSAT capture/summary. Без LLM, без новой таблицы.

**Architecture:** Простые модульные async-функции (не классы — нет общего состояния), тот же
паттерн, что и `knowledge_base.search`. `csat.recorded` — новый тип события в уже существующей
таблице `events`, публикуется через уже существующий `EventPublisher`.

**Tech Stack:** Python 3.12+, asyncpg, pytest (session-scoped event loop). Никаких новых
зависимостей.

## Global Constraints

- RLS через `tenant_connection(tenant_id)` — как весь остальной код сессии.
- Не делить на ноль: `resolution_rate.rate = 0.0`, `TtfrStats.avg_seconds/median_seconds = None`,
  `CsatSummary.avg_score = None`, если данных нет — не исключение.
- `record_csat` валидирует `1 <= score <= 5` до записи в Event Store — `ValueError` на
  некорректном значении.
- TTFR — приближение (время до ближайшего следующего события инцидента), не точная метрика
  «когда клиент реально получил ответ».

Ссылка на дизайн: `docs/superpowers/specs/2026-08-04-analytics-csat-design.md`.

---

### Task 1: Analytics & CSAT read-model

**Files:**
- Create: `src/etiology/domain/analytics/reporting.py`
- Create: `src/etiology/domain/analytics/feedback.py`
- Create: `src/etiology/domain/analytics/__init__.py`
- Create: `tests/domain/analytics/__init__.py`
- Test: `tests/domain/analytics/test_reporting.py`
- Test: `tests/domain/analytics/test_feedback.py`

**Interfaces:**
- Consumes: `EventPublisher` из `etiology.platform_core.event_bus` (уже существует);
  `tenant_connection` из `etiology.data.db.pool` (уже существует).
- Produces: `TopicCount`, `ResolutionRate`, `TtfrStats` (dataclasses), `top_topics`,
  `resolution_rate`, `ttfr_stats` (async-функции) из `reporting.py`; `CsatSummary` (dataclass),
  `record_csat`, `csat_summary` (async-функции) из `feedback.py`. Всё экспортируется из
  `etiology.domain.analytics`.

- [ ] **Step 1: Создать пакет для тестовой директории**

`tests/domain/analytics/__init__.py` — пустой файл.

- [ ] **Step 2: Написать падающие тесты**

`tests/domain/analytics/test_reporting.py`:
```python
import uuid

from etiology.domain.analytics import resolution_rate, top_topics, ttfr_stats
from etiology.platform_core.event_bus import EventPublisher


async def test_top_topics_counts_by_topic_tag(tenant_id):
    publisher = EventPublisher()
    for topic in ["tracking", "tracking", "billing"]:
        await publisher.publish(
            tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
            aggregate_id=str(uuid.uuid4()), payload={"topic_tag": topic},
        )

    results = await top_topics(tenant_id, limit=5)

    counts = {r.topic_tag: r.count for r in results}
    assert counts["tracking"] == 2
    assert counts["billing"] == 1


async def test_top_topics_returns_empty_list_when_no_data(tenant_id):
    results = await top_topics(tenant_id)

    assert results == []


async def test_resolution_rate_computes_ratio(tenant_id):
    publisher = EventPublisher()
    incident_a = str(uuid.uuid4())
    incident_b = str(uuid.uuid4())
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
        aggregate_id=incident_a, payload={},
    )
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
        aggregate_id=incident_b, payload={},
    )
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.resolved", aggregate_type="incident",
        aggregate_id=incident_a, payload={},
    )

    rate = await resolution_rate(tenant_id)

    assert rate.triaged_count == 2
    assert rate.resolved_count == 1
    assert rate.rate == 0.5


async def test_resolution_rate_is_zero_without_division_error_when_no_data(tenant_id):
    rate = await resolution_rate(tenant_id)

    assert rate.triaged_count == 0
    assert rate.resolved_count == 0
    assert rate.rate == 0.0


async def test_ttfr_stats_computes_average_and_median(tenant_id):
    publisher = EventPublisher()
    incident_id = str(uuid.uuid4())
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
        aggregate_id=incident_id, payload={},
    )
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.resolved", aggregate_type="incident",
        aggregate_id=incident_id, payload={},
    )

    stats = await ttfr_stats(tenant_id)

    assert stats.count == 1
    assert stats.avg_seconds is not None
    assert stats.avg_seconds >= 0
    assert stats.median_seconds == stats.avg_seconds


async def test_ttfr_stats_returns_none_averages_when_no_data(tenant_id):
    stats = await ttfr_stats(tenant_id)

    assert stats.count == 0
    assert stats.avg_seconds is None
    assert stats.median_seconds is None
```

`tests/domain/analytics/test_feedback.py`:
```python
import uuid

import pytest

from etiology.domain.analytics import csat_summary, record_csat
from etiology.platform_core.event_bus import EventPublisher


async def test_record_csat_and_summary(tenant_id):
    publisher = EventPublisher()
    await record_csat(tenant_id, str(uuid.uuid4()), 5, publisher, comment="отлично")
    await record_csat(tenant_id, str(uuid.uuid4()), 3, publisher)

    summary = await csat_summary(tenant_id)

    assert summary.count == 2
    assert summary.avg_score == 4.0


async def test_csat_summary_returns_none_average_when_no_data(tenant_id):
    summary = await csat_summary(tenant_id)

    assert summary.count == 0
    assert summary.avg_score is None


async def test_record_csat_rejects_score_out_of_range(tenant_id):
    publisher = EventPublisher()
    incident_id = str(uuid.uuid4())

    with pytest.raises(ValueError):
        await record_csat(tenant_id, incident_id, 6, publisher)

    with pytest.raises(ValueError):
        await record_csat(tenant_id, incident_id, 0, publisher)
```

- [ ] **Step 3: Запустить тесты, убедиться что падают**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/analytics -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 4: Реализовать `reporting.py` и `feedback.py`**

`src/etiology/domain/analytics/reporting.py`:
```python
import statistics
from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection


@dataclass
class TopicCount:
    topic_tag: str | None
    count: int


@dataclass
class ResolutionRate:
    triaged_count: int
    resolved_count: int
    rate: float


@dataclass
class TtfrStats:
    count: int
    avg_seconds: float | None
    median_seconds: float | None


async def top_topics(tenant_id: str, limit: int = 10) -> list[TopicCount]:
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT payload->>'topic_tag' AS topic_tag, count(*) AS cnt
            FROM events
            WHERE event_type = 'incident.triaged'
            GROUP BY payload->>'topic_tag'
            ORDER BY cnt DESC
            LIMIT $1
            """,
            limit,
        )
    return [TopicCount(topic_tag=row["topic_tag"], count=row["cnt"]) for row in rows]


async def resolution_rate(tenant_id: str) -> ResolutionRate:
    async with tenant_connection(tenant_id) as conn:
        row = await conn.fetchrow(
            """
            SELECT
                (SELECT count(*) FROM events WHERE event_type = 'incident.triaged') AS triaged_count,
                (SELECT count(*) FROM events WHERE event_type = 'incident.resolved') AS resolved_count
            """
        )
    triaged_count = row["triaged_count"]
    resolved_count = row["resolved_count"]
    rate = resolved_count / triaged_count if triaged_count else 0.0
    return ResolutionRate(triaged_count=triaged_count, resolved_count=resolved_count, rate=rate)


async def ttfr_stats(tenant_id: str) -> TtfrStats:
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            """
            WITH triaged AS (
                SELECT aggregate_id, created_at AS triaged_at
                FROM events
                WHERE event_type = 'incident.triaged'
            ),
            first_response AS (
                SELECT e.aggregate_id, min(e.created_at) AS response_at
                FROM events e
                JOIN triaged t ON t.aggregate_id = e.aggregate_id
                WHERE e.event_type <> 'incident.triaged' AND e.created_at >= t.triaged_at
                GROUP BY e.aggregate_id
            )
            SELECT t.triaged_at, f.response_at
            FROM triaged t
            JOIN first_response f ON f.aggregate_id = t.aggregate_id
            """
        )
    deltas = [(row["response_at"] - row["triaged_at"]).total_seconds() for row in rows]
    if not deltas:
        return TtfrStats(count=0, avg_seconds=None, median_seconds=None)
    return TtfrStats(
        count=len(deltas),
        avg_seconds=statistics.mean(deltas),
        median_seconds=statistics.median(deltas),
    )
```

`src/etiology/domain/analytics/feedback.py`:
```python
from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection
from etiology.platform_core.event_bus import EventPublisher


@dataclass
class CsatSummary:
    count: int
    avg_score: float | None


async def record_csat(
    tenant_id: str,
    incident_id: str,
    score: int,
    publisher: EventPublisher,
    comment: str | None = None,
) -> None:
    if not 1 <= score <= 5:
        raise ValueError(f"score должен быть в диапазоне 1..5, получено {score}")
    await publisher.publish(
        tenant_id=tenant_id,
        event_type="csat.recorded",
        aggregate_type="incident",
        aggregate_id=incident_id,
        payload={"score": score, "comment": comment},
    )


async def csat_summary(tenant_id: str) -> CsatSummary:
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            "SELECT (payload->>'score')::int AS score FROM events WHERE event_type = 'csat.recorded'"
        )
    scores = [row["score"] for row in rows]
    if not scores:
        return CsatSummary(count=0, avg_score=None)
    return CsatSummary(count=len(scores), avg_score=sum(scores) / len(scores))
```

`src/etiology/domain/analytics/__init__.py`:
```python
from .feedback import CsatSummary, csat_summary, record_csat
from .reporting import ResolutionRate, TopicCount, TtfrStats, resolution_rate, top_topics, ttfr_stats

__all__ = [
    "TopicCount",
    "ResolutionRate",
    "TtfrStats",
    "top_topics",
    "resolution_rate",
    "ttfr_stats",
    "CsatSummary",
    "record_csat",
    "csat_summary",
]
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/analytics -v`
Expected: PASS (9 тестов)

- [ ] **Step 6: Прогнать весь набор тестов проекта**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (все тесты)

- [ ] **Step 7: Commit**

```bash
git add src/etiology/domain/analytics tests/domain/analytics
git commit -m "feat: добавлен Analytics & CSAT read-model"
```

---

## После выполнения плана

Ручная проверка не на живом API (эта фича не вызывает модель) — вместо этого прогнать
`top_topics`/`resolution_rate`/`ttfr_stats`/`csat_summary` на tenant'е с данными, накопленными
за сессию через прошлые смоук-тесты (или на свежих данных), и убедиться, что цифры выглядят
осмысленно.
