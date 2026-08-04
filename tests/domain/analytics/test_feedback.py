import uuid

import pytest

from etiology.domain.analytics import csat_summary, record_csat
from etiology.platform_core.event_bus import EventPublisher


async def test_record_csat_and_summary(tenant_id):
    publisher = EventPublisher()
    await record_csat(tenant_id, str(uuid.uuid4()), 5, publisher, comment="отлично")
    await record_csat(tenant_id, str(uuid.uuid4()), 3, publisher)

    summary = await csat_summary(tenant_id)

    assert summary.count == 2
    assert summary.avg_score == 4.0


async def test_csat_summary_returns_none_average_when_no_data(tenant_id):
    summary = await csat_summary(tenant_id)

    assert summary.count == 0
    assert summary.avg_score is None


async def test_record_csat_rejects_score_out_of_range(tenant_id):
    publisher = EventPublisher()
    incident_id = str(uuid.uuid4())

    with pytest.raises(ValueError):
        await record_csat(tenant_id, incident_id, 6, publisher)

    with pytest.raises(ValueError):
        await record_csat(tenant_id, incident_id, 0, publisher)
