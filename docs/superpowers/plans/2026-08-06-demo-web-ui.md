# Демо веб-интерфейс v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Streamlit-приложение поверх уже написанной и протестированной доменной логики Etiology — браузерная витрина для показа прототипа коллегам вместо чтения вывода в терминале.

**Architecture:** Один Streamlit entrypoint (`scripts/demo_ui.py`) + `st.navigation` из функций-страниц (не файлов — избегаем проблем с sys.path у multipage), каждая страница напрямую вызывает те же async-функции, что уже используют `scripts/demo.py`/`demo_mass_outage.py` (`triage`, `collect`, `compose`, `curate`, `coordinate`, `ApprovalGate`, `publish_approved`, analytics). Никакой новой бизнес-логики, никакого MCP-слоя.

**Tech Stack:** Python 3.14, Streamlit 1.61 (`st.navigation`/`st.Page` с callable-страницами, `st.status`, `st.cache_resource`), существующий async-домен через `asyncio.run` на каждое действие.

## Global Constraints

- Спек: `docs/superpowers/specs/2026-08-06-demo-web-ui-design.md` — читать перед началом, если что-то неясно.
- Никакой новой бизнес-логики в UI-файлах — только вызовы уже существующих доменных функций (`src/etiology/domain/...`, `src/etiology/platform_core/...`). Если кажется, что чего-то не хватает в домене — не добавлять туда логику из UI-слоя, а вернуться к спеку.
- Тенант фиксирован: `TENANT_SLUG = "keitaro-demo"` (как в `scripts/demo.py`).
- `streamlit` — новая dev-only зависимость, extras-группа `demo` в `pyproject.toml`, НЕ добавлять в основной `dependencies` и НЕ добавлять в `dev` (та группа — тест-тулинг).
- Эта работа не имеет автотестов (UI-обвязка поверх уже покрытого тестами домена, см. спек §6) — вместо TDD-цикла каждая задача проверяется живым запуском `streamlit run scripts/demo_ui.py` и просмотром в браузере. После КАЖДОЙ задачи прогонять `.venv/Scripts/python.exe -m pytest -q` — новые файлы не должны ломать существующие 85 тестов (импорт streamlit не должен требоваться ни одним тестируемым модулем).
- Понятная ошибка вместо трейсбека при недоступной БД — тот же паттерн, что уже в `scripts/demo.py` (`ConnectionRefusedError`/`OSError` → сообщение "Запустите: bash scripts/db_start.sh"), но через `st.error` + `st.stop()`, а не `SystemExit`.
- Коммитить после каждой задачи.

---

### Task 1: Зависимость streamlit + общий модуль `demo_ui_common.py`

**Files:**
- Modify: `pyproject.toml`
- Create: `scripts/demo_ui_common.py`

**Interfaces:**
- Produces: `TENANT_SLUG: str`, `run_async(coro) -> Any` (запускает корутину, ловит `ConnectionRefusedError`/`OSError` → `st.error` + `st.stop()`), `get_tenant_id() -> str`, `get_gateway() -> ModelGateway`, `get_publisher() -> EventPublisher`, `get_approval_gate() -> ApprovalGate` — все последующие страницы импортируют эти имена из `demo_ui_common`.

- [ ] **Step 1: Добавить extras-группу `demo` в pyproject.toml**

В `pyproject.toml`, сразу после `[project.optional-dependencies]` / `dev = [...]`, добавить:

```toml
demo = [
    "streamlit>=1.60,<2",
]
```

- [ ] **Step 2: Установить зависимость**

Run: `.venv/Scripts/python.exe -m pip install -e ".[demo]"`
Expected: `Successfully installed streamlit-...` (и её транзитивные зависимости)

- [ ] **Step 3: Написать `scripts/demo_ui_common.py`**

