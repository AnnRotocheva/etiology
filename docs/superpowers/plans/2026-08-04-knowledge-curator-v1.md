# Approval Gate + Knowledge Curator v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать платформенный `ApprovalGate` (submit/list_pending/approve/reject) и
Knowledge Curator Agent (`incident_id` → `kb_suggestion.created` через Approval Gate, либо
легитимный отказ без предложения).

**Architecture:** `ApprovalGate` — доменно-нейтральный сервис над таблицей `approval_gate` (уже
есть, миграция 0003), переиспользуемый будущими агентами (Post-mortem и др.), не только Curator'ом.
`curate()` — простая async-функция (не LangGraph): читает трейл инцидента через уже существующий
`EventReader`, ищет существующее покрытие в KB через `knowledge_base.search`, один STANDARD-tier
вызов решает `should_propose`.

**Tech Stack:** Python 3.12+, asyncpg, pydantic v2, pytest (session-scoped event loop), уже
существующие `ModelGateway`, `EventReader`, `EventPublisher`, `knowledge_base.search`.

## Global Constraints

- RLS через `tenant_connection(tenant_id)`.
- `etiology_app` имеет SELECT/INSERT/UPDATE на `approval_gate` (полный CRUD кроме DELETE) —
  в отличие от diagnostic_command_catalog/screenshot_library, реальные DB-тесты возможны для
  всех операций, включая запись.
- Не LangGraph.
- Модель — `ModelTier.STANDARD`, `max_tokens=1536` (после урока с Bug Report Composer — задаём
  запас заранее, а не только после того, как обрежется на живом API).
- Markdown-fence-strip + один retry на невалидный JSON/схему — тот же паттерн, что в трёх
  агентах Diagnostics; вторая неудача — исключение, без тихого fallback.
- Материализация одобренного предложения в `knowledge_base_articles` — вне этого плана, вручную
  человеком (как и другие курируемые активы).

Ссылка на дизайн: `docs/superpowers/specs/2026-08-04-knowledge-curator-design.md`.

---

### Task 1: `ApprovalGate`

**Files:**
- Create: `src/etiology/platform_core/approval_gate/gate.py`
- Modify: `src/etiology/platform_core/approval_gate/__init__.py` (сейчас пустой)
- Test: `tests/platform_core/approval_gate/test_gate.py`
- Create: `tests/platform_core/approval_gate/__init__.py`

**Interfaces:**
- Produces: `ApprovalItem` (dataclass: `id, object_type, payload, status, created_by, reviewed_by, reviewed_at, created_at`), `ApprovalGate` (класс с методами `submit`, `list_pending`, `approve`, `reject`), экспортируются из `etiology.platform_core.approval_gate`. Task 2 использует `ApprovalGate` (через параметр `approval_gate`) и вызывает только `submit`.

- [ ] **Step 1: Создать пакет для тестовой директории**

`tests/platform_core/approval_gate/__init__.py` — пустой файл.

- [ ] **Step 2: Написать падающие тесты**

`tests/platform_core/approval_gate/test_gate.py`:
```python
from etiology.platform_core.approval_gate import ApprovalGate


async def test_submit_creates_pending_item(tenant_id):
    gate = ApprovalGate()

    approval_id = await gate.submit(tenant_id, "kb_suggestion", {"title": "t"}, created_by="tester")

    pending = await gate.list_pending(tenant_id)
    assert any(item.id == approval_id and item.status == "pending" for item in pending)
    match = next(item for item in pending if item.id == approval_id)
    assert match.object_type == "kb_suggestion"
    assert match.payload == {"title": "t"}
    assert match.created_by == "tester"
    assert match.reviewed_by is None


async def test_list_pending_filters_by_object_type(tenant_id):
    gate = ApprovalGate()
    await gate.submit(tenant_id, "kb_suggestion", {"a": 1}, created_by="tester")
    await gate.submit(tenant_id, "post_mortem", {"b": 2}, created_by="tester")

    kb_only = await gate.list_pending(tenant_id, object_type="kb_suggestion")

    assert len(kb_only) == 1
    assert kb_only[0].object_type == "kb_suggestion"


async def test_approve_removes_item_from_pending(tenant_id):
    gate = ApprovalGate()
    approval_id = await gate.submit(tenant_id, "kb_suggestion", {"title": "t"}, created_by="tester")

    await gate.approve(tenant_id, approval_id, reviewed_by="reviewer")

    pending = await gate.list_pending(tenant_id)
    assert not any(item.id == approval_id for item in pending)


async def test_reject_removes_item_from_pending(tenant_id):
    gate = ApprovalGate()
    approval_id = await gate.submit(tenant_id, "kb_suggestion", {"title": "t"}, created_by="tester")

    await gate.reject(tenant_id, approval_id, reviewed_by="reviewer")

    pending = await gate.list_pending(tenant_id)
    assert not any(item.id == approval_id for item in pending)
```

