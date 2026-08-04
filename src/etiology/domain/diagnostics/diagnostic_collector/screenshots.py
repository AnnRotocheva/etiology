from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection


@dataclass
class Screenshot:
    id: str
    ui_version: str
    step_description: str
    image_ref: str


def _row_to_screenshot(row) -> Screenshot:
    return Screenshot(
        id=str(row["id"]),
        ui_version=row["ui_version"],
        step_description=row["step_description"],
        image_ref=row["image_ref"],
    )


async def search(tenant_id: str, query: str, limit: int = 3) -> list[Screenshot]:
    """Библиотека — курируемый актив, только реальные скриншоты, пополняется вручную
    (§5 architecture.md). etiology_app имеет только SELECT.
    """
    pattern = f"%{query}%"
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, ui_version, step_description, image_ref
            FROM screenshot_library
            WHERE step_description ILIKE $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            pattern,
            limit,
        )
    return [_row_to_screenshot(row) for row in rows]
