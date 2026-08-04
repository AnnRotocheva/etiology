# Triage Agent v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Реализовать Triage Agent v1 (сырое сообщение → событие `incident.triaged`), включая две вспомогательные функции, которых ещё нет: `EventPublisher.publish()` и `KnowledgeBase.search()`.

**Architecture:** Три независимо тестируемых слоя одной вертикали: `EventPublisher` (platform_core/event_bus, publish-only INSERT в `events`), `KnowledgeBase.search` (domain/knowledge_base, `ILIKE`-поиск по `knowledge_base_articles`), `triage()` (domain/diagnostics/triage, простая async-функция без LangGraph — один LLM-вызов + одна публикация события). Triage Agent получает `gateway`/`publisher`/`kb_search` через параметры (dependency injection), поэтому его юнит-тесты не трогают Postgres.

**Tech Stack:** Python 3.12+, asyncpg, pydantic v2, pytest + pytest-asyncio (`asyncio_mode = "auto"`), существующий `ModelGateway`/`AnthropicProvider`.

## Global Constraints

- RLS обязателен для `events` и `knowledge_base_articles` — все запросы идут через `tenant_connection(tenant_id)` (`src/etiology/data/db/pool.py`), не через голый `pool.acquire()`.
- Роль `etiology_app` не имеет `DELETE` ни на одной таблице (`scripts/grant_app_role.sql`) — это осознанное architecture-решение (append-only Event Store, curated-only KB), не баг. Тестовые данные в dev Postgres не удаляются между запусками.
- Не вводить LangGraph в этом плане — `triage()` остаётся обычной async-функцией (см. дизайн-спек, раздел 3).
- Не расширять `ModelGateway`/`ModelRequest` под tool-calling/structured output в этом плане — классификация парсится из текстового ответа.
- Модели: `ModelTier.FAST` для Triage Agent (`src/etiology/agent/model_gateway/types.py`).
- Dev Postgres должен быть запущен перед тестами Task 1–2 (`DATABASE_URL` уже в `.env`).

Ссылка на дизайн: `docs/superpowers/specs/2026-08-04-triage-agent-design.md`.

---

### Task 1: `EventPublisher` + общий тестовый fixture для tenant

**Files:**
- Create: `src/etiology/platform_core/event_bus/publisher.py`
- Modify: `src/etiology/platform_core/event_bus/__init__.py` (сейчас пустой)
- Create: `tests/conftest.py`
- Create: `tests/platform_core/__init__.py`
- Create: `tests/platform_core/event_bus/__init__.py`
- Test: `tests/platform_core/event_bus/test_publisher.py`

