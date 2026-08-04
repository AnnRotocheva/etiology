from etiology.domain.diagnostics.diagnostic_collector.screenshots import (
    Screenshot,
    _row_to_screenshot,
    search,
)


def test_row_to_screenshot_maps_all_fields():
    row = {
        "id": "3b1f6f0e-9a3b-4a3b-8f0e-1a2b3c4d5e6f",
        "ui_version": "10.x",
        "step_description": "Раздел Settings -> Licensing",
        "image_ref": "s3://etiology-kb/screenshots/licensing-settings.png",
    }

    screenshot = _row_to_screenshot(row)

    assert screenshot == Screenshot(
        id="3b1f6f0e-9a3b-4a3b-8f0e-1a2b3c4d5e6f",
        ui_version="10.x",
        step_description="Раздел Settings -> Licensing",
        image_ref="s3://etiology-kb/screenshots/licensing-settings.png",
    )


async def test_search_returns_empty_list_on_empty_library(tenant_id):
    results = await search(tenant_id, "licensing")

    assert results == []
