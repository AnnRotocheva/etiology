# Incident Coordination Agent v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Расширить `EventReader` кросс-агрегатным чтением (`read_events_by_type`), затем
реализовать Incident Coordination Agent (`coordinate()`) — обнаружение, что несколько инцидентов
за окно времени представляют один сбой, публикация `incident.merged`/`incident.status_published`.

**Architecture:** `StoredEvent` получает новое обязательное поле `aggregate_id` (точечный фикс —
это поле было нужно и раньше, просто не требовалось до кросс-агрегатных запросов). `coordinate()`
— простая async-функция с коротким замыканием без LLM-вызова, если инцидентов меньше двух.

**Tech Stack:** Python 3.12+, asyncpg, pydantic v2, pytest (session-scoped event loop), уже
существующие `ModelGateway`, `EventPublisher`.

## Global Constraints

- RLS через `tenant_connection(tenant_id)`.
- Модель — `ModelTier.STANDARD`.
- Markdown-fence-strip + один retry на невалидный JSON/схему (включая невалидный
  `master_incident_id`, не входящий в свою же группу) — вторая неудача — исключение.
- `since` для окна времени вычисляется в теле функции (`datetime.now(timezone.utc) - timedelta(...)`),
  не как значение параметра по умолчанию.
- Не менять поведение `read_aggregate_events` для существующих вызывающих (Bug Report Composer,
  Knowledge Curator) — только добавить `aggregate_id` в результат.

Ссылка на дизайн: `docs/superpowers/specs/2026-08-04-incident-coordination-design.md`.

---

### Task 1: Расширение `EventReader`

**Files:**
- Modify: `src/etiology/platform_core/event_bus/reader.py`
- Modify: `tests/platform_core/event_bus/test_reader.py`
- Modify: `tests/domain/diagnostics/bug_report_composer/test_composer.py` (добавить `aggregate_id` в существующие `StoredEvent(...)`)
- Modify: `tests/domain/knowledge_base/test_curator.py` (то же самое)

**Interfaces:**
- Produces: `StoredEvent` с новым полем `aggregate_id: str` (первым в списке полей); новый метод
  `EventReader.read_events_by_type(self, tenant_id: str, event_type: str, since: datetime | None = None) -> list[StoredEvent]`.
  Task 2 использует `read_events_by_type` как DI-параметр по умолчанию.

- [ ] **Step 1: Обновить существующие тесты под новое поле `aggregate_id`**

В `tests/domain/diagnostics/bug_report_composer/test_composer.py`, в функции `_full_trail()`,
добавить `aggregate_id="incident-fixture"` первым именованным аргументом в оба `StoredEvent(...)`.

В `tests/domain/knowledge_base/test_curator.py`, в функции `_trail_with_bug_report()`, добавить
`aggregate_id="incident-fixture"` во все три `StoredEvent(...)`.

- [ ] **Step 2: Запустить существующие тесты, убедиться что падают из-за отсутствующего поля**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/diagnostics/bug_report_composer tests/domain/knowledge_base/test_curator.py -v`
Expected: FAIL (`TypeError: StoredEvent.__init__() missing 1 required positional argument: 'aggregate_id'`) — до Step 1 если запущено раньше; после Step 1 эти тесты уже должны падать по другой причине (см. Step 3) или не падать вовсе, если явно не трогать `reader.py` — фактически падать будут только если Step 1 применён, а `reader.py` ещё нет. Порядок: Step 1 → тесты этого файла ломаются из-за отсутствующего поля в самом классе `StoredEvent` (это и есть ожидаемый красный шаг для Task 1 в целом).

- [ ] **Step 3: Написать падающий тест на `read_events_by_type`**

Добавить в `tests/platform_core/event_bus/test_reader.py`:
```python
from datetime import datetime, timedelta, timezone


async def test_read_events_by_type_returns_events_across_aggregates(tenant_id):
    publisher = EventPublisher()
    reader = EventReader()
    incident_a = str(uuid.uuid4())
    incident_b = str(uuid.uuid4())

    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
        aggregate_id=incident_a, payload={"topic_tag": "a"},
    )
    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
        aggregate_id=incident_b, payload={"topic_tag": "b"},
    )

    events = await reader.read_events_by_type(tenant_id, "incident.triaged")

    found_ids = {e.aggregate_id for e in events}
    assert incident_a in found_ids
    assert incident_b in found_ids


