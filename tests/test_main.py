from etiology.main import build_app


async def test_build_app_registers_all_mcp_tools():
    server = build_app()

    tools = await server.list_tools()

    assert {t.name for t in tools} == {"incident_create", "knowledge_base_search", "analytics_query"}
