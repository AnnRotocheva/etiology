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

    async def get(self, tenant_id: str, approval_id: str) -> ApprovalItem | None:
        async with tenant_connection(tenant_id) as conn:
            row = await conn.fetchrow(
                "SELECT id, object_type, payload, status, created_by, reviewed_by, reviewed_at, created_at "
                "FROM approval_gate WHERE id = $1::uuid",
                approval_id,
            )
        return _row_to_item(row) if row is not None else None

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
