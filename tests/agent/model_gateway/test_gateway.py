import pytest

from etiology.agent.model_gateway import (
    ModelGateway,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelTier,
    NoProviderAvailable,
)
from etiology.agent.model_gateway.base import ModelProvider


class FakeProvider(ModelProvider):
    def __init__(self, name: str, tiers: set[ModelTier], fails: bool = False):
        self.name = name
        self._tiers = tiers
        self._fails = fails
        self.calls = 0

    def supports_tier(self, tier: ModelTier) -> bool:
        return tier in self._tiers

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self._fails:
            raise RuntimeError("provider down")
        return ModelResponse(
            content="ok",
            stop_reason="end_turn",
            provider=self.name,
            model="fake-model",
            input_tokens=1,
            output_tokens=1,
        )


def _request(tier: ModelTier = ModelTier.FAST) -> ModelRequest:
    return ModelRequest(tier=tier, messages=[ModelMessage(role="user", content="hi")])


async def test_routes_to_first_provider_supporting_tier():
    primary = FakeProvider("primary", tiers={ModelTier.FAST})
    gateway = ModelGateway([primary])

    response = await gateway.complete(_request())

    assert response.provider == "primary"
    assert primary.calls == 1


async def test_skips_provider_not_supporting_tier():
    wrong_tier = FakeProvider("wrong-tier", tiers={ModelTier.STRONG})
    right_tier = FakeProvider("right-tier", tiers={ModelTier.FAST})
    gateway = ModelGateway([wrong_tier, right_tier])

    response = await gateway.complete(_request(ModelTier.FAST))

    assert response.provider == "right-tier"
    assert wrong_tier.calls == 0


async def test_falls_back_to_next_provider_on_error():
    failing = FakeProvider("failing", tiers={ModelTier.FAST}, fails=True)
    backup = FakeProvider("backup", tiers={ModelTier.FAST})
    gateway = ModelGateway([failing, backup])

    response = await gateway.complete(_request())

    assert response.provider == "backup"
    assert failing.calls == 1


async def test_raises_when_no_provider_supports_tier():
    provider = FakeProvider("only-fast", tiers={ModelTier.FAST})
    gateway = ModelGateway([provider])

    with pytest.raises(NoProviderAvailable):
        await gateway.complete(_request(ModelTier.STRONG))


def test_gateway_requires_at_least_one_provider():
    with pytest.raises(ValueError):
        ModelGateway([])
