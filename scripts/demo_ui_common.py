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


@st.cache_resource
def _event_loop() -> asyncio.AbstractEventLoop:
    """Один event loop на весь процесс Streamlit-сервера. etiology.data.db.pool
    кеширует asyncpg-пул в модульном синглтоне на весь процесс — если каждый
    run_async создаёт свой loop (как asyncio.run()), пул после первого вызова
    остаётся привязан к уже закрытому loop'у и падает с InterfaceError на
    втором обращении. scripts/demo.py этой проблемы не видит: там ровно один
    asyncio.run() на весь запуск скрипта."""
    return asyncio.new_event_loop()


def run_async(coro: Coroutine) -> Any:
    try:
        return _event_loop().run_until_complete(coro)
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