```python
"""Общая обвязка для scripts/demo_ui.py и demo_ui_*.py страниц: та же доменная
логика, что использует scripts/demo.py, но вызываемая из синхронного Streamlit.
Никакой новой бизнес-логики — только адаптация async-домена под Streamlit-ререны."""
import asyncio
from typing import Any, Coroutine

import streamlit as st

from etiology.agent.model_gateway import ModelGateway
from etiology.agent.model_gateway.providers.anthropic_provider import AnthropicProvider
from etiology.config import get_settings
from etiology.data.db.pool import get_pool
from etiology.platform_core.approval_gate import ApprovalGate
from etiology.platform_core.event_bus import EventPublisher

TENANT_SLUG = "keitaro-demo"


def run_async(coro: Coroutine) -> Any:
    try:
        return asyncio.run(coro)
    except (ConnectionRefusedError, OSError):
        st.error("Не удалось подключиться к локальной БД. Запустите: `bash scripts/db_start.sh`")
        st.stop()


async def _resolve_tenant_id() -> str:
    pool = await get_pool()
    tenant_id = await pool.fetchval("SELECT id FROM tenants WHERE slug = $1", TENANT_SLUG)
    if tenant_id is None:
        st.error(f"Демо-тенант {TENANT_SLUG!r} не найден. Запустите: `python scripts/seed_demo.py`")
        st.stop()
    return str(tenant_id)


def get_tenant_id() -> str:
    if "tenant_id" not in st.session_state:
        st.session_state["tenant_id"] = run_async(_resolve_tenant_id())
    return st.session_state["tenant_id"]


@st.cache_resource
def get_gateway() -> ModelGateway:
    settings = get_settings()
    return ModelGateway([AnthropicProvider(api_key=settings.anthropic_api_key)])


@st.cache_resource
def get_publisher() -> EventPublisher:
    return EventPublisher()


@st.cache_resource
def get_approval_gate() -> ApprovalGate:
    return ApprovalGate()
```

- [ ] **Step 4: Прогнать тесты — убедиться, что новый файл ничего не сломал**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `85 passed` (столько же, сколько до этой задачи — новый файл не добавляет тестов)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml scripts/demo_ui_common.py
git commit -m "chore: добавлена зависимость streamlit и общая обвязка demo_ui_common.py"
```

---

### Task 2: Страница "Пайплайн"

**Files:**
- Create: `scripts/demo_ui_pipeline.py`

**Interfaces:**
- Consumes: `TENANT_SLUG`, `run_async`, `get_tenant_id`, `get_gateway`, `get_publisher`, `get_approval_gate` из `demo_ui_common` (Task 1); `triage` из `etiology.domain.diagnostics.triage`; `collect` из `etiology.domain.diagnostics.diagnostic_collector`; `compose` из `etiology.domain.diagnostics.bug_report_composer`; `curate` из `etiology.domain.knowledge_base`; `record_csat` из `etiology.domain.analytics`; `EventReader` из `etiology.platform_core.event_bus`.
- Produces: `render() -> None` — вызывается из `scripts/demo_ui.py` (Task 5) как `st.Page`.

- [ ] **Step 1: Написать `scripts/demo_ui_pipeline.py`**

```python
"""Страница 'Пайплайн': тот же сценарий, что scripts/demo.py, но в браузере."""
import streamlit as st

from demo_ui_common import get_approval_gate, get_gateway, get_publisher, get_tenant_id, run_async
from etiology.domain.analytics import record_csat
from etiology.domain.diagnostics.bug_report_composer import compose
from etiology.domain.diagnostics.diagnostic_collector import collect
from etiology.domain.diagnostics.triage import triage
from etiology.domain.knowledge_base import curate
from etiology.platform_core.event_bus import EventReader


