#!/usr/bin/env python3
"""Применяет неприменённые SQL-миграции из data/db/migrations по имени файла."""
import asyncio
import os
import sys
from pathlib import Path

import asyncpg

MIGRATIONS_DIR = (
    Path(__file__).resolve().parent.parent / "src" / "etiology" / "data" / "db" / "migrations"
)


async def apply_pending(database_url: str) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                filename TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        applied = {row["filename"] for row in await conn.fetch("SELECT filename FROM schema_migrations")}
        pending = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name not in applied)
        if not pending:
            print("Нет новых миграций.")
            return
        for path in pending:
            sql = path.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute("INSERT INTO schema_migrations (filename) VALUES ($1)", path.name)
            print(f"Применена: {path.name}")
    finally:
        await conn.close()


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL не задан", file=sys.stderr)
        sys.exit(1)
    asyncio.run(apply_pending(database_url))


if __name__ == "__main__":
    main()
