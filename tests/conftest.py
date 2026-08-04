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