def render() -> None:
    st.title("Пайплайн: обращение клиента")
    raw_message = st.text_area("Текст обращения клиента", height=100)
    csat_score = st.slider("CSAT-оценка (0 = не записывать)", 0, 5, 0)

    if not (st.button("Отправить", type="primary") and raw_message.strip()):
        return

    tenant_id = get_tenant_id()
    gateway = get_gateway()
    publisher = get_publisher()

    st.subheader(f"Клиент: {raw_message}")

    with st.status("Triage Agent...", expanded=True) as status:
        triage_result = run_async(triage(tenant_id, raw_message, gateway=gateway, publisher=publisher))
        st.write(f"severity={triage_result.severity}  topic_tag={triage_result.topic_tag}")
        st.write(f"kb_closable={triage_result.kb_closable}  kb_article_id={triage_result.kb_article_id}")
        status.update(label="Triage завершён", state="complete")

    with st.status("Diagnostic Collector...", expanded=True) as status:
        diag_result = run_async(
            collect(tenant_id, raw_message, triage_result, gateway=gateway, publisher=publisher)
        )
        st.write(f"outcome={diag_result.outcome}  escalated_to_human={diag_result.escalated_to_human}")
        if diag_result.matched_command:
            st.write(f"matched_command: {diag_result.matched_command.command}")
        st.write("Текст клиенту:")
        st.info(diag_result.advisory_text)
        status.update(label="Diagnostic Collector завершён", state="complete")

    if diag_result.outcome == "needs_bug_report":
        with st.status("Bug Report Composer...", expanded=True) as status:
            bug_report = run_async(
                compose(tenant_id, triage_result.incident_id, gateway=gateway, publisher=publisher)
            )
            st.write(f"**{bug_report.title}**")
            st.write(f"environment: {bug_report.environment}")
            st.write("steps_to_reproduce:")
            for step in bug_report.steps_to_reproduce:
                st.write(f"- {step}")
            st.write(f"diagnostic_summary: {bug_report.diagnostic_summary}")
            status.update(label="Bug Report составлен", state="complete")

        with st.status("Knowledge Curator...", expanded=True) as status:
            approval_gate = get_approval_gate()
            curator_result = run_async(
                curate(
                    tenant_id, triage_result.incident_id,
                    gateway=gateway, approval_gate=approval_gate, publisher=publisher,
                )
            )
            if curator_result.proposed:
                st.success(
                    f"Предложена статья KB: {curator_result.title!r} "
                    f"(approval_id={curator_result.suggestion_id})"
                )
                st.caption("Черновик ждёт утверждения человеком на странице Approval Gate.")
            else:
                st.write("Curator решил не предлагать новую статью.")
            status.update(label="Curator завершён", state="complete")

    if csat_score:
        run_async(record_csat(tenant_id, triage_result.incident_id, csat_score, get_publisher(), comment=None))
        st.write(f"CSAT записан: {csat_score}/5")

    st.subheader("Event Store (audit trail)")
    reader = EventReader()
    events = run_async(reader.read_aggregate_events(tenant_id, "incident", triage_result.incident_id))
    for event in events:
        st.write(f"`[{event.created_at:%H:%M:%S}]` {event.event_type}")
```

- [ ] **Step 2: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `85 passed`

- [ ] **Step 3: Commit**

```bash
git add scripts/demo_ui_pipeline.py
git commit -m "feat: страница Пайплайн для демо веб-интерфейса"
```

---

### Task 3: Страница "Approval Gate"

**Files:**
- Create: `scripts/demo_ui_approval_gate.py`

**Interfaces:**
- Consumes: `get_tenant_id`, `get_approval_gate`, `get_publisher`, `run_async` из `demo_ui_common`; `publish_approved` из `etiology.domain.knowledge_base`.
- Produces: `render() -> None`.

- [ ] **Step 1: Написать `scripts/demo_ui_approval_gate.py`**

```python
"""Страница 'Approval Gate': очередь черновиков, approve/reject, публикация
одобренных kb_suggestion (см. src/etiology/domain/knowledge_base/publish.py)."""
import streamlit as st

from demo_ui_common import get_approval_gate, get_publisher, get_tenant_id, run_async
from etiology.domain.knowledge_base import publish_approved


def render() -> None:
    st.title("Approval Gate")
    tenant_id = get_tenant_id()
    approval_gate = get_approval_gate()
    reviewed_by = st.text_input("Кто утверждает", value="ann")

    st.subheader("Ожидают решения человека")
    pending = run_async(approval_gate.list_pending(tenant_id))
    if not pending:
        st.info("Очередь пуста.")
    for item in pending:
        label = item.payload.get("title") or item.id
        with st.expander(f"[{item.object_type}] {label}"):
            st.json(item.payload)
            col1, col2 = st.columns(2)
            if col1.button("Утвердить", key=f"approve-{item.id}"):
                run_async(approval_gate.approve(tenant_id, item.id, reviewed_by=reviewed_by))
                st.session_state.setdefault("ready_to_publish", {})[item.id] = item
                st.rerun()
            if col2.button("Отклонить", key=f"reject-{item.id}"):
                run_async(approval_gate.reject(tenant_id, item.id, reviewed_by=reviewed_by))
                st.rerun()

    ready = st.session_state.get("ready_to_publish", {})
    if ready:
        st.subheader("Утверждено в этой сессии, готово к публикации")
        for approval_id, item in list(ready.items()):
            label = item.payload.get("title") or approval_id
            with st.expander(label):
                st.write(item.payload.get("body", ""))
                if item.object_type != "kb_suggestion":
                    st.caption(f"Публикация для типа {item.object_type!r} пока не реализована.")
                    continue
                if st.button("Опубликовать в базу знаний", key=f"publish-{approval_id}"):
                    article = run_async(
                        publish_approved(
                            tenant_id, approval_id,
                            approval_gate=approval_gate, publisher=get_publisher(),
                        )
                    )
                    st.success(f"Опубликовано: {article.title} (id={article.id})")
                    del ready[approval_id]
                    st.rerun()