- [ ] **Step 3: Запустить тесты, убедиться что падают**

Run: `.venv/Scripts/python.exe -m pytest tests/platform_core/approval_gate/test_gate.py -v`
Expected: FAIL (`ImportError: cannot import name 'ApprovalGate'`)

- [ ] **Step 4: Реализовать `ApprovalGate`**

`src/etiology/platform_core/approval_gate/gate.py`:
```python
import json
from dataclasses import dataclass
from datetime import datetime

from etiology.data.db.pool import tenant_connection


@dataclass
class ApprovalItem:
    id: str
    object_type: str
    payload: dict
    status: str
    created_by: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime


def _row_to_item(row) -> ApprovalItem:
    return ApprovalItem(
        id=str(row["id"]),
        object_type=row["object_type"],
        payload=json.loads(row["payload"]),
        status=row["status"],
        created_by=row["created_by"],
        reviewed_by=row["reviewed_by"],
        reviewed_at=row["reviewed_at"],
        created_at=row["created_at"],
    )


class ApprovalGate:
    """Сквозной платформенный сервис "черновик -> человек -> публикация"
    (docs/architecture.md §8.1). Без доменной логики — просто очередь
    pending-объектов + статус, переиспользуется любым доменом (KB, post-mortem,
    command-эскалация). Без Slack-уведомления — интеграции нет в кодовой базе,
    как и у bugtracker.create_report в Bug Report Composer.
    """

    async def submit(self, tenant_id: str, object_type: str, payload: dict, created_by: str) -> str:
        async with tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO approval_gate (tenant_id, object_type, payload, created_by)
                VALUES ($1::uuid, $2, $3::jsonb, $4)
                RETURNING id
                """,
                tenant_id,
                object_type,
                json.dumps(payload),
                created_by,
            )
        return str(row["id"])

    async def list_pending(self, tenant_id: str, object_type: str | None = None) -> list[ApprovalItem]:
        async with tenant_connection(tenant_id) as conn:
            if object_type is None:
                rows = await conn.fetch(
                    "SELECT id, object_type, payload, status, created_by, reviewed_by, reviewed_at, created_at "
                    "FROM approval_gate WHERE status = 'pending' ORDER BY created_at ASC"
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, object_type, payload, status, created_by, reviewed_by, reviewed_at, created_at "
                    "FROM approval_gate WHERE status = 'pending' AND object_type = $1 ORDER BY created_at ASC",
                    object_type,
                )
        return [_row_to_item(row) for row in rows]

    async def approve(self, tenant_id: str, approval_id: str, reviewed_by: str) -> None:
        async with tenant_connection(tenant_id) as conn:
            await conn.execute(
                "UPDATE approval_gate SET status = 'approved', reviewed_by = $2, reviewed_at = now() "
                "WHERE id = $1::uuid AND status = 'pending'",
                approval_id,
                reviewed_by,
            )

    async def reject(self, tenant_id: str, approval_id: str, reviewed_by: str) -> None:
        async with tenant_connection(tenant_id) as conn:
            await conn.execute(
                "UPDATE approval_gate SET status = 'rejected', reviewed_by = $2, reviewed_at = now() "
                "WHERE id = $1::uuid AND status = 'pending'",
                approval_id,
                reviewed_by,
            )
```

`src/etiology/platform_core/approval_gate/__init__.py`:
```python
from .gate import ApprovalGate, ApprovalItem

__all__ = ["ApprovalGate", "ApprovalItem"]
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/platform_core/approval_gate/test_gate.py -v`
Expected: PASS (4 теста)

- [ ] **Step 6: Commit**

