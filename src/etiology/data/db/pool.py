from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from etiology.config import get_settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(get_settings().database_url)
    return _pool


@asynccontextmanager
async def tenant_connection(tenant_id: str) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection scoped to one tenant's RLS context for a single transaction.

    RLS policies read `app.tenant_id` via `current_setting(..., true)`, which is NULL
    (and therefore deny-by-default) unless set here. `set_config(..., true)` scopes the
    setting to the current transaction (SET LOCAL semantics).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.tenant_id', $1, true)", tenant_id)
            yield conn