```

- [ ] **Step 2: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `85 passed`

- [ ] **Step 3: Commit**

```bash
git add scripts/demo_ui_approval_gate.py
git commit -m "feat: страница Approval Gate для демо веб-интерфейса"
```

---

### Task 4: Страницы "Массовый сбой" и "Аналитика"

**Files:**
- Create: `scripts/demo_ui_mass_outage.py`
- Create: `scripts/demo_ui_analytics.py`

**Interfaces:**
- Consumes: `get_tenant_id`, `get_gateway`, `get_publisher`, `run_async` из `demo_ui_common`; `triage` из `etiology.domain.diagnostics.triage`; `coordinate` из `etiology.domain.escalation_sync.incident_coordination`; `top_topics`, `resolution_rate`, `csat_summary` из `etiology.domain.analytics`.
- Produces: `render() -> None` в каждом файле.

- [ ] **Step 1: Написать `scripts/demo_ui_mass_outage.py`**

```python
"""Страница 'Массовый сбой': тот же сценарий, что scripts/demo_mass_outage.py."""
import streamlit as st

from demo_ui_common import get_gateway, get_publisher, get_tenant_id, run_async
from etiology.domain.diagnostics.triage import triage
from etiology.domain.escalation_sync.incident_coordination import coordinate

MESSAGES = [
    "Трекер вообще не открывается, все ссылки на кампании дают ошибку 502",
    "У нас со всех кампаний сайт трекера не отвечает уже минут 10, это авария?",
    "Помогите, весь трафик падает мимо — домен трекера не открывается в браузере",
]


def render() -> None:
    st.title("Массовый сбой — Incident Coordination")
    st.write("Три независимых обращения про одну и ту же аварию:")
    for message in MESSAGES:
        st.write(f"- {message}")

    if not st.button("Запустить сценарий", type="primary"):
        return

    tenant_id = get_tenant_id()
    gateway = get_gateway()
    publisher = get_publisher()

    with st.status("Triage трёх обращений...", expanded=True) as status:
        for raw_message in MESSAGES:
            result = run_async(triage(tenant_id, raw_message, gateway=gateway, publisher=publisher))
            st.write(f"[{result.incident_id}] severity={result.severity} topic_tag={result.topic_tag}")
        status.update(label="Triage завершён", state="complete")

    with st.status("Incident Coordination Agent...", expanded=True) as status:
        coordination = run_async(
            coordinate(tenant_id, gateway=gateway, publisher=publisher, window_minutes=60)
        )
        if not coordination.correlated:
            st.warning("Агент не нашёл корреляции в этом прогоне (LLM не детерминирован).")
        else:
            for group in coordination.groups:
                st.write(f"Master-инцидент: {group.master_incident_id}")
                st.write(f"В группе: {group.incident_ids}")
                st.write(f"Статус: {group.status_summary}")
        status.update(label="Готово", state="complete")
```

- [ ] **Step 2: Написать `scripts/demo_ui_analytics.py`**

```python
"""Страница 'Аналитика': read-model поверх Event Store."""
import streamlit as st

from demo_ui_common import get_tenant_id, run_async
from etiology.domain.analytics import csat_summary, resolution_rate, top_topics


def render() -> None:
    st.title("Аналитика")
    tenant_id = get_tenant_id()
    topics = run_async(top_topics(tenant_id))
    rate = run_async(resolution_rate(tenant_id))
    csat = run_async(csat_summary(tenant_id))

    col1, col2, col3 = st.columns(3)
    col1.metric("Resolution rate", f"{rate.rate:.0%}", f"{rate.resolved_count}/{rate.triaged_count}")
    col2.metric(
        "CSAT среднее",
        f"{csat.avg_score:.1f}" if csat.avg_score is not None else "—",
        f"{csat.count} оценок",
    )
    col3.metric("Инцидентов (triaged)", rate.triaged_count)

    st.subheader("Топ тем")
    if topics:
        st.table({"topic_tag": [t.topic_tag for t in topics], "count": [t.count for t in topics]})
    else:
        st.info("Пока нет данных.")