```bash
git add src/etiology/platform_core/approval_gate tests/platform_core/approval_gate
git commit -m "feat: добавлен ApprovalGate"
```

---

### Task 2: Knowledge Curator Agent

**Files:**
- Create: `src/etiology/domain/knowledge_base/curator.py`
- Modify: `src/etiology/domain/knowledge_base/__init__.py`
- Test: `tests/domain/knowledge_base/test_curator.py`

**Interfaces:**
- Consumes: `EventReader`, `StoredEvent`, `EventPublisher` из `etiology.platform_core.event_bus`;
  `ApprovalGate` из `etiology.platform_core.approval_gate` (Task 1); `KbArticle`, `search` из
  `.search` (тот же модуль, уже существует); `ModelGateway`, `ModelMessage`, `ModelRequest`,
  `ModelTier` из `etiology.agent.model_gateway`.
- Produces: `CuratorResult` (dataclass: `incident_id, proposed, suggestion_id, title, topic_tag`),
  `CurationError(RuntimeError)`, `async def curate(tenant_id, incident_id, *, gateway, approval_gate,
  publisher, read_aggregate_events=EventReader().read_aggregate_events, kb_search=search) -> CuratorResult`.
  Экспортируются из `etiology.domain.knowledge_base`.

- [ ] **Step 1: Написать падающие тесты**

`tests/domain/knowledge_base/test_curator.py`:
```python
import json
from datetime import datetime, timezone

import pytest

from etiology.agent.model_gateway import ModelGateway, ModelRequest, ModelResponse, ModelTier
from etiology.agent.model_gateway.base import ModelProvider
from etiology.domain.knowledge_base import CurationError, curate
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


class FakeApprovalGate:
    def __init__(self):
        self.submissions = []

    async def submit(self, tenant_id, object_type, payload, created_by):
        suggestion_id = f"suggestion-{len(self.submissions) + 1}"
        self.submissions.append(
            dict(tenant_id=tenant_id, object_type=object_type, payload=payload, created_by=created_by)
        )
        return suggestion_id


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


def _trail_with_bug_report():
    return [
        StoredEvent(
            event_type="incident.triaged",
            payload={"raw_message": "msg", "topic_tag": "tracking"},
            metadata={}, created_at=_now(),
        ),
        StoredEvent(event_type="incident.needs_bug_report", payload={}, metadata={}, created_at=_now()),
        StoredEvent(
            event_type="bug_report.created",
            payload={"title": "t", "diagnostic_summary": "s", "actual_behavior": "a"},
            metadata={}, created_at=_now(),
        ),
    ]


async def _read_bug_report_trail(tenant_id, aggregate_type, aggregate_id):
    return _trail_with_bug_report()


async def _read_empty_trail(tenant_id, aggregate_type, aggregate_id):
    return []


async def _no_existing_articles(tenant_id, query):
    return []


def _decision_json(should_propose=True):
    return json.dumps(
        {
            "should_propose": should_propose,
            "title": "Заголовок статьи" if should_propose else None,
            "body": "Тело статьи" if should_propose else None,
            "topic_tag": "tracking" if should_propose else None,
            "reasoning": "обоснование",
        }
    )


async def test_curate_proposes_and_publishes_suggestion():
    provider = FakeProvider("fake", [_decision_json(should_propose=True)])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    result = await curate(
        "tenant-1", "incident-1",
        gateway=gateway, approval_gate=approval_gate, publisher=publisher,
        read_aggregate_events=_read_bug_report_trail, kb_search=_no_existing_articles,
    )

    assert result.proposed is True
    assert result.title == "Заголовок статьи"
    assert len(approval_gate.submissions) == 1
    assert approval_gate.submissions[0]["payload"]["source_incident_id"] == "incident-1"
    assert len(publisher.calls) == 1
    assert publisher.calls[0]["event_type"] == "kb_suggestion.created"


async def test_curate_does_not_submit_when_model_declines():
    provider = FakeProvider("fake", [_decision_json(should_propose=False)])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    result = await curate(
        "tenant-1", "incident-1",
        gateway=gateway, approval_gate=approval_gate, publisher=publisher,
        read_aggregate_events=_read_bug_report_trail, kb_search=_no_existing_articles,
    )

    assert result.proposed is False
    assert approval_gate.submissions == []
    assert publisher.calls == []


async def test_curate_raises_when_incident_not_closed():
    provider = FakeProvider("fake", [_decision_json()])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    with pytest.raises(CurationError):
        await curate(
            "tenant-1", "incident-1",
            gateway=gateway, approval_gate=approval_gate, publisher=publisher,
            read_aggregate_events=_read_empty_trail, kb_search=_no_existing_articles,
        )

    assert provider.calls == 0
    assert approval_gate.submissions == []


async def test_curate_retries_once_on_malformed_json_then_succeeds():
    provider = FakeProvider("fake", ["не json", _decision_json(should_propose=True)])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    result = await curate(
        "tenant-1", "incident-1",
        gateway=gateway, approval_gate=approval_gate, publisher=publisher,
        read_aggregate_events=_read_bug_report_trail, kb_search=_no_existing_articles,
    )

    assert provider.calls == 2
    assert result.proposed is True


async def test_curate_raises_after_two_malformed_responses():
    provider = FakeProvider("fake", ["не json", "тоже не json"])
    gateway = ModelGateway([provider])
    approval_gate = FakeApprovalGate()
    publisher = FakePublisher()

    with pytest.raises(CurationError):
        await curate(
            "tenant-1", "incident-1",
            gateway=gateway, approval_gate=approval_gate, publisher=publisher,
            read_aggregate_events=_read_bug_report_trail, kb_search=_no_existing_articles,
        )

    assert provider.calls == 2
    assert approval_gate.submissions == []
```

