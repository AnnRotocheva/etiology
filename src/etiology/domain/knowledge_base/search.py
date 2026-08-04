import re
from dataclasses import dataclass

from etiology.data.db.pool import tenant_connection


@dataclass
class KbArticle:
    id: str
    kind: str
    title: str
    body: str
    topic_tag: str | None


def _keyword_patterns(query: str) -> list[str]:
    """Слова длиннее 3 символов как отдельные ILIKE-паттерны. Совпадение всего
    сообщения клиента целиком как одной подстроки почти никогда не сработает —
    статья должна дословно содержать всё сообщение. Ищем наоборот: содержит ли
    статья хотя бы одно значимое слово из сообщения."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 3]
    return [f"%{w}%" for w in words] or [f"%{query}%"]


async def search(tenant_id: str, query: str, limit: int = 5) -> list[KbArticle]:
    """Простой ILIKE-поиск по title/body/topic_tag (docs/architecture.md §5).
    Полнотекстовый индекс — не сейчас, апгрейд не потребует смены сигнатуры.
    """
    patterns = _keyword_patterns(query)
    async with tenant_connection(tenant_id) as conn:
        rows = await conn.fetch(
            """
            SELECT id, kind, title, body, topic_tag
            FROM knowledge_base_articles
            WHERE title ILIKE ANY($1::text[]) OR body ILIKE ANY($1::text[]) OR topic_tag ILIKE ANY($1::text[])
            ORDER BY updated_at DESC
            LIMIT $2
            """,
            patterns,
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


async def get_by_id(tenant_id: str, article_id: str) -> KbArticle | None:
    async with tenant_connection(tenant_id) as conn:
        row = await conn.fetchrow(
            "SELECT id, kind, title, body, topic_tag FROM knowledge_base_articles WHERE id = $1::uuid",
            article_id,
        )
    if row is None:
        return None
    return KbArticle(
        id=str(row["id"]),
        kind=row["kind"],
        title=row["title"],
        body=row["body"],
        topic_tag=row["topic_tag"],
    )
