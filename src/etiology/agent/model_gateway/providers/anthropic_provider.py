from anthropic import AsyncAnthropic

from ..base import ModelProvider
from ..types import ModelRequest, ModelResponse, ModelTier

DEFAULT_TIER_MODELS: dict[ModelTier, str] = {
    ModelTier.FAST: "claude-haiku-4-5",
    ModelTier.STANDARD: "claude-sonnet-5",
    ModelTier.STRONG: "claude-opus-5",
}


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def __init__(self, api_key: str, tier_models: dict[ModelTier, str] | None = None):
        self._client = AsyncAnthropic(api_key=api_key)
        self._tier_models = tier_models or DEFAULT_TIER_MODELS

    def supports_tier(self, tier: ModelTier) -> bool:
        return tier in self._tier_models

    async def complete(self, request: ModelRequest) -> ModelResponse:
        model = self._tier_models[request.tier]
        kwargs = {}
        if request.system is not None:
            kwargs["system"] = request.system
        response = await self._client.messages.create(
            model=model,
            max_tokens=request.max_tokens,
            messages=[{"role": m.role, "content": m.content} for m in request.messages],
            **kwargs,
        )
        text = "".join(block.text for block in response.content if block.type == "text")
        return ModelResponse(
            content=text,
            stop_reason=response.stop_reason or "",
            provider=self.name,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