- [ ] **Step 2: Запустить тесты, убедиться что падают**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/knowledge_base/test_curator.py -v`
Expected: FAIL (`ImportError: cannot import name 'curate'`)

- [ ] **Step 3: Реализовать `curator.py`**

`src/etiology/domain/knowledge_base/curator.py`:
```python
import json
from dataclasses import dataclass
from typing import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from etiology.agent.model_gateway import ModelGateway, ModelMessage, ModelRequest, ModelTier
from etiology.platform_core.approval_gate import ApprovalGate
from etiology.platform_core.event_bus import EventPublisher, EventReader, StoredEvent

from .search import KbArticle
from .search import search as kb_search_default


class CurationError(RuntimeError):
    pass


@dataclass
class CuratorResult:
    incident_id: str
    proposed: bool
    suggestion_id: str | None
    title: str | None
    topic_tag: str | None


class _CuratorDecision(BaseModel):
    should_propose: bool
    title: str | None = None
    body: str | None = None
    topic_tag: str | None = None
    reasoning: str


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        _, _, rest = text.partition("\n")
        text = rest.removesuffix("```").strip()
    return text


def _find_latest(events: list[StoredEvent], event_type: str) -> StoredEvent | None:
    matches = [e for e in events if e.event_type == event_type]
    return matches[-1] if matches else None


def _build_prompt(
    triaged: StoredEvent | None, terminal: StoredEvent, existing: list[KbArticle]
) -> tuple[str, str]:
    system = (
        "Ты — Knowledge Curator службы поддержки Keitaro. По закрытому инциденту реши, стоит ли "
        "предложить новую статью базы знаний — только если случай представляет переиспользуемый "
        "паттерн (то же самое может повториться у других клиентов), а не одноразовую специфику "
        "этого клиента. Если в списке существующих статей ниже уже есть покрывающая эту тему — "
        "откажись предлагать дубликат. Верни ТОЛЬКО JSON-объект без markdown-разметки: "
        '{"should_propose": true|false, "title": "..." или null, "body": "..." или null, '
        '"topic_tag": "..." или null, "reasoning": "..."}.'
    )
    parts = []
    if triaged is not None:
        parts.append(
            f"Исходное сообщение клиента: {triaged.payload.get('raw_message')}\n"
            f"Тема (Triage): {triaged.payload.get('topic_tag')}"
        )
    if terminal.event_type == "bug_report.created":
        parts.append(
            f"Итог — создан bug report: {terminal.payload.get('title')}\n"
            f"Диагностическая сводка: {terminal.payload.get('diagnostic_summary')}\n"
            f"Фактическое поведение: {terminal.payload.get('actual_behavior')}"
        )
    else:
        parts.append(f"Итог — инцидент решён по базе знаний: {terminal.payload.get('advisory_text')}")
    if existing:
        existing_block = "\n".join(f"- id={a.id} topic={a.topic_tag!r} title={a.title!r}" for a in existing)
    else:
        existing_block = "(существующих статей по теме не найдено)"
    parts.append(f"Существующие статьи базы знаний:\n{existing_block}")
    user = "\n\n".join(parts)
    return system, user