async def test_read_events_by_type_filters_by_since(tenant_id):
    publisher = EventPublisher()
    reader = EventReader()
    incident_a = str(uuid.uuid4())

    await publisher.publish(
        tenant_id=tenant_id, event_type="incident.triaged", aggregate_type="incident",
        aggregate_id=incident_a, payload={"topic_tag": "a"},
    )

    future = datetime.now(timezone.utc) + timedelta(hours=1)
    events = await reader.read_events_by_type(tenant_id, "incident.triaged", since=future)

    assert events == []
```

Также обновить `test_read_aggregate_events_returns_events_in_order` в том же файле — добавить
проверку `assert events[0].aggregate_id == aggregate_id` и `assert events[1].aggregate_id == aggregate_id`.

- [ ] **Step 4: Запустить тесты, убедиться что падают**

Run: `.venv/Scripts/python.exe -m pytest tests/platform_core/event_bus/test_reader.py -v`
Expected: FAIL (`TypeError` на отсутствующем поле / `AttributeError: 'EventReader' object has no attribute 'read_events_by_type'`)

- [ ] **Step 5: Реализовать расширение `EventReader`**

`src/etiology/platform_core/event_bus/reader.py` (полная замена содержимого):
```python
import json
from dataclasses import dataclass
from datetime import datetime

from etiology.data.db.pool import tenant_connection


@dataclass
class StoredEvent:
    aggregate_id: str
    event_type: str
    payload: dict
    metadata: dict
    created_at: datetime


