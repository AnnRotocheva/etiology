from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection


@dataclass
class DiagnosticCommand:
    id: str
    scenario: str
    command: str
    environment_version: str | None
    is_read_only: bool


def _row_to_command(row) -> DiagnosticCommand:
    return DiagnosticCommand(
        id=str(row["id"]),
        scenario=row["scenario"],
        command=row["command"],
        environment_version=row["environment_version"],
        is_read_only=row["is_read_only"],
    )


async def search(tenant_id: str, query: str, limit: int = 1) -> list[DiagnosticCommand]:
    """Каталог — курируемый актив, пополняется только вручную (§4.4 architecture.md).
    etiology_app имеет только SELECT — эта функция никогда не пишет в каталог.
    """
    pattern = f"%{query}%"
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, scenario, command, environment_version, is_read_only
            FROM diagnostic_command_catalog
            WHERE scenario ILIKE $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            pattern,
            limit,
        )
    return [_row_to_command(row) for row in rows]
