from etiology.main import build_app


async def test_build_app_registers_all_mcp_tools():
    server = build_app()

    tools = await server.list_tools()

    assert {t.name for t in tools} == {
        "incident_create",
        "diagnostic_collect",
        "bug_report_compose",
        "incident_coordinate",
        "post_mortem_draft",
        "kb_curate",
        "approval_gate_list_pending",
        "approval_gate_approve",
        "approval_gate_reject",
        "kb_publish_approved",
        "knowledge_base_search",
        "analytics_query",
        "csat_record",
    }