class EventReader:
    """Читающая сторона Event Bus (docs/architecture.md §8.2) — дополняет
    publish-сторону (EventPublisher). Используется агентами, которым по
    контракту положено читать историю событий, а не принимать результат
    напрямую от предыдущего шага (Bug Report Composer, Knowledge Curator,
    §4.3, §5), и агентами с межагрегатным доступом (Incident Coordination,
    §6.1 — осознанное исключение из принципа минимального доступа).
    """

    async def read_aggregate_events(
        self, tenant_id: str, aggregate_type: str, aggregate_id: str
    ) -> list[StoredEvent]:
        async with tenant_connection(tenant_id) as conn:
            rows = await conn.fetch(
                """
                SELECT event_type, payload, metadata, created_at
                FROM events
                WHERE aggregate_type = $1 AND aggregate_id = $2::uuid
                ORDER BY created_at ASC
                """,
                aggregate_type,
                aggregate_id,
            )
        return [
            StoredEvent(
                aggregate_id=aggregate_id,
                event_type=row["event_type"],
                payload=json.loads(row["payload"]),
                metadata=json.loads(row["metadata"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def read_events_by_type(
        self, tenant_id: str, event_type: str, since: datetime | None = None
    ) -> list[StoredEvent]:
        async with tenant_connection(tenant_id) as conn:
            if since is None:
                rows = await conn.fetch(
                    "SELECT aggregate_id, event_type, payload, metadata, created_at "
                    "FROM events WHERE event_type = $1 ORDER BY created_at ASC",
                    event_type,
                )
            else:
                rows = await conn.fetch(
                    "SELECT aggregate_id, event_type, payload, metadata, created_at "
                    "FROM events WHERE event_type = $1 AND created_at >= $2 ORDER BY created_at ASC",
                    event_type,
                    since,
                )
        return [
            StoredEvent(
                aggregate_id=str(row["aggregate_id"]),
                event_type=row["event_type"],
                payload=json.loads(row["payload"]),
                metadata=json.loads(row["metadata"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]
```

`src/etiology/platform_core/event_bus/__init__.py` не меняется — `StoredEvent`/`EventReader` уже
экспортируются.

- [ ] **Step 6: Запустить весь набор тестов, убедиться что проходит**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (все тесты, включая обновлённые bug_report_composer/curator)

- [ ] **Step 7: Commit**

```bash
git add src/etiology/platform_core/event_bus tests/platform_core/event_bus/test_reader.py tests/domain/diagnostics/bug_report_composer/test_composer.py tests/domain/knowledge_base/test_curator.py
git commit -m "feat: EventReader.read_events_by_type + aggregate_id в StoredEvent"
```

---

### Task 2: Incident Coordination Agent

**Files:**
- Create: `src/etiology/domain/escalation_sync/__init__.py`
- Create: `src/etiology/domain/escalation_sync/incident_coordination/__init__.py`
- Create: `src/etiology/domain/escalation_sync/incident_coordination/agent.py`
- Create: `tests/domain/escalation_sync/__init__.py`
- Create: `tests/domain/escalation_sync/incident_coordination/__init__.py`
- Test: `tests/domain/escalation_sync/incident_coordination/test_agent.py`

**Interfaces:**
- Consumes: `EventReader`, `StoredEvent`, `EventPublisher` из `etiology.platform_core.event_bus`
  (Task 1); `ModelGateway`, `ModelMessage`, `ModelRequest`, `ModelTier` из
  `etiology.agent.model_gateway`.
- Produces: `IncidentGroup` (dataclass: `incident_ids: list[str], master_incident_id: str,
  status_summary: str`), `CoordinationResult` (dataclass: `correlated: bool, groups: list[IncidentGroup]`),
  `CoordinationError(RuntimeError)`, `async def coordinate(tenant_id: str, *, gateway: ModelGateway,
  publisher: EventPublisher, read_events_by_type=EventReader().read_events_by_type,
  window_minutes: int = 60) -> CoordinationResult`. Экспортируются из
  `etiology.domain.escalation_sync.incident_coordination`.

- [ ] **Step 1: Создать пакеты для тестовой директории**

`tests/domain/escalation_sync/__init__.py` — пустой файл.
`tests/domain/escalation_sync/incident_coordination/__init__.py` — пустой файл.

- [ ] **Step 2: Написать падающие тесты**

`tests/domain/escalation_sync/incident_coordination/test_agent.py`:
```python
import json
from datetime import datetime, timezone

import pytest

from etiology.agent.model_gateway import ModelGateway, ModelRequest, ModelResponse, ModelTier
from etiology.agent.model_gateway.base import ModelProvider
from etiology.domain.escalation_sync.incident_coordination import CoordinationError, coordinate
from etiology.platform_core.event_bus import StoredEvent


class FakeProvider(ModelProvider):
    def __init__(self, name: str, responses: list[str]):
        self.name = name
        self._responses = list(responses)
        self.calls = 0

    def supports_tier(self, tier: ModelTier) -> bool:
        return tier == ModelTier.STANDARD

    async def complete(self, request: ModelRequest) -> ModelResponse:
        content = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return ModelResponse(
            content=content, stop_reason="end_turn", provider=self.name,
            model="fake-model", input_tokens=1, output_tokens=1,
        )


class FakePublisher:
    def __init__(self):
        self.calls = []

    async def publish(self, tenant_id, event_type, aggregate_type, aggregate_id, payload, metadata=None):
        self.calls.append(
            dict(
                tenant_id=tenant_id, event_type=event_type, aggregate_type=aggregate_type,
                aggregate_id=aggregate_id, payload=payload, metadata=metadata,
            )
        )


def _now():
    return datetime.now(timezone.utc)


def _triaged(incident_id, topic_tag):
    return StoredEvent(
        aggregate_id=incident_id, event_type="incident.triaged",
        payload={"topic_tag": topic_tag, "severity": "high", "raw_message": "msg"},
        metadata={}, created_at=_now(),
    )


async def _one_incident(tenant_id, event_type, since=None):
    return [_triaged("incident-1", "tracking")]


async def _no_incidents(tenant_id, event_type, since=None):
    return []


def _two_incidents():
    return [_triaged("incident-1", "tracking"), _triaged("incident-2", "tracking")]


async def _read_two_incidents(tenant_id, event_type, since=None):
    return _two_incidents()


def _correlation_json(master="incident-1"):
    return json.dumps(
        {
            "groups": [
                {"incident_ids": ["incident-1", "incident-2"], "master_incident_id": master, "status_summary": "сводка"}
            ],
            "reasoning": "обоснование",
        }
    )


def _no_correlation_json():
    return json.dumps({"groups": [], "reasoning": "не связаны"})


async def test_coordinate_short_circuits_with_fewer_than_two_incidents():
    provider = FakeProvider("fake", [])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await coordinate(
        "tenant-1", gateway=gateway, publisher=publisher, read_events_by_type=_one_incident
    )

    assert result.correlated is False
    assert result.groups == []
    assert provider.calls == 0
    assert publisher.calls == []


async def test_coordinate_publishes_merge_and_status_for_correlated_group():
    provider = FakeProvider("fake", [_correlation_json(master="incident-1")])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await coordinate(
        "tenant-1", gateway=gateway, publisher=publisher, read_events_by_type=_read_two_incidents
    )

    assert result.correlated is True
    assert result.groups[0].master_incident_id == "incident-1"
    assert len(publisher.calls) == 2
    merged = next(c for c in publisher.calls if c["event_type"] == "incident.merged")
    status = next(c for c in publisher.calls if c["event_type"] == "incident.status_published")
    assert merged["aggregate_id"] == "incident-2"
    assert merged["payload"]["merged_into"] == "incident-1"
    assert status["aggregate_id"] == "incident-1"


async def test_coordinate_returns_no_correlation_without_publishing():
    provider = FakeProvider("fake", [_no_correlation_json()])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await coordinate(
        "tenant-1", gateway=gateway, publisher=publisher, read_events_by_type=_read_two_incidents
    )

    assert result.correlated is False
    assert publisher.calls == []


async def test_coordinate_retries_when_master_incident_id_not_in_group():
    provider = FakeProvider(
        "fake", [_correlation_json(master="incident-does-not-exist"), _correlation_json(master="incident-1")]
    )
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await coordinate(
        "tenant-1", gateway=gateway, publisher=publisher, read_events_by_type=_read_two_incidents
    )

    assert provider.calls == 2
    assert result.groups[0].master_incident_id == "incident-1"


async def test_coordinate_raises_after_two_malformed_responses():
    provider = FakeProvider("fake", ["не json", "тоже не json"])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    with pytest.raises(CoordinationError):
        await coordinate(
            "tenant-1", gateway=gateway, publisher=publisher, read_events_by_type=_read_two_incidents
        )

    assert provider.calls == 2
    assert publisher.calls == []
```

- [ ] **Step 3: Запустить тесты, убедиться что падают**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/escalation_sync/incident_coordination/test_agent.py -v`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 4: Реализовать Incident Coordination Agent**

`src/etiology/domain/escalation_sync/__init__.py` — пустой файл.

`src/etiology/domain/escalation_sync/incident_coordination/agent.py`:
```python
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from etiology.agent.model_gateway import ModelGateway, ModelMessage, ModelRequest, ModelTier
from etiology.platform_core.event_bus import EventPublisher, EventReader, StoredEvent


class CoordinationError(RuntimeError):
    pass


@dataclass
class IncidentGroup:
    incident_ids: list[str]
    master_incident_id: str
    status_summary: str


@dataclass
class CoordinationResult:
    correlated: bool
    groups: list[IncidentGroup]


class _Group(BaseModel):
    incident_ids: list[str]
    master_incident_id: str
    status_summary: str


class _Correlation(BaseModel):
    groups: list[_Group]
    reasoning: str


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        _, _, rest = text.partition("\n")
        text = rest.removesuffix("```").strip()
    return text


def _build_prompt(incidents: list[StoredEvent]) -> tuple[str, str]:
    system = (
        "Ты — Incident Coordination Agent службы поддержки Keitaro. Перед тобой список инцидентов "
        "за недавнее окно времени. Определи, представляют ли несколько из них ОДИН и тот же сбой "
        "(похожая тема/симптом и близкое время создания) — их нужно объединить в группу с "
        "master-инцидентом (самым ранним из группы). Не объединяй инциденты с разными темами "
        "просто потому что они рядом по времени. Верни ТОЛЬКО JSON-объект без markdown-разметки: "
        '{"groups": [{"incident_ids": ["...", ...], "master_incident_id": "...", '
        '"status_summary": "..."}], "reasoning": "..."}. "groups" — пустой список, если корреляций нет. '
        "master_incident_id обязан быть одним из incident_ids этой же группы."
    )
    lines = [
        f"- incident_id={e.aggregate_id} severity={e.payload.get('severity')} "
        f"topic={e.payload.get('topic_tag')!r} triaged_at={e.created_at.isoformat()}\n"
        f"  сообщение: {e.payload.get('raw_message')}"
        for e in incidents
    ]
    user = "Инциденты:\n" + "\n".join(lines)
    return system, user


def _parse_correlation(text: str, known_incident_ids: set[str]) -> _Correlation:
    data = json.loads(_strip_code_fence(text))
    correlation = _Correlation.model_validate(data)
    for group in correlation.groups:
        if group.master_incident_id not in group.incident_ids:
            raise ValueError(
                f"master_incident_id {group.master_incident_id!r} не входит в incident_ids группы"
            )
        if not set(group.incident_ids) <= known_incident_ids:
            raise ValueError("incident_ids группы содержат id, отсутствующий в переданном списке инцидентов")
    return correlation


async def coordinate(
    tenant_id: str,
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    read_events_by_type: Callable[[str, str, datetime | None], Awaitable[list[StoredEvent]]] = (
        EventReader().read_events_by_type
    ),
    window_minutes: int = 60,
) -> CoordinationResult:
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    incidents = await read_events_by_type(tenant_id, "incident.triaged", since)

    if len(incidents) < 2:
        return CoordinationResult(correlated=False, groups=[])

    known_ids = {e.aggregate_id for e in incidents}
    system, user = _build_prompt(incidents)
    messages = [ModelMessage(role="user", content=user)]

    correlation: _Correlation | None = None
    last_error: Exception | None = None
    response = None
    for _attempt in range(2):
        if last_error is not None:
            messages.append(ModelMessage(role="assistant", content=response.content))
            messages.append(
                ModelMessage(
                    role="user",
                    content=f"Ответ не прошёл валидацию: {last_error}. Верни только исправленный JSON-объект.",
                )
            )
        response = await gateway.complete(
            ModelRequest(tier=ModelTier.STANDARD, messages=messages, system=system, max_tokens=1536)
        )
        try:
            correlation = _parse_correlation(response.content, known_ids)
            break
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc

    if correlation is None:
        raise CoordinationError(f"Не удалось получить корреляцию после 2 попыток: {last_error}")

    groups: list[IncidentGroup] = []
    for group in correlation.groups:
        for incident_id in group.incident_ids:
            if incident_id == group.master_incident_id:
                continue
            await publisher.publish(
                tenant_id=tenant_id,
                event_type="incident.merged",
                aggregate_type="incident",
                aggregate_id=incident_id,
                payload={"merged_into": group.master_incident_id, "status_summary": group.status_summary},
            )
        await publisher.publish(
            tenant_id=tenant_id,
            event_type="incident.status_published",
            aggregate_type="incident",
            aggregate_id=group.master_incident_id,
            payload={"member_incident_ids": group.incident_ids, "status_summary": group.status_summary},
        )
        groups.append(
            IncidentGroup(
                incident_ids=group.incident_ids,
                master_incident_id=group.master_incident_id,
                status_summary=group.status_summary,
            )
        )

    return CoordinationResult(correlated=bool(groups), groups=groups)
```

`src/etiology/domain/escalation_sync/incident_coordination/__init__.py`:
```python
from .agent import CoordinationError, CoordinationResult, IncidentGroup, coordinate

__all__ = ["CoordinationError", "CoordinationResult", "IncidentGroup", "coordinate"]
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/escalation_sync/incident_coordination/test_agent.py -v`
Expected: PASS (5 тестов)

- [ ] **Step 6: Прогнать весь набор тестов проекта**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (все тесты)

- [ ] **Step 7: Commit**

```bash
git add src/etiology/domain/escalation_sync tests/domain/escalation_sync
git commit -m "feat: добавлен Incident Coordination Agent"
```

---

## После выполнения плана

Ручная проверка на живом API: протриажить 2-3 сообщения с явно похожей темой (симулируя один
сбой у разных клиентов) в один tenant, вызвать `coordinate()`, убедиться что группа найдена и
`incident.merged`/`incident.status_published` появились в `events` с корректными aggregate_id.
Также проверить сценарий с непохожими темами — `correlated=False`.
