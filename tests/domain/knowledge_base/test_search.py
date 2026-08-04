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
