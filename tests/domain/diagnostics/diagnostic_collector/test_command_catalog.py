from etiology.domain.diagnostics.diagnostic_collector.command_catalog import (
    DiagnosticCommand,
    _row_to_command,
    search,
)


def test_row_to_command_maps_all_fields():
    row = {
        "id": "3b1f6f0e-9a3b-4a3b-8f0e-1a2b3c4d5e6f",
        "scenario": "campaign_not_tracking_clicks",
        "command": "tail -n 200 /var/log/keitaro/tracker.log",
        "environment_version": "10.x",
        "is_read_only": True,
    }

    command = _row_to_command(row)

    assert command == DiagnosticCommand(
        id="3b1f6f0e-9a3b-4a3b-8f0e-1a2b3c4d5e6f",
        scenario="campaign_not_tracking_clicks",
        command="tail -n 200 /var/log/keitaro/tracker.log",
        environment_version="10.x",
        is_read_only=True,
    )


async def test_search_returns_empty_list_on_empty_catalog(tenant_id):
    results = await search(tenant_id, "campaign_not_tracking_clicks")

    assert results == []