def _parse_decision(text: str) -> _CuratorDecision:
    data = json.loads(_strip_code_fence(text))
    return _CuratorDecision.model_validate(data)


async def curate(
    tenant_id: str,
    incident_id: str,
    *,
    gateway: ModelGateway,
    approval_gate: ApprovalGate,
    publisher: EventPublisher,
    read_aggregate_events: Callable[[str, str, str], Awaitable[list[StoredEvent]]] = (
        EventReader().read_aggregate_events
    ),
    kb_search: Callable[[str, str], Awaitable[list[KbArticle]]] = kb_search_default,
) -> CuratorResult:
    events = await read_aggregate_events(tenant_id, "incident", incident_id)
    terminal = _find_latest(events, "bug_report.created") or _find_latest(events, "incident.resolved")
    if terminal is None:
        raise CurationError(
            f"Для инцидента {incident_id!r} не найдено ни bug_report.created, ни incident.resolved — "
            "Curator анализирует только закрытые инциденты"
        )
    triaged = _find_latest(events, "incident.triaged")

    search_query = (triaged.payload.get("topic_tag") if triaged else None) or terminal.event_type
    existing = await kb_search(tenant_id, search_query)

    system, user = _build_prompt(triaged, terminal, existing)
    messages = [ModelMessage(role="user", content=user)]

    decision: _CuratorDecision | None = None
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
            decision = _parse_decision(response.content)
            break
        except (json.JSONDecodeError, ValidationError) as exc:
            last_error = exc

    if decision is None:
        raise CurationError(f"Не удалось получить решение куратора после 2 попыток: {last_error}")

    if not decision.should_propose:
        return CuratorResult(
            incident_id=incident_id, proposed=False, suggestion_id=None, title=None, topic_tag=None
        )

    suggestion_id = await approval_gate.submit(
        tenant_id,
        "kb_suggestion",
        {
            "title": decision.title,
            "body": decision.body,
            "topic_tag": decision.topic_tag,
            "source_incident_id": incident_id,
            "reasoning": decision.reasoning,
        },
        created_by="knowledge_curator_agent",
    )

    await publisher.publish(
        tenant_id=tenant_id,
        event_type="kb_suggestion.created",
        aggregate_type="kb_suggestion",
        aggregate_id=suggestion_id,
        payload={
            "title": decision.title,
            "topic_tag": decision.topic_tag,
            "source_incident_id": incident_id,
        },
    )

    return CuratorResult(
        incident_id=incident_id,
        proposed=True,
        suggestion_id=suggestion_id,
        title=decision.title,
        topic_tag=decision.topic_tag,
    )
```

`src/etiology/domain/knowledge_base/__init__.py` (полная замена содержимого):
```python
from .curator import CurationError, CuratorResult, curate
from .search import KbArticle, get_by_id, search

__all__ = ["KbArticle", "get_by_id", "search", "CurationError", "CuratorResult", "curate"]
```

- [ ] **Step 4: Запустить тесты, убедиться что проходят**

Run: `.venv/Scripts/python.exe -m pytest tests/domain/knowledge_base/test_curator.py -v`
Expected: PASS (5 тестов)

- [ ] **Step 5: Прогнать весь набор тестов проекта**

Run: `.venv/Scripts/python.exe -m pytest -v`
Expected: PASS (все тесты)

- [ ] **Step 6: Commit**

```bash
git add src/etiology/domain/knowledge_base tests/domain/knowledge_base/test_curator.py
git commit -m "feat: добавлен Knowledge Curator Agent"
```

---

## После выполнения плана

Ручная проверка на живом API: прогнать полную цепочку `triage → collect → compose → curate` с
реальным `AnthropicProvider`, убедиться что `kb_suggestion.created` появляется в `events`, и что
`approval_gate.list_pending()` возвращает предложение. Также стоит проверить ветку
`should_propose=false`, если модель сочтёт кейс слишком специфичным.