```

- [ ] **Step 3: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `85 passed`

- [ ] **Step 4: Commit**

```bash
git add scripts/demo_ui_mass_outage.py scripts/demo_ui_analytics.py
git commit -m "feat: страницы Массовый сбой и Аналитика для демо веб-интерфейса"
```

---

### Task 5: Entrypoint и сборка навигации

**Files:**
- Create: `scripts/demo_ui.py`
- Modify: `DEMO.md`

**Interfaces:**
- Consumes: `render` из `demo_ui_pipeline`, `demo_ui_approval_gate`, `demo_ui_mass_outage`, `demo_ui_analytics` (Tasks 2-4).

- [ ] **Step 1: Написать `scripts/demo_ui.py`**

```python
#!/usr/bin/env python3
"""Точка входа демо веб-интерфейса. Запуск: streamlit run scripts/demo_ui.py
(см. DEMO.md). Никакой бизнес-логики — только сборка навигации из страниц."""
import streamlit as st

import demo_ui_analytics
import demo_ui_approval_gate
import demo_ui_mass_outage
import demo_ui_pipeline

st.set_page_config(page_title="Etiology — демо", layout="wide")

pg = st.navigation(
    [
        st.Page(demo_ui_pipeline.render, title="Пайплайн", icon="🔍", default=True),
        st.Page(demo_ui_approval_gate.render, title="Approval Gate", icon="✅"),
        st.Page(demo_ui_mass_outage.render, title="Массовый сбой", icon="🔥"),
        st.Page(demo_ui_analytics.render, title="Аналитика", icon="📊"),
    ]
)
pg.run()
```

- [ ] **Step 2: Живой прогон — запустить приложение**

Run: `bash scripts/db_start.sh && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m streamlit run scripts/demo_ui.py --server.headless true --server.port 8501`
Expected: в логе строка `You can now view your Streamlit app in your browser.` и `Local URL: http://localhost:8501`, без traceback при старте.

- [ ] **Step 3: Проверить в браузере все 4 страницы**

Открыть `http://localhost:8501`, пройти по каждой странице через навигацию слева:
- Пайплайн — ввести тестовое сообщение, отправить, убедиться что все `st.status`-блоки отрабатывают без ошибок и заполняются реальными данными
- Approval Gate — убедиться, что список pending отображается (если пусто — это нормально, если до этого не было прогонов с эскалацией)
- Массовый сбой — запустить сценарий, убедиться что корреляция отображается
- Аналитика — убедиться что метрики и таблица тем отображаются

Остановить процесс после проверки (Ctrl+C в терминале, где запускался streamlit).

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: `85 passed`

- [ ] **Step 5: Обновить DEMO.md — добавить способ запуска через UI**

В `DEMO.md`, в раздел "3. Сценарии для показа", добавить перед существующими сценариями:

```markdown
## 0. Способ показа через браузер (рекомендуется)

```bash
bash scripts/db_start.sh
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m streamlit run scripts/demo_ui.py
```

Откроется браузер на `http://localhost:8501` — слева навигация по 4 экранам (Пайплайн,
Approval Gate, Массовый сбой, Аналитика). Ниже — те же сценарии через терминал, для
скриптового прогона без интерфейса.
```

- [ ] **Step 6: Commit**

```bash
git add scripts/demo_ui.py DEMO.md
git commit -m "feat: собран веб-интерфейс демо (entrypoint + навигация), обновлён DEMO.md"
```

---

## Self-Review Checklist (для исполнителя)

- Все 4 страницы используют только уже существующие и протестированные доменные функции — новой бизнес-логики нет.
- `run_async` — единая точка обработки ошибки недоступной БД, используется во всех страницах.
- `st.cache_resource` — только на стейтлесс-объектах (`ModelGateway`, `EventPublisher`, `ApprovalGate`), не на данных запроса.
- После каждой задачи: `85 passed` в pytest (число тестов не меняется — это UI-обвязка, не домен).