**Interfaces:**
- Produces: `EventPublisher` (класс, метод `async publish(self, tenant_id: str, event_type: str, aggregate_type: str, aggregate_id: str, payload: dict, metadata: dict | None = None) -> None`), экспортируется из `etiology.platform_core.event_bus`.
- Produces: pytest-фикстура `tenant_id` (строка-UUID существующего tenant'а) в `tests/conftest.py`, доступна всем тестам в `tests/`.

- [ ] **Step 1: Написать общий conftest-fixture `tenant_id`**

`tests/conftest.py`:
```python
import uuid

import pytest

from etiology.data.db.pool import get_pool


@pytest.fixture
async def tenant_id() -> str:
    """Создаёт tenant для теста. Не удаляется после теста — etiology_app
    намеренно не имеет DELETE ни на одну таблицу (append-only/curated-only
    дисциплина, см. scripts/grant_app_role.sql). Для dev БД это ожидаемо;
    периодический сброс — вручную (dropdb/recreate), вне рамок этого плана.
    """
    tid = str(uuid.uuid4())
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO tenants (id, slug, name) VALUES ($1::uuid, $2, $3)",
            tid,
            f"test-{tid}",
            "Test Tenant",
        )
    return tid
```

- [ ] **Step 2: Создать пакеты для новых тестовых директорий**

`tests/platform_core/__init__.py` — пустой файл.
`tests/platform_core/event_bus/__init__.py` — пустой файл.

- [ ] **Step 3: Написать падающий тест**

`tests/platform_core/event_bus/test_publisher.py`:
```python
import json
import uuid

from etiology.data.db.pool import tenant_connection
from etiology.platform_core.event_bus import EventPublisher


async def test_publish_inserts_event_row(tenant_id):
    publisher = EventPublisher()
    aggregate_id = str(uuid.uuid4())

    await publisher.publish(
        tenant_id=tenant_id,
        event_type="incident.triaged",
        aggregate_type="incident",
        aggregate_id=aggregate_id,
        payload={"severity": "high"},
        metadata={"model": "fake"},
    )

    async with tenant_connection(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT tenant_id, event_type, aggregate_type, aggregate_id, payload, metadata "
            "FROM events WHERE aggregate_id = $1::uuid",
            aggregate_id,
        )

    assert row is not None
    assert str(row["tenant_id"]) == tenant_id
    assert row["event_type"] == "incident.triaged"
    assert row["aggregate_type"] == "incident"
    assert json.loads(row["payload"]) == {"severity": "high"}
    assert json.loads(row["metadata"]) == {"model": "fake"}
```

- [ ] **Step 4: Запустить тест, убедиться что падает**

Run: `pytest tests/platform_core/event_bus/test_publisher.py -v`
Expected: FAIL (`ImportError: cannot import name 'EventPublisher'`)

- [ ] **Step 5: Реализовать `EventPublisher`**

`src/etiology/platform_core/event_bus/publisher.py`:
```python
import json

from etiology.data.db.pool import tenant_connection


class EventPublisher:
    """Publish-сторона Event Bus (docs/architecture.md §8.2). Только запись
    в Event Store — LISTEN/NOTIFY и outbox добавляются отдельно, когда
    появится первый реальный подписчик.
    """

    async def publish(
        self,
        tenant_id: str,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: dict,
        metadata: dict | None = None,
    ) -> None:
        async with tenant_connection(tenant_id) as conn:
            await conn.execute(
                """
                INSERT INTO events (tenant_id, event_type, aggregate_type, aggregate_id, payload, metadata)
                VALUES ($1::uuid, $2, $3, $4::uuid, $5::jsonb, $6::jsonb)
                """,
                tenant_id,
                event_type,
                aggregate_type,
                aggregate_id,
                json.dumps(payload),
                json.dumps(metadata or {}),
            )
```

`src/etiology/platform_core/event_bus/__init__.py`:
```python
from .publisher import EventPublisher

__all__ = ["EventPublisher"]
```

- [ ] **Step 6: Запустить тест, убедиться что проходит**

Run: `pytest tests/platform_core/event_bus/test_publisher.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/platform_core src/etiology/platform_core/event_bus
git commit -m "feat: add EventPublisher publish-side of Event Bus"
```

---

### Task 2: `KnowledgeBase.search`

**Files:**
- Create: `src/etiology/domain/knowledge_base/search.py`
- Modify: `src/etiology/domain/knowledge_base/__init__.py` (сейчас пустой)
- Create: `tests/domain/__init__.py`
- Create: `tests/domain/knowledge_base/__init__.py`
- Test: `tests/domain/knowledge_base/test_search.py`

**Interfaces:**
- Consumes: `tenant_connection(tenant_id)` из `etiology.data.db.pool` (Task 1 не требуется — используется напрямую, уже существует).
- Produces: `KbArticle` (dataclass: `id: str, kind: str, title: str, body: str, topic_tag: str | None`) и `async def search(tenant_id: str, query: str, limit: int = 5) -> list[KbArticle]`, экспортируются из `etiology.domain.knowledge_base`. Task 3 использует оба имени.

- [ ] **Step 1: Создать пакеты для тестовой директории**

`tests/domain/__init__.py` — пустой файл.
`tests/domain/knowledge_base/__init__.py` — пустой файл.

- [ ] **Step 2: Написать падающий тест**

`tests/domain/knowledge_base/test_search.py`:
```python
from etiology.data.db.pool import tenant_connection
from etiology.domain import knowledge_base


async def test_search_finds_matching_article_by_body(tenant_id):
    async with tenant_connection(tenant_id) as conn:
        await conn.execute(
            """
            INSERT INTO knowledge_base_articles (tenant_id, kind, title, body, topic_tag)
            VALUES ($1::uuid, $2::kb_article_kind, $3, $4, $5)
            """,
            tenant_id,
            "known_issue",
            "Кампания не запускается",
            "Проверьте статус лицензии в разделе Settings",
            "licensing",
        )

    results = await knowledge_base.search(tenant_id, "лицензии")

    assert len(results) == 1
    assert results[0].title == "Кампания не запускается"
    assert results[0].topic_tag == "licensing"


async def test_search_returns_empty_list_when_no_match(tenant_id):
    results = await knowledge_base.search(tenant_id, "что-то несуществующее")

    assert results == []
```

- [ ] **Step 3: Запустить тест, убедиться что падает**

Run: `pytest tests/domain/knowledge_base/test_search.py -v`
Expected: FAIL (`AttributeError: module 'etiology.domain.knowledge_base' has no attribute 'search'`)

- [ ] **Step 4: Реализовать `search`**

`src/etiology/domain/knowledge_base/search.py`:
```python
from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection


@dataclass
class KbArticle:
    id: str
    kind: str
    title: str
    body: str
    topic_tag: str | None


async def search(tenant_id: str, query: str, limit: int = 5) -> list[KbArticle]:
    """Простой ILIKE-поиск по title/body/topic_tag (docs/architecture.md §5).
    Полнотекстовый индекс — не сейчас, апгрейд не потребует смены сигнатуры.
    """
    pattern = f"%{query}%"
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, kind, title, body, topic_tag
            FROM knowledge_base_articles
            WHERE title ILIKE $1 OR body ILIKE $1 OR topic_tag ILIKE $1
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            pattern,
            limit,
        )
    return [
        KbArticle(
            id=str(row["id"]),
            kind=row["kind"],
            title=row["title"],
            body=row["body"],
            topic_tag=row["topic_tag"],
        )
        for row in rows
    ]
```

`src/etiology/domain/knowledge_base/__init__.py`:
```python
from .search import KbArticle, search

__all__ = ["KbArticle", "search"]
```

- [ ] **Step 5: Запустить тест, убедиться что проходит**

Run: `pytest tests/domain/knowledge_base/test_search.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add tests/domain src/etiology/domain/knowledge_base
git commit -m "feat: add KnowledgeBase.search"
```

---

### Task 3: Triage Agent

**Files:**
- Create: `src/etiology/domain/diagnostics/triage/agent.py`
- Modify: `src/etiology/domain/diagnostics/triage/__init__.py` (сейчас пустой)
- Create: `tests/domain/diagnostics/__init__.py`
- Create: `tests/domain/diagnostics/triage/__init__.py`
- Test: `tests/domain/diagnostics/triage/test_agent.py`

**Interfaces:**
- Consumes: `ModelGateway`, `ModelMessage`, `ModelRequest`, `ModelTier` из `etiology.agent.model_gateway` (Task 0, уже существует); `EventPublisher` из `etiology.platform_core.event_bus` (Task 1); `KbArticle`, `search` из `etiology.domain.knowledge_base` (Task 2).
- Produces: `TriageResult` (dataclass: `incident_id: str, severity: str, topic_tag: str, kb_closable: bool, kb_article_id: str | None`), `TriageClassificationError(RuntimeError)`, `async def triage(tenant_id: str, raw_message: str, *, gateway: ModelGateway, publisher: EventPublisher, kb_search: Callable[[str, str], Awaitable[list[KbArticle]]] = search) -> TriageResult`. Всё экспортируется из `etiology.domain.diagnostics.triage`.

- [ ] **Step 1: Создать пакеты для тестовой директории**

`tests/domain/diagnostics/__init__.py` — пустой файл.
`tests/domain/diagnostics/triage/__init__.py` — пустой файл.

- [ ] **Step 2: Написать падающие тесты**

`tests/domain/diagnostics/triage/test_agent.py`:
```python
import json
import uuid

import pytest

from etiology.agent.model_gateway import ModelGateway, ModelRequest, ModelResponse, ModelTier
from etiology.agent.model_gateway.base import ModelProvider
from etiology.domain.diagnostics.triage import TriageClassificationError, triage
from etiology.domain.knowledge_base import KbArticle


class FakeProvider(ModelProvider):
    def __init__(self, name: str, responses: list[str]):
        self.name = name
        self._responses = list(responses)
        self.calls = 0

    def supports_tier(self, tier: ModelTier) -> bool:
        return tier == ModelTier.FAST

    async def complete(self, request: ModelRequest) -> ModelResponse:
        content = self._responses[min(self.calls, len(self._responses) - 1)]
        self.calls += 1
        return ModelResponse(
            content=content,
            stop_reason="end_turn",
            provider=self.name,
            model="fake-model",
            input_tokens=1,
            output_tokens=1,
        )


class FakePublisher:
    def __init__(self):
        self.calls = []

    async def publish(self, tenant_id, event_type, aggregate_type, aggregate_id, payload, metadata=None):
        self.calls.append(
            dict(
                tenant_id=tenant_id,
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
                metadata=metadata,
            )
        )


async def _no_articles(tenant_id, query):
    return []


def _valid_json(kb_article_id=None, kb_closable=False):
    return json.dumps(
        {
            "severity": "high",
            "topic_tag": "billing",
            "kb_closable": kb_closable,
            "kb_article_id": kb_article_id,
            "reasoning": "тестовое обоснование",
        }
    )


async def test_triage_publishes_incident_triaged_event():
    provider = FakeProvider("fake", [_valid_json()])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await triage(
        "tenant-1",
        "У меня не работает биллинг",
        gateway=gateway,
        publisher=publisher,
        kb_search=_no_articles,
    )

    assert result.severity == "high"
    assert result.topic_tag == "billing"
    assert result.kb_closable is False
    assert len(publisher.calls) == 1
    call = publisher.calls[0]
    assert call["event_type"] == "incident.triaged"
    assert call["aggregate_type"] == "incident"
    assert call["aggregate_id"] == result.incident_id
    assert call["payload"]["raw_message"] == "У меня не работает биллинг"


async def test_triage_grounds_kb_closable_in_found_article():
    article = KbArticle(id=str(uuid.uuid4()), kind="known_issue", title="t", body="b", topic_tag="billing")

    async def _with_article(tenant_id, query):
        return [article]

    provider = FakeProvider("fake", [_valid_json(kb_article_id=article.id, kb_closable=True)])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await triage(
        "tenant-1",
        "У меня не работает биллинг",
        gateway=gateway,
        publisher=publisher,
        kb_search=_with_article,
    )

    assert result.kb_closable is True
    assert result.kb_article_id == article.id


async def test_triage_retries_once_on_malformed_json_then_succeeds():
    provider = FakeProvider("fake", ["не json", _valid_json()])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await triage(
        "tenant-1",
        "У меня не работает биллинг",
        gateway=gateway,
        publisher=publisher,
        kb_search=_no_articles,
    )

    assert provider.calls == 2
    assert result.severity == "high"
    assert len(publisher.calls) == 1


async def test_triage_raises_after_two_malformed_responses():
    provider = FakeProvider("fake", ["не json", "тоже не json"])
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    with pytest.raises(TriageClassificationError):
        await triage(
            "tenant-1",
            "У меня не работает биллинг",
            gateway=gateway,
            publisher=publisher,
            kb_search=_no_articles,
        )

    assert provider.calls == 2
    assert publisher.calls == []


async def test_triage_rejects_kb_article_id_not_in_search_results():
    unknown_id = str(uuid.uuid4())
    provider = FakeProvider(
        "fake",
        [
            _valid_json(kb_article_id=unknown_id, kb_closable=True),
            _valid_json(),
        ],
    )
    gateway = ModelGateway([provider])
    publisher = FakePublisher()

    result = await triage(
        "tenant-1",
        "У меня не работает биллинг",
        gateway=gateway,
        publisher=publisher,
        kb_search=_no_articles,
    )

    assert provider.calls == 2
    assert result.kb_article_id is None
```

- [ ] **Step 3: Запустить тесты, убедиться что падают**

Run: `pytest tests/domain/diagnostics/triage/test_agent.py -v`
Expected: FAIL (`ImportError: cannot import name 'triage'`)

- [ ] **Step 4: Реализовать Triage Agent**

`src/etiology/domain/diagnostics/triage/agent.py`:
```python
import json
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from etiology.agent.model_gateway import ModelGateway, ModelMessage, ModelRequest, ModelTier
from etiology.domain.knowledge_base import KbArticle
from etiology.domain.knowledge_base import search as kb_search_default
from etiology.platform_core.event_bus import EventPublisher


class TriageClassificationError(RuntimeError):
    pass


@dataclass
class TriageResult:
    incident_id: str
    severity: str
    topic_tag: str
    kb_closable: bool
    kb_article_id: str | None


class _Classification(BaseModel):
    severity: Literal["critical", "high", "medium", "low"]
    topic_tag: str
    kb_closable: bool
    kb_article_id: str | None = None
    reasoning: str


def _build_prompt(raw_message: str, articles: list[KbArticle]) -> tuple[str, str]:
    system = (
        "Ты — Triage Agent службы поддержки Keitaro. Классифицируй обращение клиента. "
        "Верни ТОЛЬКО JSON-объект без пояснений и без markdown-разметки, со строго такими полями: "
        '{"severity": "critical|high|medium|low", "topic_tag": "краткий тег темы", '
        '"kb_closable": true|false, "kb_article_id": "id статьи из списка ниже или null", '
        '"reasoning": "короткое обоснование"}. '
        "kb_closable=true и kb_article_id разрешены только если одна из предложенных ниже статей "
        "действительно решает проблему клиента. Если подходящей статьи нет — kb_closable=false, kb_article_id=null."
    )
    if articles:
        articles_block = "\n".join(
            f"- id={a.id} topic={a.topic_tag!r} title={a.title!r}\n  {a.body[:300]}" for a in articles
        )
    else:
        articles_block = "(статей не найдено)"
    user = f"Сообщение клиента:\n{raw_message}\n\nСтатьи базы знаний:\n{articles_block}"
    return system, user


def _parse_classification(text: str, known_article_ids: set[str]) -> _Classification:
    data = json.loads(text)
    classification = _Classification.model_validate(data)
    if classification.kb_article_id is not None and classification.kb_article_id not in known_article_ids:
        raise ValueError(f"kb_article_id {classification.kb_article_id!r} не входит в список найденных статей")
    return classification


async def triage(
    tenant_id: str,
    raw_message: str,
    *,
    gateway: ModelGateway,
    publisher: EventPublisher,
    kb_search: Callable[[str, str], Awaitable[list[KbArticle]]] = kb_search_default,
) -> TriageResult:
    articles = await kb_search(tenant_id, raw_message)
    known_ids = {a.id for a in articles}
    system, user = _build_prompt(raw_message, articles)
    messages = [ModelMessage(role="user", content=user)]

    classification: _Classification | None = None
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
        response = await gateway.complete(ModelRequest(tier=ModelTier.FAST, messages=messages, system=system))
        try:
            classification = _parse_classification(response.content, known_ids)
            break
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc

    if classification is None:
        raise TriageClassificationError(f"Не удалось получить валидную классификацию после 2 попыток: {last_error}")

    incident_id = str(uuid4())
    await publisher.publish(
        tenant_id=tenant_id,
        event_type="incident.triaged",
        aggregate_type="incident",
        aggregate_id=incident_id,
        payload={
            "raw_message": raw_message,
            "severity": classification.severity,
            "topic_tag": classification.topic_tag,
            "kb_closable": classification.kb_closable,
            "kb_article_id": classification.kb_article_id,
            "reasoning": classification.reasoning,
        },
        metadata={"model_provider": response.provider, "model": response.model},
    )

    return TriageResult(
        incident_id=incident_id,
        severity=classification.severity,
        topic_tag=classification.topic_tag,
        kb_closable=classification.kb_closable,
        kb_article_id=classification.kb_article_id,
    )
```

`src/etiology/domain/diagnostics/triage/__init__.py`:
```python
from .agent import TriageClassificationError, TriageResult, triage

__all__ = ["TriageClassificationError", "TriageResult", "triage"]
```

- [ ] **Step 5: Запустить тесты, убедиться что проходят**

Run: `pytest tests/domain/diagnostics/triage/test_agent.py -v`
Expected: PASS (5 тестов)

- [ ] **Step 6: Прогнать весь набор тестов проекта**

Run: `pytest -v`
Expected: PASS (все тесты, включая Task 1/2 и существующие `tests/agent/model_gateway`)

- [ ] **Step 7: Commit**

```bash
git add tests/domain/diagnostics src/etiology/domain/diagnostics/triage
git commit -m "feat: add Triage Agent"
```

---

## После выполнения плана

Ручная проверка (не автотест, аналогично прошлой проверке `ModelGateway`): вызвать `triage()` с реальным `AnthropicProvider` (ключ уже в `.env`) на тестовом сообщении и убедиться, что в `events` появилась осмысленная запись `incident.triaged`. Это подтверждает, что промпт реально работает с живой моделью — юнит-тесты этого не проверяют (там всегда `FakeProvider`).

Diagnostic Collector — следующий отдельный спек/план, не часть этого документа.
